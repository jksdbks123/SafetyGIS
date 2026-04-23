import json
import math
import os
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv(override=True)   # always prefer .env over any inherited OS env vars

app = FastAPI(title="GIS-Track Phase 1")

BASE_DIR    = os.path.dirname(__file__)
DATA_DIR    = os.path.join(BASE_DIR, "data")
STATIC_DIR  = os.path.join(BASE_DIR, "static")
MLY_CACHE   = os.path.join(DATA_DIR, "mapillary_cache")

OSM_CACHE          = os.path.join(DATA_DIR, "osm_cache")
OSM_RELATION_CACHE = os.path.join(DATA_DIR, "osm_relation_cache")
CRASH_CACHE  = os.path.join(DATA_DIR, "crash_cache")
PARTY_CACHE  = os.path.join(DATA_DIR, "party_cache")
RANKINGS_DIR = os.environ.get("RANKINGS_DIR", os.path.join(DATA_DIR, "rankings"))
AADT_FILE    = os.path.join(DATA_DIR, "CaltransAADT", "aadt_geocoded.geojson")

os.makedirs(MLY_CACHE,          exist_ok=True)
os.makedirs(OSM_CACHE,          exist_ok=True)
os.makedirs(OSM_RELATION_CACHE, exist_ok=True)
os.makedirs(CRASH_CACHE,        exist_ok=True)
os.makedirs(PARTY_CACHE,        exist_ok=True)
os.makedirs(RANKINGS_DIR,       exist_ok=True)

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

# Reverse lookup: county_code → county_name
_CC_TO_NAME: dict[int, str] = {code: name for name, (code, _) in CA_COUNTIES.items()}

# Background-fetch state — tracks counties currently being fetched
_fetching_counties: set = set()
_fetching_lock = threading.Lock()

# Crash download progress: county_name → {"fetched": int, "year": int}
# Written by the background fetch thread; read-only in the status endpoint.
_crash_progress: dict = {}

# OSM background-fetch state (per-county systematic tile download)
_fetching_osm_counties: set = set()
_fetching_osm_lock = threading.Lock()

