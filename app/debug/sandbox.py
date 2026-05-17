import json
import os
import threading
import time

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse
import requests

from app.config import DATA_DIR, DEBUG_CACHE, OVERPASS_URLS, SAC_TILES
from app.osm.cache import count_cached_sacramento_tiles

router = APIRouter()

_consolidation_state: dict = {
    "active": False, "complete": False, "error": "",
    "num_intersections": 0, "num_roads": 0, "tolerance": 10.0,
}
_consolidation_lock = threading.Lock()
_consolidation_result: dict | None = None

_SANDBOX_OSM_DIR = os.path.join(DEBUG_CACHE, "osm_sandbox")
_SANDBOX_LABEL_FILE = os.path.join(DEBUG_CACHE, "intersection_labels.geojson")
os.makedirs(_SANDBOX_OSM_DIR, exist_ok=True)


def _parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = [float(v) for v in bbox.split(",")]
    except Exception as exc:
        raise HTTPException(400, "bbox must be west,south,east,north") from exc
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise HTTPException(400, "bbox coordinates are invalid")
    if (east - west) * (north - south) > 0.0025:
        raise HTTPException(400, "Sandbox edit-element bbox is too large. Zoom in or draw a smaller area.")
    return west, south, east, north


def _sandbox_cache_path(west: float, south: float, east: float, north: float) -> str:
    key = f"{west:.5f}_{south:.5f}_{east:.5f}_{north:.5f}".replace("-", "m").replace(".", "p")
    return os.path.join(_SANDBOX_OSM_DIR, f"{key}.json")


def _is_parcel_like(tags: dict) -> bool:
    if not tags:
        return False
    excluded_keys = {
        "addr:housenumber", "addr:street", "addr:city", "addr:postcode",
        "building", "building:part", "landuse", "boundary", "admin_level",
        "parcel", "cadastre", "ownership",
    }
    if any(k in tags for k in excluded_keys):
        return True
    if tags.get("type") in {"boundary", "multipolygon"} and (
        "landuse" in tags or "boundary" in tags or "building" in tags
    ):
        return True
    return False


def _element_kind(tags: dict) -> str:
    for key in (
        "highway", "railway", "public_transport", "traffic_sign",
        "traffic_calming", "barrier", "crossing", "cycleway", "footway",
        "junction", "route", "type", "amenity", "leisure", "natural",
    ):
        val = tags.get(key)
        if val:
            return str(val)
    return "tagged"


def _fetch_sandbox_osm_elements(west: float, south: float, east: float, north: float) -> dict:
    cache_path = _sandbox_cache_path(west, south, east, north)
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    bbox = f"{south},{west},{north},{east}"
    query = f"""
[out:json][timeout:60];
(
  node["highway"]({bbox});
  node["traffic_sign"]({bbox});
  node["traffic_calming"]({bbox});
  node["barrier"]({bbox});
  node["public_transport"]({bbox});
  node["railway"~"^(crossing|level_crossing|tram_stop|station)$"]({bbox});
  node["crossing"]({bbox});
  node["amenity"~"^(bus_station|parking|bicycle_parking)$"]({bbox});
  way["highway"]({bbox});
  way["cycleway"]({bbox});
  way["footway"]({bbox});
  way["railway"]({bbox});
  way["public_transport"]({bbox});
  way["barrier"]({bbox});
  relation["type"~"^(restriction|route|route_master|public_transport|connectivity|destination_sign)$"]({bbox});
  relation["restriction"]({bbox});
);
out body;
>;
out skel qt;
"""
    raw = None
    headers = {"User-Agent": "SafetyGIS OSM edit sandbox"}
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query}, headers=headers, timeout=75)
            resp.raise_for_status()
            raw = resp.json()
            break
        except Exception as exc:
            print(f"[debug] sandbox Overpass failed at {url}: {exc}")
    if raw is None:
        raise HTTPException(502, "Overpass request failed for sandbox edit elements")

    elements = raw.get("elements", [])
    nodes = {}
    ways = {}
    tagged_nodes = []
    relations = []

    for el in elements:
        if el.get("type") == "node":
            nodes[el["id"]] = (el.get("lon"), el.get("lat"), el.get("tags", {}))
            if el.get("tags"):
                tagged_nodes.append(el)

    for el in elements:
        if el.get("type") != "way":
            continue
        coords = []
        for nid in el.get("nodes", []):
            if nid in nodes:
                lon, lat, _tags = nodes[nid]
                if lon is not None and lat is not None:
                    coords.append([lon, lat])
        ways[el["id"]] = {"element": el, "coordinates": coords}

    features = []
    counts = {"nodes": 0, "ways": 0, "relations": 0, "skipped_parcel_like": 0}

    for el in tagged_nodes:
        tags = el.get("tags", {})
        if _is_parcel_like(tags):
            counts["skipped_parcel_like"] += 1
            continue
        lon, lat, _tags = nodes.get(el["id"], (None, None, {}))
        if lon is None or lat is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": f"node/{el['id']}",
                "osm_id": el["id"],
                "osm_type": "node",
                "element_kind": _element_kind(tags),
                **tags,
            },
        })
        counts["nodes"] += 1

    for wid, item in ways.items():
        el = item["element"]
        tags = el.get("tags", {})
        coords = item["coordinates"]
        if not tags or _is_parcel_like(tags) or len(coords) < 2:
            if tags and _is_parcel_like(tags):
                counts["skipped_parcel_like"] += 1
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "id": f"way/{wid}",
                "osm_id": wid,
                "osm_type": "way",
                "element_kind": _element_kind(tags),
                **tags,
            },
        })
        counts["ways"] += 1

    for el in elements:
        if el.get("type") != "relation":
            continue
        tags = el.get("tags", {})
        if not tags or _is_parcel_like(tags):
            if tags and _is_parcel_like(tags):
                counts["skipped_parcel_like"] += 1
            continue
        rel_id = el["id"]
        relation_feature_count = 0
        for idx, member in enumerate(el.get("members", [])):
            role = member.get("role", "")
            ref = member.get("ref")
            mtype = member.get("type")
            geom = None
            if mtype == "node" and ref in nodes:
                lon, lat, _tags = nodes[ref]
                geom = {"type": "Point", "coordinates": [lon, lat]}
            elif mtype == "way" and ref in ways and len(ways[ref]["coordinates"]) >= 2:
                geom = {"type": "LineString", "coordinates": ways[ref]["coordinates"]}
            if not geom:
                continue
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": f"relation/{rel_id}/{idx}",
                    "osm_id": rel_id,
                    "osm_type": "relation",
                    "element_kind": _element_kind(tags),
                    "member_type": mtype,
                    "member_ref": ref,
                    "member_role": role,
                    **tags,
                },
            })
            relation_feature_count += 1
        if relation_feature_count:
            counts["relations"] += 1

    result = {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "bbox": [west, south, east, north],
            "counts": counts,
            "source": "OpenStreetMap Overpass API",
            "filtered": "Parcel-like building, address, landuse, boundary, and cadastre elements are excluded.",
        },
    }
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return result


