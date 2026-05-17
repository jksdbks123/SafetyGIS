"""
Intersection consolidation via OSMnx geometric node merging.

Algorithm (Boeing 2025):
  1. Build projected graph from OSM data
  2. Filter to nodes with degree >= 2 (dead_ends=False)
  3. Buffer each node by tolerance distance
  4. Union overlapping buffers → one polygon per intersection cluster
  5. Centroid of each polygon → consolidated intersection point

The consolidated graph retains `osmid_original` on each node:
a list of original OSM node IDs that were merged into it.
"""

import json
import os
import time

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString, Point

from app.config import DATA_DIR, SAC_TILES, OSM_CACHE_ZOOM
from app.osm.helpers import tile2bbox

_CONSOLIDATED_DIR = os.path.join(DATA_DIR, "osm_consolidated")
_CONSOLIDATED_CACHE_VERSION = 2  # bump when result format changes
os.makedirs(_CONSOLIDATED_DIR, exist_ok=True)


def _tiles_bbox(tiles: list, zoom: int) -> tuple:
    """Compute the lat/lon bounding box covering a set of tiles."""
    xs, ys = set(), set()
    lons, lats_min, lats_max = [], [], []
    for x, y in tiles:
        b = tile2bbox(x, y, zoom)
        lons.extend([b[0], b[2]])
        lats_min.append(b[1])
        lats_max.append(b[3])
    return (min(lons), min(lats_min), max(lons), max(lats_max))


