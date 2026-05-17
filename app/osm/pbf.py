import json
import os
import re
from collections import defaultdict
from typing import Callable

import requests

from app.config import (
    DATA_DIR, OSM_CACHE, OSM_RELATION_CACHE, OSM_CACHE_ZOOM,
    _RANKABLE_HIGHWAY_FOR_CENTROIDS, CA_COUNTIES,
)
from app.osm.helpers import tile2bbox, lat2tile, lon2tile
from app.osm.tile import _compute_tile_topologies

GEOFABRIK_URL = "https://download.geofabrik.de/north-america/us/california-latest.osm.pbf"

_RANKABLE_HW_RE = re.compile(
    r"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|"
    r"secondary|secondary_link|tertiary|tertiary_link|residential|"
    r"unclassified|living_street)$"
)

_EXPAND_DEG = 0.05  # ~5.5 km buffer to capture cross-tile way nodes


def _county_tiles(county_names: list[str]) -> set[tuple[int, int]]:
    tiles: set = set()
    for name in county_names:
        _, (lat_min, lon_min, lat_max, lon_max) = CA_COUNTIES[name]
        x_min = lon2tile(lon_min, OSM_CACHE_ZOOM)
        x_max = lon2tile(lon_max, OSM_CACHE_ZOOM)
        y_min = lat2tile(lat_max, OSM_CACHE_ZOOM)
        y_max = lat2tile(lat_min, OSM_CACHE_ZOOM)
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tiles.add((x, y))
    return tiles


def _tile_set_bbox(target_tiles: set[tuple[int, int]]) -> tuple[float, float, float, float]:
    lons, lats = [], []
    for x, y in target_tiles:
        t_lon_min, t_lat_min, t_lon_max, t_lat_max = tile2bbox(x, y, OSM_CACHE_ZOOM)
        lons.extend([t_lon_min, t_lon_max])
        lats.extend([t_lat_min, t_lat_max])
    return (min(lons), min(lats), max(lons), max(lats))


def _way_matches(tags: dict) -> bool:
    jn = tags.get("junction", "")
    if jn == "roundabout":
        return True
    hw = tags.get("highway", "")
    if _RANKABLE_HW_RE.match(hw):
        return True
    if tags.get("cycleway"):
        return True
    if hw in ("cycleway", "footway", "pedestrian"):
        return True
    if hw == "path" and tags.get("foot") != "no":
        return True
    if tags.get("footway") == "sidewalk":
        return True
    return False


def _classify_way(tags: dict) -> str:
    hw = tags.get("highway", "")
    cy = tags.get("cycleway", "")
    ft = tags.get("footway", "")
    jn = tags.get("junction", "")
    if jn == "roundabout":
        return "roundabout"
    if hw == "cycleway" or cy:
        return "cycleway"
    if hw in ("footway", "path", "pedestrian") or ft == "sidewalk":
        return "footway"
    return hw or "unknown"


def _classify_node(tags: dict) -> str:
    hw = tags.get("highway")
    am = tags.get("amenity")
    tc = tags.get("traffic_calming")
    jn = tags.get("junction")
    if hw == "give_way":
        return "give_way"
    if hw == "mini_roundabout" or jn == "roundabout":
        return "roundabout"
    if hw:
        return hw
    if am:
        return am
    if tc:
        return "traffic_calming"
    return "unknown"


