import bisect
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pyproj import Transformer
from shapely.geometry import Point, shape
from shapely.ops import transform
from shapely.strtree import STRtree

from app.config import CRASH_CACHE, DEBUG_CACHE
from app.facility_model.model import compute_facility_model, load_cached_result, _utm_epsg_for_bbox


FACILITY_RANKING_VERSION = 4
FACILITY_RANKING_DIR = os.path.join(DEBUG_CACHE, "facility_ranking")
os.makedirs(FACILITY_RANKING_DIR, exist_ok=True)

YEAR_WINDOW = 5
JUNCTION_MATCH_RADIUS_M = 50.0
LINE_MATCH_RADIUS_M = 30.0
TOP_FEATURE_LIMIT = 500
GROUP_FEATURE_LIMIT = 100
EPDO_WEIGHTS = {
    "fatal": 9.5,
    "severe_injury": 3.5,
    "other_injury": 1.0,
    "pdo": 1.0,
}

ROAD_CLASS = {
    "motorway": "highway",
    "motorway_link": "highway",
    "trunk": "highway",
    "trunk_link": "highway",
    "primary": "arterial",
    "primary_link": "arterial",
    "secondary": "arterial",
    "secondary_link": "arterial",
    "tertiary": "collector",
    "tertiary_link": "collector",
    "residential": "local",
    "unclassified": "local",
    "living_street": "local",
    "roundabout": "collector",
    "service": "local",
    "track": "local",
    "track_grade1": "local",
    "track_grade2": "local",
    "pedestrian": "local",
}
ROAD_CLASS_ORDER = ["highway", "arterial", "collector", "local"]
DEFAULT_SPEED = {"highway": 65, "arterial": 45, "collector": 35, "local": 25}
DEFAULT_LANES = {"highway": 4, "arterial": 2, "collector": 2, "local": 2}
SPEED_BINS = [(0, 25, "<=25mph"), (26, 40, "26-40mph"), (41, 55, "41-55mph"), (56, 999, ">55mph")]
LANE_BINS = [(0, 2, "1-2"), (3, 4, "3-4"), (5, 99, "5+")]


def ranking_cache_path(name: str = "sacramento") -> str:
    return os.path.join(FACILITY_RANKING_DIR, f"{name}_v{FACILITY_RANKING_VERSION}.json")


