import json
import os
import re
import time
from collections import defaultdict

import requests

from app.config import (
    OSM_CACHE, OSM_RELATION_CACHE, OSM_CACHE_ZOOM,
    OVERPASS_URLS, OVERPASS_QUERY, _RANKABLE_HIGHWAY_FOR_CENTROIDS,
    _VEHICULAR_HIGHWAY_EXTRAS,
    HIGHWAY_DEFAULT_SPEED_MPH, HIGHWAY_DEFAULT_LANES,
    APPROACH_LENGTH_CAP_M,
)
from app.osm.helpers import tile2bbox, haversine_m, bearing


_MAXSPEED_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mph|km/?h)?\s*$", re.I)


def _parse_maxspeed_mph(raw: str) -> float | None:
    """Parse OSM maxspeed (`'45 mph'`, `'80 km/h'`, `'30'`) into mph or None."""
    if not raw:
        return None
    m = _MAXSPEED_RE.match(raw)
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "").lower().replace("/", "")
    if unit in ("kmh", "km h", ""):
        # OSM convention: bare number is km/h outside the US, mph inside.
        # All our tiles are in California → bare numbers are mph.
        if unit == "":
            return val
        return val * 0.621371
    return val


def _parse_turn_lanes(raw: str) -> list:
    """Split OSM `turn:lanes` ('left|through|through;right') into list[list[str]]."""
    if not raw:
        return []
    return [
        [t.strip() for t in slot.split(";") if t.strip()]
        for slot in raw.split("|")
    ]


def _walk_approach_length(
    nid_list: list, start_idx: int, fwd: bool,
    nodes_dict: dict, cap_m: float,
) -> tuple:
    """
    Walk along `nid_list` starting at `start_idx` in direction `fwd`, summing
    haversine segments until cap_m is reached or the way ends. Returns
    (terminus_node_id, length_m). length_m is capped at cap_m.
    """
    step = 1 if fwd else -1
    n = len(nid_list)
    prev_id = nid_list[start_idx]
    total = 0.0
    terminus = prev_id
    k = start_idx + step
    while 0 <= k < n:
        cur_id = nid_list[k]
        if cur_id not in nodes_dict or prev_id not in nodes_dict:
            break
        lon1, lat1 = nodes_dict[prev_id]
        lon2, lat2 = nodes_dict[cur_id]
        seg = haversine_m(lat1, lon1, lat2, lon2)
        if total + seg >= cap_m:
            total = cap_m
            terminus = cur_id
            break
        total += seg
        terminus = cur_id
        prev_id = cur_id
        k += step
    return (terminus, total)


