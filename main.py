import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="GIS-Track Phase 1")

BASE_DIR    = os.path.dirname(__file__)
DATA_DIR    = os.path.join(BASE_DIR, "data")
STATIC_DIR  = os.path.join(BASE_DIR, "static")
MLY_CACHE   = os.path.join(DATA_DIR, "mapillary_cache")

OSM_CACHE    = os.path.join(DATA_DIR, "osm_cache")
CRASH_CACHE  = os.path.join(DATA_DIR, "crash_cache")

os.makedirs(MLY_CACHE,   exist_ok=True)
os.makedirs(OSM_CACHE,   exist_ok=True)
os.makedirs(CRASH_CACHE, exist_ok=True)

# CCRS API constants
CCRS_BASE_URL    = "https://data.ca.gov/api/3/action"
CCRS_PACKAGE_ID  = "ccrs"
CCRS_PAGE_SIZE   = 5000
CCRS_TARGET_YEARS = {2019, 2020, 2021, 2022, 2023, 2024}
CCRS_MAX_PAGES   = 40   # cap at 200 k records/county

# California counties: name → (ccrs_code, (south, west, north, east))
CA_COUNTIES: dict[str, tuple[int, tuple[float, float, float, float]]] = {
    "alameda":         (1,  (37.45, -122.38, 37.91, -121.47)),
    "alpine":          (2,  (38.50, -120.10, 38.90, -119.33)),
    "amador":          (3,  (38.19, -120.74, 38.60, -120.33)),
    "butte":           (4,  (39.50, -122.03, 40.15, -121.03)),
    "calaveras":       (5,  (37.95, -120.76, 38.52, -119.89)),
    "colusa":          (6,  (38.72, -122.57, 39.32, -121.96)),
    "contra_costa":    (7,  (37.71, -122.44, 38.07, -121.55)),
    "del_norte":       (8,  (41.48, -124.22, 41.99, -123.54)),
    "el_dorado":       (9,  (38.53, -120.74, 39.07, -119.89)),
    "fresno":          (10, (35.79, -121.16, 37.58, -118.36)),
    "glenn":           (11, (39.32, -123.01, 39.80, -121.96)),
    "humboldt":        (12, (40.00, -124.42, 41.47, -123.41)),
    "imperial":        (13, (32.50, -115.48, 33.43, -114.43)),
    "inyo":            (14, (35.79, -118.37, 38.00, -117.03)),
    "kern":            (15, (34.80, -120.07, 36.00, -117.63)),
    "kings":           (16, (35.79, -120.36, 36.74, -119.39)),
    "lake":            (17, (38.63, -123.11, 39.40, -122.15)),
    "lassen":          (18, (40.00, -121.34, 41.19, -119.88)),
    "los_angeles":     (19, (33.70, -118.95, 34.82, -117.65)),
    "madera":          (20, (36.74, -119.90, 37.95, -118.68)),
    "marin":           (21, (37.83, -123.03, 38.26, -122.43)),
    "mariposa":        (22, (37.10, -120.06, 37.95, -119.27)),
    "mendocino":       (23, (38.77, -124.02, 40.00, -122.73)),
    "merced":          (24, (36.89, -121.27, 37.63, -119.90)),
    "modoc":           (25, (41.18, -121.34, 42.00, -119.99)),
    "mono":            (26, (37.45, -119.34, 38.50, -117.83)),
    "monterey":        (27, (35.79, -121.95, 36.92, -120.21)),
    "napa":            (28, (38.17, -122.64, 38.72, -122.05)),
    "nevada":          (29, (39.07, -121.28, 39.50, -120.00)),
    "orange":          (30, (33.40, -118.11, 33.94, -117.41)),
    "placer":          (31, (38.72, -121.47, 39.32, -120.00)),
    "plumas":          (32, (39.73, -121.28, 40.42, -120.07)),
    "riverside":       (33, (33.43, -117.71, 34.08, -114.43)),
    "sacramento":      (34, (38.22, -121.87, 38.74, -121.03)),
    "san_benito":      (35, (36.35, -121.57, 36.92, -120.89)),
    "san_bernardino":  (36, (33.60, -117.66, 35.79, -114.13)),
    "san_diego":       (37, (32.53, -117.61, 33.51, -116.08)),
    "san_francisco":   (38, (37.70, -122.53, 37.83, -122.35)),
    "san_joaquin":     (39, (37.48, -121.59, 38.20, -120.92)),
    "san_luis_obispo": (40, (34.80, -121.34, 35.80, -119.44)),
    "san_mateo":       (41, (37.11, -122.53, 37.71, -122.11)),
    "santa_barbara":   (42, (34.35, -120.63, 35.10, -119.52)),
    "santa_clara":     (43, (36.89, -122.21, 37.48, -121.21)),
    "santa_cruz":      (44, (36.89, -122.32, 37.29, -121.58)),
    "shasta":          (45, (40.42, -122.88, 41.19, -121.33)),
    "sierra":          (46, (39.50, -120.51, 39.84, -120.00)),
    "siskiyou":        (47, (41.19, -123.01, 42.00, -121.33)),
    "solano":          (48, (38.02, -122.44, 38.54, -121.55)),
    "sonoma":          (49, (38.15, -123.54, 38.87, -122.34)),
    "stanislaus":      (50, (37.22, -121.27, 37.93, -120.00)),
    "sutter":          (51, (39.00, -121.90, 39.55, -121.46)),
    "tehama":          (52, (39.80, -123.01, 40.42, -121.33)),
    "trinity":         (53, (40.00, -123.55, 41.18, -122.54)),
    "tulare":          (54, (35.79, -119.48, 36.74, -117.93)),
    "tuolumne":        (55, (37.69, -120.48, 38.52, -119.20)),
    "ventura":         (56, (34.04, -119.34, 34.80, -118.63)),
    "yolo":            (57, (38.22, -122.40, 38.72, -121.46)),
    "yuba":            (58, (39.00, -121.47, 39.55, -120.99)),
}

