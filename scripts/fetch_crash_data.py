"""
Fetches real crash data from California CHP CCRS via data.ca.gov CKAN API.
Also fetches Parties and InjuredWitnessPassengers tables and enriches crash features.

Usage: python scripts/fetch_crash_data.py

County codes: Sacramento=34, Humboldt=12
"""

import json
import os
import sys
import requests

BASE_URL     = "https://data.ca.gov/api/3/action"
PACKAGE_ID   = "ccrs"
CACHE_DIR    = os.path.join(os.path.dirname(__file__), "..", "data", "crash_cache")
PAGE_SIZE    = 5000
TARGET_YEARS = {2019, 2020, 2021, 2022, 2023, 2024}

AREAS = {
    "sacramento": {"county_code": 34, "label": "Sacramento County"},
    "humboldt":   {"county_code": 12, "label": "Humboldt County"},
}


def get_all_resources() -> dict:
    """Return {year: resource_id} for Crashes resources."""
    resp = requests.get(f"{BASE_URL}/package_show", params={"id": PACKAGE_ID}, timeout=20)
    resp.raise_for_status()
    crashes = {}
    for r in resp.json()["result"]["resources"]:
        raw_name = r.get("name", "")
        parts = raw_name.split("_")
        try:
            year = int(parts[-1])
        except (ValueError, IndexError):
            continue
        if year not in TARGET_YEARS:
            continue
        if raw_name.lower().startswith("crashes") and year not in crashes:
            crashes[year] = r["id"]
    return crashes


def fetch_county_records(resources: dict, county_code: int, label: str) -> list:
    """Fetch all records for a county from a set of yearly resources with pagination."""
    all_records = []
    filters = json.dumps({"County Code": str(county_code)})
    for year, resource_id in sorted(resources.items()):
        offset = 0
        print(f"    [{label}] county={county_code} year={year}…", end=" ", flush=True)
        while True:
            params = {
                "resource_id": resource_id,
                "filters":     filters,
                "limit":       PAGE_SIZE,
                "offset":      offset,
            }
            try:
                resp = requests.get(f"{BASE_URL}/datastore_search", params=params, timeout=90)
                resp.raise_for_status()
            except Exception as e:
                print(f"ERROR: {e}")
                break
            batch = resp.json().get("result", {}).get("records", [])
            all_records.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        print(f"{len(all_records)} total")
    return all_records



def record_to_feature(r: dict) -> dict | None:
    """Convert a CCRS crash record to a GeoJSON Feature. Returns None if unusable."""
    try:
        lat = float(r.get("Latitude")  or 0)
        lon = float(r.get("Longitude") or 0)
    except (ValueError, TypeError):
        return None
    if lat == 0 or lon == 0:
        return None

    killed  = int(r.get("NumberKilled")  or 0)
    injured = int(r.get("NumberInjured") or 0)

    cond = str(r.get("Special Condition") or "").strip().lower()
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
        props[k.lower().replace(" ", "_")] = v

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



def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    print("Discovering CCRS resources…")
    try:
        crash_res = get_all_resources()
    except Exception as e:
        print(f"ERROR discovering resources: {e}")
        sys.exit(1)

    print(f"  Crashes: {sorted(crash_res)} years")

    for area_name, cfg in AREAS.items():
        cc = cfg["county_code"]
        print(f"\n=== {cfg['label']} (county_code={cc}) ===")

        crash_records = fetch_county_records(crash_res, cc, "crashes")
        features, seen_ids = [], set()
        for rec in crash_records:
            feat = record_to_feature(rec)
            if feat is None:
                continue
            fid = feat["properties"]["id"]
            if fid not in seen_ids:
                seen_ids.add(fid)
                features.append(feat)

        with open(os.path.join(CACHE_DIR, f"{area_name}.geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)

        fatal_n = sum(1 for ft in features if ft["properties"]["severity"] == "fatal")
        print(f"  Saved: {len(features)} crashes ({fatal_n} fatal) → {CACHE_DIR}/{area_name}.geojson")

    print("\nDone.")


if __name__ == "__main__":
    main()
