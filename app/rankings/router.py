import json
import os
import subprocess
import sys
import threading

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from app.config import (
    BASE_DIR, DATA_DIR, RANKINGS_DIR, CRASH_CACHE, AADT_FILE, CA_COUNTIES,
)
from app.osm.cache import require_sacramento_osm_cache

router = APIRouter()

_rank_job: dict = {"status": "idle", "progress": 0, "message": "", "log": []}
_rank_job_lock = threading.Lock()
_active_rankings_dir: str = RANKINGS_DIR

_SCRIPT_PATH = os.path.join(BASE_DIR, "scripts", "build_safety_rankings.py")
_CA_COUNTY_NAMES = set(CA_COUNTIES.keys())


def _get_rankings_path() -> str:
    return os.path.join(_active_rankings_dir, "statewide.json")


_rankings_cache: dict | None = None
_rankings_cache_path: str = ""
_rankings_cache_mtime: float = 0.0


def _load_rankings() -> dict:
    global _rankings_cache, _rankings_cache_path, _rankings_cache_mtime
    path = _get_rankings_path()
    if not os.path.exists(path):
        return {}
    mtime = os.path.getmtime(path)
    if _rankings_cache is None or path != _rankings_cache_path or mtime != _rankings_cache_mtime:
        print(f"[rankings] Loading {path} into memory cache", flush=True)
        with open(path, encoding="utf-8") as f:
            _rankings_cache = json.load(f)
        _rankings_cache_path  = path
        _rankings_cache_mtime = mtime
    return _rankings_cache


def _run_rankings_script(county: str | None, output_dir: str,
                         weights: str | None = None,
                         counties: str | None = None,
                         min_osm_pct: float = 80.0) -> None:
    global _active_rankings_dir
    cmd = [sys.executable, _SCRIPT_PATH]
    if counties:
        cmd += ["--counties", counties]
    elif county and county != "all":
        cmd += ["--county", county]
    if weights:
        cmd += ["--weights", weights]
    if min_osm_pct != 80.0:
        cmd += ["--min-osm-pct", str(min_osm_pct)]
    env = os.environ.copy()
    env["RANKINGS_DIR"] = output_dir
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        os.makedirs(output_dir, exist_ok=True)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", env=env, cwd=BASE_DIR,
        )
    except Exception as e:
        with _rank_job_lock:
            _rank_job.update({"status": "error", "message": str(e)})
        return

    total_counties = 1
    done_counties  = 0

    for raw in proc.stdout:
        line = raw.rstrip()
        with _rank_job_lock:
            _rank_job["log"].append(line)
            if len(_rank_job["log"]) > 500:
                _rank_job["log"] = _rank_job["log"][-500:]
            _rank_job["message"] = line
            if line.startswith("Processing ") and " county" in line:
                try:
                    total_counties = max(1, int(line.split()[1]))
                except (ValueError, IndexError):
                    pass
                _rank_job["progress"] = 2
            elif line.startswith("[") and "Loading crashes" in line:
                done_counties += 1
                pct = 5 + int(85 * (done_counties - 1) / total_counties)
                _rank_job["progress"] = pct
            elif "Ranking statewide" in line:
                _rank_job["progress"] = 92
            elif line.startswith("Written:"):
                _rank_job["progress"] = 100

    proc.wait()
    with _rank_job_lock:
        if proc.returncode == 0:
            _active_rankings_dir = output_dir
            _rank_job.update({"status": "done", "progress": 100,
                              "output_dir": output_dir})
        else:
            _rank_job.update({"status": "error",
                               "message": _rank_job["message"] or "Script failed"})


@router.get("/api/aadt")
def get_aadt():
    if not os.path.exists(AADT_FILE):
        raise HTTPException(404, "AADT data not found. Run: python scripts/geocode_caltrans_aadt.py")
    return FileResponse(AADT_FILE, media_type="application/geo+json")


@router.get("/api/system/pick_dir")
def pick_directory():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes("-topmost", 1)
        path = filedialog.askdirectory(title="Select Rankings Output Folder")
        root.destroy()
        return JSONResponse({"path": path or ""})
    except Exception as e:
        raise HTTPException(500, f"Folder picker unavailable: {e}")