# # Migrate old area crash files into crash_cache on first run
# for _area in ["sacramento", "humboldt"]:
#     _old = os.path.join(DATA_DIR, f"{_area}_crashes.geojson")
#     _new = os.path.join(CRASH_CACHE, f"{_area}.geojson")
#     if os.path.exists(_old) and not os.path.exists(_new):
#         shutil.copy(_old, _new)
#         print(f"[crash] Migrated {_area} → crash_cache")

# Reverse lookup: county_code → county_name
_CC_TO_NAME: dict[int, str] = {code: name for name, (code, _) in CA_COUNTIES.items()}

# Background-fetch state — tracks counties currently being fetched
_fetching_counties: set = set()
_fetching_lock = threading.Lock()

MAPILLARY_TOKEN  = os.getenv("MAPILLARY_TOKEN", "")
GOOGLE_MAPS_KEY  = os.getenv("GOOGLE_MAPS_KEY", "")
MAPILLARY_API    = "https://graph.mapillary.com"
CACHE_ZOOM       = 14          # tile granularity for Mapillary caching
OSM_CACHE_ZOOM   = 12          # tile granularity for OSM caching

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

OVERPASS_QUERY = """
[out:json][timeout:90];
(
  node["highway"="traffic_signals"]({bbox});
  node["highway"="crossing"]({bbox});
  node["highway"="stop"]({bbox});
  node["amenity"="bus_station"]({bbox});
  node["highway"="bus_stop"]({bbox});
  node["traffic_calming"]({bbox});
  node["highway"="street_lamp"]({bbox});
  way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|residential|unclassified|living_street)$"]({bbox});
  way["cycleway"]({bbox});
  way["highway"="cycleway"]({bbox});
  way["highway"="footway"]({bbox});
  way["highway"="path"]["foot"!="no"]({bbox});
  way["footway"="sidewalk"]({bbox});
  way["highway"="pedestrian"]({bbox});
);
out geom;
"""

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lon2tile(lon: float, z: int) -> int:
    return int((lon + 180) / 360 * 2 ** z)


def _lat2tile(lat: float, z: int) -> int:
    r = math.radians(lat)
    return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2 ** z)


def _tile2bbox(x: int, y: int, z: int):
    n = 2 ** z
    lon_min = x / n * 360 - 180
    lon_max = (x + 1) / n * 360 - 180
    lat_max = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_min = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_min, lat_min, lon_max, lat_max


# ---------------------------------------------------------------------------
# CCRS crash helpers
# ---------------------------------------------------------------------------

_ccrs_resources_cache: dict | None = None     # Crashes
_ccrs_parties_res_cache: dict | None = None   # Parties
_ccrs_victims_res_cache: dict | None = None   # InjuredWitnessPassengers

