import json
import os
import threading
import time

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from app.config import OSM_CACHE, OSM_RELATION_CACHE, OSM_CACHE_ZOOM, SAC_TILES
from app.osm.cache import count_cached_sacramento_tiles, require_sacramento_osm_cache

router = APIRouter()

_sac_dl: dict = {"total": len(SAC_TILES), "done": 0, "active": False, "complete": False,
                 "speed": 0.0, "elapsed": 0.0}
_sac_dl_lock  = threading.Lock()

_bulk_dl: dict = {"active": False, "complete": False, "done": 0, "total": 0,
                  "phase": "", "phase_done": 0, "phase_total": 0, "error": ""}
_bulk_dl_lock = threading.Lock()

_sac_stats: dict = {}
_sac_stats_lock  = threading.Lock()


def clear_sacramento_stats_cache() -> None:
    with _sac_stats_lock:
        _sac_stats.clear()


def _download_sac_bg() -> None:
    from app.osm.tile import osm_tile_features
    total = len(SAC_TILES)
    done  = 0
    t0 = time.monotonic()
    for x, y in SAC_TILES:
        path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
        rel_path = os.path.join(OSM_RELATION_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
        if os.path.exists(path) and os.path.getsize(path) <= 10:
            os.remove(path)
        if os.path.exists(rel_path):
            os.remove(rel_path)
        if not os.path.exists(path):
            try:
                osm_tile_features(x, y)
            except Exception as e:
                print(f"[debug] sac tile {x},{y} failed: {e}")
        done += 1
        now = time.monotonic()
        elapsed_total = now - t0
        with _sac_dl_lock:
            _sac_dl["done"] = done
            _sac_dl["elapsed"] = round(elapsed_total, 1)
            _sac_dl["speed"] = round(done / elapsed_total, 2) if elapsed_total > 0 else 0.0
    with _sac_dl_lock:
        _sac_dl["active"]   = False
        _sac_dl["complete"] = True
        _sac_dl["done"]     = done
    with _sac_stats_lock:
        _sac_stats.clear()
    print(f"[debug] Sacramento download complete: {done}/{total} tiles")


def _download_bulk_bg(county_names: list[str]) -> None:
    from app.osm.pbf import process_pbf_to_tiles

    def progress_cb(phase: str, done: int, total: int) -> None:
        with _bulk_dl_lock:
            _bulk_dl["phase"]       = phase
            _bulk_dl["phase_done"]  = done
            _bulk_dl["phase_total"] = total

    try:
        result = process_pbf_to_tiles(county_names, progress_cb)
        with _bulk_dl_lock:
            _bulk_dl["active"]   = False
            _bulk_dl["complete"] = True
            _bulk_dl["done"]     = result.get("tiles_cached", 0)
            _bulk_dl["total"]    = result.get("tiles_total", 0)
            _bulk_dl["phase"]    = "done"
    except Exception as e:
        import traceback
        print(f"[debug] bulk download error: {e}\n{traceback.format_exc()}")
        with _bulk_dl_lock:
            _bulk_dl["active"]   = False
            _bulk_dl["complete"] = False
            _bulk_dl["error"]    = str(e)
            _bulk_dl["phase"]    = ""

    with _sac_stats_lock:
        _sac_stats.clear()


# ── Routes: Sacramento download ───────────────────────────────────────────────

@router.get("/api/debug/sacramento/status")
def sac_status():
    cached = count_cached_sacramento_tiles()
    total  = len(SAC_TILES)
    return JSONResponse({"cached": cached, "total": total, "complete": cached >= total})


@router.post("/api/debug/sacramento/fetch")
def sac_fetch():
    with _sac_dl_lock:
        if _sac_dl["active"]:
            return JSONResponse({"status": "already_running"})
        cached = count_cached_sacramento_tiles()
        if cached >= len(SAC_TILES):
            return JSONResponse({"status": "already_complete", "cached": cached})
        _sac_dl["active"]   = True
        _sac_dl["complete"] = False
        _sac_dl["done"]     = cached
        _sac_dl["speed"]    = 0.0
        _sac_dl["elapsed"]  = 0.0
    threading.Thread(target=_download_sac_bg, daemon=True).start()
    return JSONResponse({"status": "started"})


@router.get("/api/debug/sacramento/progress")
def sac_progress():
    with _sac_dl_lock:
        cached = _sac_dl["done"] if _sac_dl["active"] else count_cached_sacramento_tiles()
        total  = _sac_dl["total"]
        return JSONResponse({
            "done":     cached,
            "total":    total,
            "pct":      round(cached / total * 100, 1) if total else 0,
            "active":   _sac_dl["active"],
            "complete": cached >= total,
            "speed":    _sac_dl.get("speed", 0.0),
            "elapsed":  _sac_dl.get("elapsed", 0.0),
        })


# ── Routes: Bulk PBF download ─────────────────────────────────────────────────

@router.post("/api/debug/sacramento/fetch_bulk")
def sac_fetch_bulk(body: dict = Body(default={})):
    county_names = body.get("counties", ["sacramento"])
    if isinstance(county_names, str):
        county_names = [county_names]
    with _bulk_dl_lock:
        if _bulk_dl["active"]:
            return JSONResponse({"status": "already_running"})
        _bulk_dl["active"]       = True
        _bulk_dl["complete"]     = False
        _bulk_dl["done"]         = 0
        _bulk_dl["total"]        = 0
        _bulk_dl["phase"]        = ""
        _bulk_dl["phase_done"]   = 0
        _bulk_dl["phase_total"]  = 0
        _bulk_dl["error"]        = ""
    threading.Thread(
        target=_download_bulk_bg, args=(county_names,), daemon=True
    ).start()
    return JSONResponse({"status": "started"})


@router.get("/api/debug/sacramento/fetch_bulk/progress")
def sac_fetch_bulk_progress():
    with _bulk_dl_lock:
        return JSONResponse({
            "active":       _bulk_dl["active"],
            "complete":     _bulk_dl["complete"],
            "done":         _bulk_dl["done"],
            "total":        _bulk_dl["total"],
            "phase":        _bulk_dl["phase"],
            "phase_done":   _bulk_dl["phase_done"],
            "phase_total":  _bulk_dl["phase_total"],
            "error":        _bulk_dl["error"],
        })


@router.get("/api/debug/sacramento/element_stats")
def sac_element_stats():
    """Count all OSM element types in the cached Sacramento tiles."""
    with _sac_stats_lock:
        if _sac_stats:
            return JSONResponse(dict(_sac_stats))

    node_counts: dict = {}
    way_counts:  dict = {}
    tiles_counted = 0

    for x, y in SAC_TILES:
        path = os.path.join(OSM_CACHE, f"{OSM_CACHE_ZOOM}_{x}_{y}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                feats = json.load(f)
            for feat in feats:
                gt    = feat.get("geometry", {}).get("type", "")
                props = feat.get("properties", {})
                ftype = props.get("type", "unknown")
                if gt == "Point":
                    node_counts[ftype] = node_counts.get(ftype, 0) + 1
                elif gt == "LineString":
                    way_counts[ftype] = way_counts.get(ftype, 0) + 1
            tiles_counted += 1
        except Exception:
            pass

    stats = {"nodes": node_counts, "ways": way_counts, "tiles_counted": tiles_counted}
    with _sac_stats_lock:
        _sac_stats.update(stats)
    return JSONResponse(stats)
