"""
explore_aadt_spatial_join.py
----------------------------
Spatial-join diagnostic: measures how well Caltrans AADT count stations
(aadt_geocoded.geojson) align with cached OSM road geometry (data/osm_cache/).

Outputs:
  - Console statistics (10 sections)
  - data/CaltransAADT/aadt_join_diagnostic.geojson  (AADT pts + join metadata)

Run:
  python scripts/explore_aadt_spatial_join.py
"""

import glob
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, MultiPoint, Point
from shapely.strtree import STRtree

BASE_DIR  = Path(__file__).resolve().parent.parent
OSM_CACHE = BASE_DIR / "data" / "osm_cache"
AADT_FILE = BASE_DIR / "data" / "CaltransAADT" / "aadt_geocoded.geojson"
OUT_FILE  = BASE_DIR / "data" / "CaltransAADT" / "aadt_join_diagnostic.geojson"

# ---------------------------------------------------------------------------
# Projection — equirectangular, CA statewide centroid
# ---------------------------------------------------------------------------
LAT_CENTER = 37.5
_COS = math.cos(math.radians(LAT_CENTER))
_M   = 111_320.0


def proj(lon, lat):
    return (_COS * _M * lon, _M * lat)


# ---------------------------------------------------------------------------
# OSM highway groupings
# ---------------------------------------------------------------------------
MAINLINE_HWY = {"motorway", "trunk"}
RAMP_HWY     = {"motorway_link", "trunk_link"}
ARTERIAL_HWY = {"primary", "primary_link", "secondary", "secondary_link"}
COLLECTOR_HWY = {"tertiary", "tertiary_link"}
LOCAL_HWY    = {"residential", "unclassified", "living_street"}
ALL_ROAD_HWY = MAINLINE_HWY | RAMP_HWY | ARTERIAL_HWY | COLLECTOR_HWY | LOCAL_HWY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct_table(values, label=""):
    if not values:
        return f"  {label}: (no data)"
    a = np.array(values, dtype=float)
    lines = [f"  {label} (n={len(a):,})"]
    percs = [10, 25, 50, 75, 90, 95, 99]
    vals  = np.percentile(a, percs)
    lines.append("  " + "  ".join(f"p{p}={v:.0f}m" for p, v in zip(percs, vals)))
    lines.append(f"  max={a.max():.0f}m  mean={a.mean():.0f}m")
    return "\n".join(lines)


def _parse_route(ref_tag):
    """'CA 1' -> '1', 'US 101' -> '101', 'I-5' -> '5', 'CA 35;I-280' -> '35'"""
    if not ref_tag:
        return None
    seg  = str(ref_tag).split(";")[0].strip()
    nums = re.findall(r"\d+", seg)
    return nums[0] if nums else None


def _way_centroid_proj(coords_proj):
    """Projected centroid of a projected coordinate list."""
    xs = [c[0] for c in coords_proj]
    ys = [c[1] for c in coords_proj]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _dist2d(p1, p2):
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


# ---------------------------------------------------------------------------
# Section 1: Load OSM tiles + inventory
# ---------------------------------------------------------------------------

def load_osm_ways():
    """
    Load all cached OSM tiles. Deduplicate ways by osm_id (keep most vertices).
    Returns: dict of {osm_id: {hwy, ref, name, destination, oneway, junction,
                               lanes, coords_wgs84, coords_proj, length_m,
                               p_first, p_last, p_centroid}}
    """
    print("\n=== Section 1: Load OSM Tiles ===")
    tile_files = sorted(OSM_CACHE.glob("*.json"))
    print(f"  Tile files found: {len(tile_files)}")

    ways   = {}   # osm_id -> record
    hw_ctr = Counter()   # highway type -> count (after dedup)
    raw_ctr = Counter()  # highway type -> count (before dedup, for info)

    for tf in tile_files:
        with open(tf, encoding="utf-8") as f:
            features = json.load(f)
        for feat in features:
            props = feat.get("properties", {})
            geom  = feat.get("geometry", {})
            if geom.get("type") != "LineString":
                continue
            hwy = props.get("type") or props.get("highway", "")
            if hwy not in ALL_ROAD_HWY:
                continue
            raw_ctr[hwy] += 1
            osm_id = props.get("id")
            if osm_id is None:
                continue
            coords = geom.get("coordinates", [])
            if len(coords) < 2:
                continue
            # Dedup: keep copy with most vertices
            if osm_id in ways and len(coords) <= len(ways[osm_id]["coords_wgs84"]):
                continue

            cp = [proj(lon, lat) for lon, lat in coords]
            # Haversine length
            ln = sum(
                math.sqrt((_M * (coords[i][1] - coords[i-1][1]))**2 +
                           (_COS * _M * (coords[i][0] - coords[i-1][0]))**2)
                for i in range(1, len(coords))
            )
            ways[osm_id] = {
                "hwy":         hwy,
                "ref":         props.get("ref"),
                "name":        props.get("name"),
                "destination": props.get("destination"),
                "oneway":      props.get("oneway"),
                "junction":    props.get("junction"),
                "lanes":       props.get("lanes"),
                "coords_wgs84": coords,
                "coords_proj":  cp,
                "length_m":    ln,
                "p_first":     cp[0],
                "p_last":      cp[-1],
                "p_centroid":  _way_centroid_proj(cp),
            }

    for osm_id, w in ways.items():
        hw_ctr[w["hwy"]] += 1

    print("\n  Way count by highway type (after dedup):")
    for hwy in sorted(hw_ctr, key=lambda h: -hw_ctr[h]):
        print(f"    {hwy:20s}  {hw_ctr[hwy]:6,}")
    print(f"  Total unique ways: {len(ways):,}")

    return ways