def load_cached_ranking(name: str = "sacramento") -> dict[str, Any] | None:
    path = ranking_cache_path(name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _road_class(highway: str) -> str:
    return ROAD_CLASS.get(str(highway or ""), "local")


def _highest_road_class(highways: list[str]) -> str:
    best = "local"
    best_rank = ROAD_CLASS_ORDER.index(best)
    for highway in highways:
        road_class = _road_class(highway)
        rank = ROAD_CLASS_ORDER.index(road_class) if road_class in ROAD_CLASS_ORDER else 99
        if rank < best_rank:
            best = road_class
            best_rank = rank
    return best


def _safe_float(raw: Any, fallback: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(fallback)


def _safe_int(raw: Any, fallback: int = 0) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return int(fallback)


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"true", "1", "yes", "y"}


def _bin(value: float, bins: list[tuple[float, float, str]]) -> str:
    for lo, hi, label in bins:
        if lo <= value <= hi:
            return label
    return bins[-1][2]


def _length_bin(length_m: float) -> str:
    if length_m < 100:
        return "short"
    if length_m < 400:
        return "medium"
    return "long"


def _speed_bin(props: dict[str, Any], road_class: str) -> str:
    raw = props.get("approach_max_speed_mph") if props.get("facility_type") == "junction" else props.get("speed_mph")
    return _bin(_safe_float(raw, DEFAULT_SPEED.get(road_class, 25)), SPEED_BINS)


def _lane_bin(props: dict[str, Any], road_class: str) -> str:
    raw = props.get("approach_max_lanes") if props.get("facility_type") == "junction" else props.get("lanes")
    return _bin(_safe_int(raw, DEFAULT_LANES.get(road_class, 2)), LANE_BINS)


def _peer_dimensions(props: dict[str, Any]) -> list[dict[str, str]]:
    facility_type = props.get("facility_type", "")
    if facility_type == "junction":
        highways = [v for v in str(props.get("approach_highways", "")).split(",") if v]
        road_class = _highest_road_class(highways)
        return [
            {"key": "facility_type", "value": "junction", "label": "Junctions"},
            {"key": "road_class", "value": road_class, "label": road_class.title()},
            {"key": "speed_bin", "value": _speed_bin(props, road_class), "label": _speed_bin(props, road_class)},
            {"key": "lane_bin", "value": _lane_bin(props, road_class), "label": f"{_lane_bin(props, road_class)} lanes"},
            {"key": "geometric_configuration", "value": str(props.get("geometric_configuration") or "unknown_geometry"), "label": str(props.get("geometric_configuration") or "unknown_geometry").replace("_", " ").title()},
            {"key": "traffic_control_type", "value": str(props.get("traffic_control_type") or "unknown_control"), "label": str(props.get("traffic_control_type") or "unknown_control").replace("_", " ").title()},
            {"key": "structural_grade", "value": str(props.get("structural_grade") or "unknown_grade"), "label": str(props.get("structural_grade") or "unknown_grade").replace("_", " ").title()},
        ]
    road_class = _road_class(props.get("highway", ""))
    if facility_type == "ramp":
        return [
            {"key": "facility_type", "value": "ramp", "label": "Ramps"},
            {"key": "road_class", "value": road_class, "label": road_class.title()},
            {"key": "speed_bin", "value": _speed_bin(props, road_class), "label": _speed_bin(props, road_class)},
            {"key": "lane_bin", "value": _lane_bin(props, road_class), "label": f"{_lane_bin(props, road_class)} lanes"},
            {"key": "subtype", "value": str(props.get("subtype") or "ramp_or_slip_lane"), "label": str(props.get("subtype") or "ramp_or_slip_lane").replace("_", " ").title()},
            {"key": "length_bin", "value": _length_bin(_safe_float(props.get("length_m"), 0.0)), "label": _length_bin(_safe_float(props.get("length_m"), 0.0)).title()},
        ]
    return [
        {"key": "facility_type", "value": "road_segment", "label": "Road Segments"},
        {"key": "road_class", "value": road_class, "label": road_class.title()},
        {"key": "speed_bin", "value": _speed_bin(props, road_class), "label": _speed_bin(props, road_class)},
        {"key": "lane_bin", "value": _lane_bin(props, road_class), "label": f"{_lane_bin(props, road_class)} lanes"},
        {"key": "subtype", "value": str(props.get("subtype") or "roadway"), "label": str(props.get("subtype") or "roadway").replace("_", " ").title()},
        {"key": "length_bin", "value": _length_bin(_safe_float(props.get("length_m"), 0.0)), "label": _length_bin(_safe_float(props.get("length_m"), 0.0)).title()},
    ]


def _peer_group(props: dict[str, Any]) -> str:
    return "|".join(dim["value"] for dim in _peer_dimensions(props))


def _epdo_band(percentile: float) -> str:
    if percentile >= 95:
        return "critical"
    if percentile >= 80:
        return "elevated"
    if percentile >= 50:
        return "typical"
    return "low"


def _load_crashes(county_name: str) -> list[dict[str, Any]]:
    path = os.path.join(CRASH_CACHE, f"{county_name}.geojson")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Crash cache not found for {county_name}. Download crash data first.")
    with open(path, encoding="utf-8") as f:
        features = json.load(f).get("features", [])
    cutoff = datetime.now(timezone.utc).year - YEAR_WINDOW
    crashes = []
    for feat in features:
        props = feat.get("properties") or {}
        if int(props.get("year") or 0) < cutoff:
            continue
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        lon, lat = float(coords[0]), float(coords[1])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            continue
        crashes.append({
            "lon": lon,
            "lat": lat,
            "severity": props.get("severity") or "pdo",
            "collision_id": str(props.get("collision_id") or props.get("id") or ""),
            "isfreeway": _truthy(props.get("isfreeway")),
            "pedestrian": _truthy(props.get("has_pedestrian")),
            "cyclist": _truthy(props.get("has_cyclist")),
            "collision_type": str(props.get("collision_type_description") or "").upper().strip(),
            "motor_vehicle": str(props.get("motorvehicleinvolvedwithdesc") or "").upper().strip(),
            "day_of_week": str(props.get("day_of_week") or ""),
            "hour": _crash_hour(props),
            "lighting": str(props.get("lightingdescription") or ""),
            "weather": str(props.get("weather_1") or ""),
            "road_condition": str(props.get("road_condition_1") or ""),
            "pcf": str(props.get("primary_collision_factor_violation") or props.get("primary_collision_factor_code") or ""),
        })
    return crashes


def _crash_hour(props: dict[str, Any]) -> str:
    raw = str(props.get("crash_time_description") or "").strip()
    if len(raw) >= 2 and raw[:2].isdigit():
        return raw[:2]
    raw_dt = str(props.get("crash_date_time") or "").strip()
    for marker in (" AM", " PM"):
        if marker in raw_dt.upper():
            try:
                dt = datetime.strptime(raw_dt, "%m/%d/%Y %I:%M:%S %p")
                return f"{dt.hour:02d}"
            except ValueError:
                return ""
    return ""


def _conflict_type(crash: dict[str, Any]) -> str:
    collision_type = str(crash.get("collision_type") or "").upper().strip()
    motor_vehicle = str(crash.get("motor_vehicle") or "").upper().strip()
    if "PEDESTRIAN" in collision_type or motor_vehicle == "PEDESTRIAN":
        return "ped_veh"
    if motor_vehicle == "BICYCLE":
        return "bike_veh"
    if collision_type == "BROADSIDE":
        return "angle"
    if collision_type == "REAR END":
        return "rear_end"
    if collision_type == "HEAD-ON":
        return "head_on"
    if "SIDE SWIPE" in collision_type or "SIDESWIPE" in collision_type:
        return "sideswipe"
    if collision_type == "OVERTURNED":
        return "overturn"
    return "other"


def _projector(bbox: list[float]):
    epsg = _utm_epsg_for_bbox(bbox)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return lambda geom: transform(transformer.transform, geom)


def _prepare_facilities(facility_model: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str], list[Any], list[str], list[Any]]:
    bbox = facility_model.get("metadata", {}).get("bbox") or [-122, 38, -121, 39]
    project = _projector(bbox)
    registry: dict[str, dict[str, Any]] = {}
    junction_ids, junction_geoms = [], []
    line_ids, line_geoms = [], []

    for layer_name in ("junction_candidates", "road_segments", "ramp_segments"):
        for feat in facility_model.get(layer_name, {}).get("features", []):
            props = dict(feat.get("properties") or {})
            facility_id = props.get("facility_id")
            if not facility_id:
                continue
            geom_ll = shape(feat.get("geometry"))
            geom_m = project(geom_ll)
            props["peer_group"] = _peer_group(props)
            props["peer_dimensions"] = _peer_dimensions(props)
            registry[facility_id] = {
                "feature": feat,
                "properties": props,
                "geometry_m": geom_m,
                "crashes": [],
            }
            if props.get("facility_type") == "junction":
                junction_ids.append(facility_id)
                junction_geoms.append(geom_m)
            else:
                line_ids.append(facility_id)
                line_geoms.append(geom_m)
    return registry, junction_ids, junction_geoms, line_ids, line_geoms