def _build_graph_from_tiles(tiles: list, zoom: int) -> tuple[nx.MultiDiGraph, dict]:
    """Build a NetworkX MultiDiGraph from cached tile GeoJSON features.

    Each way (LineString) becomes a chain of directed edges.
    Endpoints shared by ≥2 ways become graph nodes.

    Returns:
        (G, road_lookup): graph and dict of {osm_way_id: geojson_feature} for all
                          road features used in the graph.
    """
    from collections import defaultdict

    G = nx.MultiDiGraph()
    road_lookup: dict = {}  # osm_way_id → GeoJSON feature

    ways = []
    for x, y in tiles:
        cache_path = os.path.join(DATA_DIR, "osm_cache", f"{zoom}_{x}_{y}.json")
        if not os.path.exists(cache_path):
            continue
        with open(cache_path, encoding="utf-8") as f:
            features = json.load(f)
        for feat in features:
            if feat.get("geometry", {}).get("type") == "LineString":
                hw = feat.get("properties", {}).get("highway", "")
                if hw:
                    ways.append(feat)
                    osm_id = feat.get("properties", {}).get("id")
                    if osm_id:
                        road_lookup[str(osm_id)] = feat

    # Build spatial index of endpoints
    # Each endpoint: (lon, lat) → [(way_idx, is_start), ...]
    endpoint_groups = defaultdict(list)
    for wi, w in enumerate(ways):
        coords = w["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        # Snap to ~6 decimal places (~0.1m precision) to group shared nodes
        start = (round(coords[0][0], 6), round(coords[0][1], 6))
        end = (round(coords[-1][0], 6), round(coords[-1][1], 6))
        endpoint_groups[start].append((wi, True))
        endpoint_groups[end].append((wi, False))

    # Find nodes (endpoints shared by ≥2 ways, or isolated)
    node_counter = 0
    coord_to_nid = {}

    def _make_node(lon, lat):
        nonlocal node_counter
        nid = f"n{node_counter}"
        node_counter += 1
        G.add_node(nid, x=lon, y=lat, lon=lon, lat=lat)
        return nid

    # First pass: endpoints with degree >= 2
    for (lon, lat), refs in endpoint_groups.items():
        way_count = len(set(r[0] for r in refs))
        if way_count >= 2:
            nid = _make_node(lon, lat)
            coord_to_nid[(lon, lat)] = nid

    # Second pass: also add degree-1 nodes that are endpoints
    for (lon, lat), refs in endpoint_groups.items():
        if (lon, lat) not in coord_to_nid:
            nid = _make_node(lon, lat)
            coord_to_nid[(lon, lat)] = nid

    # Add edges for each way
    for wi, w in enumerate(ways):
        coords = w["geometry"]["coordinates"]
        if len(coords) < 2:
            continue
        start_key = (round(coords[0][0], 6), round(coords[0][1], 6))
        end_key = (round(coords[-1][0], 6), round(coords[-1][1], 6))

        start_nid = coord_to_nid.get(start_key)
        end_nid = coord_to_nid.get(end_key)
        if start_nid is None or end_nid is None:
            continue

        props = w.get("properties", {})
        osm_way_id = str(props.get("id", ""))
        edge_geom = LineString([(c[0], c[1]) for c in coords])
        attrs = {
            "geometry": edge_geom,
            "highway": props.get("highway", ""),
            "name": props.get("name", ""),
            "osm_way_id": osm_way_id,
            "length": edge_geom.length,  # in degrees; approximate
        }
        G.add_edge(start_nid, end_nid, key=0, **attrs)
        # Also add reverse direction (OSM ways are bidirectional unless oneway)
        if not props.get("oneway", "") in ("yes", "1", "-1", True):
            G.add_edge(end_nid, start_nid, key=1, **attrs)

    return G, road_lookup


def compute_consolidated_intersections(
    tiles: list | None = None,
    tolerance: float = 10.0,
    force_refresh: bool = False,
) -> dict:
    """Compute consolidated intersections for a set of tiles.

    Args:
        tiles: list of (x, y) tile tuples at OSM_CACHE_ZOOM. Defaults to SAC_TILES.
        tolerance: per-node buffer radius in meters. Nodes within 2*tolerance
                   of each other get merged. Default 10 → merge within 20 m.
        force_refresh: if True, recompute even if cache exists.

    Returns:
        dict with keys:
            intersections: GeoJSON FeatureCollection of consolidated intersection points
            roads:         GeoJSON FeatureCollection of road edges from the graph
            num_intersections: int
            num_roads: int
            tolerance: float
            tiles_bbox: [w, s, e, n]
    """
    if tiles is None:
        tiles = SAC_TILES

    cache_key = f"sacramento_t{tolerance:.0f}"
    cache_path = os.path.join(_CONSOLIDATED_DIR, f"{cache_key}.json")

    if not force_refresh and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        # Invalidate old caches that lack road_lookup
        if "road_lookup" in cached:
            # Return without road_lookup (served separately by /roads endpoint)
            return {
                "intersections": cached["intersections"],
                "num_intersections": cached["num_intersections"],
                "tolerance": cached.get("tolerance", tolerance),
                "tiles_bbox": cached.get("tiles_bbox", list(_tiles_bbox(tiles, OSM_CACHE_ZOOM))),
            }
        print("[consolidate] Cache missing road_lookup, recomputing...")

    t0 = time.time()

    # 1. Build graph from tile cache
    print(f"[consolidate] Building graph from {len(tiles)} tiles...")
    G, road_lookup = _build_graph_from_tiles(tiles, OSM_CACHE_ZOOM)
    print(f"[consolidate] Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges, "
          f"{len(road_lookup)} road features")

    if G.number_of_nodes() == 0:
        return {"intersections": {"type": "FeatureCollection", "features": []},
                "num_intersections": 0,
                "tolerance": tolerance, "tiles_bbox": [0, 0, 0, 0]}

    # 2. Project to meters (California Albers or appropriate UTM)
    # Use a local projection centered on the data
    bbox = _tiles_bbox(tiles, OSM_CACHE_ZOOM)
    center_lon = (bbox[0] + bbox[2]) / 2
    center_lat = (bbox[1] + bbox[3]) / 2

    # Convert to GeoDataFrame for projection
    node_ids = list(G.nodes())
    node_geoms = [Point(G.nodes[n].get("x", G.nodes[n].get("lon", 0)),
                        G.nodes[n].get("y", G.nodes[n].get("lat", 0)))
                  for n in node_ids]
    nodes_gdf = gpd.GeoDataFrame({"node_id": node_ids}, geometry=node_geoms, crs="EPSG:4326")

    # Project to appropriate UTM
    import math
    utm_zone = int((math.floor((center_lon + 180) / 6) + 1))
    if center_lat >= 0:
        epsg = 32600 + utm_zone  # Northern hemisphere
    else:
        epsg = 32700 + utm_zone  # Southern hemisphere

    nodes_proj = nodes_gdf.to_crs(epsg=epsg)
    print(f"[consolidate] Projected to EPSG:{epsg}, center: ({center_lon:.2f}, {center_lat:.2f})")

    # 3. Geometric consolidation (reimplement OSMnx algorithm)
    # Buffer each node, union all, take centroids
    from shapely import union_all

    # Filter to nodes with degree >= 2 (dead_ends=False equivalent)
    degree_map = dict(G.degree())
    intersection_nodes = nodes_proj[nodes_proj["node_id"].map(
        lambda nid: degree_map.get(nid, 0) >= 2
    )]

    if len(intersection_nodes) == 0:
        print("[consolidate] No intersection nodes found (all degree < 2)")
        return {"intersections": {"type": "FeatureCollection", "features": []},
                "num_intersections": 0, "num_roads_total": G.number_of_edges(),
                "tolerance": tolerance, "tiles_bbox": list(bbox)}

    print(f"[consolidate] {len(intersection_nodes)} intersection nodes (degree >= 2)")

    # Buffer and union
    buffers = intersection_nodes.geometry.buffer(tolerance)
    merged_polygons = union_all(buffers)

    # Handle single polygon vs multi
    if hasattr(merged_polygons, "geoms"):
        polys = list(merged_polygons.geoms)
    else:
        polys = [merged_polygons]

    # Compute centroids
    centroids = [p.centroid for p in polys]

    # Build result GeoDataFrame
    result_gdf = gpd.GeoDataFrame(
        {"cluster_id": range(len(centroids))},
        geometry=centroids, crs=nodes_proj.crs
    )

    # 4. Spatial join: which original nodes belong to each cluster
    cluster_polys_gdf = gpd.GeoDataFrame(
        {"cluster_id": range(len(polys))},
        geometry=polys, crs=nodes_proj.crs
    )
    joined = gpd.sjoin(intersection_nodes, cluster_polys_gdf, how="left", predicate="within")

    # Convert member node geometries back to lat/lon for frontend
    joined_ll = joined.to_crs("EPSG:4326")

    # Build merge metadata with proper lat/lon coordinates
    cluster_members = {}
    for _, row in joined_ll.iterrows():
        cid = row["cluster_id"]
        if cid not in cluster_members:
            cluster_members[cid] = []
        cluster_members[cid].append({
            "node_id": row["node_id"],
            "lon": row.geometry.x if row.geometry else None,
            "lat": row.geometry.y if row.geometry else None,
        })

    # Collect incident OSM way IDs per cluster (roads connected to member nodes)
    cluster_road_ids = {}
    for cid, members in cluster_members.items():
        way_ids = set()
        for m in members:
            nid = m["node_id"]  # synthetic graph node ID, e.g. "n42"
            if nid in G:
                for _, neighbor, edge_data in G.edges(nid, data=True):
                    wid = edge_data.get("osm_way_id", "")
                    if wid:
                        way_ids.add(wid)
        cluster_road_ids[cid] = sorted(way_ids)

    # 5. Convert centroid results to lat/lon GeoJSON
    result_ll = result_gdf.to_crs("EPSG:4326")

    # 6. Build GeoJSON with intersection points and incident road metadata
    intersections = []
    for _, row in result_ll.iterrows():
        cid = row["cluster_id"]
        members = cluster_members.get(cid, [])
        member_ids = [m["node_id"] for m in members]
        incident_ids = cluster_road_ids.get(cid, [])
        intersections.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [row.geometry.x, row.geometry.y]},
            "properties": {
                "cluster_id": cid,
                "merged_node_ids": member_ids,
                "merged_count": len(member_ids),
                "member_details": members,
                "incident_osm_way_ids": incident_ids,
            },
        })

    elapsed = time.time() - t0
    n_single = sum(1 for f in intersections if f["properties"]["merged_count"] == 1)
    n_multi = len(intersections) - n_single
    print(f"[consolidate] Done in {elapsed:.1f}s: {len(intersections)} consolidated intersections "
          f"({n_single} single, {n_multi} multi-node), tolerance={tolerance}m")

    # Cache full result (with road_lookup for the per-cluster roads endpoint)
    cache_data = {
        "intersections": {"type": "FeatureCollection", "features": intersections},
        "num_intersections": len(intersections),
        "num_roads": len(road_lookup),
        "tolerance": tolerance,
        "tiles_bbox": list(bbox),
        "road_lookup": road_lookup,
    }

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f)
    print(f"[consolidate] Cached to {cache_path}")

    # Return without road_lookup (served separately by cluster roads endpoint)
    return {
        "intersections": {"type": "FeatureCollection", "features": intersections},
        "num_intersections": len(intersections),
        "num_roads": len(road_lookup),
        "tolerance": tolerance,
        "tiles_bbox": list(bbox),
    }


def get_merge_detail(cluster_id: int, consolidated_data: dict | None = None) -> dict | None:
    """Return detail for a specific consolidated intersection cluster."""
    if consolidated_data is None:
        consolidated_data = compute_consolidated_intersections()
    features = consolidated_data["intersections"]["features"]
    for feat in features:
        if feat["properties"]["cluster_id"] == cluster_id:
            return feat["properties"]
    return None