def _load_all_ccrs_resources() -> None:
    """Populate all three resource caches with a single package_show call."""
    global _ccrs_resources_cache, _ccrs_parties_res_cache, _ccrs_victims_res_cache
    if _ccrs_resources_cache is not None:
        return
    crashes, parties, victims = {}, {}, {}
    try:
        resp = requests.get(f"{CCRS_BASE_URL}/package_show", params={"id": CCRS_PACKAGE_ID}, timeout=20)
        resp.raise_for_status()
        for r in resp.json()["result"]["resources"]:
            raw_name = r.get("name", "")
            name = raw_name.lower()
            parts = raw_name.split("_")
            try:
                year = int(parts[-1])
            except (ValueError, IndexError):
                continue
            if year not in CCRS_TARGET_YEARS:
                continue
            if name.startswith("crashes") and year not in crashes:
                crashes[year] = r["id"]
            elif name.startswith("parties") and year not in parties:
                parties[year] = r["id"]
            elif name.startswith("injuredwitnesspassengers") and year not in victims:
                victims[year] = r["id"]
    except Exception as e:
        print(f"[crash] Failed to get CCRS resources: {e}")
    _ccrs_resources_cache = crashes
    _ccrs_parties_res_cache = parties
    _ccrs_victims_res_cache = victims

def _get_ccrs_resources() -> dict:
    _load_all_ccrs_resources()
    return _ccrs_resources_cache or {}

def _get_ccrs_parties_resources() -> dict:
    _load_all_ccrs_resources()
    return _ccrs_parties_res_cache or {}

def _get_ccrs_victims_resources() -> dict:
    _load_all_ccrs_resources()
    return _ccrs_victims_res_cache or {}


def _crash_record_to_feature(r: dict) -> dict | None:
    """Convert CCRS record to GeoJSON Feature, or None if unusable."""
    try:
        lat = float(r.get("Latitude")  or 0)
        lon = float(r.get("Longitude") or 0)
    except (ValueError, TypeError):
        return None
    if lat == 0 or lon == 0:
        return None

    killed  = int(r.get("NumberKilled")  or 0)
    injured = int(r.get("NumberInjured") or 0)
    cond    = str(r.get("Special Condition") or "").strip().lower()

    if killed > 0 or "fatal" in cond:
        severity = "fatal"
    elif "severe" in cond:
        severity = "severe_injury"
    elif "injury" in cond or "pain" in cond:
        severity = "other_injury"
    else:
        severity = "pdo"

    crash_dt = str(r.get("Crash Date Time") or "")
    try:
        year = int(crash_dt[:4])
    except ValueError:
        year = 0

    # Pass through ALL non-empty raw CCRS fields (skip coords and internal CKAN keys)
    _SKIP = {"Latitude", "Longitude", "_id", "_full_text", "rank"}
    props: dict = {}
    for k, v in r.items():
        if k in _SKIP:
            continue
        if v is None:
            continue
        # Normalize string values; skip whitespace-only strings
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        norm = k.lower().replace(" ", "_")
        props[norm] = v

    # Ensure core computed/normalized fields (override raw where needed)
    props["id"]       = str(r.get("Collision Id") or r.get("_id", ""))
    props["severity"] = severity
    props["year"]     = year
    props["killed"]   = killed
    props["injured"]  = injured
    props["date"]     = crash_dt[:10] if len(crash_dt) >= 10 else crash_dt

    # Computed flags from Crashes table (MotorVehicleInvolvedWithCode: B=Pedestrian, E=Bicycle)
    mviw = str(r.get("MotorVehicleInvolvedWithCode") or "").strip().upper()
    props["has_pedestrian"] = mviw == "B"
    props["has_cyclist"]    = mviw == "E"

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": props,
    }


def _fetch_county_crashes(county_code: int) -> list:
    """Fetch all crash GeoJSON features for a county from CCRS (all target years)."""
    resources = _get_ccrs_resources()
    if not resources:
        return []

    features: list = []
    seen_ids: set[str] = set()
    filters = json.dumps({"County Code": str(county_code)})

    for year, resource_id in sorted(resources.items()):
        offset = 0
        for _ in range(CCRS_MAX_PAGES):
            params = {
                "resource_id": resource_id,
                "filters":     filters,
                "limit":       CCRS_PAGE_SIZE,
                "offset":      offset,
            }
            try:
                resp = requests.get(f"{CCRS_BASE_URL}/datastore_search", params=params, timeout=90)
                resp.raise_for_status()
            except Exception as e:
                print(f"[crash] county={county_code} year={year}: {e}")
                break
            batch = resp.json().get("result", {}).get("records", [])
            for rec in batch:
                feat = _crash_record_to_feature(rec)
                if feat is None:
                    continue
                fid = feat["properties"]["id"]
                if fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(feat)
            if len(batch) < CCRS_PAGE_SIZE:
                break
            offset += CCRS_PAGE_SIZE

    return features


