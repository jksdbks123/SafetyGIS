from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from app.config import SAC_TILES
from app.osm.cache import require_sacramento_osm_cache

router = APIRouter()


# ---- Routes: Facility model demo --------------------------------------------

@router.post("/api/debug/facility_model/compute")
def facility_model_compute(body: dict = Body(default={})):
    """Build the SafetyGIS facility-model demo from cached Sacramento OSM tiles."""
    from app.facility_model.model import compute_facility_model

    cached = require_sacramento_osm_cache()
    if cached == 0:
        raise HTTPException(
            400,
            "No Sacramento OSM tiles are cached. Download data first.",
        )
    tolerance = float(body.get("tolerance_m", body.get("tolerance", 15.0)))
    tolerance = max(2.0, min(tolerance, 100.0))
    result = compute_facility_model(
        tiles=SAC_TILES,
        tolerance_m=tolerance,
        force_refresh=bool(body.get("force_refresh", True)),
    )
    return JSONResponse({
        "status": "complete",
        "metadata": result["metadata"],
    })


@router.get("/api/debug/facility_model/result")
def facility_model_result():
    """Return diagnostic GeoJSON layers for the SafetyGIS facility-model demo."""
    from app.facility_model.model import load_cached_result

    result = load_cached_result()
    if result is None:
        raise HTTPException(404, "Facility model has not been built yet.")
    return JSONResponse(result)


@router.get("/api/debug/facility_model/junction/{facility_id}")
def facility_model_junction_detail(facility_id: str):
    """Return one facility-model junction candidate by stable facility ID."""
    from app.facility_model.model import get_junction_detail

    detail = get_junction_detail(facility_id)
    if detail is None:
        raise HTTPException(404, f"Facility junction {facility_id} not found")
    return JSONResponse(detail)

