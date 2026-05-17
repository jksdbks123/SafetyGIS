import os
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR   = os.path.dirname(os.path.dirname(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
STATIC_DIR = os.path.join(BASE_DIR, "static")

MLY_CACHE          = os.path.join(DATA_DIR, "mapillary_cache")
OSM_CACHE          = os.path.join(DATA_DIR, "osm_cache")
OSM_RELATION_CACHE = os.path.join(DATA_DIR, "osm_relation_cache")
CRASH_CACHE        = os.path.join(DATA_DIR, "crash_cache")
PARTY_CACHE        = os.path.join(DATA_DIR, "party_cache")
DEBUG_CACHE        = os.path.join(DATA_DIR, "debug")
RANKINGS_DIR       = os.environ.get("RANKINGS_DIR", os.path.join(DATA_DIR, "rankings"))
AADT_FILE          = os.path.join(DATA_DIR, "CaltransAADT", "aadt_geocoded.geojson")

for _d in (MLY_CACHE, OSM_CACHE, OSM_RELATION_CACHE, CRASH_CACHE,
           PARTY_CACHE, DEBUG_CACHE, RANKINGS_DIR):
    os.makedirs(_d, exist_ok=True)

MAPILLARY_TOKEN = os.getenv("MAPILLARY_TOKEN", "")
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY", "")
MAPILLARY_API   = "https://graph.mapillary.com"
CACHE_ZOOM      = 14
OSM_CACHE_ZOOM  = 12

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
  node["highway"="give_way"]({bbox});
  node["highway"="mini_roundabout"]({bbox});
  way["junction"="roundabout"]({bbox});
  node["amenity"="bus_station"]({bbox});
  node["highway"="bus_stop"]({bbox});
  node["traffic_calming"]({bbox});
  node["highway"="street_lamp"]({bbox});
  way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|residential|unclassified|living_street)$"]({bbox});
  way["highway"~"^(service|track|track_grade1|track_grade2)$"]({bbox});
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

_RANKABLE_HIGHWAY_FOR_CENTROIDS = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "residential", "unclassified", "living_street",
    "roundabout",
}

# Vehicular highway types broader than the centroid rankable set.
# These appear as approaches (if incident to a centroid) and in the
# "all roads" debug reference layer, but do NOT create new centroids.
_VEHICULAR_HIGHWAY_EXTRAS = {
    "service", "track", "track_grade1", "track_grade2", "pedestrian",
}

# Class-aware path-distance radii for proximity-clustering of intersection
# candidates. Larger highway classes need wider merge radii because their
# at-grade intersection footprints are physically larger (turn lanes, gore
# points, channelized RTs sit farther from the nucleus). Path distance is
# walked along the shared way, not straight-line. See app/osm/cluster.py.
INTERSECTION_CLUSTER_RADII: dict = {
    "motorway":       200.0,
    "motorway_link":  150.0,
    "trunk":          150.0,
    "trunk_link":     120.0,
    "primary":        120.0,
    "primary_link":   100.0,
    "secondary":       90.0,
    "secondary_link":  80.0,
    "tertiary":        70.0,
    "tertiary_link":   60.0,
    "residential":     60.0,
    "unclassified":    60.0,
    "living_street":   40.0,
    "roundabout":     150.0,
}
INTERSECTION_CLUSTER_DEFAULT_RADIUS_M: float = 80.0

