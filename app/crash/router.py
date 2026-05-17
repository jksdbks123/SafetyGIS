import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse

from app.config import CA_COUNTIES, _CC_TO_NAME, CRASH_CACHE, PARTY_CACHE
from app.crash.ccrs import (
    _fetching_counties, _fetching_lock, _crash_progress,
    cache_county_bg, get_ccrs_parties_resources, get_ccrs_victims_resources,
    fetch_detail_records,
)
from app.osm.cache import _fetching_osm_counties, county_osm_status, fetch_county_osm_bg, _fetching_osm_lock

router = APIRouter()

_pbf_job: dict = {
    "active": False,
    "complete": False,
    "counties": [],
    "phase": "",
    "phase_done": 0,
    "phase_total": 0,
    "done": 0,
    "total": 0,
    "error": "",
}
_pbf_job_lock = threading.Lock()

_STATS_ALLOWED_FIELDS = {
    "severity", "year", "collision_type_description", "weather_1",
    "road_condition_1", "lightingdescription", "motorvehicleinvolvedwithcode", "day_of_week",
    "type",
}


def _run_pbf_county_fetch(county_names: list[str]) -> None:
    from app.osm.pbf import process_pbf_to_tiles

    def progress(phase: str, done: int, total: int) -> None:
        with _pbf_job_lock:
            _pbf_job["phase"] = phase
            _pbf_job["phase_done"] = done
            _pbf_job["phase_total"] = total

    try:
        result = process_pbf_to_tiles(county_names, progress)
        with _pbf_job_lock:
            _pbf_job["active"] = False
            _pbf_job["complete"] = True
            _pbf_job["done"] = result.get("tiles_cached", 0)
            _pbf_job["total"] = result.get("tiles_total", 0)
            _pbf_job["phase"] = "done"
            _pbf_job["error"] = ""
    except Exception as exc:
        import traceback
        print(f"[pbf] county fetch error: {exc}\n{traceback.format_exc()}", flush=True)
        with _pbf_job_lock:
            _pbf_job["active"] = False
            _pbf_job["complete"] = False
            _pbf_job["error"] = str(exc)
            _pbf_job["phase"] = ""


@router.get("/api/counties")
def get_counties():
    return JSONResponse({name: code for name, (code, _) in CA_COUNTIES.items()})


@router.get("/api/crashes/dynamic")
def get_crashes_dynamic(bbox: str = Query(..., description="west,south,east,north")):
    try:
        west, south, east, north = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")

    features:       list = []
    still_fetching: list = []
    new_bg_started  = 0
    MAX_BG_FETCHES  = 4

    for county_name, (county_code, (c_s, c_w, c_n, c_e)) in CA_COUNTIES.items():
        if c_w > east or c_e < west or c_s > north or c_n < south:
            continue
        cache_path = os.path.join(CRASH_CACHE, f"{county_name}.geojson")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                county_features = json.load(f).get("features", [])
            for feat in county_features:
                lon, lat = feat["geometry"]["coordinates"]
                if west <= lon <= east and south <= lat <= north:
                    features.append(feat)
        else:
            with _fetching_lock:
                already = county_name in _fetching_counties
                if not already and new_bg_started < MAX_BG_FETCHES:
                    _fetching_counties.add(county_name)
                    threading.Thread(
                        target=cache_county_bg,
                        args=(county_name, county_code),
                        daemon=True,
                    ).start()
                    new_bg_started += 1
                    print(f"[crash] Background fetch started: {county_name}")
            still_fetching.append(county_name)

    return JSONResponse({
        "type":     "FeatureCollection",
        "features": features,
        "fetching": still_fetching,
    })


@router.get("/api/crashes/detail")
def get_crash_detail(
    ids: str = Query(..., description="Comma-separated collision IDs (max 10)"),
    years: str = Query("", description="Comma-separated year hints to narrow search"),
):
    id_list = [i.strip() for i in ids.split(",") if i.strip()][:10]
    year_hints: set[int] = set()
    for y in years.split(","):
        try:
            year_hints.add(int(y.strip()))
        except ValueError:
            pass

    resources_p = get_ccrs_parties_resources()
    resources_v = get_ccrs_victims_resources()
    if year_hints:
        resources_p = {k: v for k, v in resources_p.items() if k in year_hints}
        resources_v = {k: v for k, v in resources_v.items() if k in year_hints}

    result: dict = {}
    for cid in id_list:
        parties = fetch_detail_records(resources_p, cid, numeric_id=True)
        victims = fetch_detail_records(resources_v, cid, numeric_id=False)
        if parties or victims:
            result[cid] = {"parties": parties, "victims": victims}
    return JSONResponse(result)