def _line_is_freeway_eligible(props: dict[str, Any]) -> bool:
    if props.get("facility_type") == "ramp":
        return True
    return _road_class(props.get("highway", "")) == "highway"


def _junction_is_freeway_eligible(props: dict[str, Any]) -> bool:
    if props.get("subtype") == "ramp_terminal":
        return True
    highways = [v for v in str(props.get("approach_highways", "")).split(",") if v]
    return _highest_road_class(highways) == "highway"


def match_crashes_to_facilities(
    facility_model: dict[str, Any],
    crashes: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    registry, junction_ids, junction_geoms, line_ids, line_geoms = _prepare_facilities(facility_model)
    bbox = facility_model.get("metadata", {}).get("bbox") or [-122, 38, -121, 39]
    project = _projector(bbox)
    junction_tree = STRtree(junction_geoms) if junction_geoms else None
    line_tree = STRtree(line_geoms) if line_geoms else None
    matched_junctions = 0
    matched_lines = 0

    for crash in crashes:
        point = project(Point(float(crash["lon"]), float(crash["lat"])))
        if junction_tree is not None:
            hits = list(junction_tree.query(point, predicate="dwithin", distance=JUNCTION_MATCH_RADIUS_M))
            if crash.get("isfreeway"):
                hits = [i for i in hits if _junction_is_freeway_eligible(registry[junction_ids[int(i)]]["properties"])]
            if hits:
                best = min((int(i) for i in hits), key=lambda i: junction_geoms[i].distance(point))
                registry[junction_ids[best]]["crashes"].append(crash)
                matched_junctions += 1

        if line_tree is not None:
            hits = list(line_tree.query(point, predicate="dwithin", distance=LINE_MATCH_RADIUS_M))
            if crash.get("isfreeway"):
                hits = [i for i in hits if _line_is_freeway_eligible(registry[line_ids[int(i)]]["properties"])]
            if hits:
                best = min((int(i) for i in hits), key=lambda i: line_geoms[i].distance(point))
                registry[line_ids[best]]["crashes"].append(crash)
                matched_lines += 1

    return registry, {
        "crash_count": len(crashes),
        "matched_junction_assignments": matched_junctions,
        "matched_line_assignments": matched_lines,
    }


def _score_crashes(crashes: list[dict[str, Any]]) -> dict[str, Any]:
    fatal = sum(1 for c in crashes if c.get("severity") == "fatal")
    severe = sum(1 for c in crashes if c.get("severity") == "severe_injury")
    other = sum(1 for c in crashes if c.get("severity") == "other_injury")
    pdo = sum(1 for c in crashes if c.get("severity") == "pdo")
    epdo = sum(EPDO_WEIGHTS.get(c.get("severity", "pdo"), EPDO_WEIGHTS["pdo"]) for c in crashes)
    return {
        "epdo_score": round(epdo, 2),
        "crash_total": len(crashes),
        "fatal": fatal,
        "severe_injury": severe,
        "other_injury": other,
        "pdo": pdo,
        "pedestrian_crashes": sum(1 for c in crashes if c.get("pedestrian")),
        "cyclist_crashes": sum(1 for c in crashes if c.get("cyclist")),
    }


def _count_values(crashes: list[dict[str, Any]], key: str, limit: int = 8) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for crash in crashes:
        value = str(crash.get(key) or "").strip()
        if value:
            counts[value] += 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return {k: v for k, v in ordered}


def _crash_distributions(crashes: list[dict[str, Any]]) -> dict[str, Any]:
    conflict_counts: dict[str, int] = defaultdict(int)
    hour_counts: dict[str, int] = defaultdict(int)
    for crash in crashes:
        conflict_counts[_conflict_type(crash)] += 1
        if crash.get("hour"):
            hour_counts[str(crash["hour"])] += 1
    return {
        "conflict_type": dict(sorted(conflict_counts.items())),
        "collision_type": _count_values(crashes, "collision_type"),
        "mveh": _count_values(crashes, "motor_vehicle"),
        "hour": dict(sorted(hour_counts.items())),
        "day": _count_values(crashes, "day_of_week", 7),
        "lighting": _count_values(crashes, "lighting"),
        "weather": _count_values(crashes, "weather"),
        "road_cond": _count_values(crashes, "road_condition"),
        "pcf": _count_values(crashes, "pcf"),
        "ped": sum(1 for c in crashes if c.get("pedestrian")),
        "cyc": sum(1 for c in crashes if c.get("cyclist")),
        "imp": 0,
    }


def _compact_crash_coords(crashes: list[dict[str, Any]]) -> str:
    sev_code = {"fatal": "k", "severe_injury": "s", "other_injury": "i", "pdo": "p"}
    return json.dumps([
        [round(float(c["lon"]), 6), round(float(c["lat"]), 6), sev_code.get(str(c.get("severity")), "p")]
        for c in crashes[:200]
    ])


def _collision_ids(crashes: list[dict[str, Any]]) -> str:
    return json.dumps([c["collision_id"] for c in crashes[:200] if c.get("collision_id")])


def _midrank_percentile(value: float, sorted_values: list[float]) -> float:
    if not sorted_values:
        return 0.0
    lo = bisect.bisect_left(sorted_values, value)
    hi = bisect.bisect_right(sorted_values, value)
    return round(((lo + hi) / 2) / len(sorted_values) * 100, 2)


def _nearest_rank_percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, round((percentile / 100) * (len(sorted_values) - 1))))
    return round(float(sorted_values[idx]), 2)