@router.get("/api/rankings/config")
def get_rankings_config():
    cached = sorted(
        f[:-8] for f in os.listdir(CRASH_CACHE)
        if f.endswith(".geojson") and f[:-8] in _CA_COUNTY_NAMES
    ) if os.path.isdir(CRASH_CACHE) else []
    has_file = os.path.exists(_get_rankings_path())
    return JSONResponse({
        "active_dir": _active_rankings_dir,
        "has_rankings": has_file,
        "cached_counties": cached,
    })


@router.post("/api/rankings/compute")
def start_rankings_compute(
    county: str | None = None,
    counties: str | None = None,
    output_dir: str | None = None,
    weights: str | None = None,
    min_osm_pct: float = 80.0,
):
    if county and county != "all" and county not in _CA_COUNTY_NAMES:
        raise HTTPException(400, f"Unknown county '{county}'")
    if counties:
        unknown = [c for c in counties.split(",") if c.strip() and c.strip() not in _CA_COUNTY_NAMES]
        if unknown:
            raise HTTPException(400, f"Unknown counties: {', '.join(unknown)}")
    effective_dir = output_dir.strip() if output_dir and output_dir.strip() else _active_rankings_dir
    with _rank_job_lock:
        if _rank_job["status"] == "running":
            raise HTTPException(409, "Computation already running")
        _rank_job.update({"status": "running", "progress": 0,
                          "message": "Starting...", "log": [],
                          "output_dir": effective_dir})
    threading.Thread(
        target=_run_rankings_script,
        args=(county, effective_dir, weights, counties, min_osm_pct),
        daemon=True,
    ).start()
    return JSONResponse({"status": "started", "output_dir": effective_dir})


@router.post("/api/rankings/set_dir")
def set_rankings_dir(output_dir: str = Body(..., embed=True)):
    global _active_rankings_dir
    path = os.path.join(output_dir.strip(), "statewide.json")
    if not os.path.exists(path):
        raise HTTPException(404, f"No statewide.json found in '{output_dir}'")
    _active_rankings_dir = output_dir.strip()
    return JSONResponse({"active_dir": _active_rankings_dir})


@router.get("/api/rankings/status")
def get_rankings_status():
    with _rank_job_lock:
        return JSONResponse(dict(_rank_job, log=_rank_job["log"][-20:]))


@router.get("/api/rankings/download")
def download_rankings():
    path = _get_rankings_path()
    if not os.path.exists(path):
        raise HTTPException(404, "Rankings file not found. Run computation first.")
    return FileResponse(path, media_type="application/json",
                        filename="statewide_rankings.json")


@router.get("/api/rankings/bins")
def list_ranking_bins():
    if not os.path.exists(_get_rankings_path()):
        raise HTTPException(404, "Rankings not computed. Run scripts/build_safety_rankings.py first.")
    data = _load_rankings()
    return JSONResponse({
        "generated_at": data["generated_at"],
        "counties": data.get("counties_included", []),
        "bins": {
            k: {
                "count":       v["facility_count"],
                "has_data":    "insufficient_data" not in v,
                "group_stats": v.get("group_stats", {}),
            }
            for k, v in data["bins"].items()
        },
    })


@router.get("/api/rankings/bin/{bin_key:path}")
def get_ranking_bin(bin_key: str):
    if not os.path.exists(_get_rankings_path()):
        raise HTTPException(404, "Rankings not computed")
    data = _load_rankings()
    bin_data = data["bins"].get(bin_key)
    if not bin_data:
        raise HTTPException(404, f"Bin '{bin_key}' not found")
    return JSONResponse(bin_data)


@router.post("/api/rankings/facility_model/compute")
def compute_facility_model_ranking(body: dict = Body(default={})):
    """Compute the facility-model based ranking used by Analysis mode."""
    from app.facility_model.ranking import compute_facility_ranking

    county = str(body.get("county") or "sacramento").strip().lower()
    if county != "sacramento":
        raise HTTPException(400, "Facility-model ranking currently supports Sacramento only.")
    require_sacramento_osm_cache()
    try:
        result = compute_facility_ranking(
            county_name=county,
            force_refresh=bool(body.get("force_refresh", True)),
        )
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse({
        "status": "complete",
        "metadata": result["metadata"],
    })


@router.get("/api/rankings/facility_model/result")
def get_facility_model_ranking():
    """Return the cached facility-model ranking for Analysis mode."""
    from app.facility_model.ranking import load_cached_ranking

    result = load_cached_ranking()
    if result is None:
        raise HTTPException(404, "Facility-model ranking has not been computed yet.")
    return JSONResponse(result)