# ---------------------------------------------------------------------------
# Section 2: Load AADT points
# ---------------------------------------------------------------------------

def load_aadt():
    print("\n=== Section 2: Load AADT Points ===")
    with open(AADT_FILE, encoding="utf-8") as f:
        fc = json.load(f)
    features = fc.get("features", [])

    pts = []
    ctr = Counter()
    for feat in features:
        p   = feat.get("properties", {})
        src = p.get("source", "")
        ctr[src] += 1
        aadt_raw = p.get("aadt", "")
        try:
            aadt_val = float(aadt_raw) if aadt_raw else None
        except (ValueError, TypeError):
            aadt_val = None

        coords = feat.get("geometry", {}).get("coordinates", [])
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        px, py   = proj(lon, lat)

        pts.append({
            "source":  src,
            "route":   str(p.get("route", "") or ""),
            "county":  p.get("county", ""),
            "aadt":    aadt_val,
            "lon":     lon,
            "lat":     lat,
            "px":      px,
            "py":      py,
            "props":   p,
        })

    print(f"  Total AADT features: {len(pts):,}")
    for src in sorted(ctr):
        print(f"    {src:10s} {ctr[src]:6,}")
    return pts


# ---------------------------------------------------------------------------
# Build STRtrees for each road group
# ---------------------------------------------------------------------------

def build_trees(ways):
    """
    Return dict of group_name -> (STRtree, list_of_shapely_geoms, list_of_osm_ids)
    We build one tree per road group for targeted queries.
    """
    groups = {
        "mainline": MAINLINE_HWY,
        "ramp":     RAMP_HWY,
        "arterial": ARTERIAL_HWY,
        "collector":COLLECTOR_HWY,
        "local":    LOCAL_HWY,
        "all":      ALL_ROAD_HWY,
    }
    trees = {}
    for name, hwy_set in groups.items():
        geoms   = []
        osm_ids = []
        for osm_id, w in ways.items():
            if w["hwy"] in hwy_set:
                geoms.append(LineString(w["coords_proj"]))
                osm_ids.append(osm_id)
        if geoms:
            trees[name] = (STRtree(geoms), geoms, osm_ids)
        else:
            trees[name] = (None, [], [])
        print(f"  Tree '{name}': {len(geoms):,} ways")
    return trees


def nearest_way(px, py, tree_tuple, max_dist=1e9):
    """
    Return (osm_id, distance_m) of the nearest way within max_dist, or (None, None).
    """
    strtree, geoms, osm_ids = tree_tuple
    if strtree is None:
        return None, None
    pt = Point(px, py)
    idx = strtree.nearest(pt)
    if idx is None:
        return None, None
    dist = geoms[idx].distance(pt)
    if dist > max_dist:
        return None, None
    return osm_ids[idx], dist


def ways_within(px, py, tree_tuple, radius):
    """Return list of (osm_id, distance_m) within radius."""
    strtree, geoms, osm_ids = tree_tuple
    if strtree is None:
        return []
    pt = Point(px, py)
    idxs = strtree.query(pt, predicate="dwithin", distance=radius)
    result = []
    for i in idxs:
        d = geoms[i].distance(pt)
        result.append((osm_ids[i], d))
    return sorted(result, key=lambda x: x[1])


# ---------------------------------------------------------------------------
# Section 3: Mainline AADT -> OSM mainline/arterial distance analysis
# ---------------------------------------------------------------------------

