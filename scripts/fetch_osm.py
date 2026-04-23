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
  relation["type"="restriction"]({bbox});
);
out body;
>;
out skel qt;
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

    elements = raw.get("elements", [])

    # Pass 1: collect node coordinates (tagged infra nodes + untagged skel nodes)
    nodes_dict = {}
    tagged_nodes = []
    for el in elements:
        if el["type"] != "node":
            continue
        nodes_dict[el["id"]] = (el["lon"], el["lat"])
        if el.get("tags"):
            tagged_nodes.append(el)

    # Pass 2: process ways using node-ID lists
    features = []
    for el in elements:
        if el["type"] == "way":
            tags = el.get("tags", {})
            nid_list = el.get("nodes", [])
            coords = [(nodes_dict[n][0], nodes_dict[n][1]) for n in nid_list if n in nodes_dict]
            if len(coords) < 2:
                continue
            hw = tags.get("highway", "")
            cy = tags.get("cycleway", "")
            ft = tags.get("footway", "")
            jn = tags.get("junction", "")
            if jn == "roundabout":
                wtype = "roundabout"
            elif hw == "cycleway" or cy:
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

    for el in tagged_nodes:
        tags = el.get("tags", {})
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
        lon, lat = nodes_dict[el["id"]]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"id": el["id"], "type": ftype, **tags}
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
