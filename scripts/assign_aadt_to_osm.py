"""
assign_aadt_to_osm.py
---------------------
Assigns Caltrans AADT values to OSM road segments using three phases:

  Phase 1 — Direct spatial match
    motorway/trunk      : nearest mainline AADT within MAINLINE_R (route-aware)
    motorway_link       : nearest ramp AADT within RAMP_R (spatial only)
    primary/secondary   : nearest mainline AADT within ARTERIAL_R (route-aware where ref exists)
    tertiary/local      : skip (no Caltrans coverage; handled by Phase 3 defaults)

  Phase 2 — Network graph propagation
    BFS from Phase-1-matched freeway ways (motorway/trunk/links) to adjacent
    unmatched freeway ways.  Applies a per-hop decay factor.
    Does NOT cross road-class boundaries (freeway propagation only).

  Phase 3 — Wide spatial fallback + functional-class defaults
    For still-unmatched ways: cast a wider net (FALLBACK_R).
    For tertiary/local/still-unmatched: use California functional-class ADT defaults.

Search radii (justified by explore_aadt_spatial_join.py Section 8 results):
  MAINLINE_R  = 2000 m  →  88% of motorway/trunk ways covered
  RAMP_R      =  500 m  →  86% of motorway_link ways covered
  ARTERIAL_R  = 1500 m  →  ~55% of primary/secondary ways covered
  FALLBACK_R  = 5000 m  →  wide net for isolated ways

Outputs:
  data/CaltransAADT/osm_aadt_lookup.json
  {
    "<osm_id>": {
      "aadt":       <float>,
      "method":     "<method_str>",
      "distance_m": <float | null>
    },
    ...
  }

Usage:
  python scripts/assign_aadt_to_osm.py [--mainline-radius 2000] [--ramp-radius 500]
  python scripts/assign_aadt_to_osm.py --max-hops 5 --hop-decay 0.95

Integration:
  build_safety_rankings.py loads this file and populates fac["aadt"].
"""

import argparse
import json
import logging
import math
import re
import sys
from collections import defaultdict, deque
from pathlib import Path

from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR  = Path(__file__).resolve().parent.parent
OSM_CACHE = BASE_DIR / "data" / "osm_cache"
AADT_FILE = BASE_DIR / "data" / "CaltransAADT" / "aadt_geocoded.geojson"
OUT_FILE  = BASE_DIR / "data" / "CaltransAADT" / "osm_aadt_lookup.json"

# ---------------------------------------------------------------------------
# Projection — equirectangular, CA statewide centroid (~37.5 N)
# Accurate to <0.1% across CA (same approach as build_safety_rankings.py)
# ---------------------------------------------------------------------------
_LAT_C = 37.5
_COS   = math.cos(math.radians(_LAT_C))
_M     = 111_320.0


def _proj(lon: float, lat: float):
    return (_COS * _M * lon, _M * lat)


def _dist2d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# Road type groups (consistent with build_safety_rankings.py ROAD_CLASS)
# ---------------------------------------------------------------------------
FREEWAY_HWY  = {"motorway", "trunk", "motorway_link", "trunk_link"}
MAINLINE_HWY = {"motorway", "trunk"}
RAMP_HWY     = {"motorway_link", "trunk_link"}
ARTERIAL_HWY = {"primary", "primary_link", "secondary", "secondary_link"}
COLLECTOR_HWY = {"tertiary", "tertiary_link"}
LOCAL_HWY    = {"residential", "unclassified", "living_street"}
ALL_ROAD_HWY = FREEWAY_HWY | ARTERIAL_HWY | COLLECTOR_HWY | LOCAL_HWY