def analyze_mainline(pts, ways, trees):
    print("\n=== Section 3: Mainline AADT -> OSM Way Distance Analysis (cache bbox only) ===")

    mainline_pts = [p for p in pts if p["source"] == "mainline" and p["aadt"]]
    print(f"  Mainline AADT pts with valid aadt: {len(mainline_pts):,}")

    dist_to_mainline = []   # distance to nearest motorway/trunk
    dist_to_any      = []   # distance to nearest any way
    route_match_n    = 0
    route_nomatch_n  = 0
    ref_absent_n     = 0
    no_mainline_n    = 0

    for p in mainline_pts:
        # Nearest motorway/trunk
        osm_id, d = nearest_way(p["px"], p["py"], trees["mainline"])
        if osm_id is not None:
            dist_to_mainline.append(d)
            # Route match check
            ref = ways[osm_id]["ref"]
            parsed = _parse_route(ref)
            if parsed is None:
                ref_absent_n += 1
            elif parsed == p["route"]:
                route_match_n += 1
            else:
                route_nomatch_n += 1
        else:
            no_mainline_n += 1

        # Nearest any way
        _, d2 = nearest_way(p["px"], p["py"], trees["all"])
        if d2 is not None:
            dist_to_any.append(d2)

    print(_pct_table(dist_to_mainline, "Distance -> nearest motorway/trunk way"))
    print(_pct_table(dist_to_any,      "Distance -> nearest any road way"))

    total_checked = route_match_n + route_nomatch_n + ref_absent_n
    if total_checked:
        print(f"\n  Route ref quality (among {total_checked} pts with nearest motorway/trunk):")
        print(f"    ref tag absent / unparseable : {ref_absent_n:5,}  ({100*ref_absent_n/total_checked:.1f}%)")
        print(f"    ref matches Caltrans route   : {route_match_n:5,}  ({100*route_match_n/total_checked:.1f}%)")
        print(f"    ref does NOT match           : {route_nomatch_n:5,}  ({100*route_nomatch_n/total_checked:.1f}%)")
    if no_mainline_n:
        print(f"    pts with no motorway/trunk in cache: {no_mainline_n}")

    # Breakdown: how many mainline pts are >500m, >1000m from any motorway/trunk?
    for thresh in (100, 200, 500, 1000, 2000):
        n = sum(1 for d in dist_to_mainline if d > thresh)
        print(f"  Mainline AADT pts >  {thresh:5d}m from nearest motorway/trunk: {n:5,}  ({100*n/max(len(dist_to_mainline),1):.1f}%)")


# ---------------------------------------------------------------------------
# Section 4: Ramp AADT -> OSM ramp distance + junction proximity analysis
# ---------------------------------------------------------------------------

def analyze_ramps(pts, ways, trees):
    print("\n=== Section 4: Ramp AADT -> OSM Ramp Distance Analysis (cache bbox only) ===")

    ramp_pts = [p for p in pts if p["source"] == "ramp" and p["aadt"]]
    print(f"  Ramp AADT pts with valid aadt: {len(ramp_pts):,}")

    dist_to_ramp_way    = []   # distance from ramp AADT pt to nearest motorway_link/trunk_link
    dist_to_mainline    = []   # distance to nearest motorway/trunk
    dist_to_first_ep    = []   # distance to first endpoint of nearest ramp way (likely junction)
    dist_to_last_ep     = []   # distance to last endpoint (likely terminal)
    dist_to_centroid    = []   # distance to centroid of nearest ramp way
    ramp_closer_n       = 0    # pts closer to ramp way than mainline way
    mainline_closer_n   = 0

    # Collision counts per ramp pt
    coll_100 = []  # n ramp AADT pts within 100m of same motorway_link
    coll_200 = []
    coll_300 = []

    # Build STRtree for ramp AADT points themselves
    ramp_pt_geoms  = [Point(p["px"], p["py"]) for p in ramp_pts]
    ramp_pt_tree   = STRtree(ramp_pt_geoms)

    for p in ramp_pts:
        pt = Point(p["px"], p["py"])

        # --- Distance to nearest OSM ramp way ---
        osm_id_r, d_ramp = nearest_way(p["px"], p["py"], trees["ramp"])
        if d_ramp is not None:
            dist_to_ramp_way.append(d_ramp)
            w = ways[osm_id_r]
            dist_to_first_ep.append(_dist2d((p["px"], p["py"]), w["p_first"]))
            dist_to_last_ep.append( _dist2d((p["px"], p["py"]), w["p_last"]))
            dist_to_centroid.append(_dist2d((p["px"], p["py"]), w["p_centroid"]))

        # --- Distance to nearest OSM mainline way ---
        _, d_main = nearest_way(p["px"], p["py"], trees["mainline"])
        if d_main is not None:
            dist_to_mainline.append(d_main)

        # --- Which is closer? ---
        if d_ramp is not None and d_main is not None:
            if d_ramp <= d_main:
                ramp_closer_n += 1
            else:
                mainline_closer_n += 1

        # --- Collision: other ramp AADT pts nearby ---
        for r in (100, 200, 300):
            nearby = ramp_pt_tree.query(pt, predicate="dwithin", distance=r)
            # Subtract 1 for the point itself
            count = max(0, len(nearby) - 1)
            if r == 100:
                coll_100.append(count)
            elif r == 200:
                coll_200.append(count)
            else:
                coll_300.append(count)

    print(_pct_table(dist_to_ramp_way,  "Distance -> nearest motorway_link/trunk_link way"))
    print(_pct_table(dist_to_mainline,  "Distance -> nearest motorway/trunk way"))

    if dist_to_ramp_way:
        print(f"\n  Ramp AADT pt proximity to nearest ramp way geometry:")
        print(_pct_table(dist_to_first_ep,  "  to first endpoint (likely junction)"))
        print(_pct_table(dist_to_last_ep,   "  to last endpoint  (likely terminal)"))
        print(_pct_table(dist_to_centroid,  "  to way centroid"))

        total = ramp_closer_n + mainline_closer_n
        if total:
            print(f"\n  Ramp way closer than mainline way: {ramp_closer_n}/{total} ({100*ramp_closer_n/total:.0f}%)")
            print(f"  Mainline way closer:               {mainline_closer_n}/{total} ({100*mainline_closer_n/total:.0f}%)")

    # Collision histogram
    for r, coll in [(100, coll_100), (200, coll_200), (300, coll_300)]:
        c = Counter(coll)
        total = len(coll)
        print(f"\n  Other ramp AADT pts within {r}m of same point:")
        for k in sorted(c)[:6]:
            print(f"    {k} other pts: {c[k]:5,}  ({100*c[k]/total:.1f}%)")
        if max(c) >= 6:
            print(f"    ... up to {max(c)} other pts at one location")

    # Threshold breakdown
    print()
    for thresh in (50, 100, 200, 300, 500):
        n = sum(1 for d in dist_to_ramp_way if d > thresh)
        pct = 100 * n / max(len(dist_to_ramp_way), 1)
        print(f"  Ramp AADT pts > {thresh:4d}m from nearest motorway_link: {n:5,}  ({pct:.1f}%)")