def download_california_pbf(progress_cb: Callable = None) -> str:
    """Download California PBF from Geofabrik with HTTP Range resume.

    Returns path to the downloaded file.
    """
    pbf_path = os.path.join(DATA_DIR, "california-latest.osm.pbf")
    part_path = pbf_path + ".download"
    os.makedirs(DATA_DIR, exist_ok=True)

    # Get remote file size. Geofabrik may redirect, so follow redirects here.
    try:
        head_resp = requests.head(GEOFABRIK_URL, timeout=30, allow_redirects=True)
        total_size = int(head_resp.headers.get("Content-Length", 0))
    except Exception:
        total_size = 0

    if os.path.exists(pbf_path):
        final_size = os.path.getsize(pbf_path)
        if total_size and final_size == total_size:
            if progress_cb:
                progress_cb("downloading_pbf", total_size, total_size)
            return pbf_path
        if not total_size and final_size > 500_000_000:
            if progress_cb:
                progress_cb("downloading_pbf", final_size, final_size)
            return pbf_path
        if total_size and final_size < total_size:
            if not os.path.exists(part_path) or os.path.getsize(part_path) < final_size:
                os.replace(pbf_path, part_path)

    existing_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    if total_size and existing_size > total_size:
        os.remove(part_path)
        existing_size = 0

    headers: dict = {}
    mode = "wb"
    if existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"
        mode = "ab"

    if progress_cb:
        progress_cb("downloading_pbf", existing_size, max(total_size, existing_size, 500_000_000))

    resp = requests.get(GEOFABRIK_URL, headers=headers, stream=True, timeout=120, allow_redirects=True)
    resp.raise_for_status()
    if mode == "ab" and resp.status_code != 206:
        existing_size = 0
        mode = "wb"

    downloaded = existing_size
    with open(part_path, mode) as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb and total_size:
                    progress_cb("downloading_pbf", downloaded, total_size)

    part_size = os.path.getsize(part_path)
    if total_size and part_size < total_size:
        raise RuntimeError(f"Incomplete PBF download: {part_size}/{total_size} bytes")

    os.replace(part_path, pbf_path)

    if progress_cb and total_size == 0:
        progress_cb("downloading_pbf", downloaded, downloaded)

    return pbf_path


