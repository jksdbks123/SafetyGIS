import json
import os
import threading

import requests

from app.config import (
    CCRS_BASE_URL, CCRS_PACKAGE_ID, CCRS_PAGE_SIZE,
    CCRS_TARGET_YEARS, CCRS_MAX_PAGES, CRASH_CACHE,
)

_ccrs_resources_cache: dict | None = None
_ccrs_parties_res_cache: dict | None = None
_ccrs_victims_res_cache: dict | None = None
_ccrs_load_lock = threading.Lock()

_fetching_counties: set = set()
_fetching_lock = threading.Lock()
_crash_progress: dict = {}


def _load_all_ccrs_resources() -> None:
    global _ccrs_resources_cache, _ccrs_parties_res_cache, _ccrs_victims_res_cache
    if _ccrs_resources_cache is not None:
        return
    with _ccrs_load_lock:
        if _ccrs_resources_cache is not None:
            return
    crashes, parties, victims = {}, {}, {}
    try:
        resp = requests.get(f"{CCRS_BASE_URL}/package_show",
                            params={"id": CCRS_PACKAGE_ID}, timeout=20)
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
    _ccrs_resources_cache     = crashes
    _ccrs_parties_res_cache   = parties
    _ccrs_victims_res_cache   = victims


def get_ccrs_resources() -> dict:
    _load_all_ccrs_resources()
    return _ccrs_resources_cache or {}


def get_ccrs_parties_resources() -> dict:
    _load_all_ccrs_resources()
    return _ccrs_parties_res_cache or {}


def get_ccrs_victims_resources() -> dict:
    _load_all_ccrs_resources()
    return _ccrs_victims_res_cache or {}


def crash_record_to_feature(r: dict) -> dict | None:
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

    _SKIP = {"Latitude", "Longitude", "_id", "_full_text", "rank"}
    props: dict = {}
    for k, v in r.items():
        if k in _SKIP:
            continue
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        norm = k.lower().replace(" ", "_")
        props[norm] = v

    props["id"]       = str(r.get("Collision Id") or r.get("_id", ""))
    props["severity"] = severity
    props["year"]     = year
    props["killed"]   = killed
    props["injured"]  = injured
    props["date"]     = crash_dt[:10] if len(crash_dt) >= 10 else crash_dt

    mviw = str(r.get("MotorVehicleInvolvedWithCode") or "").strip().upper()
    props["has_pedestrian"] = mviw == "B"
    props["has_cyclist"]    = mviw == "E"

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": props,
    }


def fetch_county_crashes(county_code: int, county_name: str | None = None) -> list:
    resources = get_ccrs_resources()
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
                resp = requests.get(f"{CCRS_BASE_URL}/datastore_search",
                                    params=params, timeout=90)
                resp.raise_for_status()
            except Exception as e:
                print(f"[crash] county={county_code} year={year}: {e}")
                break
            batch = resp.json().get("result", {}).get("records", [])
            for rec in batch:
                feat = crash_record_to_feature(rec)
                if feat is None:
                    continue
                fid = feat["properties"]["id"]
                if fid not in seen_ids:
                    seen_ids.add(fid)
                    features.append(feat)
            if county_name and county_name in _crash_progress:
                _crash_progress[county_name] = {"fetched": len(features), "year": year}
            if len(batch) < CCRS_PAGE_SIZE:
                break
            offset += CCRS_PAGE_SIZE

    return features


def cache_county_bg(county_name: str, county_code: int) -> None:
    _crash_progress[county_name] = {"fetched": 0, "year": 0}
    try:
        features = fetch_county_crashes(county_code, county_name)
        with open(os.path.join(CRASH_CACHE, f"{county_name}.geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
        print(f"[crash] {county_name}: {len(features)} records cached")
    except Exception as e:
        print(f"[crash] Background fetch failed for {county_name}: {e}")
    finally:
        _crash_progress.pop(county_name, None)
        with _fetching_lock:
            _fetching_counties.discard(county_name)


def fetch_detail_records(resources: dict, cid: str, numeric_id: bool, limit: int = 20) -> list:
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