# ---------------------------------------------------------------------------
# Section 5: motorway_link attribute inventory
# ---------------------------------------------------------------------------

def analyze_ramp_attributes(ways):
    print("\n=== Section 5: OSM motorway_link Attribute Inventory ===")

    ramp_ways = {k: v for k, v in ways.items() if v["hwy"] in RAMP_HWY}
    n = len(ramp_ways)
    if n == 0:
        print("  No motorway_link ways found in cache.")
        return

    print(f"  Total motorway_link + trunk_link ways: {n:,}")

    def pct_with(attr):
        return sum(1 for w in ramp_ways.values() if w.get(attr)) / n * 100

    for attr in ("ref", "name", "destination", "oneway", "junction", "lanes"):
        cnt = sum(1 for w in ramp_ways.values() if w.get(attr))
        print(f"    %-12s present: %5d  (%5.1f%%)" % (attr, cnt, 100*cnt/n))

    # Sample ref values
    refs = [w["ref"] for w in ramp_ways.values() if w.get("ref")]
    if refs:
        print(f"\n  Sample ref values (first 10): {refs[:10]}")

    # Length distribution
    lengths = [w["length_m"] for w in ramp_ways.values()]
    print(_pct_table(lengths, "Ramp way length_m"))

    # Oneway values
    oneway_ctr = Counter(w["oneway"] for w in ramp_ways.values() if w.get("oneway"))
    print(f"\n  oneway values: {dict(oneway_ctr.most_common(5))}")

    # Junction values
    junc_ctr = Counter(w["junction"] for w in ramp_ways.values() if w.get("junction"))
    print(f"  junction values: {dict(junc_ctr.most_common(5))}")


# ---------------------------------------------------------------------------
# Section 6: Route ref quality for all OSM ways
# ---------------------------------------------------------------------------

def analyze_ref_quality(ways, pts):
    print("\n=== Section 6: OSM Route Ref Quality ===")

    # Build set of Caltrans route numbers present in the AADT data
    caltrans_routes = {p["route"] for p in pts if p["source"] == "mainline" and p["route"]}
    print(f"  Distinct Caltrans route numbers in AADT data: {len(caltrans_routes)}")

    ways_with_ref = {k: v for k, v in ways.items() if v.get("ref")}
    n_with_ref    = len(ways_with_ref)
    n_total       = len(ways)
    print(f"  OSM ways with 'ref' tag: {n_with_ref:,} / {n_total:,}  ({100*n_with_ref/max(n_total,1):.1f}%)")

    parsed_ok   = 0
    match_caltrans = 0
    no_parse    = 0
    bad_ref_samples = []

    for osm_id, w in ways_with_ref.items():
        if w["hwy"] not in (MAINLINE_HWY | RAMP_HWY | ARTERIAL_HWY):
            continue
        parsed = _parse_route(w["ref"])
        if parsed is None:
            no_parse += 1
            bad_ref_samples.append(w["ref"])
        else:
            parsed_ok += 1
            if parsed in caltrans_routes:
                match_caltrans += 1

    total = parsed_ok + no_parse
    if total:
        print(f"\n  Among highway/arterial/ramp ways with ref:")
        print(f"    Parseable (has digits)        : {parsed_ok:5,} / {total} ({100*parsed_ok/total:.1f}%)")
        print(f"    Matches a Caltrans route      : {match_caltrans:5,} / {parsed_ok} ({100*match_caltrans/max(parsed_ok,1):.1f}%)")
        print(f"    Unparseable ref               : {no_parse:5,}")
    if bad_ref_samples:
        print(f"  Unparseable ref samples: {bad_ref_samples[:10]}")

    # Which highway types have the best ref coverage?
    print("\n  Ref coverage by highway type:")
    by_hwy = defaultdict(lambda: [0, 0])  # hwy -> [has_ref, total]
    for w in ways.values():
        if w["hwy"] in (MAINLINE_HWY | RAMP_HWY | ARTERIAL_HWY):
            by_hwy[w["hwy"]][1] += 1
            if w.get("ref"):
                by_hwy[w["hwy"]][0] += 1
    for hwy in sorted(by_hwy):
        has, tot = by_hwy[hwy]
        print(f"    {hwy:20s} ref present: {has:5,}/{tot:5,}  ({100*has/max(tot,1):.0f}%)")