# Tiles currently being re-fetched to build missing relation cache
_retopology_pending: set = set()
_retopology_lock = threading.Lock()

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
  node["highway"="give_way"]({bbox});
  node["highway"="mini_roundabout"]({bbox});
  way["junction"="roundabout"]({bbox});
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
  relation["type"="restriction"]({bbox});
);
out body;
>;
out skel qt;
"""

# Way types whose nodes contribute to intersection centroid detection
_RANKABLE_HIGHWAY_FOR_CENTROIDS = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "residential", "unclassified", "living_street",
    "roundabout",
}

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


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000.0
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _bearing(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _compute_tile_topologies(
    nodes_dict: dict,
    ways_lookup: dict,   # way_id → {"tags": {}, "nid_list": [...], "wtype": str}
    node_degree: dict,
    restrictions: list,
) -> dict:
    """Compute intersection topology for all intersection nodes in a tile."""
    # Build node → connected way IDs index
    node_ways: dict = {}
    for wid, wdata in ways_lookup.items():
        for nid in wdata["nid_list"]:
            node_ways.setdefault(nid, []).append(wid)

    # Index restrictions by via_node
    restr_by_node: dict = {}
    for r in restrictions:
        restr_by_node.setdefault(r["via_node"], []).append(r)

    topologies: dict = {}

    for nid, deg in node_degree.items():
        if deg < 2 or nid not in nodes_dict:
            continue
        lon, lat = nodes_dict[nid]

        # Build approach list: one entry per rankable way connected at this node
        approaches = []
        for wid in node_ways.get(nid, []):
            wdata = ways_lookup.get(wid)
            if not wdata or wdata["wtype"] not in _RANKABLE_HIGHWAY_FOR_CENTROIDS:
                continue
            tags     = wdata["tags"]
            nid_list = wdata["nid_list"]
            try:
                idx = nid_list.index(nid)
            except ValueError:
                continue
            # Pick adjacent node: prefer forward (idx+1), fall back to backward (idx-1)
            if idx + 1 < len(nid_list) and nid_list[idx + 1] in nodes_dict:
                adj_nid = nid_list[idx + 1]
                fwd = True
            elif idx - 1 >= 0 and nid_list[idx - 1] in nodes_dict:
                adj_nid = nid_list[idx - 1]
                fwd = False
            else:
                continue
            adj_lon, adj_lat = nodes_dict[adj_nid]
            brg = _bearing(lon, lat, adj_lon, adj_lat)

            oneway_tag = tags.get("oneway", "")
            if oneway_tag == "yes":
                oneway = 1 if fwd else -1
            elif oneway_tag == "-1":
                oneway = -1 if fwd else 1
            else:
                oneway = 0

            approaches.append({
                "way_id":     wid,
                "name":       tags.get("name") or tags.get("ref", ""),
                "highway":    wdata["wtype"],
                "oneway":     oneway,
                "lanes":      int(tags.get("lanes", 1)),
                "turn_lanes": tags.get("turn:lanes", ""),
                "bearing":    round(brg, 1),
            })

        if not approaches:
            continue

        # Classify configuration
        wtypes = {a["highway"] for a in approaches}
        names  = [a["name"] for a in approaches if a["name"]]
        oneways = [a["oneway"] for a in approaches]

        if "roundabout" in wtypes:
            config = "ROUNDABOUT"
        elif any(hw.endswith("_link") for hw in wtypes):
            link_brgs  = [a["bearing"] for a in approaches if a["highway"].endswith("_link")]
            other_brgs = [a["bearing"] for a in approaches if not a["highway"].endswith("_link")]
            config = "UNDIVIDED"
            for lb in link_brgs:
                for ob in other_brgs:
                    if abs((lb - ob + 180) % 360 - 180) <= 30:
                        config = "CHANNELIZED_RT"
                        break
                if config == "CHANNELIZED_RT":
                    break
        elif names and any(oneways):
            name_counts: dict = {}
            for a in approaches:
                if a["name"]:
                    name_counts[a["name"]] = name_counts.get(a["name"], 0) + 1
            config = "DIVIDED" if any(v >= 2 for v in name_counts.values()) else "UNDIVIDED"
        else:
            config = "UNDIVIDED"

        # Conflict points (Garber & Hoel, capped at 56)
        n  = len(approaches)
        cp = min(3 * n * (n - 1) // 2, 56)
        node_restrictions = restr_by_node.get(nid, [])
        no_count = sum(1 for r in node_restrictions if r["restriction"].startswith("no_"))
        cp = max(0, cp - 2 * no_count)

        topologies[str(nid)] = {
            "configuration":  config,
            "approaches":     approaches,
            "restrictions":   [
                {"id": r["id"], "restriction": r["restriction"],
                 "from_way": r["from_way"], "to_way": r["to_way"]}
                for r in node_restrictions
            ],
            "conflict_points": cp,
            "compound_nodes":  [],
        }

    # Compound node detection: pair nodes within 80 m sharing a named road
    topo_ids = list(topologies.keys())
    for i, a_str in enumerate(topo_ids):
        a_int = int(a_str)
        lon_a, lat_a = nodes_dict[a_int]
        names_a = {
            ways_lookup[wid]["tags"].get("name") or ways_lookup[wid]["tags"].get("ref", "")
            for wid in node_ways.get(a_int, [])
            if wid in ways_lookup and
               (ways_lookup[wid]["tags"].get("name") or ways_lookup[wid]["tags"].get("ref"))
        }
        for b_str in topo_ids[i + 1:]:
            b_int = int(b_str)
            lon_b, lat_b = nodes_dict[b_int]
            if _haversine_m(lat_a, lon_a, lat_b, lon_b) > 80:
                continue
            names_b = {
                ways_lookup[wid]["tags"].get("name") or ways_lookup[wid]["tags"].get("ref", "")
                for wid in node_ways.get(b_int, [])
                if wid in ways_lookup and
                   (ways_lookup[wid]["tags"].get("name") or ways_lookup[wid]["tags"].get("ref"))
            }
            if names_a & names_b:
                topologies[a_str]["compound_nodes"].append(b_int)
                topologies[b_str]["compound_nodes"].append(a_int)

    # Roundabout-way grouping: all topology nodes sharing a roundabout way → one compound group.
    # The 80 m + named-road detection above misses roundabout rings because the ring way has no
    # name. Group by shared way_id instead, pick one primary node, merge all leg-road approaches
    # into it, and mark the rest as secondaries (roundabout_primary = primary node_id).
    roundabout_groups: dict = defaultdict(set)
    for nid_str in topologies:
        nid_int = int(nid_str)
        for wid in node_ways.get(nid_int, []):
            if wid in ways_lookup and ways_lookup[wid]["wtype"] == "roundabout":
                roundabout_groups[wid].add(nid_str)

    for _wid, members in roundabout_groups.items():
        if len(members) < 2:
            continue

        primary_str = max(
            members,
            key=lambda n: sum(
                1 for a in topologies[n].get("approaches", [])
                if ways_lookup.get(a["way_id"], {}).get("wtype") != "roundabout"
            ),
        )

        # Merge non-roundabout approaches from secondaries into primary
        primary_way_ids = {a["way_id"] for a in topologies[primary_str].get("approaches", [])}
        for m_str in members:
            if m_str == primary_str:
                continue
            for appr in topologies[m_str].get("approaches", []):
                if (appr["way_id"] not in primary_way_ids and
                        ways_lookup.get(appr["way_id"], {}).get("wtype") != "roundabout"):
                    topologies[primary_str]["approaches"].append(appr)
                    primary_way_ids.add(appr["way_id"])
            topologies[m_str]["roundabout_primary"] = int(primary_str)
            if int(primary_str) not in topologies[m_str]["compound_nodes"]:
                topologies[m_str]["compound_nodes"].append(int(primary_str))
            if int(m_str) not in topologies[primary_str]["compound_nodes"]:
                topologies[primary_str]["compound_nodes"].append(int(m_str))

        # Recompute conflict points now that primary has merged approaches
        n_ap     = len(topologies[primary_str]["approaches"])
        no_count = sum(
            1 for r in topologies[primary_str].get("restrictions", [])
            if r.get("restriction", "").startswith("no_")
        )
        topologies[primary_str]["conflict_points"] = max(
            0, min(3 * n_ap * (n_ap - 1) // 2, 56) - 2 * no_count
        )

    return topologies


# ---------------------------------------------------------------------------
# CCRS crash helpers
# ---------------------------------------------------------------------------

_ccrs_resources_cache: dict | None = None     # Crashes
_ccrs_parties_res_cache: dict | None = None   # Parties
_ccrs_victims_res_cache: dict | None = None   # InjuredWitnessPassengers
_ccrs_load_lock = threading.Lock()

def _load_all_ccrs_resources() -> None:
    """Populate all three resource caches with a single package_show call."""
    global _ccrs_resources_cache, _ccrs_parties_res_cache, _ccrs_victims_res_cache
    if _ccrs_resources_cache is not None:
        return
    with _ccrs_load_lock:
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


def _fetch_county_crashes(county_code: int, county_name: str | None = None) -> list:
    """Fetch all crash GeoJSON features for a county from CCRS (all target years).

    If county_name is provided and present in _crash_progress, updates progress
    (fetched record count + current year) after each page so the status endpoint
    can expose real-time download speed.
    """
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
            if county_name and county_name in _crash_progress:
                _crash_progress[county_name] = {"fetched": len(features), "year": year}
            if len(batch) < CCRS_PAGE_SIZE:
                break
            offset += CCRS_PAGE_SIZE

    return features


def _cache_county_bg(county_name: str, county_code: int) -> None:
    """Fetch and cache county crash data in a background thread."""
    _crash_progress[county_name] = {"fetched": 0, "year": 0}
    try:
        features = _fetch_county_crashes(county_code, county_name)
        with open(os.path.join(CRASH_CACHE, f"{county_name}.geojson"), "w") as f:
            json.dump({"type": "FeatureCollection", "features": features}, f)
        print(f"[crash] {county_name}: {len(features)} records cached")
    except Exception as e:
        print(f"[crash] Background fetch failed for {county_name}: {e}")
    finally:
        _crash_progress.pop(county_name, None)
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

@app.get("/api/aadt")
def get_aadt():
    """Serve the pre-geocoded Caltrans AADT GeoJSON (all CA state routes)."""
    if not os.path.exists(AADT_FILE):
        raise HTTPException(
            404,
            "AADT data not found. Run: python scripts/geocode_caltrans_aadt.py"
        )
    return FileResponse(AADT_FILE, media_type="application/geo+json")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


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
        for attempt in range(2):   # retry once on 429
            try:
                resp = requests.post(url, data={"data": query}, timeout=60)
                if resp.status_code == 429:
                    if attempt == 0:
                        time.sleep(8)   # back off before retry
                        continue
                    raise requests.HTTPError("429 Too Many Requests", response=resp)
                resp.raise_for_status()
                raw = resp.json()
                break
            except Exception as e:
                print(f"  [osm] overpass {url} failed: {e}")
                break
        if raw is not None:
            break

    if raw is None:
        # Write empty cache so this tile counts as attempted and osm_pct advances
        with open(cache_path, "w") as f:
            json.dump([], f)
        return []

    # Two-pass parse for out body; >; out skel qt; format.
    # Pass 1: collect ALL node coordinates (tagged infra nodes + untagged skel nodes).
    # Pass 2: process ways using node-ID lists to reconstruct geometry, and track
    #         how many rankable ways reference each node → intersection centroids.
    elements = raw.get("elements", [])

    nodes_dict: dict  = {}   # node_id → (lon, lat)
    tagged_nodes: list = []  # nodes with tags (infra features for display)

    for el in elements:
        if el["type"] != "node":
            continue
        nid = el["id"]
        nodes_dict[nid] = (el["lon"], el["lat"])
        if el.get("tags"):
            tagged_nodes.append(el)

    node_degree: dict = {}   # node_id → count of rankable ways referencing it
    ways_lookup: dict = {}   # way_id → {"tags", "nid_list", "wtype"} for topology
    features = []

    for el in elements:
        if el["type"] != "way":
            continue
        tags     = el.get("tags", {})
        nid_list = el.get("nodes", [])
        coords   = [(nodes_dict[n][0], nodes_dict[n][1])
                    for n in nid_list if n in nodes_dict]
        if len(coords) < 2:
            continue
        hw = tags.get("highway", "")
        cy = tags.get("cycleway", "")
        ft = tags.get("footway", "")
        jn = tags.get("junction", "")
        if jn == "roundabout":
            wtype = "roundabout"
        elif hw == "cycleway" or cy:
            wtype = "cycleway"
        elif hw in ("footway", "path", "pedestrian") or ft == "sidewalk":
            wtype = "footway"
        else:
            wtype = hw or "unknown"

        # Track node degree for rankable road ways only
        if wtype in _RANKABLE_HIGHWAY_FOR_CENTROIDS:
            for nid in nid_list:
                node_degree[nid] = node_degree.get(nid, 0) + 1
            ways_lookup[el["id"]] = {"tags": tags, "nid_list": nid_list, "wtype": wtype}

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": el["id"], "type": wtype, **tags},
        })

    # Emit tagged infrastructure node features (signals, stops, bus stops, etc.)
    for el in tagged_nodes:
        tags = el.get("tags", {})
        hw   = tags.get("highway")
        am   = tags.get("amenity")
        tc   = tags.get("traffic_calming")
        jn   = tags.get("junction")
        if hw == "give_way":
            ftype = "give_way"
        elif hw == "mini_roundabout" or jn == "roundabout":
            ftype = "roundabout"
        elif hw:
            ftype = hw
        elif am:
            ftype = am
        elif tc:
            ftype = "traffic_calming"
        else:
            ftype = "unknown"
        lon, lat = nodes_dict[el["id"]]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": el["id"], "type": ftype, **tags},
        })

    # Pass 3 — parse type=restriction relations (must run before intersection_centroid
    # emission so topology data is available to filter secondary roundabout nodes).
    restrictions: list = []
    for el in elements:
        if el["type"] != "relation":
            continue
        tags = el.get("tags", {})
        if tags.get("type") != "restriction":
            continue
        from_way = via_node = to_way = None
        for m in el.get("members", []):
            if m["role"] == "from"  and m["type"] == "way":  from_way = m["ref"]
            if m["role"] == "via"   and m["type"] == "node": via_node = m["ref"]
            if m["role"] == "to"    and m["type"] == "way":  to_way   = m["ref"]
        if via_node and from_way and to_way:
            restrictions.append({
                "id":          el["id"],
                "restriction": tags.get("restriction", ""),
                "from_way":    from_way,
                "via_node":    via_node,
                "to_way":      to_way,
            })

    topologies = _compute_tile_topologies(nodes_dict, ways_lookup, node_degree, restrictions)

    # Embed node coordinates in each topology entry for nearest-centroid fallback
    for nid_str, topo in topologies.items():
        nid_int = int(nid_str)
        if nid_int in nodes_dict:
            topo["lon"] = nodes_dict[nid_int][0]
            topo["lat"] = nodes_dict[nid_int][1]

    # Emit topological intersection centroid features.
    # A node referenced by 2+ rankable road ways is a true intersection center.
    # Secondary roundabout ring nodes (marked roundabout_primary) are skipped —
    # the whole roundabout is represented by its primary node.
    for nid, deg in node_degree.items():
        if deg < 2 or nid not in nodes_dict:
            continue
        if topologies.get(str(nid), {}).get("roundabout_primary"):
            continue
        lon, lat = nodes_dict[nid]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": nid, "type": "intersection_centroid", "degree": deg},
        })

    with open(cache_path, "w") as f:
        json.dump(features, f)

    rel_cache_path = os.path.join(OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
    with open(rel_cache_path, "w", encoding="utf-8") as f:
        json.dump({"restrictions": restrictions, "topologies": topologies}, f)

    return features


def _county_osm_status(county_name: str) -> dict:
    """Return tile completeness for a county's OSM data (cached at z12)."""
    _, (south, west, north, east) = CA_COUNTIES[county_name]
    tiles = [
        (x, y)
        for x in range(_lon2tile(west, OSM_CACHE_ZOOM), _lon2tile(east, OSM_CACHE_ZOOM) + 1)
        for y in range(_lat2tile(north, OSM_CACHE_ZOOM), _lat2tile(south, OSM_CACHE_ZOOM) + 1)
    ]
    total  = len(tiles)
    cached = sum(
        1 for x, y in tiles
        if os.path.exists(os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json"))
    )
    pct = round(cached / total * 100, 1) if total else 0.0
    return {"total": total, "cached": cached, "pct": pct}


def _fetch_county_osm_bg(county_name: str) -> None:
    """Fetch all z12 OSM tiles for a county bbox in a background thread."""
    _, (south, west, north, east) = CA_COUNTIES[county_name]
    tiles = [
        (x, y)
        for x in range(_lon2tile(west, OSM_CACHE_ZOOM), _lon2tile(east, OSM_CACHE_ZOOM) + 1)
        for y in range(_lat2tile(north, OSM_CACHE_ZOOM), _lat2tile(south, OSM_CACHE_ZOOM) + 1)
    ]
    total = len(tiles)
    print(f"[osm] {county_name}: fetching {total} tiles")
    done = 0
    # Use 4 workers to be polite to Overpass; already-cached tiles return instantly
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_osm_tile_features, x, y): (x, y) for x, y in tiles}
        for fut in as_completed(futures):
            done += 1
            if done % 10 == 0 or done == total:
                print(f"[osm] {county_name}: {done}/{total} tiles")
    print(f"[osm] {county_name}: done")
    with _fetching_osm_lock:
        _fetching_osm_counties.discard(county_name)


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


