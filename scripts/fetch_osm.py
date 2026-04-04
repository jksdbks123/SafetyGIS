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

# Query for: traffic signals, crossings, stop signs, bike lanes, bus stops
OVERPASS_QUERY = """
[out:json][timeout:60];
(
  node["highway"="traffic_signals"]({bbox});
  node["highway"="crossing"]({bbox});
  node["highway"="stop"]({bbox});
  node["amenity"="bus_station"]({bbox});
  node["highway"="bus_stop"]({bbox});
  way["cycleway"]({bbox});
  way["highway"="cycleway"]({bbox});
);
out body;
>;
out skel qt;
"""

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def fetch_area(name: str, config: dict) -> dict:
    print(f"Fetching OSM data for {config['label']}...")
    query = OVERPASS_QUERY.format(bbox=config["bbox"])
    raw = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(url, data={"data": query}, timeout=90)
            resp.raise_for_status()
            raw = resp.json()
            break
        except Exception as e:
            print(f"  Mirror {url} failed: {e}, trying next...")
    if raw is None:
        raise RuntimeError("All Overpass mirrors failed")

    features = []
    nodes = {}

    for el in raw.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])
            tags = el.get("tags", {})
            if tags:
                feature_type = tags.get("highway") or tags.get("amenity") or "unknown"
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]},
                    "properties": {"id": el["id"], "type": feature_type, **tags}
                })
        elif el["type"] == "way":
            tags = el.get("tags", {})
            coords = [nodes[n] for n in el.get("nodes", []) if n in nodes]
            if len(coords) >= 2:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"id": el["id"], "type": "cycleway", **tags}
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