# ---------------------------------------------------------------------------
# Cache bbox filter (Section 3 prerequisite)
# ---------------------------------------------------------------------------

def compute_cache_bbox(ways):
    """Return (min_lon, min_lat, max_lon, max_lat) from all cached way coords."""
    lons = [c[0] for w in ways.values() for c in w["coords_wgs84"]]
    lats = [c[1] for w in ways.values() for c in w["coords_wgs84"]]
    return min(lons), min(lats), max(lons), max(lats)


def filter_pts_by_bbox(pts, bbox, buffer_deg=0.1):
    """Keep only AADT points within the cached OSM tile bounding box + buffer."""
    min_lon, min_lat, max_lon, max_lat = bbox
    filtered = [
        p for p in pts
        if (min_lon - buffer_deg <= p["lon"] <= max_lon + buffer_deg and
            min_lat - buffer_deg <= p["lat"] <= max_lat + buffer_deg)
    ]
    return filtered


# ---------------------------------------------------------------------------
# Section 7: OSM Node Connectivity Analysis
# (critical input for the connectivity extrapolation algorithm)
# ---------------------------------------------------------------------------

def analyze_connectivity(ways):
    """
    Build an endpoint-coordinate -> [way_id, ...] index and report:
      - How motorway_link endpoints connect to mainline / surface roads
      - Cross-type connection matrix at shared nodes
      - What connectivity information is available for graph propagation
    """
    print("\n=== Section 7: OSM Node Connectivity Analysis ===")

    # Build endpoint coordinate -> list of (osm_id, 'first'|'last', hwy_type)
    # We use rounded coords (7 dp) as keys to match identical OSM nodes.
    # OSM node coordinates are exact when from the same node ID, so
    # rounding to 7 dp (~1 cm) is safe for detecting shared endpoints.
    ep_index = defaultdict(list)  # (lon7, lat7) -> list of (osm_id, end, hwy)
    for osm_id, w in ways.items():
        hwy = w["hwy"]
        if hwy not in (MAINLINE_HWY | RAMP_HWY | ARTERIAL_HWY | COLLECTOR_HWY):
            continue
        c0 = w["coords_wgs84"][0]
        cN = w["coords_wgs84"][-1]
        k0 = (round(c0[0], 7), round(c0[1], 7))
        kN = (round(cN[0], 7), round(cN[1], 7))
        ep_index[k0].append((osm_id, "first", hwy))
        ep_index[kN].append((osm_id, "last",  hwy))

    # Only keep nodes where >=2 ways share the endpoint (actual junctions)
    junction_nodes = {k: v for k, v in ep_index.items() if len(v) >= 2}
    print(f"  Shared endpoint nodes (junctions): {len(junction_nodes):,}")
    print(f"  Total endpoint entries:            {sum(len(v) for v in junction_nodes.values()):,}")

    # --- Degree distribution ---
    degree = Counter(len(v) for v in junction_nodes.values())
    print("\n  Ways meeting at shared node (degree distribution):")
    for d in sorted(degree)[:8]:
        print(f"    degree {d}: {degree[d]:5,} nodes")

    # --- Cross-type connection matrix ---
    # For each junction with a motorway_link, what other types are present?
    print("\n  motorway_link endpoint connections (what types does it meet?):")
    ramp_junction_ctr = Counter()
    ramp_junction_nodes = 0
    for node, entries in junction_nodes.items():
        types = {e[2] for e in entries}
        if not (types & RAMP_HWY):
            continue
        ramp_junction_nodes += 1
        # For every non-ramp type that connects here, record it
        for t in types - RAMP_HWY:
            ramp_junction_ctr[t] += 1
    print(f"    Nodes with >=1 motorway_link: {ramp_junction_nodes:,}")
    for hwy, cnt in ramp_junction_ctr.most_common(10):
        print(f"      connects to '{hwy}': {cnt:5,} nodes")

    # --- Per-ramp way: what does each endpoint connect to? ---
    # For each motorway_link way, classify its connectivity:
    #   first_ep_types: highway types at first endpoint
    #   last_ep_types:  highway types at last endpoint
    ramp_ways_classified = []
    for osm_id, w in ways.items():
        if w["hwy"] not in RAMP_HWY:
            continue
        c0 = w["coords_wgs84"][0]
        cN = w["coords_wgs84"][-1]
        k0 = (round(c0[0], 7), round(c0[1], 7))
        kN = (round(cN[0], 7), round(cN[1], 7))
        first_types = {e[2] for e in ep_index.get(k0, []) if e[0] != osm_id}
        last_types  = {e[2] for e in ep_index.get(kN, []) if e[0] != osm_id}
        ramp_ways_classified.append({
            "osm_id":      osm_id,
            "first_types": first_types,
            "last_types":  last_types,
        })

    total_ramp = len(ramp_ways_classified)
    has_mainline_ep = sum(
        1 for r in ramp_ways_classified
        if (r["first_types"] | r["last_types"]) & MAINLINE_HWY
    )
    has_surface_ep = sum(
        1 for r in ramp_ways_classified
        if (r["first_types"] | r["last_types"]) & (ARTERIAL_HWY | COLLECTOR_HWY)
    )
    isolated = sum(
        1 for r in ramp_ways_classified
        if not (r["first_types"] | r["last_types"])
    )
    both_ends_known = sum(
        1 for r in ramp_ways_classified
        if (r["first_types"] | r["last_types"]) & MAINLINE_HWY
        and (r["first_types"] | r["last_types"]) & (ARTERIAL_HWY | COLLECTOR_HWY)
    )
    print(f"\n  Per motorway_link way connectivity ({total_ramp:,} ramp ways):")
    print(f"    Has endpoint on mainline (motorway/trunk):     {has_mainline_ep:5,}  ({100*has_mainline_ep/max(total_ramp,1):.0f}%)")
    print(f"    Has endpoint on surface (primary/secondary/..): {has_surface_ep:5,}  ({100*has_surface_ep/max(total_ramp,1):.0f}%)")
    print(f"    Has BOTH (mainline + surface) endpoints:        {both_ends_known:5,}  ({100*both_ends_known/max(total_ramp,1):.0f}%)")
    print(f"    Isolated (no shared endpoint with other ways):  {isolated:5,}  ({100*isolated/max(total_ramp,1):.0f}%)")

    print("\n  NOTE for extrapolation:")
    print("    Ramp ways with mainline endpoint = can inherit fraction of mainline AADT")
    print("    Ramp ways with surface endpoint  = can anchor ramp terminal to surface road")
    print("    Isolated ramp ways               = tile-edge fragments; use spatial fallback")

    return ep_index, ramp_ways_classified


