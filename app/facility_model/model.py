import hashlib
import json
import math
import os
import time
from collections import defaultdict
from typing import Any

import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, Point, mapping
from shapely.strtree import STRtree

from app.config import (
    DATA_DIR,
    DEBUG_CACHE,
    HIGHWAY_DEFAULT_LANES,
    HIGHWAY_DEFAULT_SPEED_MPH,
    OSM_CACHE_ZOOM,
    SAC_TILES,
    _RANKABLE_HIGHWAY_FOR_CENTROIDS,
    _VEHICULAR_HIGHWAY_EXTRAS,
)
from app.osm.helpers import haversine_m, tile2bbox
from app.osm.helpers import bearing as bearing_degrees


FACILITY_MODEL_VERSION = 3
DEFAULT_JUNCTION_TOLERANCE_M = 15.0
FACILITY_MODEL_DIR = os.path.join(DEBUG_CACHE, "facility_model")
os.makedirs(FACILITY_MODEL_DIR, exist_ok=True)

LINK_HIGHWAYS = {
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}
VEHICULAR_HIGHWAYS = set(_RANKABLE_HIGHWAY_FOR_CENTROIDS) | set(_VEHICULAR_HIGHWAY_EXTRAS)
CONTROL_POINT_TYPES = {"traffic_signals", "stop", "give_way", "roundabout", "mini_roundabout"}


