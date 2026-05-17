import json
import os

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.config import (
    MAPILLARY_TOKEN, MAPILLARY_API, MLY_CACHE, CACHE_ZOOM,
)
from app.osm.helpers import lon2tile, lat2tile, tile2bbox

router = APIRouter()


def _fetch_mly_tile(x: int, y: int) -> list:
    cache_path = os.path.join(MLY_CACHE, f"{CACHE_ZOOM}_{x}_{y}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    lon_min, lat_min, lon_max, lat_max = tile2bbox(x, y, CACHE_ZOOM)
    params = {
        "fields": "id,thumb_256_url,thumb_1024_url,geometry,captured_at,compass_angle,is_pano",
        "bbox":   f"{lon_min},{lat_min},{lon_max},{lat_max}",
        "limit":  500,
        "access_token": MAPILLARY_TOKEN,
    }
    resp = requests.get(f"{MAPILLARY_API}/images", params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json().get("data", [])

    with open(cache_path, "w") as f:
        json.dump(data, f)
    return data


@router.get("/api/mapillary/token")
def mapillary_token():
    if not MAPILLARY_TOKEN:
        raise HTTPException(status_code=503, detail="MAPILLARY_TOKEN not configured")
    return {"token": MAPILLARY_TOKEN}


@router.get("/api/mapillary/images")
def mapillary_images(bbox: str = Query(..., description="west,south,east,north")):
    if not MAPILLARY_TOKEN:
        raise HTTPException(status_code=503, detail="MAPILLARY_TOKEN not configured")

    try:
        west, south, east, north = map(float, bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be west,south,east,north")

    x_min = lon2tile(west,  CACHE_ZOOM)
    x_max = lon2tile(east,  CACHE_ZOOM)
    y_min = lat2tile(north, CACHE_ZOOM)
    y_max = lat2tile(south, CACHE_ZOOM)

    features = []
    seen = set()

    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            try:
                images = _fetch_mly_tile(x, y)
            except Exception as e:
                print(f"  [mapillary] tile {x},{y} error: {e}")
                continue
            for img in images:
                if img["id"] in seen:
                    continue
                seen.add(img["id"])
                features.append({
                    "type": "Feature",
                    "geometry": img["geometry"],
                    "properties": {
                        "id":            img["id"],
                        "thumb_256":     img.get("thumb_256_url", ""),
                        "thumb_1024":    img.get("thumb_1024_url", ""),
                        "captured_at":   img.get("captured_at", ""),
                        "compass_angle": img.get("compass_angle", 0),
                        "is_pano":       img.get("is_pano", False),
                    },
                })

    return JSONResponse({"type": "FeatureCollection", "features": features})


@router.get("/api/mapillary/image/{image_id}")
def mapillary_single(image_id: str):
    if not MAPILLARY_TOKEN:
        raise HTTPException(status_code=503, detail="MAPILLARY_TOKEN not configured")

    cache_path = os.path.join(MLY_CACHE, f"img_{image_id}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return JSONResponse(json.load(f))

    params = {
        "fields": "id,thumb_256_url,thumb_1024_url,captured_at,compass_angle,is_pano",
        "access_token": MAPILLARY_TOKEN,
    }
    resp = requests.get(f"{MAPILLARY_API}/{image_id}", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    with open(cache_path, "w") as f:
        json.dump(data, f)
    return JSONResponse(data)