def _retopology_bg(x: int, y: int) -> None:
    """Background worker: re-fetch a tile to populate its missing relation cache."""
    try:
        _osm_tile_features(x, y)
    finally:
        with _retopology_lock:
            _retopology_pending.discard((x, y))


@app.get("/api/osm/topology")
def get_osm_topology(
    node_id: int   = Query(..., description="OSM node ID"),
    lon:     float = Query(..., description="Longitude of the node"),
    lat:     float = Query(..., description="Latitude of the node"),
):
    """Return pre-computed intersection topology for one OSM node."""
    tx = _lon2tile(lon, OSM_CACHE_ZOOM)
    ty = _lat2tile(lat, OSM_CACHE_ZOOM)
    rel_path  = os.path.join(OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json")
    main_path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json")

    if not os.path.exists(rel_path):
        # Tile cached before relation support was added — invalidate and re-fetch so
        # the next retry from the frontend will have topology data.
        with _retopology_lock:
            if os.path.exists(main_path) and (tx, ty) not in _retopology_pending:
                _retopology_pending.add((tx, ty))
                try:
                    os.remove(main_path)
                except OSError:
                    pass
                threading.Thread(target=_retopology_bg, args=(tx, ty), daemon=True).start()

        return JSONResponse({"status": "not_cached"}, status_code=202)

    with open(rel_path, encoding="utf-8") as f:
        rel = json.load(f)
    topo = rel["topologies"].get(str(node_id))

    if not topo:
        # Nearest-centroid fallback for control-device nodes (stop, signal, give_way)
        # that have no topology entry of their own.
        best_id: int | None = None
        best_dist = 51.0
        for cand_str, cand in rel["topologies"].items():
            if cand.get("roundabout_primary"):
                continue
            if "lat" not in cand or "lon" not in cand:
                continue
            d = _haversine_m(lat, lon, cand["lat"], cand["lon"])
            if d < best_dist:
                best_dist, best_id = d, int(cand_str)
        if best_id is None:
            return JSONResponse({"status": "no_topology"}, status_code=404)
        topo = rel["topologies"][str(best_id)]
        node_id = best_id

    topo["node_id"] = node_id
    return topo


# Dynamic crash endpoint
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


@app.post("/api/party_data")
async def get_party_data(body: dict = Body(...)):
    """Batch-fetch CCRS party records for a list of collision IDs.

    Caches results to data/party_cache/{cid}.json so repeated calls for the
    same facility are served from disk. Returns {collision_id: [party_record, ...]}.
    Capped at 200 IDs per call.
    """
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
        party_resources = _get_ccrs_parties_resources()

        def _fetch_and_cache(cid: str) -> tuple[str, list]:
            records = _fetch_detail_records(party_resources, cid, numeric_id=True, limit=10)
            cache_path = os.path.join(PARTY_CACHE, f"{cid}.json")
            try:
                with open(cache_path, "w") as f:
                    json.dump(records, f)
            except Exception:
                pass
            return cid, records

        with ThreadPoolExecutor(max_workers=8) as pool:
            for cid, records in pool.map(_fetch_and_cache, uncached):
                results[cid] = records

    return JSONResponse(results)


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


# ---------------------------------------------------------------------------
# Safety Rankings — compute + serve
# ---------------------------------------------------------------------------

_rank_job: dict = {"status": "idle", "progress": 0, "message": "", "log": []}
_rank_job_lock = threading.Lock()
# Active rankings directory — can be changed at runtime via the compute endpoint.
_active_rankings_dir: str = RANKINGS_DIR

_SCRIPT_PATH = os.path.join(BASE_DIR, "scripts", "build_safety_rankings.py")

# Counties present in CA_COUNTIES (for validation)
_CA_COUNTY_NAMES = set(CA_COUNTIES.keys())


def _get_rankings_path() -> str:
    return os.path.join(_active_rankings_dir, "statewide.json")


# ---------------------------------------------------------------------------
# Rankings in-memory cache (mtime-invalidated)
# ---------------------------------------------------------------------------

_rankings_cache: dict | None = None
_rankings_cache_path: str = ""
_rankings_cache_mtime: float = 0.0


def _load_rankings() -> dict:
    """Return statewide.json as a dict, using an mtime-invalidated in-memory cache.

    The cache is automatically invalidated when build_safety_rankings.py rewrites
    the file (mtime changes).  First call after a new run pays one disk read;
    all subsequent calls for the same file version are pure dict lookups.
    """
    global _rankings_cache, _rankings_cache_path, _rankings_cache_mtime
    path = _get_rankings_path()
    if not os.path.exists(path):
        return {}
    mtime = os.path.getmtime(path)
    if _rankings_cache is None or path != _rankings_cache_path or mtime != _rankings_cache_mtime:
        print(f"[rankings] Loading {path} into memory cache", flush=True)
        with open(path, encoding="utf-8") as f:
            _rankings_cache = json.load(f)
        _rankings_cache_path  = path
        _rankings_cache_mtime = mtime
    return _rankings_cache


def _run_rankings_script(county: str | None, output_dir: str,
                         weights: str | None = None,
                         counties: str | None = None,
                         min_osm_pct: float = 80.0) -> None:
    global _active_rankings_dir
    cmd = [sys.executable, _SCRIPT_PATH]
    if counties:
        cmd += ["--counties", counties]
    elif county and county != "all":
        cmd += ["--county", county]
    if weights:
        cmd += ["--weights", weights]
    if min_osm_pct != 80.0:
        cmd += ["--min-osm-pct", str(min_osm_pct)]
    env = os.environ.copy()
    env["RANKINGS_DIR"] = output_dir
    env["PYTHONIOENCODING"] = "utf-8"   # prevent UnicodeEncodeError on Windows cp1252

    try:
        os.makedirs(output_dir, exist_ok=True)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", env=env, cwd=BASE_DIR,
        )
    except Exception as e:
        with _rank_job_lock:
            _rank_job.update({"status": "error", "message": str(e)})
        return

    total_counties = 1
    done_counties  = 0

    for raw in proc.stdout:
        line = raw.rstrip()
        with _rank_job_lock:
            _rank_job["log"].append(line)
            if len(_rank_job["log"]) > 500:
                _rank_job["log"] = _rank_job["log"][-500:]
            _rank_job["message"] = line

            if line.startswith("Processing ") and " county" in line:
                try:
                    total_counties = max(1, int(line.split()[1]))
                except (ValueError, IndexError):
                    pass
                _rank_job["progress"] = 2
            elif line.startswith("[") and "Loading crashes" in line:
                done_counties += 1
                pct = 5 + int(85 * (done_counties - 1) / total_counties)
                _rank_job["progress"] = pct
            elif "Ranking statewide" in line:
                _rank_job["progress"] = 92
            elif line.startswith("Written:"):
                _rank_job["progress"] = 100

    proc.wait()
    with _rank_job_lock:
        if proc.returncode == 0:
            _active_rankings_dir = output_dir   # point all read endpoints here
            _rank_job.update({"status": "done", "progress": 100,
                              "output_dir": output_dir})
        else:
            _rank_job.update({"status": "error",
                               "message": _rank_job["message"] or "Script failed"})