def _cache_county_bg(county_name: str, county_code: int) -> None:
    """Fetch and cache county crash data in a background thread."""
    try:
        features = _fetch_county_crashes(county_code)
        with open(os.path.join(CRASH_CACHE, f"{county_name}.geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
        print(f"[crash] {county_name}: {len(features)} records cached")
    except Exception as e:
        print(f"[crash] Background fetch failed for {county_name}: {e}")
    finally:
        with _fetching_lock:
            _fetching_counties.discard(county_name)


def _fetch_detail_records(resources: dict, cid: str, numeric_id: bool, limit: int = 20) -> list:
    """Fetch Parties or IWP records for a single CollisionId across yearly resources.
    Returns the first year that has results (crashes belong to exactly one year)."""
    filter_val = int(cid) if numeric_id and cid.isdigit() else cid
    for _year, resource_id in sorted(resources.items(), reverse=True):
        try:
            resp = requests.get(
                f"{CCRS_BASE_URL}/datastore_search",
                params={
                    "resource_id": resource_id,
                    "filters":     json.dumps({"CollisionId": filter_val}),
                    "limit":       limit,
                },
                timeout=15,
            )
            resp.raise_for_status()
            records = resp.json().get("result", {}).get("records", [])
            if records:
                return [{k: v for k, v in r.items()
                         if k not in ("_id", "_full_text", "rank") and v is not None}
                        for r in records]
        except Exception as e:
            print(f"[detail] resource {resource_id}: {e}")
    return []


def _fetch_tile(x: int, y: int) -> list:
    """Fetch or load cached Mapillary images for one z14 tile."""
    cache_path = os.path.join(MLY_CACHE, f"{CACHE_ZOOM}_{x}_{y}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    lon_min, lat_min, lon_max, lat_max = _tile2bbox(x, y, CACHE_ZOOM)
    params = {
        "fields": "id,thumb_256_url,thumb_1024_url,geometry,captured_at,compass_angle,is_pano",
        "bbox":   f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "limit":  500,
        "access_token": MAPILLARY_TOKEN,
    }
    resp = requests.get(f"{MAPILLARY_API}/images", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])

    with open(cache_path, "w") as f:
        json.dump(data, f)

    return data


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ---------------------------------------------------------------------------
# Config (exposes whether Mapillary token is set — never exposes the token itself)
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    return {"has_mapillary": bool(MAPILLARY_TOKEN)}


@app.get("/api/googlemaps/config")
def googlemaps_config():
    return {"has_google_maps": bool(GOOGLE_MAPS_KEY), "key": GOOGLE_MAPS_KEY}


# ---------------------------------------------------------------------------
# OSM & crash data
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dynamic OSM — fetches any bbox via Overpass, cached at z12 tile granularity
# IMPORTANT: this route must be registered BEFORE /api/osm/{area} so FastAPI
# doesn't match "dynamic" as a path parameter.
# ---------------------------------------------------------------------------

def _osm_tile_features(x: int, y: int) -> list:
    """Return cached or freshly-fetched OSM features for one z12 tile."""
    cache_path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    lon_min, lat_min, lon_max, lat_max = _tile2bbox(x, y, OSM_CACHE_ZOOM)
    bbox_str = f"{lat_min},{lon_min},{lat_max},{lon_max}"   # Overpass: S,W,N,E
    query = OVERPASS_QUERY.format(bbox=bbox_str)

    raw = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query}, timeout=60)
            resp.raise_for_status()
            raw = resp.json()
            break
        except Exception as e:
            print(f"  [osm] overpass {url} failed: {e}")

    if raw is None:
        return []

    # out geom; returns coordinates directly on each element — no node resolution needed.
    features = []
    for el in raw.get("elements", []):
        if el["type"] == "node":
            tags = el.get("tags", {})
            if not tags:
                continue
            hw  = tags.get("highway")
            am  = tags.get("amenity")
            tc  = tags.get("traffic_calming")
            if hw:
                ftype = hw
            elif am:
                ftype = am
            elif tc:
                ftype = "traffic_calming"
            else:
                ftype = "unknown"
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
                "properties": {"id": el["id"], "type": ftype, **tags},
            })
        elif el["type"] == "way":
            tags = el.get("tags", {})
            # out geom; embeds geometry directly — [(lon, lat), ...]
            coords = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
            if len(coords) < 2:
                continue
            hw = tags.get("highway", "")
            cy = tags.get("cycleway", "")
            ft = tags.get("footway", "")
            if hw == "cycleway" or cy:
                wtype = "cycleway"
            elif hw in ("footway", "path", "pedestrian") or ft == "sidewalk":
                wtype = "footway"
            else:
                wtype = hw or "unknown"
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"id": el["id"], "type": wtype, **tags},
            })

    with open(cache_path, "w") as f:
        json.dump(features, f)
    return features