# ---------------------------------------------------------------------------
# Section 8: Coverage Gap Analysis
# (what % of OSM ways are within AADT matching range?)
# ---------------------------------------------------------------------------

def analyze_coverage_gap(ways, pts, trees, bbox):
    """
    For each road group, measure: what % of OSM ways have an AADT point within
    various radii? This directly tells us how much network extrapolation is needed.
    Only considers AADT points within the cache bbox (filtered).
    """
    print("\n=== Section 8: Coverage Gap Analysis (within cache bbox) ===")

    mainline_pts_f = [p for p in pts if p["source"] in ("mainline","truck") and p["aadt"]]
    ramp_pts_f     = [p for p in pts if p["source"] == "ramp" and p["aadt"]]

    # Build point-based STRtrees (points, not ways) to query from way side
    def build_pt_tree(point_list):
        geoms   = [Point(p["px"], p["py"]) for p in point_list]
        ids     = list(range(len(point_list)))
        return STRtree(geoms), geoms, point_list

    ml_tree_pts,  ml_geoms,  ml_list  = build_pt_tree(mainline_pts_f)
    rmp_tree_pts, rmp_geoms, rmp_list = build_pt_tree(ramp_pts_f)

    groups = [
        ("motorway + trunk",       MAINLINE_HWY, ml_tree_pts,  ml_geoms),
        ("motorway_link",          RAMP_HWY,     rmp_tree_pts, rmp_geoms),
        ("primary + secondary",    ARTERIAL_HWY, ml_tree_pts,  ml_geoms),
        ("tertiary",               COLLECTOR_HWY,ml_tree_pts,  ml_geoms),
    ]

    for label, hwy_set, pt_tree, pt_geoms in groups:
        group_ways = [w for w in ways.values() if w["hwy"] in hwy_set]
        n = len(group_ways)
        if n == 0:
            continue
        print(f"\n  {label} ({n:,} ways):")
        for radius in (100, 250, 500, 1000, 2000):
            covered = 0
            for w in group_ways:
                cx, cy = w["p_centroid"]
                nearby = pt_tree.query(Point(cx, cy), predicate="dwithin", distance=radius)
                if len(nearby) > 0:
                    covered += 1
            pct = 100 * covered / n
            print(f"    Within {radius:5d}m of AADT point: {covered:5,}/{n:,}  ({pct:.1f}%)")