def _compute_tile_topologies(
    nodes_dict: dict,
    ways_lookup: dict,
    node_degree: dict,
    restrictions: list,
    node_tags: dict | None = None,
) -> dict:
    node_ways: dict = {}
    for wid, wdata in ways_lookup.items():
        for nid in wdata["nid_list"]:
            node_ways.setdefault(nid, []).append(wid)

    restr_by_node: dict = {}
    for r in restrictions:
        restr_by_node.setdefault(r["via_node"], []).append(r)

    topologies: dict = {}

    for nid, deg in node_degree.items():
        if deg < 2 or nid not in nodes_dict:
            continue
        lon, lat = nodes_dict[nid]

        approaches = []
        for wid in node_ways.get(nid, []):
            wdata = ways_lookup.get(wid)
            is_rankable = wdata["wtype"] in _RANKABLE_HIGHWAY_FOR_CENTROIDS
            is_vehicular_extra = wdata["wtype"] in _VEHICULAR_HIGHWAY_EXTRAS
            if not wdata or (not is_rankable and not is_vehicular_extra):
                continue
            tags     = wdata["tags"]
            nid_list = wdata["nid_list"]
            try:
                idx = nid_list.index(nid)
            except ValueError:
                continue
            if idx + 1 < len(nid_list) and nid_list[idx + 1] in nodes_dict:
                adj_nid = nid_list[idx + 1]
                fwd = True
            elif idx - 1 >= 0 and nid_list[idx - 1] in nodes_dict:
                adj_nid = nid_list[idx - 1]
                fwd = False
            else:
                continue
            adj_lon, adj_lat = nodes_dict[adj_nid]
            brg = bearing(lon, lat, adj_lon, adj_lat)

            oneway_tag = tags.get("oneway", "")
            if oneway_tag == "yes":
                oneway = 1 if fwd else -1
            elif oneway_tag == "-1":
                oneway = -1 if fwd else 1
            else:
                oneway = 0

            layer_tag  = (tags.get("layer", "") or "0").strip() or "0"
            bridge_tag = (tags.get("bridge", "") or "").strip().lower()
            tunnel_tag = (tags.get("tunnel", "") or "").strip().lower()

            wtype = wdata["wtype"]
            speed_mph_obs = _parse_maxspeed_mph(tags.get("maxspeed", ""))
            if speed_mph_obs is not None:
                speed_mph, speed_estimated = round(speed_mph_obs, 1), False
            else:
                speed_mph, speed_estimated = HIGHWAY_DEFAULT_SPEED_MPH.get(wtype, 25), True

            try:
                lanes_obs = int(tags.get("lanes", "") or "")
                lanes_val, lanes_estimated = lanes_obs, False
            except (TypeError, ValueError):
                lanes_val, lanes_estimated = HIGHWAY_DEFAULT_LANES.get(wtype, 1), True

            terminus_id, walked_m = _walk_approach_length(
                nid_list, idx, fwd, nodes_dict, APPROACH_LENGTH_CAP_M
            )

            approaches.append({
                "way_id":            wid,
                "name":              tags.get("name") or tags.get("ref", ""),
                "highway":           wtype,
                "oneway":            oneway,
                "lanes":             lanes_val,
                "lanes_estimated":   lanes_estimated,
                "speed_mph":         speed_mph,
                "speed_estimated":   speed_estimated,
                "surface":           tags.get("surface", ""),
                "sidewalk":          tags.get("sidewalk", ""),
                "bicycle":           tags.get("bicycle", ""),
                "turn_lanes":        tags.get("turn:lanes", ""),
                "turn_lanes_list":   _parse_turn_lanes(tags.get("turn:lanes", "")),
                "bearing":           round(brg, 1),
                "approach_length_m": round(walked_m, 1),
                "terminus_node_id":  terminus_id,
                "layer":             layer_tag,
                "bridge":            bridge_tag in ("yes", "true", "1", "viaduct"),
                "tunnel":            tunnel_tag in ("yes", "true", "1", "culvert"),
            })

        if not approaches:
            continue

        # Grade-separation suppression. If approach ways span more than one OSM
        # `layer` value, the node is a 2D crossing (overpass-over-road) or a
        # bridge-endpoint split — not a real intersection. METHODOLOGY.md §5.
        layers_set = {a["layer"] for a in approaches}
        if len(layers_set) > 1:
            continue

        wtypes  = {a["highway"] for a in approaches}
        names   = [a["name"] for a in approaches if a["name"]]
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

        n  = len(approaches)
        cp = min(3 * n * (n - 1) // 2, 56)
        node_restrictions = restr_by_node.get(nid, [])
        no_count = sum(1 for r in node_restrictions if r["restriction"].startswith("no_"))
        cp = max(0, cp - 2 * no_count)

        topologies[str(nid)] = {
            "configuration":   config,
            "approaches":      approaches,
            "restrictions":    [
                {"id": r["id"], "restriction": r["restriction"],
                 "from_way": r["from_way"], "to_way": r["to_way"]}
                for r in node_restrictions
            ],
            "conflict_points": cp,
            "compound_nodes":  [],
        }


    # Roundabout compound grouping. Must run before proximity clustering so
    # ring secondaries are flagged with `roundabout_primary` and excluded
    # from the cluster_tile_intersections candidate set.
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

        n_ap = len(topologies[primary_str]["approaches"])
        no_count = sum(
            1 for r in topologies[primary_str].get("restrictions", [])
            if r.get("restriction", "").startswith("no_")
        )
        topologies[primary_str]["conflict_points"] = max(
            0, min(3 * n_ap * (n_ap - 1) // 2, 56) - 2 * no_count
        )

    return topologies


def osm_tile_features(x: int, y: int) -> list:
    """Return cached or freshly-fetched OSM features for one z12 tile."""
    cache_path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    lon_min, lat_min, lon_max, lat_max = tile2bbox(x, y, OSM_CACHE_ZOOM)
    bbox_str = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    query = OVERPASS_QUERY.format(bbox=bbox_str)

    raw = None
    for url in OVERPASS_URLS:
        for attempt in range(2):
            try:
                resp = requests.post(url, data={"data": query}, timeout=60)
                if resp.status_code == 429:
                    if attempt == 0:
                        time.sleep(8)
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
        with open(cache_path, "w") as f:
            json.dump([], f)
        return []

    elements = raw.get("elements", [])

    nodes_dict: dict  = {}
    tagged_nodes: list = []
    for el in elements:
        if el["type"] != "node":
            continue
        nid = el["id"]
        nodes_dict[nid] = (el["lon"], el["lat"])
        if el.get("tags"):
            tagged_nodes.append(el)

    # Build node_tags lookup for classifier (nid → tags dict)
    node_tags: dict = {nd["id"]: nd.get("tags", {}) for nd in tagged_nodes}

    node_degree: dict = {}
    ways_lookup: dict = {}
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

        # Populate ways_lookup for ALL vehicular ways (rankable + service/track/…)
        # so approaches capture every road meeting at an intersection.
        # Only rankable ways increment node_degree → centroid candidates.
        vehicular = wtype in _RANKABLE_HIGHWAY_FOR_CENTROIDS or wtype in _VEHICULAR_HIGHWAY_EXTRAS
        if vehicular:
            ways_lookup[el["id"]] = {"tags": tags, "nid_list": nid_list, "wtype": wtype}
        if wtype in _RANKABLE_HIGHWAY_FOR_CENTROIDS:
            for nid in nid_list:
                node_degree[nid] = node_degree.get(nid, 0) + 1

        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": el["id"], "type": wtype, **tags},
        })

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

    topologies = _compute_tile_topologies(
        nodes_dict, ways_lookup, node_degree, restrictions, node_tags=node_tags
    )

    for nid_str, topo in topologies.items():
        nid_int = int(nid_str)
        if nid_int in nodes_dict:
            topo["lon"] = nodes_dict[nid_int][0]
            topo["lat"] = nodes_dict[nid_int][1]

    for nid, deg in node_degree.items():
        if deg < 2 or nid not in nodes_dict:
            continue
        topo = topologies.get(str(nid))
        if not topo:
            continue
        if topo.get("roundabout_primary"):
            continue
        lon, lat = nodes_dict[nid]
        props = {
            "id": nid, "type": "intersection_centroid", "degree": deg,
        }
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    with open(cache_path, "w") as f:
        json.dump(features, f)

    rel_cache_path = os.path.join(OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
    with open(rel_cache_path, "w", encoding="utf-8") as f:
        json.dump({"restrictions": restrictions, "topologies": topologies}, f)

    return features