@app.get("/api/osm/dynamic")
def get_osm_dynamic(bbox: str = Query(..., description="west,south,east,north")):
    """
    Return OSM infrastructure for any bbox.
    Responses are cached at z12 tile granularity under data/osm_cache/.
    """
    try:
        west, south, east, north = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")

    x_min = _lon2tile(west,  OSM_CACHE_ZOOM)
    x_max = _lon2tile(east,  OSM_CACHE_ZOOM)
    y_min = _lat2tile(north, OSM_CACHE_ZOOM)
    y_max = _lat2tile(south, OSM_CACHE_ZOOM)

    # Guard against enormous bboxes
    n_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
    if n_tiles > 64:
        raise HTTPException(status_code=400, detail=f"Bbox too large ({n_tiles} tiles). Zoom in first.")

    tiles: list[tuple[int, int]] = [
        (x, y)
        for x in range(x_min, x_max + 1)
        for y in range(y_min, y_max + 1)
    ]

    features: list = []
    seen:     set   = set()

    # Fetch tiles in parallel (up to 8 workers) for fast first-load
    with ThreadPoolExecutor(max_workers=min(len(tiles), 8)) as pool:
        future_map = {pool.submit(_osm_tile_features, x, y): (x, y) for x, y in tiles}
        for future in as_completed(future_map):
            x, y = future_map[future]
            try:
                tile_feats = future.result()
            except Exception as e:
                print(f"  [osm] tile {x},{y} error: {e}")
                continue
            for feat in tile_feats:
                fid = feat["properties"].get("id")
                if fid in seen:
                    continue
                seen.add(fid)
                features.append(feat)

    return JSONResponse({"type": "FeatureCollection", "features": features})


# Static pre-fetched area files (registered AFTER /api/osm/dynamic)
@app.get("/api/osm/{area}")
def get_osm(area: str):
    path = os.path.join(DATA_DIR, f"{area}_osm.geojson")
    if not os.path.exists(path):
        return JSONResponse({"type": "FeatureCollection", "features": []})
    with open(path) as f:
        return JSONResponse(json.load(f))


# Dynamic crash endpoint — MUST be before /api/crashes/{area}
@app.get("/api/crashes/dynamic")
def get_crashes_dynamic(bbox: str = Query(..., description="west,south,east,north")):
    """
    Return crash data for any California bbox.
    - Cached counties: returned immediately.
    - Uncached counties: background thread starts fetching; response includes
      a 'fetching' list so the frontend can poll until ready.
    """
    try:
        west, south, east, north = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")

    features:       list = []
    still_fetching: list = []
    new_bg_started  = 0
    MAX_BG_FETCHES  = 4   # throttle concurrent county downloads

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
            # Start background fetch — max MAX_BG_FETCHES new threads per request
            with _fetching_lock:
                already = county_name in _fetching_counties
                if not already and new_bg_started < MAX_BG_FETCHES:
                    _fetching_counties.add(county_name)
                    threading.Thread(
                        target=_cache_county_bg,
                        args=(county_name, county_code),
                        daemon=True,
                    ).start()
                    new_bg_started += 1
                    print(f"[crash] Background fetch started: {county_name}")
            still_fetching.append(county_name)

    return JSONResponse({
        "type":     "FeatureCollection",
        "features": features,
        "fetching": still_fetching,   # frontend polls until this list is empty
    })


@app.post("/api/ai/query")
async def ai_query(body: dict = Body(...)):
    """Placeholder AI analytics endpoint. Replace with real LLM call later."""
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