def _load_sandbox_labels() -> dict:
    if not os.path.exists(_SANDBOX_LABEL_FILE):
        return {"type": "FeatureCollection", "features": []}
    with open(_SANDBOX_LABEL_FILE, encoding="utf-8") as f:
        return json.load(f)


def _write_sandbox_labels(fc: dict) -> None:
    os.makedirs(os.path.dirname(_SANDBOX_LABEL_FILE), exist_ok=True)
    with open(_SANDBOX_LABEL_FILE, "w", encoding="utf-8") as f:
        json.dump(fc, f, indent=2)


def _compute_consolidation_bg(tolerance: float) -> None:
    global _consolidation_result
    from app.osm.consolidate import compute_consolidated_intersections
    try:
        result = compute_consolidated_intersections(
            tiles=SAC_TILES, tolerance=tolerance, force_refresh=True
        )
        _consolidation_result = result
        with _consolidation_lock:
            _consolidation_state["active"] = False
            _consolidation_state["complete"] = True
            _consolidation_state["error"] = ""
            _consolidation_state["num_intersections"] = result.get("num_intersections", 0)
            _consolidation_state["num_roads"] = result.get("num_roads", 0)
            _consolidation_state["tolerance"] = tolerance
        print(f"[debug] Consolidation complete: {result.get('num_intersections', 0)} intersections")
    except Exception as e:
        import traceback
        print(f"[debug] Consolidation error: {e}\n{traceback.format_exc()}")
        with _consolidation_lock:
            _consolidation_state["active"] = False
            _consolidation_state["complete"] = False
            _consolidation_state["error"] = str(e)


# ---- Routes: OSM edit sandbox ------------------------------------------------

@router.get("/api/debug/osm/edit_elements")
def sandbox_edit_elements(bbox: str = Query(..., description="west,south,east,north")):
    """Return tagged OSM edit elements for a small bbox, excluding parcel-like data."""
    west, south, east, north = _parse_bbox(bbox)
    return JSONResponse(_fetch_sandbox_osm_elements(west, south, east, north))


@router.get("/api/debug/osm/intersection_labels")
def sandbox_intersection_labels():
    """Return manually drawn sandbox intersection labels."""
    return JSONResponse(_load_sandbox_labels())


