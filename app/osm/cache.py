import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import HTTPException

from app.config import (
    CA_COUNTIES, OSM_CACHE, OSM_CACHE_ZOOM, SAC_TILES,
)
from app.osm.helpers import lon2tile, lat2tile
from app.osm.tile import osm_tile_features

_fetching_osm_counties: set = set()
_fetching_osm_lock = threading.Lock()

_retopology_pending: set = set()
_retopology_lock = threading.Lock()


def county_osm_status(county_name: str) -> dict:
    _, (south, west, north, east) = CA_COUNTIES[county_name]
    tiles = [
        (x, y)
        for x in range(lon2tile(west, OSM_CACHE_ZOOM), lon2tile(east, OSM_CACHE_ZOOM) + 1)
        for y in range(lat2tile(north, OSM_CACHE_ZOOM), lat2tile(south, OSM_CACHE_ZOOM) + 1)
    ]
    total  = len(tiles)
    cached = sum(
        1 for x, y in tiles
        if os.path.exists(os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json"))
    )
    pct = round(cached / total * 100, 1) if total else 0.0
    return {"total": total, "cached": cached, "pct": pct}


def count_cached_tiles(tiles: list[tuple[int, int]]) -> int:
    return sum(
        1 for x, y in tiles
        if os.path.exists(os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json"))
        and os.path.getsize(os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")) > 10
    )


def count_cached_sacramento_tiles() -> int:
    return count_cached_tiles(SAC_TILES)


def require_sacramento_osm_cache() -> int:
    cached = count_cached_sacramento_tiles()
    if cached == 0:
        raise HTTPException(
            400,
            "No Sacramento OSM tiles are cached. Download or build OSM data first.",
        )
    return cached


def fetch_county_osm_bg(county_name: str) -> None:
    _, (south, west, north, east) = CA_COUNTIES[county_name]
    tiles = [
        (x, y)
        for x in range(lon2tile(west, OSM_CACHE_ZOOM), lon2tile(east, OSM_CACHE_ZOOM) + 1)
        for y in range(lat2tile(north, OSM_CACHE_ZOOM), lat2tile(south, OSM_CACHE_ZOOM) + 1)
    ]
    total = len(tiles)
    print(f"[osm] {county_name}: fetching {total} tiles")
    done = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(osm_tile_features, x, y): (x, y) for x, y in tiles}
        for fut in as_completed(futures):
            done += 1
            if done % 10 == 0 or done == total:
                print(f"[osm] {county_name}: {done}/{total} tiles")
    print(f"[osm] {county_name}: done")
    with _fetching_osm_lock:
        _fetching_osm_counties.discard(county_name)


def retopology_bg(x: int, y: int) -> None:
    from app.osm.tile import osm_tile_features as _fetch
    try:
        _fetch(x, y)
    finally:
        with _retopology_lock:
            _retopology_pending.discard((x, y))