# Highway-class default speed (mph) and lanes used when OSM tags are missing.
# Flagged with `*_estimated: True` on the approach so downstream consumers can
# distinguish observed values from fallbacks.
HIGHWAY_DEFAULT_SPEED_MPH: dict = {
    "motorway":      65, "motorway_link":  45,
    "trunk":         55, "trunk_link":     40,
    "primary":       45, "primary_link":   35,
    "secondary":     35, "secondary_link": 30,
    "tertiary":      30, "tertiary_link":  25,
    "residential":   25, "unclassified":   25,
    "living_street": 15, "roundabout":     20,
    # Non-rankable vehicular extras
    "service":       15, "track":          15,
    "track_grade1":  25, "track_grade2":   15,
    "pedestrian":     5,
}
HIGHWAY_DEFAULT_LANES: dict = {
    "motorway":      3, "motorway_link":  1,
    "trunk":         2, "trunk_link":     1,
    "primary":       2, "primary_link":   1,
    "secondary":     2, "secondary_link": 1,
    "tertiary":      1, "tertiary_link":  1,
    "residential":   1, "unclassified":   1,
    "living_street": 1, "roundabout":     1,
    # Non-rankable vehicular extras
    "service":       1, "track":          1,
    "track_grade1":  1, "track_grade2":   1,
    "pedestrian":    0,
}
APPROACH_LENGTH_CAP_M: float = 120.0

# Intersection classifier thresholds (see docs/intersection_classifier_design.md)
INTERSECTION_THRESHOLD: float = 0.20         # score >= this → is_intersection
INTERSECTION_STRONG_THRESHOLD: float = 0.40  # score >= this → high-confidence
INTERSECTION_REJECT_THRESHOLD: float = 0.10  # score <= this → confident non-intersection
FILTER_CENTROIDS_BY_CLASSIFIER: bool = False # True → suppress centroids below threshold

CCRS_BASE_URL     = "https://data.ca.gov/api/3/action"
CCRS_PACKAGE_ID   = "ccrs"
CCRS_PAGE_SIZE    = 5000
CCRS_TARGET_YEARS = {2019, 2020, 2021, 2022, 2023, 2024}
CCRS_MAX_PAGES    = 40