@router.post("/api/party_data")
async def get_party_data(body: dict = Body(...)):
    collision_ids: list[str] = [str(i) for i in body.get("ids", []) if i][:200]
    results: dict = {}
    uncached: list[str] = []

    for cid in collision_ids:
        cache_path = os.path.join(PARTY_CACHE, f"{cid}.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path) as f:
                    results[cid] = json.load(f)
            except Exception:
                uncached.append(cid)
        else:
            uncached.append(cid)

    if uncached:
        party_resources = get_ccrs_parties_resources()

        def _fetch_and_cache(cid: str) -> tuple[str, list]:
            records = fetch_detail_records(party_resources, cid, numeric_id=True, limit=10)
            cp = os.path.join(PARTY_CACHE, f"{cid}.json")
            try:
                with open(cp, "w") as f:
                    json.dump(records, f)
            except Exception:
                pass
            return cid, records

        with ThreadPoolExecutor(max_workers=8) as pool:
            for cid, records in pool.map(_fetch_and_cache, uncached):
                results[cid] = records

    return JSONResponse(results)


@router.get("/api/crashes/stats")
def get_crashes_stats(
    scope: str = Query(..., description="county | city"),
    county_code: int = Query(None),
    city_name: str = Query(""),
    group_by: str = Query("severity"),
    year: str = Query("", description="Comma-separated years to filter, empty = all"),
):
    if group_by not in _STATS_ALLOWED_FIELDS:
        raise HTTPException(status_code=400, detail=f"Invalid group_by: {group_by}")

    year_filter: set[int] = set()
    for y in year.split(","):
        try:
            year_filter.add(int(y.strip()))
        except ValueError:
            pass

    if scope == "county":
        if county_code is None:
            raise HTTPException(status_code=400, detail="county_code required for scope=county")
        county_name = _CC_TO_NAME.get(county_code)
        if not county_name:
            raise HTTPException(status_code=404, detail="Unknown county code")
        target_files = [os.path.join(CRASH_CACHE, f"{county_name}.geojson")]
        display_name = county_name.replace("_", " ").title() + " County"
        if not os.path.exists(target_files[0]):
            return JSONResponse({"fetching": True})
    elif scope == "city":
        city_q = city_name.strip().lower()
        if not city_q:
            raise HTTPException(status_code=400, detail="city_name required for scope=city")
        target_files = [
            os.path.join(CRASH_CACHE, f"{n}.geojson")
            for n in CA_COUNTIES
            if os.path.exists(os.path.join(CRASH_CACHE, f"{n}.geojson"))
        ]
        display_name = city_name.strip().title()
    else:
        raise HTTPException(status_code=400, detail="scope must be county or city")

    counts: dict[str, int] = {}
    total = 0
    for path in target_files:
        with open(path) as fh:
            features = json.load(fh).get("features", [])
        for feat in features:
            props = feat.get("properties", {})
            if scope == "city":
                cn = str(props.get("city_name", "")).strip().lower()
                if cn != city_q:
                    continue
            if year_filter and props.get("year") not in year_filter:
                continue
            val = props.get(group_by)
            val = str(val).strip() if val is not None else "Unknown"
            if not val:
                val = "Unknown"
            counts[val] = counts.get(val, 0) + 1
            total += 1

    sorted_groups = sorted(counts.items(), key=lambda x: -x[1])[:15]
    return JSONResponse({
        "groups":       [{"label": lbl, "count": cnt} for lbl, cnt in sorted_groups],
        "total":        total,
        "display_name": display_name,
    })


@router.post("/api/ai/query")
async def ai_query(body: dict = Body(...)):
    question = body.get("question", "")
    ctx      = body.get("context", {})
    crashes  = int(ctx.get("total_crashes", 0))
    fatal    = int(ctx.get("fatal_crashes", 0))
    osm      = int(ctx.get("osm_features", 0))
    bbox     = ctx.get("bbox", "unknown")
    answer = (
        f"[Placeholder] Received: \"{question}\"\n\n"
        f"Current view ({bbox}) contains {crashes:,} crash records "
        f"({fatal:,} fatal) and {osm:,} infrastructure features.\n\n"
        f"Connect an LLM (e.g. Claude API) to this endpoint for real safety analysis."
    )
    return {"answer": answer, "placeholder": True}


def _county_status_payload(name: str, crash_cached: set[str], pbf_snapshot: dict) -> dict:
    code, bbox = CA_COUNTIES[name]
    osm = county_osm_status(name)
    crash_ready = name in crash_cached
    crash_prog = _crash_progress.get(name, {})
    pbf_counties = set(pbf_snapshot.get("counties") or [])
    pbf_for_county = name in pbf_counties
    pbf_active = bool(pbf_snapshot.get("active"))
    fetching_osm = name in _fetching_osm_counties or (pbf_active and pbf_for_county)
    return {
        "code":                  code,
        "bbox":                  list(bbox),
        "crash_ready":           crash_ready,
        "fetching_crash":        name in _fetching_counties,
        "crash_records_fetched": crash_prog.get("fetched", 0),
        "crash_current_year":    crash_prog.get("year", 0),
        "osm_tile_total":        osm["total"],
        "osm_tile_cached":       osm["cached"],
        "osm_pct":               osm["pct"],
        "fetching_osm":          fetching_osm,
        "osm_fetch_method":      "pbf" if pbf_for_county else "tile",
        "pbf_progress":          pbf_snapshot if pbf_for_county else {},
        "analysis_ready":        crash_ready and osm["pct"] >= 95,
    }


def _crash_cached_counties() -> set[str]:
    return set(
        f[:-8] for f in os.listdir(CRASH_CACHE)
        if f.endswith(".geojson") and f[:-8] in CA_COUNTIES
    ) if os.path.isdir(CRASH_CACHE) else set()


@router.get("/api/crashes/county_status")
@router.get("/api/data/county_status")
def get_county_status(names: str = Query("", description="Optional comma-separated county names")):
    crash_cached = set(
        f[:-8] for f in os.listdir(CRASH_CACHE)
        if f.endswith(".geojson") and f[:-8] in CA_COUNTIES
    ) if os.path.isdir(CRASH_CACHE) else set()

    with _pbf_job_lock:
        pbf_snapshot = dict(_pbf_job)

    requested = [n.strip() for n in names.split(",") if n.strip()]
    county_names = requested if requested else list(CA_COUNTIES.keys())
    unknown = [n for n in county_names if n not in CA_COUNTIES]
    if unknown:
        raise HTTPException(404, f"Unknown county '{unknown[0]}'")

    result = {}
    for name in county_names:
        result[name] = _county_status_payload(name, crash_cached, pbf_snapshot)
    return JSONResponse(result)


@router.get("/api/data/county/{county_name}/status")
def get_single_county_status(county_name: str):
    if county_name not in CA_COUNTIES:
        raise HTTPException(404, f"Unknown county '{county_name}'")
    with _pbf_job_lock:
        pbf_snapshot = dict(_pbf_job)
    return JSONResponse({
        county_name: _county_status_payload(county_name, _crash_cached_counties(), pbf_snapshot)
    })


@router.post("/api/data/county/{county_name}/fetch_crash")
def fetch_county_crash(county_name: str):
    if county_name not in CA_COUNTIES:
        raise HTTPException(404, f"Unknown county '{county_name}'")
    county_code = CA_COUNTIES[county_name][0]
    cache_path  = os.path.join(CRASH_CACHE, f"{county_name}.geojson")
    with _fetching_lock:
        if os.path.exists(cache_path):
            return JSONResponse({"status": "already_cached"})
        if county_name in _fetching_counties:
            return JSONResponse({"status": "already_fetching"})
        _fetching_counties.add(county_name)
    threading.Thread(
        target=cache_county_bg, args=(county_name, county_code), daemon=True
    ).start()
    return JSONResponse({"status": "started"})


@router.post("/api/data/county/{county_name}/fetch_osm")
def fetch_county_osm(county_name: str):
    if county_name not in CA_COUNTIES:
        raise HTTPException(404, f"Unknown county '{county_name}'")
    with _fetching_osm_lock:
        if county_name in _fetching_osm_counties:
            return JSONResponse({"status": "already_fetching"})
        _fetching_osm_counties.add(county_name)
    threading.Thread(
        target=fetch_county_osm_bg, args=(county_name,), daemon=True
    ).start()
    return JSONResponse({"status": "started"})


@router.post("/api/data/county/{county_name}/fetch_osm_pbf")
def fetch_county_osm_pbf(county_name: str):
    if county_name not in CA_COUNTIES:
        raise HTTPException(404, f"Unknown county '{county_name}'")
    with _pbf_job_lock:
        if _pbf_job["active"]:
            return JSONResponse({"status": "already_fetching", "counties": _pbf_job.get("counties", [])})
        _pbf_job.update({
            "active": True,
            "complete": False,
            "counties": [county_name],
            "phase": "",
            "phase_done": 0,
            "phase_total": 0,
            "done": 0,
            "total": 0,
            "error": "",
        })
    threading.Thread(target=_run_pbf_county_fetch, args=([county_name],), daemon=True).start()
    return JSONResponse({"status": "started", "method": "pbf"})


@router.get("/api/data/osm_pbf/progress")
def osm_pbf_progress():
    with _pbf_job_lock:
        return JSONResponse(dict(_pbf_job))