@app.get("/api/crashes/detail")
def get_crash_detail(
    ids: str = Query(..., description="Comma-separated collision IDs (max 10)"),
    years: str = Query("", description="Comma-separated year hints to narrow search"),
):
    """Return parties and victims for the given crash collision IDs (on-demand CKAN fetch)."""
    id_list = [i.strip() for i in ids.split(",") if i.strip()][:10]
    year_hints: set[int] = set()
    for y in years.split(","):
        try:
            year_hints.add(int(y.strip()))
        except ValueError:
            pass

    resources_p = _get_ccrs_parties_resources()
    resources_v = _get_ccrs_victims_resources()
    if year_hints:
        resources_p = {k: v for k, v in resources_p.items() if k in year_hints}
        resources_v = {k: v for k, v in resources_v.items() if k in year_hints}

    result: dict = {}
    for cid in id_list:
        parties = _fetch_detail_records(resources_p, cid, numeric_id=True)
        victims = _fetch_detail_records(resources_v, cid, numeric_id=False)
        if parties or victims:
            result[cid] = {"parties": parties, "victims": victims}
    return JSONResponse(result)


@app.get("/api/counties")
def get_counties():
    """Return {county_name: county_code} for all 58 CA counties."""
    return JSONResponse({name: code for name, (code, _) in CA_COUNTIES.items()})


_STATS_ALLOWED_FIELDS = {
    "severity", "year", "collision_type_description", "weather_1",
    "road_condition_1", "lightingdescription", "motorvehicleinvolvedwithcode", "day_of_week",
    "type",  # OSM feature type (not used here but harmless)
}

@app.get("/api/crashes/stats")
def get_crashes_stats(
    scope: str = Query(..., description="county | city"),
    county_code: int = Query(None),
    city_name: str = Query(""),
    group_by: str = Query("severity"),
    year: str = Query("", description="Comma-separated years to filter, empty = all"),
):
    """Aggregate crash counts from cached county files.
    Viewport/selection scopes are handled client-side.
    """
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


@app.get("/api/crashes/{area}")
def get_crashes(area: str):
    path = os.path.join(DATA_DIR, f"{area}_crashes.geojson")
    if not os.path.exists(path):
        return JSONResponse({"type": "FeatureCollection", "features": []})
    with open(path) as f:
        return JSONResponse(json.load(f))


# ---------------------------------------------------------------------------
# Mapillary proxy
# ---------------------------------------------------------------------------

@app.get("/api/mapillary/token")
def mapillary_token():
    """Return the token to the frontend (used to set up vector tile URLs)."""
    if not MAPILLARY_TOKEN:
        raise HTTPException(status_code=503, detail="MAPILLARY_TOKEN not configured")
    return {"token": MAPILLARY_TOKEN}


@app.get("/api/mapillary/images")
def mapillary_images(bbox: str = Query(..., description="west,south,east,north")):
    """
    Return Mapillary images within the given bbox.
    Responses are cached at z14 tile granularity under data/mapillary_cache/.
    """
    if not MAPILLARY_TOKEN:
        raise HTTPException(status_code=503, detail="MAPILLARY_TOKEN not configured")

    try:
        west, south, east, north = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")

    x_min = _lon2tile(west,  CACHE_ZOOM)
    x_max = _lon2tile(east,  CACHE_ZOOM)
    y_min = _lat2tile(north, CACHE_ZOOM)   # y-axis is flipped in tile coords
    y_max = _lat2tile(south, CACHE_ZOOM)

    features = []
    seen = set()

    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            try:
                images = _fetch_tile(x, y)
            except Exception as e:
                print(f"  [mapillary] tile {x},{y} error: {e}")
                continue
            for img in images:
                if img["id"] in seen:
                    continue
                seen.add(img["id"])
                features.append({
                    "type": "Feature",
                    "geometry": img["geometry"],
                    "properties": {
                        "id":            img["id"],
                        "thumb_256":     img.get("thumb_256_url", ""),
                        "thumb_1024":    img.get("thumb_1024_url", ""),
                        "captured_at":   img.get("captured_at", ""),
                        "compass_angle": img.get("compass_angle", 0),
                        "is_pano":       img.get("is_pano", False),
                    },
                })

    return JSONResponse({"type": "FeatureCollection", "features": features})


@app.get("/api/mapillary/image/{image_id}")
def mapillary_single(image_id: str):
    """Fetch and cache a single image's full metadata."""
    if not MAPILLARY_TOKEN:
        raise HTTPException(status_code=503, detail="MAPILLARY_TOKEN not configured")

    cache_path = os.path.join(MLY_CACHE, f"img_{image_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return JSONResponse(json.load(f))

    params = {
        "fields": "id,thumb_256_url,thumb_1024_url,captured_at,compass_angle,is_pano",
        "access_token": MAPILLARY_TOKEN,
    }
    resp = requests.get(f"{MAPILLARY_API}/{image_id}", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    with open(cache_path, "w") as f:
        json.dump(data, f)

    return JSONResponse(data)
