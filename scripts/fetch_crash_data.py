"""
Fetches real crash data from California CHP CCRS via data.ca.gov CKAN API.
Replaces the synthetic crash generator.

Usage: python scripts/fetch_crash_data.py

County codes (numeric): Sacramento=67, Humboldt=23
"""

import json
import os
import sys
import requests

BASE_URL    = "https://data.ca.gov/api/3/action"
PACKAGE_ID  = "ccrs"
OUTPUT_DIR  = os.path.join(os.path.dirname(__file__), "..", "data")
PAGE_SIZE   = 5000
TARGET_YEARS = {2019, 2020, 2021, 2022, 2023, 2024}

AREAS = {
    "sacramento": {"county_code": 34,  "label": "Sacramento County"},
    "humboldt":   {"county_code": 12,  "label": "Humboldt County"},
}


def get_crash_resources():
    """Return list of (year, resource_id) for Crashes_* resources."""
    resp = requests.get(f"{BASE_URL}/package_show", params={"id": PACKAGE_ID}, timeout=20)
    resp.raise_for_status()
    resources = resp.json()["result"]["resources"]
    crash_resources = []
    for r in resources:
        name = r.get("name", "")
        if not name.lower().startswith("crashes"):
            continue
        # Extract year from name like "Crashes_2024"
        parts = name.split("_")
        if len(parts) < 2:
            continue
        try:
            year = int(parts[-1])
        except ValueError:
            continue
        if year in TARGET_YEARS:
            crash_resources.append((year, r["id"]))
    crash_resources.sort()
    return crash_resources


def fetch_county(resource_id: str, county_code: int, year: int) -> list:
    """Fetch all records for a county from one resource with pagination."""
    records = []
    offset  = 0
    filters = json.dumps({"County Code": county_code})
    print(f"    Fetching county={county_code} year={year} resource={resource_id[:8]}…", end=" ", flush=True)

    while True:
        params = {
            "resource_id": resource_id,
            "filters":     filters,
            "limit":       PAGE_SIZE,
            "offset":      offset,
        }
        try:
            resp = requests.get(f"{BASE_URL}/datastore_search", params=params, timeout=60)
            resp.raise_for_status()
        except Exception as e:
            print(f"ERROR: {e}")
            break

        result = resp.json().get("result", {})
        batch  = result.get("records", [])
        records.extend(batch)

        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"{len(records)} records")
    return records


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

    # Derive severity from Special Condition field, then override for kills
    cond = str(r.get("Special Condition") or "").strip().lower()
    if killed > 0 or "fatal" in cond:
        severity = "fatal"
    elif "severe" in cond:
        severity = "severe_injury"
    elif "injury" in cond or "pain" in cond:
        severity = "other_injury"
    else:
        severity = "pdo"

    # Year from Crash Date Time (e.g. "2024-01-22T11:34:00")
    crash_dt = str(r.get("Crash Date Time") or "")
    try:
        year = int(crash_dt[:4])
    except ValueError:
        year = 0

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": {
            "id":             str(r.get("Collision Id") or r.get("_id", "")),
            "severity":       severity,
            "collision_type": str(r.get("Collision Type Description", "")).strip().lower().replace(" ", "_") or "unknown",
            "year":           year,
            "killed":         killed,
            "injured":        injured,
            "date":           crash_dt[:10] if len(crash_dt) >= 10 else crash_dt,
            "special_cond":   str(r.get("Special Condition") or "").strip(),
        },
    }


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Discovering CCRS crash resources…")
    try:
        resources = get_crash_resources()
    except Exception as e:
        print(f"ERROR discovering resources: {e}")
        sys.exit(1)

    if not resources:
        print("No crash resources found for target years — check package ID or year list.")
        sys.exit(1)

    print(f"Found {len(resources)} resource(s): {[y for y, _ in resources]}")

    for area_name, cfg in AREAS.items():
        print(f"\n=== {cfg['label']} ===")
        features = []
        seen_ids: set[str] = set()

        for year, rid in resources:
            records = fetch_county(rid, cfg["county_code"], year)
            for rec in records:
                feat = record_to_feature(rec)
                if feat is None:
                    continue
                fid = feat["properties"]["id"]
                if fid in seen_ids:
                    continue
                seen_ids.add(fid)
                features.append(feat)

        geojson   = {"type": "FeatureCollection", "features": features}
        out_path  = os.path.join(OUTPUT_DIR, f"{area_name}_crashes.geojson")
        with open(out_path, "w") as f:
            json.dump(geojson, f)
        fatal_n = sum(1 for f in features if f["properties"]["severity"] == "fatal")
        print(f"  Saved {len(features)} records ({fatal_n} fatal) → {out_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