CA_COUNTIES: dict[str, tuple[int, tuple[float, float, float, float]]] = {
    "alameda":         (1,  (37.45, -122.38, 37.91, -121.47)),
    "alpine":          (2,  (38.50, -120.10, 38.90, -119.33)),
    "amador":          (3,  (38.19, -120.74, 38.60, -120.33)),
    "butte":           (4,  (39.50, -122.03, 40.15, -121.03)),
    "calaveras":       (5,  (37.95, -120.76, 38.52, -119.89)),
    "colusa":          (6,  (38.72, -122.57, 39.32, -121.96)),
    "contra_costa":    (7,  (37.71, -122.44, 38.07, -121.55)),
    "del_norte":       (8,  (41.48, -124.22, 41.99, -123.54)),
    "el_dorado":       (9,  (38.53, -120.74, 39.07, -119.89)),
    "fresno":          (10, (35.79, -121.16, 37.58, -118.36)),
    "glenn":           (11, (39.32, -123.01, 39.80, -121.96)),
    "humboldt":        (12, (40.00, -124.42, 41.47, -123.41)),
    "imperial":        (13, (32.50, -115.48, 33.43, -114.43)),
    "inyo":            (14, (35.79, -118.37, 38.00, -117.03)),
    "kern":            (15, (34.80, -120.07, 36.00, -117.63)),
    "kings":           (16, (35.79, -120.36, 36.74, -119.39)),
    "lake":            (17, (38.63, -123.11, 39.40, -122.15)),
    "lassen":          (18, (40.00, -121.34, 41.19, -119.88)),
    "los_angeles":     (19, (33.70, -118.95, 34.82, -117.65)),
    "madera":          (20, (36.74, -119.90, 37.95, -118.68)),
    "marin":           (21, (37.83, -123.03, 38.26, -122.43)),
    "mariposa":        (22, (37.10, -120.06, 37.95, -119.27)),
    "mendocino":       (23, (38.77, -124.02, 40.00, -122.73)),
    "merced":          (24, (36.89, -121.27, 37.63, -119.90)),
    "modoc":           (25, (41.18, -121.34, 42.00, -119.99)),
    "mono":            (26, (37.45, -119.34, 38.50, -117.83)),
    "monterey":        (27, (35.79, -121.95, 36.92, -120.21)),
    "napa":            (28, (38.17, -122.64, 38.72, -122.05)),
    "nevada":          (29, (39.07, -121.28, 39.50, -120.00)),
    "orange":          (30, (33.40, -118.11, 33.94, -117.41)),
    "placer":          (31, (38.72, -121.47, 39.32, -120.00)),
    "plumas":          (32, (39.73, -121.28, 40.42, -120.07)),
    "riverside":       (33, (33.43, -117.71, 34.08, -114.43)),
    "sacramento":      (34, (38.22, -121.87, 38.74, -121.03)),
    "san_benito":      (35, (36.35, -121.57, 36.92, -120.89)),
    "san_bernardino":  (36, (33.60, -117.66, 35.79, -114.13)),
    "san_diego":       (37, (32.53, -117.61, 33.51, -116.08)),
    "san_francisco":   (38, (37.70, -122.53, 37.83, -122.35)),
    "san_joaquin":     (39, (37.48, -121.59, 38.20, -120.92)),
    "san_luis_obispo": (40, (34.80, -121.34, 35.80, -119.44)),
    "san_mateo":       (41, (37.11, -122.53, 37.71, -122.11)),
    "santa_barbara":   (42, (34.35, -120.63, 35.10, -119.52)),
    "santa_clara":     (43, (36.89, -122.21, 37.48, -121.21)),
    "santa_cruz":      (44, (36.89, -122.32, 37.29, -121.58)),
    "shasta":          (45, (40.42, -122.88, 41.19, -121.33)),
    "sierra":          (46, (39.50, -120.51, 39.84, -120.00)),
    "siskiyou":        (47, (41.19, -123.01, 42.00, -121.33)),
    "solano":          (48, (38.02, -122.44, 38.54, -121.55)),
    "sonoma":          (49, (38.15, -123.54, 38.87, -122.34)),
    "stanislaus":      (50, (37.22, -121.27, 37.93, -120.00)),
    "sutter":          (51, (39.00, -121.90, 39.55, -121.46)),
    "tehama":          (52, (39.80, -123.01, 40.42, -121.33)),
    "trinity":         (53, (40.00, -123.55, 41.18, -122.54)),
    "tulare":          (54, (35.79, -119.48, 36.74, -117.93)),
    "tuolumne":        (55, (37.69, -120.48, 38.52, -119.20)),
    "ventura":         (56, (34.04, -119.34, 34.80, -118.63)),
    "yolo":            (57, (38.22, -122.40, 38.72, -121.46)),
    "yuba":            (58, (39.00, -121.47, 39.55, -120.99)),
}

_CC_TO_NAME: dict[int, str] = {code: name for name, (code, _) in CA_COUNTIES.items()}

SAC_TILES = [
    (x, y)
    for x in range(661, 671)
    for y in range(1569, 1577)
]

# Cluster filter: logical group → actual wtype strings in tile/rel cache
CLUSTER_WAY_TYPE_GROUPS: dict = {
    "motorway":      {"motorway", "motorway_link"},
    "trunk":         {"trunk", "trunk_link"},
    "primary":       {"primary", "primary_link"},
    "secondary":     {"secondary", "secondary_link"},
    "tertiary":      {"tertiary", "tertiary_link"},
    "residential":   {"residential"},
    "unclassified":  {"unclassified"},
    "living_street": {"living_street"},
    "roundabout":    {"roundabout"},
    # below: in tile LineString cache but topology NOT pre-computed (re-download after query update)
    "footway":       {"footway"},
    "cycleway":      {"cycleway"},
    "path":          {"path"},
    "pedestrian":    {"pedestrian"},
    # below: now fetched via expanded Overpass query
    "service":       {"service"},
    "track":         {"track", "track_grade1", "track_grade2"},
}

_DEFAULT_CLUSTER_WAY_GROUPS: frozenset = frozenset({
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "residential", "unclassified", "living_street", "roundabout",
})