# California functional-class daily traffic defaults (ADT, conservative estimates)
# Used only as Phase-3 last resort for ways with no nearby Caltrans station.
# Sources: FHWA HPMS functional class summaries + Caltrans district data
CLASS_DEFAULTS = {
    "motorway":      60_000,   # low end; actual range 20K-250K
    "trunk":         25_000,
    "motorway_link":  5_000,
    "trunk_link":     5_000,
    "primary":       12_000,
    "primary_link":   8_000,
    "secondary":      7_000,
    "secondary_link": 5_000,
    "tertiary":       3_000,
    "tertiary_link":  2_000,
    "residential":    1_000,
    "unclassified":     800,
    "living_street":    400,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_route(ref_tag) -> str | None:
    """
    Extract numeric Caltrans route number from OSM ref tag.
    'CA 1' -> '1', 'US 101' -> '101', 'I-5' -> '5',
    'CA 35;I-280' -> '35' (first segment only).
    Returns None for unparseable values ('PCH', 'aerial', etc.).
    """
    if not ref_tag:
        return None
    seg  = str(ref_tag).split(";")[0].strip()
    nums = re.findall(r"\d+", seg)
    return nums[0] if nums else None


def _way_centroid(coords_proj: list) -> tuple:
    xs = [c[0] for c in coords_proj]
    ys = [c[1] for c in coords_proj]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


# ---------------------------------------------------------------------------
# OSM way loader
# ---------------------------------------------------------------------------

def load_osm_ways() -> dict:
    """
    Load and deduplicate all OSM ways from osm_cache/*.json.
    Returns {osm_id (int): way_record}.

    Deduplication: ways appear in multiple tiles at tile boundaries.
    Keep the copy with the most vertices (longest recorded geometry).
    """
    log.info("Loading OSM way cache from %s ...", OSM_CACHE)
    ways = {}
    for tile_path in sorted(OSM_CACHE.glob("*.json")):
        with open(tile_path, encoding="utf-8") as f:
            features = json.load(f)
        for feat in features:
            props = feat.get("properties", {})
            geom  = feat.get("geometry", {})
            if geom.get("type") != "LineString":
                continue
            hwy = props.get("type") or props.get("highway", "")
            if hwy not in ALL_ROAD_HWY:
                continue
            osm_id = props.get("id")
            if osm_id is None:
                continue
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue
            if osm_id in ways and len(coords) <= len(ways[osm_id]["coords"]):
                continue
            cp = [_proj(lon, lat) for lon, lat in coords]
            ways[osm_id] = {
                "hwy":        hwy,
                "ref":        props.get("ref"),
                "coords":     coords,            # WGS84 list
                "coords_p":   cp,                # projected list
                "centroid_p": _way_centroid(cp),
                # Endpoint keys for adjacency graph (rounded to 7 dp ≈ 1 cm)
                "ep0": (round(coords[0][0], 7), round(coords[0][1], 7)),
                "epN": (round(coords[-1][0], 7), round(coords[-1][1], 7)),
            }

    log.info("  Loaded %d unique ways", len(ways))
    return ways


# ---------------------------------------------------------------------------
# AADT loader
# ---------------------------------------------------------------------------

def load_aadt_points() -> tuple[list, list]:
    """
    Load aadt_geocoded.geojson.
    Returns (mainline_pts, ramp_pts) — only records with valid numeric AADT.

    mainline_pts includes both source=mainline and source=truck records.
    Truck records supplement mainline where no mainline station exists for
    that postmile.  Both carry vehicle AADT in the 'aadt' field.
    """
    with open(AADT_FILE, encoding="utf-8") as f:
        fc = json.load(f)

    mainline_pts = []
    ramp_pts     = []

    for feat in fc.get("features", []):
        p   = feat.get("properties", {})
        src = p.get("source", "")
        try:
            aadt_val = float(p.get("aadt") or 0)
        except (ValueError, TypeError):
            aadt_val = 0.0
        if aadt_val <= 0:
            continue
        coords = feat.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        px, py   = _proj(lon, lat)
        rec = {
            "route": str(p.get("route") or "").strip(),
            "aadt":  aadt_val,
            "lon":   lon,
            "lat":   lat,
            "px":    px,
            "py":    py,
        }
        if src in ("mainline", "truck"):
            mainline_pts.append(rec)
        elif src == "ramp":
            ramp_pts.append(rec)

    log.info("  Mainline/truck AADT pts: %d", len(mainline_pts))
    log.info("  Ramp AADT pts:           %d", len(ramp_pts))
    return mainline_pts, ramp_pts


# ---------------------------------------------------------------------------
# Spatial index helpers
# ---------------------------------------------------------------------------

def _build_pt_tree(pts: list):
    """Build STRtree over AADT points.  Returns (tree, geom_list, pts)."""
    geoms = [Point(p["px"], p["py"]) for p in pts]
    return STRtree(geoms), geoms, pts


def _nearest_pt_in_radius(cx: float, cy: float,
                          tree: STRtree, geoms: list, pts: list,
                          radius: float, route: str | None = None):
    """
    Return (aadt_value, distance_m) for the nearest AADT point within radius.
    If route is provided, prefer same-route points; fall back to any route.

    route-aware strategy:
      1. Find all points within radius.
      2. Among those, pick the nearest with matching route.
      3. If none match the route, pick the geometrically nearest overall.
    """
    query_pt = Point(cx, cy)
    idxs = tree.query(query_pt, predicate="dwithin", distance=radius)
    if len(idxs) == 0:
        return None, None

    candidates = []
    for i in idxs:
        d = geoms[i].distance(query_pt)
        candidates.append((d, pts[i]))

    if route:
        route_matches = [(d, p) for d, p in candidates if p["route"] == route]
        if route_matches:
            route_matches.sort(key=lambda x: x[0])
            best_d, best_p = route_matches[0]
            return best_p["aadt"], best_d

    candidates.sort(key=lambda x: x[0])
    best_d, best_p = candidates[0]
    return best_p["aadt"], best_d


# ---------------------------------------------------------------------------
# Endpoint adjacency graph (for Phase 2 network propagation)
# ---------------------------------------------------------------------------

def build_adjacency(ways: dict, hwy_set: set) -> dict:
    """
    Build way-to-way adjacency for the given highway type set.
    Two ways are adjacent if they share an endpoint coordinate (same OSM node).

    Returns {osm_id: set_of_adjacent_osm_ids}.
    """
    ep_index = defaultdict(list)   # endpoint_coord -> [osm_id, ...]
    for osm_id, w in ways.items():
        if w["hwy"] not in hwy_set:
            continue
        ep_index[w["ep0"]].append(osm_id)
        ep_index[w["epN"]].append(osm_id)

    adj = defaultdict(set)
    for node, ids in ep_index.items():
        if len(ids) < 2:
            continue
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                adj[a].add(b)
                adj[b].add(a)
    return adj


# ---------------------------------------------------------------------------
# Phase 1: Direct spatial match
# ---------------------------------------------------------------------------

def phase1(ways: dict,
           ml_tree, ml_geoms, mainline_pts,
           rp_tree, rp_geoms, ramp_pts,
           mainline_r: float, ramp_r: float, arterial_r: float) -> dict:
    """
    Direct spatial AADT assignment.
    Returns result dict: {osm_id: {aadt, method, distance_m}}.
    """
    result = {}

    for osm_id, w in ways.items():
        hwy   = w["hwy"]
        cx, cy = w["centroid_p"]
        route = _parse_route(w["ref"])

        aadt, dist, method = None, None, None

        if hwy in MAINLINE_HWY:
            # Route-aware: prefer same-route station within MAINLINE_R
            aadt, dist = _nearest_pt_in_radius(cx, cy,
                                               ml_tree, ml_geoms, mainline_pts,
                                               mainline_r, route=route)
            if aadt is not None:
                method = "route_mainline" if route else "spatial_mainline"

        elif hwy in RAMP_HWY:
            # Ramp: nearest ramp AADT within RAMP_R (no route matching — ramp ref=2%)
            aadt, dist = _nearest_pt_in_radius(cx, cy,
                                               rp_tree, rp_geoms, ramp_pts,
                                               ramp_r)
            if aadt is not None:
                method = "ramp_direct"

        elif hwy in ARTERIAL_HWY:
            # Arterial: route-aware match ONLY (requires ref tag to match Caltrans route).
            # Pure spatial match is suppressed because a nearby motorway AADT station
            # (e.g. I-5 at 150,000 ADT) would be assigned to a parallel primary road
            # that actually carries 10,000 ADT — off by an order of magnitude.
            # Roads without a parseable ref fall through to Phase 3 class_default.
            if route:
                aadt, dist = _nearest_pt_in_radius(cx, cy,
                                                   ml_tree, ml_geoms, mainline_pts,
                                                   arterial_r, route=route)
                if aadt is not None:
                    method = "route_arterial"

        # COLLECTOR_HWY and LOCAL_HWY: skip Phase 1; go straight to Phase 3

        if aadt is not None:
            result[osm_id] = {
                "aadt":       round(aadt),
                "method":     method,
                "distance_m": round(dist, 1),
            }

    return result


# ---------------------------------------------------------------------------
# Phase 2: Network graph propagation (freeway subgraph only)
# ---------------------------------------------------------------------------

def phase2(ways: dict, result: dict,
           adj: dict, max_hops: int, hop_decay: float) -> dict:
    """
    BFS from matched freeway ways to adjacent unmatched freeway ways.
    Only propagates within FREEWAY_HWY (motorway/trunk/links).
    Decay of hop_decay^hops is applied to the inherited AADT.

    Mutates result in place; returns result.
    """
    # Multi-source BFS: seed queue with all Phase-1-matched freeway ways
    dist_map = {}   # osm_id -> (hops, source_aadt)
    queue    = deque()

    freeway_ways = {oid for oid, w in ways.items() if w["hwy"] in FREEWAY_HWY}

    for osm_id in freeway_ways:
        if osm_id in result:
            dist_map[osm_id] = (0, result[osm_id]["aadt"])
            queue.append(osm_id)

    while queue:
        cur = queue.popleft()
        cur_hops, cur_aadt = dist_map[cur]
        if cur_hops >= max_hops:
            continue
        for nb in adj.get(cur, ()):
            if nb not in dist_map:
                nb_aadt = cur_aadt * (hop_decay ** (cur_hops + 1))
                dist_map[nb] = (cur_hops + 1, nb_aadt)
                queue.append(nb)
                if nb not in result:
                    result[nb] = {
                        "aadt":       round(nb_aadt),
                        "method":     f"network_{cur_hops + 1}hop",
                        "distance_m": None,
                    }

    return result


# ---------------------------------------------------------------------------
# Phase 3: Wide spatial fallback + functional-class defaults
# ---------------------------------------------------------------------------

def phase3(ways: dict, result: dict,
           ml_tree, ml_geoms, mainline_pts,
           rp_tree, rp_geoms, ramp_pts,
           fallback_r: float) -> dict:
    """
    For still-unmatched ways:
      1. Wide spatial search (fallback_r) — applied ONLY to freeway and arterial
         types.  Collector and local roads skip this step because assigning a
         mainline AADT (e.g. 80,000 ADT) to a residential street is wrong by
         orders of magnitude and would corrupt crash-rate calculations.
      2. Functional-class default for collector/local and any still-unmatched.

    Mutates result in place; returns result.
    """
    # Spatial fallback eligible: freeway types only.
    # Arterials without a ref tag already fell through Phase 1 route-aware matching
    # and should use class defaults rather than picking up a motorway AADT value
    # from a nearby freeway (would be wrong by an order of magnitude).
    SPATIAL_ELIGIBLE = FREEWAY_HWY

    for osm_id, w in ways.items():
        if osm_id in result:
            continue
        hwy    = w["hwy"]
        cx, cy = w["centroid_p"]

        # --- Wide spatial search (freeway + arterial only) ---
        if hwy in SPATIAL_ELIGIBLE:
            best_aadt, best_dist = None, None

            if hwy in RAMP_HWY:
                # Try ramp AADT first, then mainline * fraction
                a, d = _nearest_pt_in_radius(cx, cy, rp_tree, rp_geoms, ramp_pts, fallback_r)
                if a is not None:
                    best_aadt, best_dist = a, d
                else:
                    a, d = _nearest_pt_in_radius(cx, cy, ml_tree, ml_geoms, mainline_pts, fallback_r)
                    if a is not None:
                        # 10% ramp fraction — conservative FHWA guidance for freeway ramps
                        best_aadt, best_dist = a * 0.10, d
            else:
                a, d = _nearest_pt_in_radius(cx, cy, ml_tree, ml_geoms, mainline_pts, fallback_r)
                if a is not None:
                    best_aadt, best_dist = a, d

            if best_aadt is not None:
                result[osm_id] = {
                    "aadt":       round(best_aadt),
                    "method":     "spatial_fallback",
                    "distance_m": round(best_dist, 1),
                }
                continue

        # --- Functional-class default ---
        # Used for: collector/local (always), freeway/arterial with no AADT within fallback_r
        default = CLASS_DEFAULTS.get(hwy)
        if default is not None:
            result[osm_id] = {
                "aadt":       default,
                "method":     "class_default",
                "distance_m": None,
            }

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Assign Caltrans AADT to OSM road segments")
    parser.add_argument("--mainline-radius", type=float, default=2000.0,
                        help="Search radius (m) for motorway/trunk AADT match  [default: 2000]")
    parser.add_argument("--ramp-radius",     type=float, default=500.0,
                        help="Search radius (m) for motorway_link AADT match   [default: 500]")
    parser.add_argument("--arterial-radius", type=float, default=1500.0,
                        help="Search radius (m) for primary/secondary match     [default: 1500]")
    parser.add_argument("--fallback-radius", type=float, default=5000.0,
                        help="Phase-3 wide spatial fallback radius (m)          [default: 5000]")
    parser.add_argument("--max-hops",   type=int,   default=5,
                        help="BFS max hops in network propagation               [default: 5]")
    parser.add_argument("--hop-decay",  type=float, default=0.95,
                        help="Per-hop AADT decay factor in propagation          [default: 0.95]")
    args = parser.parse_args()

    log.info("=== AADT -> OSM Assignment ===")
    log.info("Radii: mainline=%.0fm  ramp=%.0fm  arterial=%.0fm  fallback=%.0fm",
             args.mainline_radius, args.ramp_radius,
             args.arterial_radius, args.fallback_radius)
    log.info("BFS: max_hops=%d  hop_decay=%.2f", args.max_hops, args.hop_decay)

    # --- Load data ---
    ways = load_osm_ways()
    mainline_pts, ramp_pts = load_aadt_points()

    # --- Build spatial indices ---
    log.info("Building AADT spatial indices...")
    ml_tree,  ml_geoms,  _ = _build_pt_tree(mainline_pts)
    rp_tree,  rp_geoms,  _ = _build_pt_tree(ramp_pts)

    # --- Build adjacency graph (freeway subgraph only for Phase 2) ---
    log.info("Building freeway adjacency graph...")
    adj = build_adjacency(ways, FREEWAY_HWY)
    log.info("  %d freeway ways in graph, %d with neighbors",
             sum(1 for w in ways.values() if w["hwy"] in FREEWAY_HWY),
             len(adj))

    # --- Phase 1 ---
    log.info("Phase 1: direct spatial match...")
    result = phase1(ways,
                    ml_tree, ml_geoms, mainline_pts,
                    rp_tree, rp_geoms, ramp_pts,
                    args.mainline_radius, args.ramp_radius, args.arterial_radius)
    _report_coverage(ways, result, "Phase 1")

    # --- Phase 2 ---
    log.info("Phase 2: network propagation (max %d hops)...", args.max_hops)
    result = phase2(ways, result, adj, args.max_hops, args.hop_decay)
    _report_coverage(ways, result, "Phase 2")

    # --- Phase 3 ---
    log.info("Phase 3: wide spatial fallback + class defaults (radius %.0fm)...",
             args.fallback_radius)
    result = phase3(ways, result,
                    ml_tree, ml_geoms, mainline_pts,
                    rp_tree, rp_geoms, ramp_pts,
                    args.fallback_radius)
    _report_coverage(ways, result, "Phase 3 (final)")

    # --- Write output ---
    log.info("Writing %s ...", OUT_FILE)
    out = {str(oid): rec for oid, rec in result.items()}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    log.info("Done. %d ways assigned.", len(result))

    # --- Method summary ---
    from collections import Counter
    method_ctr = Counter(rec["method"] for rec in result.values())
    log.info("Assignment method breakdown:")
    for method, cnt in sorted(method_ctr.items(), key=lambda x: -x[1]):
        log.info("  %-22s %7d  (%.1f%%)", method, cnt,
                 100 * cnt / max(len(result), 1))


def _report_coverage(ways: dict, result: dict, phase_label: str):
    total = len(ways)
    matched = len(result)
    by_hwy = defaultdict(lambda: [0, 0])  # hwy -> [matched, total]
    for osm_id, w in ways.items():
        hwy = w["hwy"]
        by_hwy[hwy][1] += 1
        if osm_id in result:
            by_hwy[hwy][0] += 1
    log.info("  %s: %d/%d ways (%.1f%%) assigned", phase_label,
             matched, total, 100 * matched / max(total, 1))
    for hwy in sorted(by_hwy):
        m, t = by_hwy[hwy]
        if t > 0:
            log.info("    %-20s %5d/%5d  (%4.0f%%)", hwy, m, t, 100 * m / t)


if __name__ == "__main__":
    main()