@router.post("/api/debug/osm/intersection_labels")
def sandbox_save_intersection_label(body: dict = Body(default={})):
    """Append a manually drawn intersection extent and label metadata."""
    geom = body.get("geometry")
    label = body.get("label", {})
    selected_ids = body.get("selected_element_ids", [])
    if not geom or geom.get("type") != "Polygon":
        raise HTTPException(400, "geometry must be a GeoJSON Polygon")
    if not isinstance(label, dict):
        raise HTTPException(400, "label must be an object")
    if not isinstance(selected_ids, list):
        raise HTTPException(400, "selected_element_ids must be a list")

    fc = _load_sandbox_labels()
    features = fc.setdefault("features", [])
    annotation_id = f"manual_intersection_{int(time.time() * 1000)}"
    props = {
        "id": annotation_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": str(label.get("label", "intersection_candidate")),
        "facility_model": str(label.get("facility_model", "cell_model")),
        "control": str(label.get("control", "")),
        "notes": str(label.get("notes", "")),
        "selected_element_ids": ",".join(str(v) for v in selected_ids),
        "selected_element_ids_json": json.dumps([str(v) for v in selected_ids]),
        "selected_count": len(selected_ids),
    }
    features.append({"type": "Feature", "geometry": geom, "properties": props})
    _write_sandbox_labels(fc)
    return JSONResponse({"status": "saved", "feature": features[-1], "count": len(features)})


# ── Routes: Consolidated Intersections (OSMnx geometric merge) ────────────────

@router.post("/api/debug/consolidated/compute")
def consolidated_compute(body: dict = Body(default={})):
    """Start or re-run geometric intersection consolidation.

    Body (optional):
        tolerance: float (default 10.0) — per-node buffer radius in meters.
                   Nodes within 2*tolerance of each other get merged.
    """
    tolerance = float(body.get("tolerance", 10.0))
    tolerance = max(2.0, min(tolerance, 100.0))

    with _consolidation_lock:
        if _consolidation_state["active"]:
            return JSONResponse({"status": "already_running"})
        cached = count_cached_sacramento_tiles()
        if cached < len(SAC_TILES):
            raise HTTPException(400,
                f"Sacramento data incomplete ({cached}/{len(SAC_TILES)} tiles). Download first.")
        _consolidation_state.update({
            "active": True, "complete": False, "error": "",
            "num_intersections": 0, "num_roads": 0, "tolerance": tolerance,
        })

    threading.Thread(
        target=_compute_consolidation_bg, args=(tolerance,), daemon=True
    ).start()
    return JSONResponse({"status": "started", "tolerance": tolerance})


@router.get("/api/debug/consolidated/progress")
def consolidated_progress():
    with _consolidation_lock:
        return JSONResponse({
            "active":           _consolidation_state["active"],
            "complete":         _consolidation_state["complete"],
            "error":            _consolidation_state["error"],
            "num_intersections": _consolidation_state["num_intersections"],
            "tolerance":         _consolidation_state["tolerance"],
        })


@router.get("/api/debug/consolidated/intersections")
def consolidated_intersections():
    """Return GeoJSON of consolidated intersection points with merge metadata."""
    if _consolidation_result is None:
        from app.osm.consolidate import compute_consolidated_intersections
        try:
            result = compute_consolidated_intersections(tiles=SAC_TILES)
        except Exception as e:
            raise HTTPException(500, f"Consolidation failed: {e}")
    else:
        result = _consolidation_result

    return JSONResponse({
        "intersections": result["intersections"],
        "num_intersections": result["num_intersections"],
        "tolerance": result["tolerance"],
    })


@router.get("/api/debug/consolidated/node/{cluster_id}")
def consolidated_node_detail(cluster_id: int):
    """Return detail of which original nodes were merged into a consolidated intersection."""
    from app.osm.consolidate import get_merge_detail
    detail = get_merge_detail(cluster_id, _consolidation_result)
    if detail is None:
        raise HTTPException(404, f"Consolidated node {cluster_id} not found")
    return JSONResponse(detail)


@router.get("/api/debug/consolidated/node/{cluster_id}/roads")
def consolidated_node_roads(cluster_id: int):
    """Return GeoJSON of road features connected to a consolidated intersection."""
    import json as _json
    if _consolidation_result is None:
        raise HTTPException(404, "No consolidation result available")
    features = _consolidation_result["intersections"]["features"]
    # Load road_lookup from cache (not kept in memory due to size)
    tol = _consolidation_result.get("tolerance", 10)
    cache_path = os.path.join(DATA_DIR, "osm_consolidated", f"sacramento_t{tol:.0f}.json")
    road_lookup = {}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            road_lookup = _json.load(f).get("road_lookup", {})
    target = None
    for feat in features:
        if feat["properties"]["cluster_id"] == cluster_id:
            target = feat
            break
    if target is None:
        raise HTTPException(404, f"Consolidated node {cluster_id} not found")
    incident_ids = target["properties"].get("incident_osm_way_ids", [])
    road_features = []
    for wid in incident_ids:
        if wid in road_lookup:
            road_features.append(road_lookup[wid])
    return JSONResponse({
        "type": "FeatureCollection",
        "features": road_features,
        "cluster_id": cluster_id,
        "num_roads": len(road_features),
    })
