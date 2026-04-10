"""
scripts/build_safety_rankings.py

Offline batch script: spatial-join crash records to OSM facilities, classify
into multi-dimensional bins, rank worst-10 / best-10 statewide.

Output: data/rankings/statewide.json  (served by main.py /api/rankings/*)

Storage: output is ~5-20 MB; to redirect to an external SSD set env var:
  RANKINGS_DIR=/Volumes/MySSD/SafetyGIS/rankings python scripts/build_safety_rankings.py

Usage:
  python scripts/build_safety_rankings.py                       # all cached counties
  python scripts/build_safety_rankings.py --county sacramento   # one county
  python scripts/build_safety_rankings.py --dry-run             # stats only, no write
"""

import argparse
import gc
import json
import math
import os
import shutil
import sys
from datetime import datetime, timezone

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRASH_CACHE  = os.path.join(BASE_DIR, "data", "crash_cache")
OSM_CACHE    = os.path.join(BASE_DIR, "data", "osm_cache")
RANKINGS_DIR = os.environ.get("RANKINGS_DIR",
                              os.path.join(BASE_DIR, "data", "rankings"))
OSM_ZOOM     = 12

# ---------------------------------------------------------------------------
# EPDO weights  (FHWA Highway Safety Manual)
# ---------------------------------------------------------------------------

# Default EPDO weights (user-configurable via --weights fatal,injury,pdo)
# injury weight applies to both severe_injury and other_injury
EPDO_DEFAULTS = (10.0, 2.0, 0.2)   # fatal, injury, pdo
EPDO = {"fatal": 10.0, "severe_injury": 2.0, "other_injury": 2.0, "pdo": 0.2}
YEAR_WINDOW = 5


def set_epdo_weights(fatal: float, injury: float, pdo: float) -> None:
    """Override module-level EPDO weights (called from main() after arg parsing)."""
    EPDO["fatal"]        = fatal
    EPDO["severe_injury"] = injury
    EPDO["other_injury"]  = injury   # treat severe + other as same tier
    EPDO["pdo"]           = pdo

# ---------------------------------------------------------------------------
# Bin definitions
# ---------------------------------------------------------------------------

# Speed bins in mph — HSM Part C + California practice
# Future: refine thresholds with state-specific calibration data
SPEED_BINS = [(0, 25, "<=25mph"), (26, 40, "26-40mph"),
              (41, 55, "41-55mph"), (56, 999, ">55mph")]

# Lane count bins
LANE_BINS = [(1, 2, "1-2"), (3, 4, "3-4"), (5, 99, "5+")]

# Leg count bins for intersections
LEG_BINS = [(0, 3, "T-int"), (4, 4, "4-leg"), (5, 99, "multi")]

# ---------------------------------------------------------------------------
# Road classification
# ---------------------------------------------------------------------------

ROAD_CLASS = {
    "motorway":     "highway",   "motorway_link":  "highway",
    "trunk":        "highway",   "trunk_link":     "highway",
    "primary":      "arterial",  "primary_link":   "arterial",
    "secondary":    "arterial",  "secondary_link": "arterial",
    "tertiary":     "collector", "tertiary_link":  "collector",
    "residential":  "local",     "unclassified":   "local",
    "living_street":"local",
}
ROAD_CLASS_ORDER = ["highway", "arterial", "collector", "local"]

RANKABLE_WAY_TYPES  = set(ROAD_CLASS.keys())
# Node types ranked as intersection facilities
RANKABLE_NODE_TYPES = {"traffic_signals", "stop", "give_way"}

# Control type label for each node type
CONTROL_LABEL = {
    "traffic_signals": "signal",
    "stop":            "stop",
    "give_way":        "yield",
}

# California default posted speed (mph) when maxspeed tag absent
DEFAULT_SPEED = {"highway": 65, "arterial": 45, "collector": 35, "local": 25}
# Default lane count when lanes tag absent
DEFAULT_LANES = {"highway": 4,  "arterial": 2,  "collector": 2,  "local": 2}

