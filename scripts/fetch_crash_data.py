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


def get_all_resources() -> tuple[dict, dict, dict]:
    """Return ({year: id} for Crashes, Parties, InjuredWitnessPassengers)."""
    resp = requests.get(f"{BASE_URL}/package_show", params={"id": PACKAGE_ID}, timeout=20)
    resp.raise_for_status()
    crashes, parties, victims = {}, {}, {}
    for r in resp.json()["result"]["resources"]:
        raw_name = r.get("name", "")
        name = raw_name.lower()
        parts = raw_name.split("_")
        try:
            year = int(parts[-1])
        except (ValueError, IndexError):
            continue
        if year not in TARGET_YEARS:
            continue
        if name.startswith("crashes"):
            crashes[year] = r["id"]
        elif name.startswith("parties"):
            parties[year] = r["id"]
        elif name.startswith("injuredwitnesspassengers"):
            victims[year] = r["id"]
    return crashes, parties, victims


def fetch_county_records(resources: dict, county_code: int, label: str) -> list:
    """Fetch all records for a county from a set of yearly resources with pagination."""
    all_records = []
    filters = json.dumps({"County Code": county_code})
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


def group_by_collision(records: list) -> dict:
    """Group records by Collision Id, deduplicating by (collision_id, party_num, victim_num)."""
    grouped: dict[str, list] = {}
    seen: set = set()
    for rec in records:
        cid = str(rec.get("Collision Id") or "")
        if not cid:
            continue
        key = (cid, rec.get("Party Number"), rec.get("Victim Number"))
        if key in seen:
            continue
        seen.add(key)
        cleaned = {k: v for k, v in rec.items()
                   if k not in ("_id", "_full_text", "rank") and v is not None}
        grouped.setdefault(cid, []).append(cleaned)
    return grouped


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
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": props,
    }


def enrich_features(features: list, parties_dict: dict, victims_dict: dict) -> None:
    """Mutate crash features in-place: add summary flags and victim-based severity."""
    NOT_IMPAIRED = {"had not been drinking", "unknown", ""}
    DEGREE_MAP = {1: "fatal", 2: "severe_injury", 3: "other_injury", 4: "other_injury"}
    for feat in features:
        cid = feat["properties"]["id"]
        parties = parties_dict.get(cid, [])
        victims = victims_dict.get(cid, [])
        feat["properties"]["has_pedestrian"] = any(
            "pedestrian" in str(p.get("Party Type", "")).lower() for p in parties)
        feat["properties"]["has_cyclist"] = any(
            "bicycl" in str(p.get("Party Type", "")).lower() for p in parties)
        feat["properties"]["has_impaired"] = any(
            str(p.get("Party Sobriety", "")).strip().lower() not in NOT_IMPAIRED
            for p in parties)
        degrees = []
        for v in victims:
            try:
                degrees.append(int(v["Victim Degree of Injury"]))
            except (KeyError, ValueError, TypeError):
                pass
        if degrees:
            feat["properties"]["severity"] = DEGREE_MAP.get(min(degrees), "pdo")


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)

    print("Discovering CCRS resources…")
    try:
        crash_res, parties_res, victims_res = get_all_resources()
    except Exception as e:
        print(f"ERROR discovering resources: {e}")
        sys.exit(1)

    print(f"  Crashes:  {sorted(crash_res)} years")
    print(f"  Parties:  {sorted(parties_res)} years")
    print(f"  Victims:  {sorted(victims_res)} years")

    for area_name, cfg in AREAS.items():
        cc = cfg["county_code"]
        print(f"\n=== {cfg['label']} (county_code={cc}) ===")

        # Crashes
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

        # Parties
        party_records = fetch_county_records(parties_res, cc, "parties")
        parties_dict = group_by_collision(party_records)

        # Victims
        victim_records = fetch_county_records(victims_res, cc, "victims")
        victims_dict = group_by_collision(victim_records)

        # Enrich and save
        enrich_features(features, parties_dict, victims_dict)

        with open(os.path.join(CACHE_DIR, f"{area_name}.geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
        with open(os.path.join(CACHE_DIR, f"{area_name}_parties.json"), "w") as f:
            json.dump(parties_dict, f)
        with open(os.path.join(CACHE_DIR, f"{area_name}_victims.json"), "w") as f:
            json.dump(victims_dict, f)

        fatal_n = sum(1 for ft in features if ft["properties"]["severity"] == "fatal")
        n_p = sum(len(v) for v in parties_dict.values())
        n_v = sum(len(v) for v in victims_dict.values())
        print(f"  Saved: {len(features)} crashes ({fatal_n} fatal), "
              f"{n_p} parties, {n_v} victims → {CACHE_DIR}/{area_name}.*")

    print("\nDone.")


if __name__ == "__main__":
    main()
