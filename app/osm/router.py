import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.config import OSM_CACHE, OSM_RELATION_CACHE, OSM_CACHE_ZOOM
from app.osm.helpers import lon2tile, lat2tile, haversine_m
from app.osm.tile import osm_tile_features
from app.osm.cache import (
    _fetching_osm_lock, _fetching_osm_counties,
    _retopology_lock, _retopology_pending,
    fetch_county_osm_bg, retopology_bg,
)

router = APIRouter()


@router.get("/api/osm/dynamic")
def get_osm_dynamic(bbox: str = Query(..., description="west,south,east,north")):
    try:
        west, south, east, north = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")

    x_min = lon2tile(west,  OSM_CACHE_ZOOM)
    x_max = lon2tile(east,  OSM_CACHE_ZOOM)
    y_min = lat2tile(north, OSM_CACHE_ZOOM)
    y_max = lat2tile(south, OSM_CACHE_ZOOM)

    n_tiles = (x_max - x_min + 1) * (y_max - y_min + 1)
    if n_tiles > 64:
        raise HTTPException(status_code=400, detail=f"Bbox too large ({n_tiles} tiles). Zoom in first.")

    tiles = [(x, y) for x in range(x_min, x_max + 1) for y in range(y_min, y_max + 1)]
    features: list = []
    seen:     set   = set()

    with ThreadPoolExecutor(max_workers=min(len(tiles), 8)) as pool:
        future_map = {pool.submit(osm_tile_features, x, y): (x, y) for x, y in tiles}
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


@router.get("/api/osm/topology")
def get_osm_topology(
    node_id: int   = Query(..., description="OSM node ID"),
    lon:     float = Query(..., description="Longitude of the node"),
    lat:     float = Query(..., description="Latitude of the node"),
):
    tx = lon2tile(lon, OSM_CACHE_ZOOM)
    ty = lat2tile(lat, OSM_CACHE_ZOOM)
    rel_path  = os.path.join(OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json")
    main_path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{tx}_{ty}.json")

    if not os.path.exists(rel_path):
        with _retopology_lock:
            if os.path.exists(main_path) and (tx, ty) not in _retopology_pending:
                _retopology_pending.add((tx, ty))
                try:
                    os.remove(main_path)
                except OSError:
                    pass
                threading.Thread(target=retopology_bg, args=(tx, ty), daemon=True).start()
        return JSONResponse({"status": "not_cached"}, status_code=202)

    with open(rel_path, encoding="utf-8") as f:
        rel = json.load(f)
    topologies = rel["topologies"]
    topo = topologies.get(str(node_id))

    # Redirect roundabout secondaries and cluster secondaries to their primary,
    # so the panel always reflects the merged-approach view of the cluster.
    if topo and topo.get("cluster_secondary"):
        primary_id = topo["cluster_secondary"]
        topo = topologies.get(str(primary_id), topo)
        node_id = primary_id
    if topo and topo.get("roundabout_primary"):
        primary_id = topo["roundabout_primary"]
        topo = topologies.get(str(primary_id), topo)
        node_id = primary_id

    if not topo:
        best_id: int | None = None
        best_dist = 51.0
        for cand_str, cand in topologies.items():
            if cand.get("roundabout_primary") or cand.get("cluster_secondary"):
                continue
            if "lat" not in cand or "lon" not in cand:
                continue
            d = haversine_m(lat, lon, cand["lat"], cand["lon"])
            if d < best_dist:
                best_dist, best_id = d, int(cand_str)
        if best_id is None:
            return JSONResponse({"status": "no_topology"}, status_code=404)
        topo = topologies[str(best_id)]
        node_id = best_id

    topo["node_id"] = node_id
    return topo