# ---------------------------------------------------------------------------
# Section 9: Network Propagation Preview
# (BFS hop analysis — how many graph steps to reach an AADT-matched segment?)
# ---------------------------------------------------------------------------

def analyze_network_propagation(ways, pts, ep_index):
    """
    Build a way-adjacency graph from shared endpoints, then BFS from
    AADT-matched ways to find how many hops are needed to reach unmatched ways.
    Focuses on motorway + motorway_link subgraph (the most important for
    connectivity extrapolation of freeway volumes).
    """
    print("\n=== Section 9: Network Propagation Preview ===")

    # --- Step 1: determine which ways are "AADT-matched" ---
    # A way is matched if there's a mainline AADT pt within 500m (generous).
    # Ramp ways matched if ramp AADT within 300m.
    MAINLINE_R = 500.0
    RAMP_R     = 300.0

    mainline_pts_all = [p for p in pts if p["source"] in ("mainline","truck") and p["aadt"]]
    ramp_pts_all     = [p for p in pts if p["source"] == "ramp" and p["aadt"]]

    ml_ptgeoms  = [Point(p["px"], p["py"]) for p in mainline_pts_all]
    rmp_ptgeoms = [Point(p["px"], p["py"]) for p in ramp_pts_all]
    ml_ptree    = STRtree(ml_ptgeoms)  if ml_ptgeoms  else None
    rmp_ptree   = STRtree(rmp_ptgeoms) if rmp_ptgeoms else None

    matched   = set()
    unmatched = set()
    freeway_hwy = MAINLINE_HWY | RAMP_HWY

    for osm_id, w in ways.items():
        if w["hwy"] not in freeway_hwy:
            continue
        cx, cy = w["p_centroid"]
        pt = Point(cx, cy)
        if w["hwy"] in MAINLINE_HWY and ml_ptree:
            near = ml_ptree.query(pt, predicate="dwithin", distance=MAINLINE_R)
            if len(near) > 0:
                matched.add(osm_id)
                continue
        if w["hwy"] in RAMP_HWY and rmp_ptree:
            near = rmp_ptree.query(pt, predicate="dwithin", distance=RAMP_R)
            if len(near) > 0:
                matched.add(osm_id)
                continue
        unmatched.add(osm_id)

    print(f"  Freeway subgraph (motorway + trunk + links):")
    print(f"    AADT-matched ways:   {len(matched):,}")
    print(f"    Unmatched ways:      {len(unmatched):,}")

    if not matched or not unmatched:
        print("  (skip BFS: trivial result)")
        return

    # --- Step 2: build adjacency from shared endpoints ---
    # ep_index already built in analyze_connectivity.
    # Build way -> set of adjacent way_ids (only within freeway subgraph)
    adj = defaultdict(set)
    for node, entries in ep_index.items():
        freeway_entries = [e for e in entries if ways.get(e[0], {}).get("hwy") in freeway_hwy]
        if len(freeway_entries) < 2:
            continue
        ids_at_node = [e[0] for e in freeway_entries]
        for i, a in enumerate(ids_at_node):
            for b in ids_at_node[i+1:]:
                adj[a].add(b)
                adj[b].add(a)

    connected_matched   = sum(1 for m in matched   if adj.get(m))
    connected_unmatched = sum(1 for u in unmatched if adj.get(u))
    print(f"    Matched ways with >=1 neighbor in graph:   {connected_matched:,}")
    print(f"    Unmatched ways with >=1 neighbor in graph: {connected_unmatched:,}")
    print(f"    Isolated ways (no shared endpoint):        {len(matched)+len(unmatched)-len(adj):,}")

    # --- Step 3: BFS from all matched ways simultaneously ---
    # Multi-source BFS: distance = hops to nearest matched way
    from collections import deque
    dist = {m: 0 for m in matched}
    queue = deque(matched)
    visited = set(matched)

    while queue:
        cur = queue.popleft()
        for nb in adj[cur]:
            if nb not in visited:
                visited.add(nb)
                dist[nb] = dist[cur] + 1
                queue.append(nb)

    # Report hop distribution for unmatched ways
    hop_counts = [dist[u] for u in unmatched if u in dist]
    unreachable = len(unmatched) - len(hop_counts)

    if hop_counts:
        hops_arr = np.array(hop_counts)
        print(f"\n  BFS hops from unmatched to nearest AADT-matched way:")
        percs = [25, 50, 75, 90, 95, 100]
        vals  = np.percentile(hops_arr, percs)
        print("  " + "  ".join(f"p{p}={v:.0f}" for p, v in zip(percs, vals)))
        hop_ctr = Counter(hop_counts)
        print(f"\n  Hop count distribution:")
        for h in sorted(hop_ctr)[:10]:
            pct = 100 * hop_ctr[h] / len(hop_counts)
            print(f"    {h} hops: {hop_ctr[h]:5,}  ({pct:.1f}%)")
    if unreachable:
        print(f"  Unreachable (isolated, no path to matched): {unreachable:,}")

    print("\n  NOTE for algorithm design:")
    print("    Ways reachable in <=1 hop can safely inherit adjacent AADT.")
    print("    Ways at >=3 hops need a decay function or class-based fallback.")
    print("    Unreachable ways (tile edges, disconnected) need spatial fallback.")