def _safe_float(raw: Any, fallback: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(fallback)


def _safe_int(raw: Any, fallback: int) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return int(fallback)


def _way_speed_mph(props: dict[str, Any], highway: str) -> tuple[float, bool]:
    if props.get("speed_mph") not in (None, ""):
        return _safe_float(props.get("speed_mph"), HIGHWAY_DEFAULT_SPEED_MPH.get(highway, 25)), bool(props.get("speed_estimated", False))
    return float(HIGHWAY_DEFAULT_SPEED_MPH.get(highway, 25)), True


def _way_lanes(props: dict[str, Any], highway: str) -> tuple[int, bool]:
    if props.get("lanes") not in (None, ""):
        return _safe_int(props.get("lanes"), HIGHWAY_DEFAULT_LANES.get(highway, 1)), bool(props.get("lanes_estimated", False))
    return int(HIGHWAY_DEFAULT_LANES.get(highway, 1)), True


def cache_path(name: str = "sacramento") -> str:
    return os.path.join(FACILITY_MODEL_DIR, f"{name}_v{FACILITY_MODEL_VERSION}.json")


def load_cached_result(name: str = "sacramento") -> dict[str, Any] | None:
    path = cache_path(name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _hash_id(prefix: str, parts: list[Any]) -> str:
    raw = "|".join(str(p) for p in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def _coord_key(coord: list[float] | tuple[float, float], precision: int = 7) -> tuple[float, float]:
    return (round(float(coord[0]), precision), round(float(coord[1]), precision))


def _node_id_for_key(key: tuple[float, float]) -> str:
    lon_i = int(round((key[0] + 180.0) * 10_000_000))
    lat_i = int(round((key[1] + 90.0) * 10_000_000))
    return f"n_{lon_i}_{lat_i}"


def _line_length_m(coords: list[list[float]]) -> float:
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        total += haversine_m(a[1], a[0], b[1], b[0])
    return total


def _cluster_bearings(values: list[float], tolerance: float = 35.0) -> int:
    if not values:
        return 0
    clusters: list[float] = []
    for value in sorted(v % 360 for v in values):
        if all(abs((value - c + 180) % 360 - 180) > tolerance for c in clusters):
            clusters.append(value)
    if len(clusters) > 1 and abs((clusters[0] - clusters[-1] + 180) % 360 - 180) <= tolerance:
        clusters.pop()
    return len(clusters)


def _geometric_configuration(subtype: str, approaches: list[dict[str, Any]]) -> tuple[str, int]:
    if subtype == "roundabout":
        return "roundabout", max(3, _cluster_bearings([a["bearing"] for a in approaches if "bearing" in a]))
    if subtype == "ramp_terminal":
        return "ramp_terminal", max(1, _cluster_bearings([a["bearing"] for a in approaches if "bearing" in a]))
    bearings = [a["bearing"] for a in approaches if "bearing" in a]
    leg_count = _cluster_bearings(bearings)
    if leg_count <= 2:
        return "two_leg_transition", leg_count
    if leg_count == 3:
        return "three_leg", leg_count
    if leg_count == 4:
        return "four_leg", leg_count
    return "multi_leg", leg_count


def _base_traffic_control_type(subtype: str) -> str:
    if subtype == "roundabout":
        return "roundabout_control"
    return "unknown_control"


def _tiles_bbox(tiles: list[tuple[int, int]], zoom: int) -> list[float]:
    boxes = [tile2bbox(x, y, zoom) for x, y in tiles]
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _utm_epsg_for_bbox(bbox: list[float]) -> int:
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2
    zone = int(math.floor((center_lon + 180) / 6) + 1)
    return (32600 if center_lat >= 0 else 32700) + zone


def _normalize_layer(raw: Any) -> str:
    text = str(raw if raw not in (None, "") else "0").strip()
    return text or "0"


def _truthy_osm(raw: Any) -> bool:
    return str(raw).strip().lower() in {"yes", "true", "1", "viaduct"}


def _is_oneway(props: dict[str, Any]) -> bool:
    return str(props.get("oneway", "")).strip().lower() in {"yes", "true", "1", "-1"}


def _road_subtype(highway: str, props: dict[str, Any]) -> str:
    if highway in LINK_HIGHWAYS:
        return "ramp_or_slip_lane"
    if props.get("junction") == "roundabout" or highway == "roundabout":
        return "roundabout"
    if _is_oneway(props):
        return "oneway_carriageway"
    return "roadway"


def _load_ways_from_tiles(tiles: list[tuple[int, int]], zoom: int) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for x, y in tiles:
        path = os.path.join(DATA_DIR, "osm_cache", f"{zoom}_{x}_{y}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            features = json.load(f)
        for feat in features:
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            if geom.get("type") != "LineString":
                continue
            highway = str(props.get("type") or props.get("highway") or "")
            if highway not in VEHICULAR_HIGHWAYS:
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            osm_id = str(props.get("id") or props.get("osm_id") or _hash_id("way", coords))
            if osm_id in by_id and len(coords) <= len(by_id[osm_id]["coords"]):
                continue
            clean_props = dict(props)
            clean_props["type"] = highway
            clean_props["highway"] = highway
            by_id[osm_id] = {
                "osm_id": osm_id,
                "coords": [[float(c[0]), float(c[1])] for c in coords],
                "props": clean_props,
                "highway": highway,
                "length_m": _line_length_m(coords),
            }
    return [by_id[k] for k in sorted(by_id)]


def _load_control_points_from_tiles(tiles: list[tuple[int, int]], zoom: int) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for x, y in tiles:
        path = os.path.join(DATA_DIR, "osm_cache", f"{zoom}_{x}_{y}.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            features = json.load(f)
        for feat in features:
            geom = feat.get("geometry") or {}
            props = feat.get("properties") or {}
            if geom.get("type") != "Point":
                continue
            kind = str(props.get("type") or props.get("highway") or "")
            if kind not in CONTROL_POINT_TYPES:
                continue
            coords = geom.get("coordinates") or []
            if len(coords) < 2:
                continue
            osm_id = str(props.get("id") or props.get("osm_id") or _hash_id("ctrl", coords + [kind]))
            by_id[osm_id] = {
                "osm_id": osm_id,
                "kind": kind,
                "lon": float(coords[0]),
                "lat": float(coords[1]),
            }
    return [by_id[k] for k in sorted(by_id)]


def build_graph_from_ways(ways: list[dict[str, Any]]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    for way in ways:
        coords = way["coords"]
        props = way["props"]
        for coord in coords:
            key = _coord_key(coord)
            nid = _node_id_for_key(key)
            if nid not in graph:
                graph.add_node(nid, x=key[0], y=key[1], lon=key[0], lat=key[1])
        for idx, (a, b) in enumerate(zip(coords, coords[1:])):
            ak = _coord_key(a)
            bk = _coord_key(b)
            u = _node_id_for_key(ak)
            v = _node_id_for_key(bk)
            seg_coords = [[ak[0], ak[1]], [bk[0], bk[1]]]
            length_m = _line_length_m(seg_coords)
            speed_mph, speed_estimated = _way_speed_mph(props, way["highway"])
            lanes, lanes_estimated = _way_lanes(props, way["highway"])
            attrs = {
                "osmid": way["osm_id"],
                "osm_way_id": way["osm_id"],
                "highway": way["highway"],
                "name": props.get("name") or props.get("ref") or "",
                "geometry": LineString(seg_coords),
                "length": length_m,
                "segment_index": idx,
                "layer": _normalize_layer(props.get("layer")),
                "bridge": _truthy_osm(props.get("bridge")),
                "tunnel": _truthy_osm(props.get("tunnel")),
                "junction": props.get("junction", ""),
                "oneway": props.get("oneway", ""),
                "speed_mph": speed_mph,
                "speed_estimated": speed_estimated,
                "lanes": lanes,
                "lanes_estimated": lanes_estimated,
            }
            graph.add_edge(u, v, **attrs)
            if not _is_oneway(props):
                graph.add_edge(v, u, **attrs)
    return graph


def build_osmnx_graph_from_ways(ways: list[dict[str, Any]]) -> nx.MultiDiGraph:
    """Build a lighter OSMnx graph using one edge per cached OSM way."""
    graph = nx.MultiDiGraph()
    graph.graph["crs"] = "EPSG:4326"
    for way in ways:
        coords = way["coords"]
        props = way["props"]
        start_key = _coord_key(coords[0])
        end_key = _coord_key(coords[-1])
        u = _node_id_for_key(start_key)
        v = _node_id_for_key(end_key)
        for nid, key in ((u, start_key), (v, end_key)):
            if nid not in graph:
                graph.add_node(nid, x=key[0], y=key[1], lon=key[0], lat=key[1])
        speed_mph, speed_estimated = _way_speed_mph(props, way["highway"])
        lanes, lanes_estimated = _way_lanes(props, way["highway"])
        attrs = {
            "osmid": way["osm_id"],
            "osm_way_id": way["osm_id"],
            "highway": way["highway"],
            "name": props.get("name") or props.get("ref") or "",
            "geometry": LineString(coords),
            "length": way["length_m"],
            "layer": _normalize_layer(props.get("layer")),
            "bridge": _truthy_osm(props.get("bridge")),
            "tunnel": _truthy_osm(props.get("tunnel")),
            "junction": props.get("junction", ""),
            "oneway": props.get("oneway", ""),
            "speed_mph": speed_mph,
            "speed_estimated": speed_estimated,
            "lanes": lanes,
            "lanes_estimated": lanes_estimated,
        }
        graph.add_edge(u, v, **attrs)
        if not _is_oneway(props):
            graph.add_edge(v, u, **attrs)
    return graph


def _approaches_for_node(graph: nx.MultiDiGraph, node_id: str) -> list[dict[str, Any]]:
    approaches = []
    seen = set()
    node = graph.nodes[node_id]
    for _u, v, key, data in graph.out_edges(node_id, keys=True, data=True):
        token = (data.get("osm_way_id"), v, key)
        if token in seen:
            continue
        seen.add(token)
        approaches.append({
            "osm_way_id": str(data.get("osm_way_id", "")),
            "highway": str(data.get("highway", "")),
            "name": str(data.get("name", "")),
            "layer": _normalize_layer(data.get("layer")),
            "bridge": bool(data.get("bridge")),
            "tunnel": bool(data.get("tunnel")),
            "junction": str(data.get("junction", "")),
            "speed_mph": _safe_float(data.get("speed_mph"), HIGHWAY_DEFAULT_SPEED_MPH.get(str(data.get("highway", "")), 25)),
            "speed_estimated": bool(data.get("speed_estimated", True)),
            "lanes": _safe_int(data.get("lanes"), HIGHWAY_DEFAULT_LANES.get(str(data.get("highway", "")), 1)),
            "lanes_estimated": bool(data.get("lanes_estimated", True)),
            "bearing": round(bearing_degrees(node["x"], node["y"], graph.nodes[v]["x"], graph.nodes[v]["y"]), 1),
        })
    for u, _v, key, data in graph.in_edges(node_id, keys=True, data=True):
        token = (data.get("osm_way_id"), u, key)
        if token in seen:
            continue
        seen.add(token)
        approaches.append({
            "osm_way_id": str(data.get("osm_way_id", "")),
            "highway": str(data.get("highway", "")),
            "name": str(data.get("name", "")),
            "layer": _normalize_layer(data.get("layer")),
            "bridge": bool(data.get("bridge")),
            "tunnel": bool(data.get("tunnel")),
            "junction": str(data.get("junction", "")),
            "speed_mph": _safe_float(data.get("speed_mph"), HIGHWAY_DEFAULT_SPEED_MPH.get(str(data.get("highway", "")), 25)),
            "speed_estimated": bool(data.get("speed_estimated", True)),
            "lanes": _safe_int(data.get("lanes"), HIGHWAY_DEFAULT_LANES.get(str(data.get("highway", "")), 1)),
            "lanes_estimated": bool(data.get("lanes_estimated", True)),
            "bearing": round(bearing_degrees(node["x"], node["y"], graph.nodes[u]["x"], graph.nodes[u]["y"]), 1),
        })
    return approaches


def _is_grade_separated(approaches: list[dict[str, Any]]) -> bool:
    if len({a["layer"] for a in approaches}) > 1:
        return True
    if len({a["bridge"] for a in approaches}) > 1:
        return True
    if len({a["tunnel"] for a in approaches}) > 1:
        return True
    return False


def _project_points(rows: list[dict[str, Any]], epsg: int) -> gpd.GeoDataFrame:
    geoms = [Point(r["lon"], r["lat"]) for r in rows]
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326").to_crs(epsg=epsg)


def _make_junction_feature(
    point: Point,
    member_ids: list[str],
    approaches: list[dict[str, Any]],
    subtype: str,
    confidence: str,
    diagnostics: list[str] | None = None,
) -> dict[str, Any]:
    incident_way_ids = sorted({a["osm_way_id"] for a in approaches if a.get("osm_way_id")})
    approach_keys = {
        (a.get("osm_way_id", ""), round(float(a.get("bearing", 0.0)) / 10) * 10)
        for a in approaches
        if a.get("osm_way_id")
    }
    approach_bearings = sorted({
        round(float(a["bearing"]), 1)
        for a in approaches
        if "bearing" in a
    })
    geometric_configuration, leg_count = _geometric_configuration(subtype, approaches)
    approach_highways = sorted({a.get("highway", "") for a in approaches if a.get("highway")})
    approach_speeds = [_safe_float(a.get("speed_mph"), 25) for a in approaches if a.get("speed_mph") not in (None, "")]
    approach_lanes = [_safe_int(a.get("lanes"), 1) for a in approaches if a.get("lanes") not in (None, "")]
    speed_estimated = not approach_speeds or all(bool(a.get("speed_estimated", True)) for a in approaches)
    lanes_estimated = not approach_lanes or all(bool(a.get("lanes_estimated", True)) for a in approaches)
    facility_id = _hash_id("j", sorted(member_ids) + incident_way_ids + [subtype])
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [point.x, point.y]},
        "properties": {
            "facility_id": facility_id,
            "facility_type": "junction",
            "subtype": subtype,
            "confidence": confidence,
            "geometric_configuration": geometric_configuration,
            "traffic_control_type": _base_traffic_control_type(subtype),
            "structural_grade": "at_grade",
            "leg_count": leg_count,
            "approach_count": len(approach_keys),
            "approach_highways": ",".join(approach_highways),
            "approach_bearings": ",".join(str(v) for v in approach_bearings[:16]),
            "approach_max_speed_mph": round(max(approach_speeds) if approach_speeds else 25, 1),
            "approach_max_lanes": max(approach_lanes) if approach_lanes else 1,
            "speed_estimated": speed_estimated,
            "lanes_estimated": lanes_estimated,
            "member_node_ids": ",".join(sorted(member_ids)),
            "member_node_count": len(member_ids),
            "incident_osm_way_ids": ",".join(incident_way_ids),
            "incident_way_count": len(incident_way_ids),
            "diagnostic_flags": ",".join(diagnostics or []),
        },
    }


def _roundabout_junctions(ways: list[dict[str, Any]], graph: nx.MultiDiGraph) -> tuple[list[dict[str, Any]], set[str]]:
    features = []
    consumed_nodes: set[str] = set()
    for way in ways:
        props = way["props"]
        if props.get("junction") != "roundabout" and way["highway"] != "roundabout":
            continue
        node_ids = [_node_id_for_key(_coord_key(c)) for c in way["coords"]]
        consumed_nodes.update(node_ids)
        approaches = []
        for nid in node_ids:
            if nid in graph:
                approaches.extend(_approaches_for_node(graph, nid))
        line = LineString(way["coords"])
        center = line.centroid
        features.append(_make_junction_feature(
            center,
            node_ids,
            approaches,
            "roundabout",
            "high",
            ["compound_roundabout"],
        ))
    return features, consumed_nodes


def _candidate_rows(graph: nx.MultiDiGraph, consumed_nodes: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    undirected_degree = dict(graph.to_undirected().degree())
    candidates = []
    diagnostics = []
    for nid, degree in undirected_degree.items():
        if degree < 2 or nid in consumed_nodes:
            continue
        node = graph.nodes[nid]
        approaches = _approaches_for_node(graph, nid)
        if len({a["osm_way_id"] for a in approaches if a.get("osm_way_id")}) < 2:
            continue
        if _is_grade_separated(approaches):
            diagnostics.append({
                "node_id": nid,
                "lon": node["x"],
                "lat": node["y"],
                "kind": "excluded_grade_separation",
                "reason": "approach layer, bridge, or tunnel tags disagree",
                "approaches": approaches,
            })
            continue
        candidates.append({
            "node_id": nid,
            "lon": node["x"],
            "lat": node["y"],
            "degree": degree,
            "approaches": approaches,
        })
    return candidates, diagnostics


def _consolidate_candidate_junctions(
    candidates: list[dict[str, Any]],
    bbox: list[float],
    tolerance_m: float,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    epsg = _utm_epsg_for_bbox(bbox)
    gdf = _project_points(candidates, epsg)
    buffers = gdf.geometry.buffer(tolerance_m)
    merged = buffers.union_all()
    polys = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    poly_gdf = gpd.GeoDataFrame(
        {"cluster_id": list(range(len(polys)))},
        geometry=polys,
        crs=gdf.crs,
    )
    joined = gpd.sjoin(gdf, poly_gdf, how="left", predicate="within")
    features = []
    for cid, group in joined.groupby("cluster_id"):
        centroid_ll = poly_gdf.loc[poly_gdf["cluster_id"] == cid].geometry.iloc[0].centroid
        centroid_ll = gpd.GeoSeries([centroid_ll], crs=gdf.crs).to_crs("EPSG:4326").iloc[0]
        member_ids = [str(v) for v in group["node_id"].tolist()]
        approaches = []
        for value in group["approaches"].tolist():
            approaches.extend(value)
        link_count = sum(1 for a in approaches if a["highway"] in LINK_HIGHWAYS)
        subtype = "ramp_terminal" if link_count else "at_grade_intersection"
        confidence = "high" if len({a["osm_way_id"] for a in approaches}) >= 3 else "medium"
        flags = ["contains_link_approach"] if link_count else []
        features.append(_make_junction_feature(
            centroid_ll,
            member_ids,
            approaches,
            subtype,
            confidence,
            flags,
        ))
    return sorted(features, key=lambda f: f["properties"]["facility_id"])


def _junction_index(junction_points: list[tuple[str, Point]]) -> tuple[STRtree | None, list[str], list[Point]]:
    if not junction_points:
        return None, [], []
    ids = [fid for fid, _geom in junction_points]
    points = [geom for _fid, geom in junction_points]
    return STRtree(points), ids, points


def _nearest_junction_id(
    point: Point,
    index: tuple[STRtree | None, list[str], list[Point]],
    max_distance_deg: float = 0.002,
) -> str:
    tree, ids, points = index
    if tree is None:
        return ""
    nearest = tree.query_nearest(point, max_distance=max_distance_deg)
    if len(nearest) == 0:
        return ""
    best_idx = int(nearest[0])
    if point.distance(points[best_idx]) > max_distance_deg:
        return ""
    return ids[best_idx]


def _split_way_at_junction_nodes(
    way: dict[str, Any],
    junction_node_ids: set[str],
) -> list[list[list[float]]]:
    coords = way["coords"]
    cuts = [0]
    for idx, coord in enumerate(coords[1:-1], start=1):
        if _node_id_for_key(_coord_key(coord)) in junction_node_ids:
            cuts.append(idx)
    cuts.append(len(coords) - 1)
    parts = []
    for a, b in zip(cuts, cuts[1:]):
        if b > a:
            part = coords[a:b + 1]
            if len(part) >= 2:
                parts.append(part)
    return parts


def _segment_features(
    ways: list[dict[str, Any]],
    junctions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    junction_node_ids = set()
    junction_points = []
    for feat in junctions:
        props = feat["properties"]
        junction_node_ids.update(v for v in props.get("member_node_ids", "").split(",") if v)
        junction_points.append((props["facility_id"], Point(feat["geometry"]["coordinates"])))
    junction_idx = _junction_index(junction_points)

    roads = []
    ramps = []
    diagnostics = []
    divided_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for way in ways:
        highway = way["highway"]
        is_link = highway in LINK_HIGHWAYS
        parts = _split_way_at_junction_nodes(way, junction_node_ids)
        if not parts:
            parts = [way["coords"]]
        for idx, coords in enumerate(parts):
            length_m = _line_length_m(coords)
            if length_m <= 1:
                continue
            geom = LineString(coords)
            start_j = _nearest_junction_id(Point(coords[0]), junction_idx)
            end_j = _nearest_junction_id(Point(coords[-1]), junction_idx)
            prefix = "ramp" if is_link else "seg"
            facility_id = _hash_id(prefix, [way["osm_id"], idx, round(length_m, 1), highway])
            speed_mph, speed_estimated = _way_speed_mph(way["props"], highway)
            lanes, lanes_estimated = _way_lanes(way["props"], highway)
            props = {
                "facility_id": facility_id,
                "facility_type": "ramp" if is_link else "road_segment",
                "subtype": _road_subtype(highway, way["props"]),
                "confidence": "medium" if is_link else "high",
                "osm_way_id": way["osm_id"],
                "highway": highway,
                "name": way["props"].get("name") or way["props"].get("ref") or "",
                "length_m": round(length_m, 1),
                "speed_mph": speed_mph,
                "speed_estimated": speed_estimated,
                "lanes": lanes,
                "lanes_estimated": lanes_estimated,
                "begin_junction_id": start_j,
                "end_junction_id": end_j,
                "diagnostic_flags": "link_facility" if is_link else "",
            }
            feat = {
                "type": "Feature",
                "geometry": mapping(geom),
                "properties": props,
            }
            if is_link:
                ramps.append(feat)
            else:
                roads.append(feat)
                if _is_oneway(way["props"]) and props["name"]:
                    divided_by_name[props["name"]].append(feat)

    for name, feats in divided_by_name.items():
        if len(feats) < 2:
            continue
        merged = LineString([Point(f["geometry"]["coordinates"][0]) for f in feats]).centroid
        diagnostics.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [merged.x, merged.y]},
            "properties": {
                "facility_id": _hash_id("diag", ["divided", name, len(feats)]),
                "facility_type": "diagnostic",
                "subtype": "possible_divided_road",
                "confidence": "low",
                "name": name,
                "member_facility_ids": ",".join(f["properties"]["facility_id"] for f in feats),
                "diagnostic_flags": "paired_oneway_name",
            },
        })
    return roads, ramps, diagnostics


def _control_index(
    control_points: list[dict[str, Any]],
    epsg: int,
) -> tuple[STRtree | None, list[dict[str, Any]], list[Point]]:
    if not control_points:
        return None, [], []
    gdf = gpd.GeoDataFrame(
        control_points,
        geometry=[Point(p["lon"], p["lat"]) for p in control_points],
        crs="EPSG:4326",
    ).to_crs(epsg=epsg)
    records = []
    points = []
    for row in gdf.itertuples():
        records.append({"osm_id": str(row.osm_id), "kind": str(row.kind)})
        points.append(row.geometry)
    return STRtree(points), records, points


def _nearby_controls(
    point_ll: Point,
    epsg: int,
    index: tuple[STRtree | None, list[dict[str, Any]], list[Point]],
    radius_m: float,
) -> list[dict[str, Any]]:
    tree, records, points = index
    if tree is None:
        return []
    point_m = gpd.GeoSeries([point_ll], crs="EPSG:4326").to_crs(epsg=epsg).iloc[0]
    hits = tree.query(point_m.buffer(radius_m), predicate="intersects")
    nearby = []
    for idx in hits:
        idx = int(idx)
        rec = dict(records[idx])
        rec["distance_m"] = round(point_m.distance(points[idx]), 1)
        nearby.append(rec)
    return sorted(nearby, key=lambda r: (r["distance_m"], r["kind"], r["osm_id"]))


def _traffic_control_from_nearby(
    subtype: str,
    nearby: list[dict[str, Any]],
    leg_count: int,
) -> tuple[str, str, list[str]]:
    kinds = [p["kind"] for p in nearby]
    flags = []
    if subtype == "roundabout" or "roundabout" in kinds or "mini_roundabout" in kinds:
        return "roundabout_control", "roundabout", flags
    if "traffic_signals" in kinds:
        return "signalized", "signal", flags
    stop_count = kinds.count("stop")
    yield_count = kinds.count("give_way")
    if stop_count:
        stop_subtype = "all_way_stop" if leg_count and stop_count >= leg_count else "partial_stop"
        return "stop_controlled", stop_subtype, flags
    if yield_count:
        return "yield_controlled", "yield", flags
    if subtype == "ramp_terminal":
        flags.append("control_unobserved_at_ramp_terminal")
        return "unknown_control", "unknown", flags
    return "unknown_control", "unknown", flags


def _classify_junctions(
    junctions: list[dict[str, Any]],
    control_points: list[dict[str, Any]],
    bbox: list[float],
    radius_m: float = 35.0,
) -> list[dict[str, Any]]:
    epsg = _utm_epsg_for_bbox(bbox)
    index = _control_index(control_points, epsg)
    for feat in junctions:
        props = feat["properties"]
        nearby = _nearby_controls(Point(feat["geometry"]["coordinates"]), epsg, index, radius_m)
        control_type, control_subtype, flags = _traffic_control_from_nearby(
            props.get("subtype", ""),
            nearby,
            int(props.get("leg_count") or 0),
        )
        existing_flags = [v for v in props.get("diagnostic_flags", "").split(",") if v]
        props["traffic_control_type"] = control_type
        props["traffic_control_subtype"] = control_subtype
        props["control_osm_ids"] = ",".join(p["osm_id"] for p in nearby[:12])
        props["control_point_count"] = len(nearby)
        props["control_search_radius_m"] = radius_m
        props["diagnostic_flags"] = ",".join(sorted(set(existing_flags + flags)))
    return junctions


def _diagnostic_features(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = []
    for row in rows:
        approaches = row.get("approaches", [])
        incident = sorted({a.get("osm_way_id", "") for a in approaches if a.get("osm_way_id")})
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
            "properties": {
                "facility_id": _hash_id("diag", [row["kind"], row["node_id"]] + incident),
                "facility_type": "diagnostic",
                "subtype": row["kind"],
                "confidence": "high",
                "geometric_configuration": "not_an_at_grade_junction",
                "traffic_control_type": "not_applicable",
                "structural_grade": "grade_separated",
                "node_id": row["node_id"],
                "reason": row["reason"],
                "incident_osm_way_ids": ",".join(incident),
                "diagnostic_flags": row["kind"],
            },
        })
    return features


def _run_osmnx_pipeline(graph: nx.MultiDiGraph, tolerance_m: float) -> dict[str, Any]:
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        return {"simplified_nodes": 0, "simplified_edges": 0, "consolidated_nodes": 0, "error": ""}
    try:
        projected = ox.projection.project_graph(graph)
        simplified = ox.simplification.simplify_graph(projected, track_merged=True)
        consolidated = ox.simplification.consolidate_intersections(
            simplified,
            tolerance=tolerance_m,
            rebuild_graph=True,
            reconnect_edges=True,
        )
        return {
            "simplified_nodes": simplified.number_of_nodes(),
            "simplified_edges": simplified.number_of_edges(),
            "consolidated_nodes": consolidated.number_of_nodes(),
            "consolidated_edges": consolidated.number_of_edges(),
            "error": "",
        }
    except Exception as exc:
        return {
            "simplified_nodes": 0,
            "simplified_edges": 0,
            "consolidated_nodes": 0,
            "consolidated_edges": 0,
            "error": str(exc),
        }


def compute_facility_model(
    tiles: list[tuple[int, int]] | None = None,
    tolerance_m: float = DEFAULT_JUNCTION_TOLERANCE_M,
    force_refresh: bool = False,
    name: str = "sacramento",
) -> dict[str, Any]:
    if tiles is None:
        tiles = SAC_TILES
    path = cache_path(name)
    if not force_refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    started = time.time()
    ways = _load_ways_from_tiles(tiles, OSM_CACHE_ZOOM)
    control_points = _load_control_points_from_tiles(tiles, OSM_CACHE_ZOOM)
    bbox = _tiles_bbox(tiles, OSM_CACHE_ZOOM)
    graph = build_graph_from_ways(ways)
    osmnx_graph = build_osmnx_graph_from_ways(ways)
    osmnx_stats = _run_osmnx_pipeline(osmnx_graph, tolerance_m)

    roundabout_features, roundabout_nodes = _roundabout_junctions(ways, graph)
    candidates, diagnostic_rows = _candidate_rows(graph, roundabout_nodes)
    junctions = roundabout_features + _consolidate_candidate_junctions(candidates, bbox, tolerance_m)
    junctions = sorted(junctions, key=lambda f: f["properties"]["facility_id"])
    junctions = _classify_junctions(junctions, control_points, bbox)
    road_segments, ramp_segments, segment_diagnostics = _segment_features(ways, junctions)
    diagnostics = _diagnostic_features(diagnostic_rows) + segment_diagnostics
    diagnostics = sorted(diagnostics, key=lambda f: f["properties"]["facility_id"])

    result = {
        "metadata": {
            "model": "SafetyGIS Facility Model Demo",
            "version": FACILITY_MODEL_VERSION,
            "source": "OSM tile cache",
            "tiles": [[x, y] for x, y in tiles],
            "bbox": bbox,
            "tolerance_m": tolerance_m,
            "elapsed_s": round(time.time() - started, 2),
            "input_way_count": len(ways),
            "control_point_count": len(control_points),
            "graph_nodes": graph.number_of_nodes(),
            "graph_edges": graph.number_of_edges(),
            "osmnx_graph_nodes": osmnx_graph.number_of_nodes(),
            "osmnx_graph_edges": osmnx_graph.number_of_edges(),
            "osmnx": osmnx_stats,
            "counts": {
                "junction_candidates": len(junctions),
                "road_segments": len(road_segments),
                "ramp_segments": len(ramp_segments),
                "diagnostics": len(diagnostics),
            },
            "classification_dimensions": [
                "geometric_configuration",
                "traffic_control_type",
                "structural_grade",
            ],
        },
        "junction_candidates": _feature_collection(junctions),
        "road_segments": _feature_collection(road_segments),
        "ramp_segments": _feature_collection(ramp_segments),
        "diagnostics": _feature_collection(diagnostics),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return result


def get_junction_detail(facility_id: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if result is None:
        result = load_cached_result()
    if not result:
        return None
    for feat in result.get("junction_candidates", {}).get("features", []):
        if feat.get("properties", {}).get("facility_id") == facility_id:
            return feat
    return None