def _point_in_bbox(lon: float, lat: float, bbox: tuple) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def _node_in_tile(lon: float, lat: float, target_tiles: set[tuple[int, int]], tile_bboxes: dict) -> set:
    """Return target tile keys whose bbox contains the point."""
    center = (lon2tile(lon, OSM_CACHE_ZOOM), lat2tile(lat, OSM_CACHE_ZOOM))
    tiles = set()
    candidates = [(center[0] + dx, center[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    for tile in candidates:
        if tile not in target_tiles:
            continue
        lon_min, lat_min, lon_max, lat_max = tile_bboxes[tile]
        if lon_min <= lon <= lon_max and lat_min <= lat <= lat_max:
            tiles.add(tile)
    return tiles


def process_pbf_to_tiles(
    county_names: list[str],
    progress_cb: Callable = None,
) -> dict:
    """Download California PBF and generate tile caches for given counties.

    Returns {"pbf_path": str, "tiles_total": int, "tiles_cached": int}.
    """
    import osmium  # lazy import

    target_tiles = _county_tiles(county_names)
    if not target_tiles:
        return {"tiles_total": 0, "tiles_cached": 0}

    # ── Phase 1: Download PBF ──────────────────────────────────────────────
    pbf_path = download_california_pbf(progress_cb)

    # ── Phase 2: Scan PBF for nodes in expanded bbox ───────────────────────
    union_bbox = _tile_set_bbox(target_tiles)
    exp_bbox = (
        union_bbox[0] - _EXPAND_DEG, union_bbox[1] - _EXPAND_DEG,
        union_bbox[2] + _EXPAND_DEG, union_bbox[3] + _EXPAND_DEG,
    )

    # Precompute per-tile bboxes for tile assignment
    tile_bboxes: dict = {t: tile2bbox(t[0], t[1], OSM_CACHE_ZOOM) for t in target_tiles}

    # Pass 1: collect node coordinates and tagged nodes
    if progress_cb:
        progress_cb("scanning_nodes", 0, 0)

    node_locations: dict[int, tuple[float, float]] = {}
    tagged_nodes: list[dict] = []

    class NodeCollector(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.seen = 0
            self.matched = 0

        def node(self, n):
            self.seen += 1
            lon, lat = n.location.lon, n.location.lat
            if _point_in_bbox(lon, lat, exp_bbox):
                node_locations[n.id] = (lon, lat)
                self.matched += 1
                tags = dict(n.tags)
                if tags:
                    tagged_nodes.append({
                        "id": n.id, "lon": lon, "lat": lat, "tags": tags,
                    })
            if progress_cb and self.seen % 250_000 == 0:
                progress_cb("scanning_nodes", self.matched, self.seen)

    nc = NodeCollector()
    nc.apply_file(pbf_path)
    if progress_cb:
        progress_cb("scanning_nodes", len(node_locations), nc.seen)

    if not node_locations:
        print("[pbf] Warning: no nodes found in expanded bbox — check county bbox data")
        return {"tiles_total": len(target_tiles), "tiles_cached": 0}

    # Pass 2: collect ways and restrictions, group by tile
    # tile_batches: { (x,y): { "ways": [...], "tagged_nodes": [...],
    #                           "restrictions": [...], "node_nids": set } }
    tile_batches: dict = defaultdict(lambda: {
        "ways": [], "tagged_nodes": [], "restrictions": [], "node_nids": set(),
    })

    # Determine which tiles each node belongs to (precompute for way routing)
    node_tiles: dict[int, set] = {}
    if progress_cb:
        progress_cb("indexing_nodes", 0, len(node_locations))
    for idx, (nid, (lon, lat)) in enumerate(node_locations.items(), start=1):
        n_tiles = _node_in_tile(lon, lat, target_tiles, tile_bboxes)
        if n_tiles:
            node_tiles[nid] = n_tiles
        if progress_cb and idx % 250_000 == 0:
            progress_cb("indexing_nodes", idx, len(node_locations))
    if progress_cb:
        progress_cb("indexing_nodes", len(node_locations), len(node_locations))

    class WayRelCollector(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.seen = 0
            self.matched = 0

        def way(self, w):
            self.seen += 1
            tags = dict(w.tags)
            if progress_cb and self.seen % 100_000 == 0:
                progress_cb("scanning_ways", self.matched, self.seen)
            if not _way_matches(tags):
                return
            nids = [n.ref for n in w.nodes]
            # Determine which target tiles this way belongs to
            way_tiles: set = set()
            for nid in nids:
                nt = node_tiles.get(nid)
                if nt:
                    way_tiles |= nt
            if not way_tiles:
                return  # way doesn't enter any target tile
            self.matched += 1
            wtype = _classify_way(tags)
            way_entry = {"id": w.id, "tags": tags, "nodes": nids, "wtype": wtype}
            for t in way_tiles:
                batch = tile_batches[t]
                batch["ways"].append(way_entry)
                # Collect all way node IDs (even those outside the tile bbox)
                for nid in nids:
                    batch["node_nids"].add(nid)

        def relation(self, r):
            tags = dict(r.tags)
            if tags.get("type") != "restriction":
                return
            from_way = via_node = to_way = None
            for m in r.members:
                if m.role == "from" and m.type == "w":
                    from_way = m.ref
                if m.role == "via" and m.type == "n":
                    via_node = m.ref
                if m.role == "to" and m.type == "w":
                    to_way = m.ref
            if not (via_node and from_way and to_way):
                return
            restr = {
                "id": r.id,
                "restriction": tags.get("restriction", ""),
                "from_way": from_way,
                "via_node": via_node,
                "to_way": to_way,
            }
            # Assign restriction to tiles containing the via_node
            vtiles = node_tiles.get(via_node, set())
            for t in vtiles:
                tile_batches[t]["restrictions"].append(restr)

    wrc = WayRelCollector()
    if progress_cb:
        progress_cb("scanning_ways", 0, 0)
    wrc.apply_file(pbf_path)
    if progress_cb:
        progress_cb("scanning_ways", wrc.matched, wrc.seen)

    # ── Phase 3: Build tile cache files ────────────────────────────────────
    total_tiles = len(tile_batches)
    tiles_written = 0

    if progress_cb:
        progress_cb("writing_tiles", 0, total_tiles)

    for (tx, ty), batch in tile_batches.items():
        _build_and_write_tile(tx, ty, batch, node_locations, tagged_nodes)
        tiles_written += 1
        if progress_cb:
            progress_cb("writing_tiles", tiles_written, total_tiles)

    # Write empty cache files for target tiles that had no matching elements
    for t in target_tiles:
        if t not in tile_batches:
            _write_empty_tile(t[0], t[1])

    if progress_cb:
        progress_cb("done", total_tiles, total_tiles)

    return {"tiles_total": len(target_tiles), "tiles_cached": len(tile_batches)}


def _build_and_write_tile(
    tx: int, ty: int,
    batch: dict,
    all_node_locations: dict[int, tuple[float, float]],
    all_tagged_nodes: list[dict],
) -> None:
    """Generate features + topology for one tile and write cache files."""
    # Build tile-specific nodes_dict (tile bbox + all nodes of tile's ways)
    tile_bbox = tile2bbox(tx, ty, OSM_CACHE_ZOOM)
    nodes_dict: dict[int, tuple[float, float]] = {}
    tagged_node_feats: list[dict] = []

    # Include: (a) tagged nodes within this tile bbox
    for tn in all_tagged_nodes:
        if _point_in_bbox(tn["lon"], tn["lat"], tile_bbox):
            nid = tn["id"]
            nodes_dict[nid] = (tn["lon"], tn["lat"])
            t_tags = tn["tags"]
            ftype = _classify_node(t_tags)
            tagged_node_feats.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [tn["lon"], tn["lat"]]},
                "properties": {"id": nid, "type": ftype, **t_tags},
            })

    # Include: (b) all way nodes (even outside tile bbox) that we have coords for
    for nid in batch["node_nids"]:
        if nid not in nodes_dict:
            loc = all_node_locations.get(nid)
            if loc:
                nodes_dict[nid] = loc

    # Build way features and tracking structures
    node_degree: dict[int, int] = {}
    ways_lookup: dict[int, dict] = {}
    way_features: list[dict] = []

    for wdata in batch["ways"]:
        wid = wdata["id"]
        tags = wdata["tags"]
        nid_list = wdata["nodes"]
        wtype = wdata["wtype"]
        coords = [
            (nodes_dict[n][0], nodes_dict[n][1])
            for n in nid_list if n in nodes_dict
        ]
        if len(coords) < 2:
            continue

        if wtype in _RANKABLE_HIGHWAY_FOR_CENTROIDS:
            for nid in nid_list:
                if nid in nodes_dict:
                    node_degree[nid] = node_degree.get(nid, 0) + 1
            ways_lookup[wid] = {"tags": tags, "nid_list": nid_list, "wtype": wtype}

        way_features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"id": wid, "type": wtype, **tags},
        })

    # Compute topology
    topologies = _compute_tile_topologies(
        nodes_dict, ways_lookup, node_degree, batch["restrictions"],
    )

    for nid_str, topo in topologies.items():
        nid_int = int(nid_str)
        if nid_int in nodes_dict:
            topo["lon"] = nodes_dict[nid_int][0]
            topo["lat"] = nodes_dict[nid_int][1]

    # Emit intersection_centroid features
    for nid, deg in node_degree.items():
        if deg < 2 or nid not in nodes_dict:
            continue
        topo = topologies.get(str(nid))
        if not topo:
            continue
        if topo.get("roundabout_primary") or topo.get("cluster_secondary"):
            continue
        lon, lat = nodes_dict[nid]
        props: dict = {"id": nid, "type": "intersection_centroid", "degree": deg}
        if topo.get("cluster_id"):
            props["cluster_id"] = topo["cluster_id"]
            props["cluster_size"] = len(topo.get("member_node_ids", []))
        way_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })

    features = way_features + tagged_node_feats

    # Write tile cache
    cache_path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json")
    with open(cache_path, "w") as f:
        json.dump(features, f)

    # Write relation cache
    rel_cache_path = os.path.join(
        OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json"
    )
    with open(rel_cache_path, "w", encoding="utf-8") as f:
        json.dump({
            "restrictions": batch["restrictions"],
            "topologies": topologies,
        }, f)


def _write_empty_tile(tx: int, ty: int) -> None:
    """Write empty cache files for a tile that has no matching OSM elements."""
    cache_path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json")
    if not os.path.exists(cache_path):
        with open(cache_path, "w") as f:
            json.dump([], f)
    rel_cache_path = os.path.join(
        OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json"
    )
    if not os.path.exists(rel_cache_path):
        with open(rel_cache_path, "w", encoding="utf-8") as f:
            json.dump({"restrictions": [], "topologies": {}}, f)