# ---------------------------------------------------------------------------
# Section 10: Write diagnostic GeoJSON (all AADT pts, enriched with join info)
# ---------------------------------------------------------------------------

def write_diagnostic(pts, ways, trees):
    print("\n=== Section 7: Writing Diagnostic GeoJSON ===")

    features = []
    for p in pts:
        if not p["aadt"]:
            continue

        osm_id_main, d_main = nearest_way(p["px"], p["py"], trees["mainline"], max_dist=5000)
        osm_id_ramp, d_ramp = nearest_way(p["px"], p["py"], trees["ramp"],     max_dist=2000)

        # Pick the "primary" match based on source
        if p["source"] == "ramp":
            pri_id, pri_dist, pri_type = osm_id_ramp, d_ramp, "ramp"
            sec_id, sec_dist, sec_type = osm_id_main, d_main, "mainline"
        else:
            pri_id, pri_dist, pri_type = osm_id_main, d_main, "mainline"
            sec_id, sec_dist, sec_type = osm_id_ramp, d_ramp, "ramp"

        pri_ref = ways[pri_id]["ref"] if pri_id else None
        pri_hwy = ways[pri_id]["hwy"] if pri_id else None
        route_match = (
            _parse_route(pri_ref) == p["route"]
            if pri_ref and p["route"]
            else None
        )

        # Collision count at ramp pts
        if p["source"] == "ramp":
            # Count ramp AADT pts within 300m — proxy for interchange density
            nearby_ramp = ways_within(p["px"], p["py"], trees["ramp"], 300)
            # Count OSM ramp ways within 300m of this AADT point
            n_ramp_300m = len(nearby_ramp)
        else:
            n_ramp_300m = None

        props = {
            **p["props"],
            "nearest_osm_id":    pri_id,
            "nearest_osm_hwy":   pri_hwy,
            "nearest_osm_ref":   pri_ref,
            "nearest_dist_m":    round(pri_dist, 1) if pri_dist else None,
            "route_match":       route_match,
            "sec_osm_id":        sec_id,
            "sec_dist_m":        round(sec_dist, 1) if sec_dist else None,
            "n_osm_ramps_300m":  n_ramp_300m,
        }

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            "properties": props,
        })

    fc = {"type": "FeatureCollection", "features": features}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"  Wrote {len(features):,} features -> {OUT_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("AADT Spatial Join Diagnostic")
    print("=" * 60)

    # 1 + 2: load data
    ways = load_osm_ways()
    pts  = load_aadt()

    # Compute cache bbox and filter AADT pts to cached area
    bbox = compute_cache_bbox(ways)
    print(f"\n=== Cache Bounding Box ===")
    print(f"  lon: {bbox[0]:.4f} to {bbox[2]:.4f}")
    print(f"  lat: {bbox[1]:.4f} to {bbox[3]:.4f}")
    pts_local = filter_pts_by_bbox(pts, bbox, buffer_deg=0.1)
    print(f"  AADT pts within bbox: {len(pts_local):,} / {len(pts):,} total")
    from collections import Counter as _Ctr
    src_ctr = _Ctr(p["source"] for p in pts_local)
    for src in sorted(src_ctr):
        print(f"    {src:10s} {src_ctr[src]:5,}")

    # Build trees (using all OSM ways; filtered AADT pts used in analysis)
    print("\n=== Building STRtrees ===")
    trees = build_trees(ways)

    # Sections 3-6: distance and attribute analyses (use filtered pts)
    analyze_mainline(pts_local, ways, trees)
    analyze_ramps(pts_local, ways, trees)
    analyze_ramp_attributes(ways)
    analyze_ref_quality(ways, pts_local)

    # Sections 7-9: connectivity and coverage (new)
    ep_index, ramp_classified = analyze_connectivity(ways)
    analyze_coverage_gap(ways, pts_local, trees, bbox)
    analyze_network_propagation(ways, pts_local, ep_index)

    # Section 10: write diagnostic GeoJSON (all pts, unfiltered, for map view)
    write_diagnostic(pts, ways, trees)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