# Search radii in metres (projected coordinates)
INTERSECTION_R = 50.0   # crash → node
SEGMENT_R      = 30.0   # crash → way
LEG_COUNT_R    = 30.0   # way endpoint → node (for leg count)

MIN_FACILITIES = 20   # minimum per bin to produce rankings
TOP_N          = 10   # worst / best count

# ---------------------------------------------------------------------------
# CA_COUNTIES  (county_name → (ccrs_code, (south, west, north, east)))
# Copied from main.py to avoid importing FastAPI.
# ---------------------------------------------------------------------------

CA_COUNTIES = {
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

# ---------------------------------------------------------------------------
# Coordinate projection  (equirectangular, accurate to <0.05% within ±3°)
# ---------------------------------------------------------------------------

def make_projector(lat_center: float):
    """Return (project, unproject) for metre-based distances at lat_center."""
    cos_lat = math.cos(math.radians(lat_center))
    M = 111_320.0
    def project(lon, lat):
        return (lon * cos_lat * M, lat * M)
    def unproject(x, y):
        return (x / (cos_lat * M), y / M)
    return project, unproject


# ---------------------------------------------------------------------------
# Tile math  (mirrors main.py)
# ---------------------------------------------------------------------------

def _lon2tile(lon, z):
    return int((lon + 180) / 360 * 2**z)

def _lat2tile(lat, z):
    r = math.radians(lat)
    return int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * 2**z)

def get_county_tiles(county_name: str) -> list:
    _, (south, west, north, east) = CA_COUNTIES[county_name]
    x0 = _lon2tile(west,  OSM_ZOOM)
    x1 = _lon2tile(east,  OSM_ZOOM)
    y0 = _lat2tile(north, OSM_ZOOM)   # y-axis flipped in tile coords
    y1 = _lat2tile(south, OSM_ZOOM)
    return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def check_county_readiness(county_name: str) -> tuple[bool, str]:
    """Return (ok, message).

    ok=False  → skip this county entirely (crash data missing or OSM < 80%)
    ok=True   → process, but may print a warning if OSM is between 80-95%
    """
    crash_path = os.path.join(CRASH_CACHE, f"{county_name}.geojson")
    if not os.path.exists(crash_path):
        return False, "crash cache missing — use the County Data panel to download"

    tiles  = get_county_tiles(county_name)
    total  = len(tiles)
    cached = sum(
        1 for x, y in tiles
        if os.path.exists(os.path.join(OSM_CACHE, f"{OSM_ZOOM}_{x}_{y}.json"))
    )
    pct = cached / total * 100 if total else 0.0

    if pct < 80:
        return False, (
            f"OSM tiles only {pct:.0f}% complete ({cached}/{total}) — "
            "use the County Data panel to download all tiles first"
        )
    if pct < 95:
        print(f"  WARNING {county_name}: OSM {pct:.0f}% complete ({cached}/{total} tiles)"
              " — some facilities near county edges may be missing")
    return True, f"OK ({cached}/{total} tiles, crash cache present)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bin(value: float, bins: list) -> str:
    for lo, hi, label in bins:
        if lo <= value <= hi:
            return label
    return bins[-1][2]


def _parse_speed_mph(tag) -> int | None:
    if not tag:
        return None
    s = str(tag).strip().lower().replace(" ", "")
    try:
        if "mph" in s:
            return round(float(s.replace("mph", "")))
        # Assume km/h, convert
        return round(float(s.replace("km/h", "").replace("kph", "")) * 0.621371)
    except ValueError:
        return None