def _group_label(dimensions: list[dict[str, str]]) -> str:
    return " / ".join(dim.get("label") or dim.get("value") or "" for dim in dimensions)


def _build_group_tree(group_stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    root = {"label": "All Facilities", "facility_count": 0, "nonzero_count": 0, "children": []}

    def ensure_child(node: dict[str, Any], dim: dict[str, str]) -> dict[str, Any]:
        for child in node["children"]:
            if child["key"] == dim["key"] and child["value"] == dim["value"]:
                return child
        child = {
            "key": dim["key"],
            "value": dim["value"],
            "label": dim.get("label") or dim["value"],
            "facility_count": 0,
            "nonzero_count": 0,
            "max_epdo": 0.0,
            "p95_epdo": 0.0,
            "children": [],
        }
        node["children"].append(child)
        return child

    for group_key, stats in group_stats.items():
        node = root
        for dim in stats.get("dimensions", []):
            node = ensure_child(node, dim)
            node["facility_count"] += int(stats.get("facility_count") or 0)
            node["nonzero_count"] += int(stats.get("nonzero_count") or 0)
            node["max_epdo"] = max(float(node.get("max_epdo") or 0.0), float(stats.get("max_epdo") or 0.0))
            node["p95_epdo"] = max(float(node.get("p95_epdo") or 0.0), float(stats.get("p95_epdo") or 0.0))
        node["group_key"] = group_key
        node["is_leaf"] = True
        node["stats"] = stats
        root["facility_count"] += int(stats.get("facility_count") or 0)
        root["nonzero_count"] += int(stats.get("nonzero_count") or 0)

    def sort_children(node: dict[str, Any]) -> None:
        node["children"].sort(key=lambda c: (-int(c.get("nonzero_count") or 0), -float(c.get("max_epdo") or 0.0), str(c.get("label") or "")))
        for child in node["children"]:
            sort_children(child)

    sort_children(root)
    return root


def _cdf(values: list[float]) -> list[float]:
    return [_nearest_rank_percentile(values, p) for p in range(0, 101, 5)]


def _dashboard_group_stats(stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "n": int(stats.get("facility_count") or 0),
        "mean": round(float(stats.get("mean_epdo") or 0.0), 2),
        "p50": round(float(stats.get("p50_epdo") or 0.0), 2),
        "p75": round(float(stats.get("p75_epdo") or 0.0), 2),
        "p90": round(float(stats.get("p90_epdo") or 0.0), 2),
        "p95": round(float(stats.get("p95_epdo") or 0.0), 2),
        "max": round(float(stats.get("max_epdo") or 0.0), 2),
        "cdf": stats.get("cdf") or [],
    }


def _dashboard_props(props: dict[str, Any], rec: dict[str, Any], group_stats: dict[str, Any]) -> dict[str, Any]:
    crashes = rec.get("crashes", [])
    facility_type = props.get("facility_type") or ""
    road_class = props.get("road_class")
    if not road_class:
        if facility_type == "junction":
            road_class = _highest_road_class([v for v in str(props.get("approach_highways", "")).split(",") if v])
        else:
            road_class = _road_class(props.get("highway", ""))
    if facility_type == "junction":
        road_type = props.get("traffic_control_type") or "unknown_control"
        speed_mph = props.get("approach_max_speed_mph")
        lanes = props.get("approach_max_lanes")
    else:
        road_type = props.get("subtype") or props.get("highway") or facility_type
        speed_mph = props.get("speed_mph")
        lanes = props.get("lanes")
    props["county"] = props.get("county") or "sacramento"
    props["road_class"] = road_class
    props["road_type"] = road_type
    props["control_type"] = props.get("traffic_control_type")
    props["speed_mph"] = _safe_float(speed_mph, DEFAULT_SPEED.get(road_class, 25))
    props["lanes"] = _safe_int(lanes, DEFAULT_LANES.get(road_class, 2))
    props["fatal_5yr"] = props.get("fatal", 0)
    props["severe_5yr"] = props.get("severe_injury", 0)
    props["total_5yr"] = props.get("crash_total", 0)
    props["crash_rate_yr"] = round(float(props.get("crash_total") or 0) / YEAR_WINDOW, 2)
    props["epdo_weights"] = dict(EPDO_WEIGHTS)
    props["year_window"] = YEAR_WINDOW
    props["group_stats"] = json.dumps(_dashboard_group_stats(group_stats))
    props["crash_dists"] = _crash_distributions(crashes)
    props["crash_coords"] = _compact_crash_coords(crashes)
    props["collision_ids"] = _collision_ids(crashes)
    return props


def rank_facilities(registry: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    scored = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    records_by_id: dict[str, dict[str, Any]] = {}
    for facility_id, rec in registry.items():
        props = dict(rec["properties"])
        score = _score_crashes(rec["crashes"])
        props.update(score)
        props["facility_id"] = facility_id
        groups[props["peer_group"]].append(props)
        scored.append((rec, props))
        records_by_id[facility_id] = rec

    group_stats = {}
    grouped_features: dict[str, list[dict[str, Any]]] = {}
    for group_key, items in groups.items():
        values = sorted(float(p["epdo_score"]) for p in items)
        ordered = sorted(items, key=lambda p: (-float(p["epdo_score"]), -int(p["crash_total"]), p["facility_id"]))
        for rank, props in enumerate(ordered, start=1):
            props["peer_rank"] = rank
            props["peer_size"] = len(items)
            props["epdo_percentile"] = _midrank_percentile(float(props["epdo_score"]), values)
            props["epdo_band"] = _epdo_band(float(props["epdo_percentile"]))
        group_stats[group_key] = {
            "group_key": group_key,
            "label": _group_label(ordered[0].get("peer_dimensions", [])) if ordered else group_key,
            "dimensions": ordered[0].get("peer_dimensions", []) if ordered else [],
            "facility_count": len(items),
            "nonzero_count": sum(1 for p in items if p["crash_total"] > 0),
            "max_epdo": round(max(values), 2) if values else 0.0,
            "mean_epdo": round(sum(values) / len(values), 2) if values else 0.0,
            "p50_epdo": _nearest_rank_percentile(values, 50),
            "p75_epdo": _nearest_rank_percentile(values, 75),
            "p90_epdo": _nearest_rank_percentile(values, 90),
            "p95_epdo": _nearest_rank_percentile(values, 95),
            "cdf": _cdf(values),
        }
        for props in ordered:
            _dashboard_props(props, records_by_id[props["facility_id"]], group_stats[group_key])
        grouped_features[group_key] = [
            {
                "type": "Feature",
                "geometry": records_by_id[p["facility_id"]]["feature"]["geometry"],
                "properties": p,
            }
            for p in ordered
            if p["crash_total"] > 0
        ][:GROUP_FEATURE_LIMIT]

    ranked_features = []
    for rec, props in scored:
        if props["crash_total"] <= 0:
            continue
        feat = {
            "type": "Feature",
            "geometry": rec["feature"]["geometry"],
            "properties": props,
        }
        ranked_features.append(feat)
    ranked_features.sort(
        key=lambda f: (
            -float(f["properties"].get("epdo_score") or 0.0),
            -int(f["properties"].get("crash_total") or 0),
            str(f["properties"].get("facility_id") or ""),
        )
    )
    return ranked_features, group_stats, grouped_features


def compute_facility_ranking(
    county_name: str = "sacramento",
    force_refresh: bool = False,
    facility_model: dict[str, Any] | None = None,
    crashes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = ranking_cache_path(county_name)
    if not force_refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    if crashes is None:
        crashes = _load_crashes(county_name)
    if facility_model is None:
        facility_model = load_cached_result(county_name) or compute_facility_model(name=county_name)

    registry, match_stats = match_crashes_to_facilities(facility_model, crashes)
    ranked_features, group_stats, grouped_features = rank_facilities(registry)
    group_tree = _build_group_tree(group_stats)
    top_features = ranked_features[:TOP_FEATURE_LIMIT]
    result = {
        "metadata": {
            "model": "SafetyGIS Facility Ranking Demo",
            "version": FACILITY_RANKING_VERSION,
            "county": county_name,
            "year_window": YEAR_WINDOW,
            "facility_model_version": facility_model.get("metadata", {}).get("version"),
            "facility_count": len(registry),
            "ranked_nonzero_count": len(ranked_features),
            "top_feature_limit": TOP_FEATURE_LIMIT,
            "group_feature_limit": GROUP_FEATURE_LIMIT,
            "match_radii_m": {
                "junction": JUNCTION_MATCH_RADIUS_M,
                "road_or_ramp": LINE_MATCH_RADIUS_M,
            },
            "epdo_weights": EPDO_WEIGHTS,
            **match_stats,
        },
        "top_ranked": _feature_collection(top_features),
        "ranked_facilities": _feature_collection(ranked_features),
        "peer_groups": group_stats,
        "group_tree": group_tree,
        "group_rankings": {
            key: _feature_collection(features)
            for key, features in grouped_features.items()
            if features
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return result


def get_ranked_facility_detail(facility_id: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if result is None:
        result = load_cached_ranking()
    if not result:
        return None
    for layer in ("ranked_facilities", "top_ranked"):
        for feat in result.get(layer, {}).get("features", []):
            if feat.get("properties", {}).get("facility_id") == facility_id:
                return feat
    return None