@app.get("/api/system/pick_dir")
def pick_directory():
    """Open a native macOS folder picker dialog and return the chosen path."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askdirectory(title="Select Rankings Output Folder")
        root.destroy()
        return JSONResponse({"path": path or ""})
    except Exception as e:
        raise HTTPException(500, f"Folder picker unavailable: {e}")


@app.get("/api/crashes/county_status")
@app.get("/api/data/county_status")
def get_county_status():
    """Return crash + OSM tile readiness for all 58 CA counties.

    Fields per county:
      crash_ready       – crash GeoJSON exists in crash_cache/
      fetching_crash    – background download running
      osm_tile_total    – expected z12 tiles in county bbox
      osm_tile_cached   – tiles present in osm_cache/
      osm_pct           – cached / total * 100
      fetching_osm      – background tile download running
      analysis_ready    – crash_ready AND osm_pct >= 95
    """
    crash_cached = set(
        f[:-8] for f in os.listdir(CRASH_CACHE)
        if f.endswith(".geojson") and f[:-8] in CA_COUNTIES
    ) if os.path.isdir(CRASH_CACHE) else set()

    result = {}
    for name, (code, bbox) in CA_COUNTIES.items():
        osm          = _county_osm_status(name)
        crash_ready  = name in crash_cached
        crash_prog   = _crash_progress.get(name, {})
        result[name] = {
            "code":                 code,
            "bbox":                 list(bbox),
            "crash_ready":          crash_ready,
            "fetching_crash":       name in _fetching_counties,
            "crash_records_fetched": crash_prog.get("fetched", 0),
            "crash_current_year":   crash_prog.get("year", 0),
            "osm_tile_total":       osm["total"],
            "osm_tile_cached":      osm["cached"],
            "osm_pct":              osm["pct"],
            "fetching_osm":         name in _fetching_osm_counties,
            "analysis_ready":       crash_ready and osm["pct"] >= 95,
        }
    return JSONResponse(result)


@app.post("/api/data/county/{county_name}/fetch_crash")
def fetch_county_crash(county_name: str):
    """Trigger background download of crash data for one county."""
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
        target=_cache_county_bg, args=(county_name, county_code), daemon=True
    ).start()
    return JSONResponse({"status": "started"})


@app.post("/api/data/county/{county_name}/fetch_osm")
def fetch_county_osm(county_name: str):
    """Trigger background download of all z12 OSM tiles for one county."""
    if county_name not in CA_COUNTIES:
        raise HTTPException(404, f"Unknown county '{county_name}'")
    with _fetching_osm_lock:
        if county_name in _fetching_osm_counties:
            return JSONResponse({"status": "already_fetching"})
        _fetching_osm_counties.add(county_name)
    threading.Thread(
        target=_fetch_county_osm_bg, args=(county_name,), daemon=True
    ).start()
    return JSONResponse({"status": "started"})


@app.get("/api/rankings/config")
def get_rankings_config():
    """Return current active rankings dir and list of cached counties."""
    cached = sorted(
        f[:-8] for f in os.listdir(CRASH_CACHE)
        if f.endswith(".geojson") and f[:-8] in _CA_COUNTY_NAMES
    ) if os.path.isdir(CRASH_CACHE) else []
    has_file = os.path.exists(_get_rankings_path())
    return JSONResponse({
        "active_dir": _active_rankings_dir,
        "has_rankings": has_file,
        "cached_counties": cached,
    })


@app.post("/api/rankings/compute")
def start_rankings_compute(
    county: str | None = None,
    counties: str | None = None,
    output_dir: str | None = None,
    weights: str | None = None,
    min_osm_pct: float = 80.0,
):
    """Start build_safety_rankings.py.
    counties: comma-separated list of county names to include.
    county: legacy single-county param (ignored when counties is set).
    min_osm_pct: minimum OSM tile coverage required (0 = allow any).
    output_dir: overrides RANKINGS_DIR.
    """
    if county and county != "all" and county not in _CA_COUNTY_NAMES:
        raise HTTPException(400, f"Unknown county '{county}'")
    if counties:
        unknown = [c for c in counties.split(",") if c.strip() and c.strip() not in _CA_COUNTY_NAMES]
        if unknown:
            raise HTTPException(400, f"Unknown counties: {', '.join(unknown)}")
    effective_dir = output_dir.strip() if output_dir and output_dir.strip() else _active_rankings_dir
    with _rank_job_lock:
        if _rank_job["status"] == "running":
            raise HTTPException(409, "Computation already running")
        _rank_job.update({"status": "running", "progress": 0,
                          "message": "Starting...", "log": [],
                          "output_dir": effective_dir})
    threading.Thread(
        target=_run_rankings_script,
        args=(county, effective_dir, weights, counties, min_osm_pct),
        daemon=True,
    ).start()
    return JSONResponse({"status": "started", "output_dir": effective_dir})


@app.post("/api/rankings/set_dir")
def set_rankings_dir(output_dir: str = Body(..., embed=True)):
    """Point the app at an existing statewide.json in output_dir without recomputing."""
    global _active_rankings_dir
    path = os.path.join(output_dir.strip(), "statewide.json")
    if not os.path.exists(path):
        raise HTTPException(404, f"No statewide.json found in '{output_dir}'")
    _active_rankings_dir = output_dir.strip()
    return JSONResponse({"active_dir": _active_rankings_dir})


@app.get("/api/rankings/status")
def get_rankings_status():
    """Poll computation progress."""
    with _rank_job_lock:
        return JSONResponse(dict(_rank_job, log=_rank_job["log"][-20:]))


@app.get("/api/rankings/download")
def download_rankings():
    """Download the active statewide.json."""
    path = _get_rankings_path()
    if not os.path.exists(path):
        raise HTTPException(404, "Rankings file not found. Run computation first.")
    return FileResponse(path, media_type="application/json",
                        filename="statewide_rankings.json")


@app.get("/api/rankings/bins")
def list_ranking_bins():
    """List all bin keys with facility counts and group percentile stats."""
    if not os.path.exists(_get_rankings_path()):
        raise HTTPException(404, "Rankings not computed. Run scripts/build_safety_rankings.py first.")
    data = _load_rankings()
    return JSONResponse({
        "generated_at": data["generated_at"],
        "counties": data.get("counties_included", []),
        "bins": {
            k: {
                "count":       v["facility_count"],
                "has_data":    "insufficient_data" not in v,
                "group_stats": v.get("group_stats", {}),
            }
            for k, v in data["bins"].items()
        },
    })


@app.get("/api/rankings/bin/{bin_key:path}")
def get_ranking_bin(bin_key: str):
    """Return percentile-ranked facilities for one bin key (top LIST_N by EPDO + group stats)."""
    if not os.path.exists(_get_rankings_path()):
        raise HTTPException(404, "Rankings not computed")
    data = _load_rankings()
    bin_data = data["bins"].get(bin_key)
    if not bin_data:
        raise HTTPException(404, f"Bin '{bin_key}' not found")
    return JSONResponse(bin_data)