def _haversine_m(lon1, lat1, lon2, lat2) -> float:
    R = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _way_length_m(coords: list) -> float:
    return sum(_haversine_m(*coords[i - 1], *coords[i]) for i in range(1, len(coords)))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_crashes(county_name: str) -> list:
    """Return list of crash dicts within YEAR_WINDOW with slim properties for dashboard."""
    path = os.path.join(CRASH_CACHE, f"{county_name}.geojson")
    with open(path) as f:
        features = json.load(f).get("features", [])
    cutoff = datetime.now().year - YEAR_WINDOW
    result = []
    for feat in features:
        p      = feat.get("properties", {})
        yr     = p.get("year", 0)
        if yr < cutoff:
            continue
        coords = (feat.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        result.append({
            "lon":      lon,
            "lat":      lat,
            "severity": p.get("severity", "pdo"),
            "lighting": (p.get("lightingdescription") or "Unknown")[:40],
            "pcf":      (p.get("primary_collision_factor_violation") or "Unknown")[:40],
            "weather":  (p.get("weather_1") or "Unknown")[:30],
            "day":      p.get("day_of_week", ""),
            "ped":      bool(p.get("has_pedestrian")),
            "cyc":      bool(p.get("has_cyclist")),
            "imp":      bool(p.get("has_impaired")),
        })
    return result


def load_osm_tile(x: int, y: int) -> list:
    path = os.path.join(OSM_CACHE, f"{OSM_ZOOM}_{x}_{y}.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Facility registry
# ---------------------------------------------------------------------------

def build_facility_registry(tiles: list, project) -> dict:
    """
    Load all OSM tiles for a county, deduplicate by OSM id, return dict of
    rankable facilities.

    Returns: {facility_id: {geom: Shapely, geometry: GeoJSON, ...props}}
    """
    registry = {}

    for x, y in tiles:
        for feat in load_osm_tile(x, y):
            props    = feat.get("properties", {})
            osm_id   = props.get("id")
            ftype    = props.get("type", "")
            geom_raw = feat.get("geometry", {})
            gtype    = geom_raw.get("type")

            if gtype == "Point":
                if ftype not in RANKABLE_NODE_TYPES:
                    continue
                fid = f"n{osm_id}"
                if fid in registry:  # nodes don't duplicate across tiles
                    continue
                lon, lat = geom_raw["coordinates"]
                px, py   = project(lon, lat)
                registry[fid] = {
                    "fid":          fid,
                    "geom":         Point(px, py),
                    "geometry":     geom_raw,
                    "geom_type":    "Point",
                    "osm_id":       osm_id,
                    "road_type":    ftype,
                    "name":         props.get("name", ""),
                }

            elif gtype == "LineString":
                if ftype not in RANKABLE_WAY_TYPES:
                    continue
                fid    = f"w{osm_id}"
                coords = geom_raw.get("coordinates", [])
                if len(coords) < 2:
                    continue
                # Ways span tile boundaries — keep copy with most vertices
                if fid in registry:
                    if len(coords) <= len(registry[fid]["geometry"]["coordinates"]):
                        continue

                rc = ROAD_CLASS.get(ftype, "local")
                spd = _parse_speed_mph(props.get("maxspeed"))
                if spd is None:
                    spd = DEFAULT_SPEED.get(rc, 35)
                try:
                    lanes = int(props.get("lanes") or 0)
                except (ValueError, TypeError):
                    lanes = 0
                if lanes <= 0:
                    lanes = DEFAULT_LANES.get(rc, 2)

                px_coords = [project(lon, lat) for lon, lat in coords]
                registry[fid] = {
                    "fid":        fid,
                    "geom":       LineString(px_coords),
                    "geometry":   geom_raw,
                    "geom_type":  "LineString",
                    "osm_id":     osm_id,
                    "road_type":  ftype,
                    "road_class": rc,
                    "name":       props.get("name", ""),
                    "speed_mph":  spd,
                    "lanes":      lanes,
                    "length_m":   round(_way_length_m(coords), 1),
                    # Future enrichment placeholders:
                    # "aadt": None,          # Caltrans AADT (fetch_enrichment.py)
                    # "turn_lanes": None,    # OSM turn:lanes (1.4% coverage)
                    # "median_type": None,   # not in OSM; needs Caltrans HPMS
                }

    return registry


# ---------------------------------------------------------------------------
# Spatial join
# ---------------------------------------------------------------------------

def match_crashes(
    crashes: list,
    node_geoms: list, node_ids: list, node_tree,
    way_geoms: list,  way_ids: list,  way_tree,
    project,
) -> dict:
    """
    Assign each crash to the nearest node within INTERSECTION_R AND/OR the
    nearest way within SEGMENT_R. Returns {fid: [severity_str, ...]}.
    A crash may match both an intersection node and a road segment (they are
    independent ranking categories).
    """
    crash_map: dict = {}

    for cr in crashes:
        lon, lat, severity = cr["lon"], cr["lat"], cr["severity"]
        cx, cy   = project(lon, lat)
        crash_pt = Point(cx, cy)

        if node_tree is not None:
            idxs = node_tree.query(crash_pt, predicate="dwithin",
                                   distance=INTERSECTION_R)
            if len(idxs):
                best = min(idxs, key=lambda i: node_geoms[i].distance(crash_pt))
                crash_map.setdefault(node_ids[best], []).append(cr)

        if way_tree is not None:
            idxs = way_tree.query(crash_pt, predicate="dwithin",
                                  distance=SEGMENT_R)
            if len(idxs):
                best = min(idxs, key=lambda i: way_geoms[i].distance(crash_pt))
                crash_map.setdefault(way_ids[best], []).append(cr)

    return crash_map


# ---------------------------------------------------------------------------
# Bin classification
# ---------------------------------------------------------------------------

def classify_node(fac: dict, node_geom: Point,
                  way_geoms: list, way_ids: list, way_tree,
                  way_registry: dict) -> str:
    """Compute bin key for an intersection node facility."""
    control    = CONTROL_LABEL.get(fac["road_type"], "uncontrolled")
    road_class = "local"
    speed_mph  = DEFAULT_SPEED["local"]
    leg_count  = 0

    if way_tree is not None:
        idxs = way_tree.query(node_geom, predicate="dwithin",
                              distance=INTERSECTION_R)
        best_rank = len(ROAD_CLASS_ORDER)

        for i in idxs:
            fid_w = way_ids[i]
            w = way_registry.get(fid_w)
            if w is None:
                continue
            rc   = w["road_class"]
            rank = ROAD_CLASS_ORDER.index(rc) if rc in ROAD_CLASS_ORDER else 99
            if rank < best_rank:
                best_rank  = rank
                road_class = rc
                speed_mph  = w["speed_mph"]
            elif rank == best_rank:
                speed_mph = max(speed_mph, w["speed_mph"])

            # Leg count: way endpoints within LEG_COUNT_R
            way_coords = list(w["geom"].coords)
            s = Point(way_coords[0])
            e = Point(way_coords[-1])
            if (node_geom.distance(s) <= LEG_COUNT_R or
                    node_geom.distance(e) <= LEG_COUNT_R):
                leg_count += 1

    speed_bin = _bin(speed_mph, SPEED_BINS)
    leg_bin   = _bin(leg_count, LEG_BINS)
    return f"int|{control}|{road_class}|{speed_bin}|{leg_bin}"


def classify_way(fac: dict) -> str:
    """Compute bin key for a road segment facility."""
    rc        = fac["road_class"]
    speed_bin = _bin(fac["speed_mph"], SPEED_BINS)
    lane_bin  = _bin(fac["lanes"], LANE_BINS)
    return f"seg|{rc}|{speed_bin}|{lane_bin}"


# ---------------------------------------------------------------------------
# EPDO scoring
# ---------------------------------------------------------------------------

def _count_dist(items: list, key: str) -> dict:
    """Count occurrences of each unique value for a key, return sorted by count desc."""
    d: dict = {}
    for item in items:
        v = item.get(key) or "Unknown"
        d[v] = d.get(v, 0) + 1
    return dict(sorted(d.items(), key=lambda x: -x[1]))


def compute_epdo(crashes: list) -> tuple:
    """
    Return (epdo_score, fatal_count, severe_count, total_count, distributions).
    crashes: list of crash dicts from load_crashes / match_crashes.
    """
    fatal  = sum(1 for c in crashes if c["severity"] == "fatal")
    severe = sum(1 for c in crashes if c["severity"] == "severe_injury")
    total  = len(crashes)
    score  = sum(EPDO.get(c["severity"], EPDO["pdo"]) for c in crashes)
    dists = {
        "lighting": _count_dist(crashes, "lighting"),
        "pcf":      _count_dist(crashes, "pcf"),
        "weather":  _count_dist(crashes, "weather"),
        "day":      _count_dist(crashes, "day"),
        "ped":      sum(1 for c in crashes if c.get("ped")),
        "cyc":      sum(1 for c in crashes if c.get("cyc")),
        "imp":      sum(1 for c in crashes if c.get("imp")),
    }
    return round(score, 2), fatal, severe, total, dists


# ---------------------------------------------------------------------------
# County processing
# ---------------------------------------------------------------------------

def process_county(county_name: str, global_stats: dict, dry_run: bool = False) -> dict:
    """
    Process one county: load crashes + OSM tiles, match, classify, accumulate
    facility stats into global_stats.  Large objects are freed after use.
    """
    crash_path = os.path.join(CRASH_CACHE, f"{county_name}.geojson")
    if not os.path.exists(crash_path):
        return {"county": county_name, "skipped": "no_crash_cache"}

    # 1. Load crashes
    print(f"\n[{county_name}] Loading crashes…", flush=True)
    crashes      = load_crashes(county_name)
    n_crashes    = len(crashes)
    print(f"  {n_crashes:,} crashes in {YEAR_WINDOW}-year window", flush=True)

    # 2. Tile + projection setup
    tiles        = get_county_tiles(county_name)
    _, (south, west, north, east) = CA_COUNTIES[county_name]
    project, _   = make_projector((south + north) / 2)

    # 3. Build facility registry
    print(f"  Building facility registry ({len(tiles)} tiles)…", flush=True)
    registry     = build_facility_registry(tiles, project)
    if not registry:
        del crashes
        print(f"  No OSM tiles cached — browse the map to populate osm_cache first")
        return {"county": county_name, "skipped": "no_osm_tiles"}

    # 4. Separate nodes / ways; build STRtrees
    node_geoms, node_ids = [], []
    way_geoms,  way_ids  = [], []
    for fid, fac in registry.items():
        if fac["geom_type"] == "Point":
            node_geoms.append(fac["geom"]); node_ids.append(fid)
        else:
            way_geoms.append(fac["geom"]);  way_ids.append(fid)

    node_tree = STRtree(node_geoms) if node_geoms else None
    way_tree  = STRtree(way_geoms)  if way_geoms  else None
    print(f"  {len(node_geoms):,} intersection nodes, {len(way_geoms):,} road segments",
          flush=True)

    # 5. Spatial join
    print(f"  Matching crashes…", flush=True)
    crash_map    = match_crashes(crashes, node_geoms, node_ids, node_tree,
                                 way_geoms,  way_ids,  way_tree,  project)
    n_matched    = sum(1 for v in crash_map.values() if v)
    print(f"  {n_matched:,} facilities with ≥1 crash", flush=True)

    # 6. Classify and accumulate
    way_reg = {fid: registry[fid] for fid in way_ids}  # quick lookup subset

    new_facs = 0
    for fid, fac in registry.items():
        sevs    = crash_map.get(fid, [])
        is_node = (fac["geom_type"] == "Point")

        if is_node:
            idx       = node_ids.index(fid)
            bin_key   = classify_node(fac, node_geoms[idx],
                                      way_geoms, way_ids, way_tree, way_reg)
        else:
            bin_key   = classify_way(fac)

        epdo, fatal, severe, total, dists = compute_epdo(sevs)

        # OSM IDs are globally unique — no true duplicates across counties.
        # Overwrite only if current county has more crash data (shouldn't happen).
        if fid not in global_stats or total > global_stats[fid]["total"]:
            rc = fac.get("road_class", ROAD_CLASS.get(fac.get("road_type", ""), "local"))
            global_stats[fid] = {
                "bin_key":       bin_key,
                "epdo":          epdo,
                "fatal":         fatal,
                "severe":        severe,
                "total":         total,
                "dists":         dists,
                "county":        county_name,
                "road_type":     fac.get("road_type", ""),
                "road_class":    rc,
                "name":          fac.get("name", ""),
                "speed_mph":     fac.get("speed_mph", 0),
                "lanes":         fac.get("lanes", 0),
                "length_m":      fac.get("length_m", 0),
                "facility_type": "intersection" if is_node else "segment",
                "geometry":      fac["geometry"],
            }
        new_facs += 1

    # 7. Free large objects before next county
    del crashes, registry, node_geoms, way_geoms, node_tree, way_tree, crash_map, way_reg
    gc.collect()

    print(f"  Accumulated {new_facs:,} facilities", flush=True)
    return {"county": county_name, "crashes": n_crashes, "facilities": new_facs}


# ---------------------------------------------------------------------------
# Statewide ranking
# ---------------------------------------------------------------------------

def _make_feature(fid: str, stats: dict,
                  rank_worst: int | None, rank_best: int | None,
                  similar_best: list) -> dict:
    return {
        "type":     "Feature",
        "geometry": stats["geometry"],
        "properties": {
            "facility_id":   fid,
            "facility_type": stats["facility_type"],
            "bin_key":       stats["bin_key"],
            "rank_worst":    rank_worst,
            "rank_best":     rank_best,
            "epdo_score":    stats["epdo"],
            "fatal_5yr":     stats["fatal"],
            "severe_5yr":    stats["severe"],
            "total_5yr":     stats["total"],
            "crash_rate_yr": round(stats["total"] / YEAR_WINDOW, 2),
            "county":        stats["county"],
            "road_type":     stats["road_type"],
            "road_class":    stats["road_class"],
            "name":          stats["name"],
            "speed_mph":     stats["speed_mph"],
            "lanes":         stats["lanes"],
            "length_m":      stats["length_m"],
            "similar_best":  similar_best,
            "crash_dists":   stats.get("dists", {}),
            # Future enrichment (None until fetch_enrichment.py is run):
            "aadt":              None,
            "turn_channelization": None,
            "median_type":       None,
        },
    }


def rank_statewide(global_stats: dict) -> dict:
    """Group all facilities by bin_key, produce worst/best lists per bin."""
    # Group
    groups: dict = {}
    for fid, stats in global_stats.items():
        groups.setdefault(stats["bin_key"], []).append((fid, stats))

    result = {}
    for bin_key, entries in groups.items():
        count = len(entries)
        if count < MIN_FACILITIES:
            result[bin_key] = {
                "facility_count": count,
                "insufficient_data": True,
                "worst": [],
                "best":  [],
            }
            continue

        # Worst: sort by epdo desc, require ≥2 crashes
        sorted_worst = sorted(entries, key=lambda x: (-x[1]["epdo"], -x[1]["total"]))
        # Best: sort by epdo asc (zero-crash facilities are safest)
        sorted_best  = sorted(entries, key=lambda x: (x[1]["epdo"], x[1]["total"]))

        worst_entries = [(fid, s) for fid, s in sorted_worst if s["total"] >= 2][:TOP_N]
        best_entries  = sorted_best[:TOP_N]

        # similar_best: best facilities sharing the same road_class as each worst entry
        best_by_class: dict = {}
        for fid_b, s_b in best_entries:
            best_by_class.setdefault(s_b["road_class"], []).append(fid_b)

        result[bin_key] = {
            "facility_count": count,
            "worst": [
                _make_feature(fid, s, rank + 1, None,
                              best_by_class.get(s["road_class"], [])[:3])
                for rank, (fid, s) in enumerate(worst_entries)
            ],
            "best": [
                _make_feature(fid, s, None, rank + 1, [])
                for rank, (fid, s) in enumerate(best_entries)
            ],
        }

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def get_cached_counties() -> list:
    if not os.path.isdir(CRASH_CACHE):
        return []
    return sorted(
        f[:-8] for f in os.listdir(CRASH_CACHE)
        if f.endswith(".geojson") and f[:-8] in CA_COUNTIES
    )


def main():
    parser = argparse.ArgumentParser(
        description="Build statewide safety rankings from OSM + crash cache")
    parser.add_argument("--county", help="Process only this county")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print stats only, do not write output")
    parser.add_argument("--weights", default="",
                        help="EPDO weights as fatal,injury,pdo  e.g. 10,2,0.2")
    args = parser.parse_args()

    if args.weights:
        try:
            parts = [float(x) for x in args.weights.split(",")]
            if len(parts) == 3:
                set_epdo_weights(*parts)
                print(f"EPDO weights: fatal={parts[0]}, injury={parts[1]}, pdo={parts[2]}")
        except ValueError:
            print(f"⚠  Invalid --weights '{args.weights}', using defaults")

    if not args.dry_run:
        os.makedirs(RANKINGS_DIR, exist_ok=True)
        free_gb = shutil.disk_usage(RANKINGS_DIR).free / 1024**3
        if RANKINGS_DIR.startswith(os.path.join(BASE_DIR, "data")) and free_gb < 2:
            print(f"⚠  Only {free_gb:.1f} GB free on internal drive.")
            print(f"   Set RANKINGS_DIR=/Volumes/<SSD>/rankings to use external SSD.")
        print(f"Output → {RANKINGS_DIR}")

    counties = [args.county] if args.county else get_cached_counties()
    if not counties:
        print("No cached counties found. Browse the map in the app to cache crash data.")
        sys.exit(1)

    print(f"Processing {len(counties)} county/counties: {', '.join(counties)}")
    global_stats: dict = {}
    summaries    = []

    for county_name in counties:
        ok, msg = check_county_readiness(county_name)
        if not ok:
            print(f"  Skipping {county_name}: {msg}")
            continue
        try:
            summary = process_county(county_name, global_stats, args.dry_run)
            summaries.append(summary)
        except Exception as exc:
            import traceback
            print(f"  ERROR in {county_name}: {exc}")
            traceback.print_exc()

    print(f"\n{'=' * 50}")
    print(f"Total facilities accumulated: {len(global_stats):,}")

    if args.dry_run:
        groups: dict = {}
        for s in global_stats.values():
            groups[s["bin_key"]] = groups.get(s["bin_key"], 0) + 1
        print("Top bins by facility count:")
        for bk, n in sorted(groups.items(), key=lambda x: -x[1])[:20]:
            tag = "" if n >= MIN_FACILITIES else " (insufficient)"
            print(f"  {n:>5}  {bk}{tag}")
        return

    print("Ranking statewide…")
    rankings  = rank_statewide(global_stats)
    n_ranked  = sum(1 for v in rankings.values() if not v.get("insufficient_data"))
    n_sparse  = sum(1 for v in rankings.values() if v.get("insufficient_data"))
    print(f"  {len(rankings)} bins: {n_ranked} with rankings, {n_sparse} insufficient data")

    good_counties = [s["county"] for s in summaries if "skipped" not in s]
    output = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "scope":              "statewide",
        "counties_included":  good_counties,
        "total_facilities":   len(global_stats),
        "epdo_weights":       EPDO,
        "year_window":        YEAR_WINDOW,
        "bins":               rankings,
        # ── Future enrichment items ─────────────────────────────────────────
        "future_enrichment": {
            "aadt": {
                "status": "not_implemented",
                "source": "Caltrans Annual Traffic Volumes",
                "url":    "https://gis.data.ca.gov/datasets/d8833219913c44358f2a9a71bda57f76",
                "note":   "State highways only. Run scripts/fetch_enrichment.py when ready.",
            },
            "local_aadt": {
                "status": "not_implemented",
                "source": "Sacramento City/County traffic counts",
                "url":    "https://data.cityofsacramento.org/datasets/SacCity::traffic-counts",
                "note":   "Per-jurisdiction data; no statewide source available.",
            },
            "turn_channelization": {
                "status": "not_implemented",
                "source": "OSM turn:lanes tag",
                "note":   "Only 1.4% coverage in current OSM cache.",
            },
            "median_type": {
                "status": "not_implemented",
                "source": "Caltrans HPMS",
                "url":    "https://gisdata-caltrans.opendata.arcgis.com",
                "note":   "Not present in OSM; requires HPMS spatial join.",
            },
        },
    }

    out_path = os.path.join(RANKINGS_DIR, "statewide.json")
    with open(out_path, "w") as f:
        json.dump(output, f)
    size_mb = os.path.getsize(out_path) / 1024**2
    print(f"Written: {out_path}  ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
