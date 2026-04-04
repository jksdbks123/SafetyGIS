"""
Fetches OSM traffic infrastructure data for Sacramento and Humboldt County
via the Overpass API and saves as GeoJSON.

Usage: python scripts/fetch_osm.py
"""

import requests
import json
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

AREAS = {
    "sacramento": {
        "bbox": "38.43,-121.56,38.68,-121.36",
        "label": "Sacramento"
    },
    "humboldt": {
        "bbox": "40.60,-124.40,41.10,-123.70",
        "label": "Humboldt County"
    }
}

# Mirrors in fallback order
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

# out geom; returns coordinates directly on each element — no node resolution needed.
OVERPASS_QUERY = """
[out:json][timeout:90];
(
  node["highway"="traffic_signals"]({bbox});
  node["highway"="crossing"]({bbox});
  node["highway"="stop"]({bbox});
  node["amenity"="bus_station"]({bbox});
  node["highway"="bus_stop"]({bbox});
  node["traffic_calming"]({bbox});
  node["highway"="street_lamp"]({bbox});
  way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|residential|unclassified|living_street)$"]({bbox});
  way["cycleway"]({bbox});
  way["highway"="cycleway"]({bbox});
  way["highway"="footway"]({bbox});
  way["highway"="path"]["foot"!="no"]({bbox});
  way["footway"="sidewalk"]({bbox});
  way["highway"="pedestrian"]({bbox});
);
out geom;
"""


def fetch_area(name: str, config: dict) -> dict:
    print(f"Fetching OSM data for {config['label']}...")
    query = OVERPASS_QUERY.format(bbox=config["bbox"])
    raw = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query}, timeout=120)
            resp.raise_for_status()
            raw = resp.json()
            print(f"  OK from {url}")
            break
        except Exception as e:
            print(f"  Mirror {url} failed: {e}, trying next...")
    if raw is None:
        raise RuntimeError("All Overpass mirrors failed")

    features = []
    for el in raw.get("elements", []):
        if el["type"] == "node":
            tags = el.get("tags", {})
            if not tags:
                continue
            hw = tags.get("highway")
            am = tags.get("amenity")
            tc = tags.get("traffic_calming")
            if hw:
                ftype = hw
            elif am:
                ftype = am
            elif tc:
                ftype = "traffic_calming"
            else:
                ftype = "unknown"
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
                "properties": {"id": el["id"], "type": ftype, **tags}
            })
        elif el["type"] == "way":
            tags = el.get("tags", {})
            coords = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
            if len(coords) < 2:
                continue
            hw = tags.get("highway", "")
            cy = tags.get("cycleway", "")
            ft = tags.get("footway", "")
            if hw == "cycleway" or cy:
                wtype = "cycleway"
            elif hw in ("footway", "path", "pedestrian") or ft == "sidewalk":
                wtype = "footway"
            else:
                wtype = hw or "unknown"
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": {"id": el["id"], "type": wtype, **tags}
            })

    geojson = {"type": "FeatureCollection", "features": features}
    out_path = os.path.join(OUTPUT_DIR, f"{name}_osm.geojson")
    with open(out_path, "w") as f:
        json.dump(geojson, f)
    print(f"  Saved {len(features)} features -> {out_path}")
    return geojson


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for name, config in AREAS.items():
        try:
            fetch_area(name, config)
        except Exception as e:
            print(f"  ERROR fetching {name}: {e}")
    print("Done.")
