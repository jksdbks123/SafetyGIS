# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language

**Three-rule policy:**
1. **Internal reasoning / chain-of-thought:** English (token-efficient for the model)
2. **Responses to the user:** Chinese (中文) — regardless of whether the user writes in English or Chinese; never switch to Korean, Japanese, or any other language
3. **All product output:** English only — code, inline comments, docstrings, API responses, documentation files (README, METHODOLOGY, TUTORIAL, DEVLOG, etc.), UI labels, log messages

The boundary is "talking to the user" vs "writing into the product." A response explaining a code change → Chinese. The code change itself → English.

## Project Overview

**GIS-Track** — A web-based GIS application for tracking transportation safety infrastructure and crash data across all 58 California counties.

- **Phase 1:** Complete — read-only god-view with Inspect Mode (lazy viewport loading) and Analysis Mode (county data manager + safety rankings engine)
- **Phase 2:** Next — PostGIS database, drawing tools, project persistence
- **Phase 3:** Planned — Tailwind UI polish, AADT integration, AI-powered queries

See `METHODOLOGY.md` for the full safety rankings algorithm documentation.  
See `DEVLOG.md` for session-by-session development history.  
See `Plan.md` for the full phase roadmap.

## Running the App

**Local (macOS/Linux):**
```bash
source .venv/bin/activate
uvicorn main:app --reload
# Open http://localhost:8000
```

**Local (Windows — Command Prompt):**
```cmd
.venv\Scripts\activate
uvicorn main:app --reload
```

**Local (Windows — PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

**Makefile shortcut (macOS/Linux only — requires GNU Make):**
```bash
make dev        # starts uvicorn on PORT=8000 (default)
make dev PORT=9000
```

Note: `make` is not available on Windows by default. Use `uvicorn` directly, or install
GNU Make via Chocolatey (`choco install make`) or run inside Git Bash / WSL.

**Docker (macOS / Linux / Windows with Docker Desktop):**
```bash
docker compose up -d --build
# App at http://localhost:8000
# data/ is mounted as a volume — cache survives container rebuilds
```

Docker image is arm64-compatible and has been tested on Raspberry Pi 5. The healthcheck polls `GET /api/mapillary/token` every 30 s.

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|----------|----------|---------|
| `MAPILLARY_TOKEN` | Optional | Street-level photo tiles and image metadata; layers are disabled if absent |
| `GOOGLE_MAPS_KEY` | Optional | Google Street View pegman; feature disabled if absent |

Both tokens are proxied server-side via `main.py` — neither is ever sent to the frontend JS. Use `load_dotenv(override=True)` (already in `main.py`) so `.env` values always win over inherited OS environment variables.

## Data Scripts

```bash
# Pull real CHP CCRS crash data for specified counties (writes to data/crash_cache/)
python scripts/fetch_crash_data.py

# Re-fetch OSM infrastructure from Overpass API (writes to data/osm_cache/)
python scripts/fetch_osm.py

# Assign Caltrans AADT to every OSM road segment (run once after fetch_osm / new AADT data)
# Output: data/CaltransAADT/osm_aadt_lookup.json  (required by build_safety_rankings.py)
python scripts/assign_aadt_to_osm.py
python scripts/assign_aadt_to_osm.py --mainline-radius 2000 --ramp-radius 500

# Compute safety rankings (EPDO spatial join, peer-group bins, outputs to data/rankings/)
# Requires osm_aadt_lookup.json to populate aadt field on facilities
python scripts/build_safety_rankings.py --counties sacramento,humboldt
python scripts/build_safety_rankings.py --counties all --min-osm-pct 80
python scripts/build_safety_rankings.py --weights 9.5,3.5,1.0,1.0   # fatal,severe_injury,other_injury,pdo (FHWA toolkit defaults)
```

`fetch_osm.py` uses a 3-mirror fallback chain:  
`overpass-api.de` → `overpass.kumi.systems` → `overpass.openstreetmap.ru`

Crash data is also fetched on-demand by the backend: `GET /api/crashes/dynamic?bbox=…` starts a background thread per county and returns immediately with a `fetching` list; the frontend polls every 25 s until counties are cached in `data/crash_cache/`.

Key constants in `build_safety_rankings.py`:
- `YEAR_WINDOW = 5` — crash analysis window
- `INTERSECTION_R = 50.0` m — crash-to-node match radius
- `SEGMENT_R = 30.0` m — crash-to-way match radius
- `EPDO_DEFAULTS = (9.5, 3.5, 1.0, 1.0)` — fatal / severe_injury / other_injury / PDO weights (FHWA toolkit, normalized PDO=1)

## Architecture

```
Browser (MapLibre GL JS + static/app.js + static/index.html)
    │
    ├─ GET  /api/osm/dynamic?bbox=          → Overpass tile fetch, z12 cached in data/osm_cache/
    ├─ GET  /api/crashes/dynamic?bbox=      → CCRS per-county, cached in data/crash_cache/
    ├─ GET  /api/counties                   → {county_name: county_code} for all 58 CA counties
    ├─ GET  /api/crashes/stats              → aggregated crash counts by scope + group_by field
    ├─ GET  /api/data/county_status         → per-county download readiness (crash_ready, osm_pct, …)
    ├─ POST /api/rankings/compute           → trigger build_safety_rankings.py subprocess
    ├─ GET  /api/rankings/status            → poll rankings computation progress
    ├─ GET  /api/rankings/bins              → list available bin keys with facility counts
    ├─ GET  /api/rankings/bin/{key}         → worst/best 10 facilities for a bin
    ├─ GET  /api/mapillary/images           → proxied + cached to data/mapillary_cache/
    ├─ GET  /api/mapillary/token            → returns token availability only (not the token itself)
    ├─ POST /api/ai/query                   → placeholder; wire real LLM here (Phase 3)
    ├─ GET  /favicon.ico                    → returns 204 (suppresses terminal 404 noise)
    └─ GET  /static/*                       → HTML/JS/CSS

FastAPI backend (main.py)
    └─ External: Overpass API, CCRS/data.ca.gov, Mapillary API, Google Maps JS API
```

**Key design choices:**
- No framework on the frontend — vanilla JS + MapLibre GL JS 4.7.1
- All crash properties from CCRS stored as-is in GeoJSON (full fidelity)
- OSM features include all raw tags (`...tags` spread) — visible in popups and GeoJSON downloads
- Dynamic OSM at z12 tile granularity; crash data cached per-county in background threads
- Basemap: OpenFreeMap (no API key); satellite fallback: Esri World Imagery
- Street View via pegman drag-drop only — not on arbitrary map click (prevents accidental opens)

## Frontend System (app.js)

### App Mode

`G_appMode = 'inspect' | 'analysis'` is the master switch:
- **Inspect mode:** Layers load lazily on viewport pan/zoom (`scheduleViewportLoad()`)
- **Analysis mode:** Viewport loading is suppressed; data is controlled by county chip downloads

`setAppMode(mode)` handles the transition. When switching back to Inspect, it must cancel `_anaComputePollTimer` (rankings polling timer) — this is already implemented but must be preserved in future refactors.

### In-Memory Feature Maps

MapLibre truncates GeoJSON properties when converting to VectorTile internally. Always store full feature objects in JS `Map` instances and look them up by ID in event handlers:

| Map | Key | Purpose |
|-----|-----|---------|
| `CRASH_FEATURE_MAP` | crash ID | Full crash properties for popup/export |
| `OSM_FEATURE_MAP` | osm_id | Full OSM tags for popup |
| `G_rankWorstMap` | facility_id | Full rankings properties for hover popup + dashboard |
| `G_rankBestMap` | facility_id | Full rankings properties for hover popup + dashboard |

Never rely on `e.features[0].properties` alone for data-rich popups.

### Analysis Mode Components

- **County Data Manager** — chip grid, `_pollAnaCounty()` at 4 s interval, `_countyChipClass()` for color state
- **Rankings compute** — `POST /api/rankings/compute`, `_anaComputePollTimer` polls status at 1.5 s
- **Bin Browser** — chip grid grouped by intersection/segment, `G_rankWorstMap` / `G_rankBestMap` populated on chip click
- **Crash Dashboard** (`openRankDash`) — EPDO score strip, distribution bars, crash point overlay on map

### Layer Lifecycle

On basemap switch, `map.setStyle()` replaces the entire style. A permanent `map.on('style.load', onStyleLoaded)` handler calls `rebuildLayers()` each time. Never use `map.once('style.load', …)` inside `switchBasemap` — it stacks multiple handlers when clicked quickly.

## Lessons Learned

### Overpass API — `out geom;` vs. `out body; >; out skel qt;`

The choice of output format depends on whether you need **node-ID connectivity** within ways:

**`out geom;`** — embeds coordinates directly in each element. Simple single-pass parsing.
Use when you only need geometry (no topology). Way coordinate extraction:
```python
coords = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
```

**`out body; >; out skel qt;`** — returns way node-ID lists + resolves all referenced node
coordinates separately. Recovers topological connectivity (which ways share which nodes).
**Requires two-pass parsing** — the response intermingles tagged nodes, ways (with `nodes`
arrays but no coordinates), and untagged skel nodes. A single-pass parser will have an empty
`nodes_dict` when it hits way elements, silently dropping every line feature.

Two-pass pattern used in `_osm_tile_features()`:
```python
# Pass 1: collect ALL node coordinates (tagged + skel)
nodes_dict = {}
tagged_nodes = []
for el in elements:
    if el["type"] == "node":
        nodes_dict[el["id"]] = (el["lon"], el["lat"])
        if el.get("tags"):
            tagged_nodes.append(el)

# Pass 2: process ways, reconstruct geometry from node IDs, track degree
node_degree = {}
for el in elements:
    if el["type"] == "way":
        coords = [(nodes_dict[n][0], nodes_dict[n][1])
                  for n in el.get("nodes", []) if n in nodes_dict]
        # count node references to identify intersection centroids
        for nid in el.get("nodes", []):
            node_degree[nid] = node_degree.get(nid, 0) + 1
```

The current `_osm_tile_features()` uses `out body; >; out skel qt;` to derive intersection
centroids (nodes shared by 2+ rankable road ways) and stores them as `type=intersection_centroid`
features in the tile cache.

### Overpass API — 429 rate limit handling

Overpass returns HTTP 429 when overloaded. Current pattern: sleep 8 s, retry once. If retry also fails, **write an empty cache file** for that tile. Without the empty file, `osm_pct` stalls at a partial percentage and county chips never reach the `ready` state.

### MapLibre — popup properties may be truncated

MapLibre GL JS converts GeoJSON to VectorTile format internally. Only properties referenced in `paint`/`filter` expressions are reliably preserved in `e.features[0].properties`.

**Always store full features in a JS `Map` keyed by ID** and look them up in click/hover handlers instead of reading from `e.features`. (See In-Memory Feature Maps section above.)

### OSM sidewalk/footway zoom levels

`highway=footway` and `footway=sidewalk` ways are offset from the road centerline (mapped as actual on-ground positions). At zoom < 14 they appear misaligned against the basemap.

Always set `minzoom: 14` on footway layers. Road network layers can start at `minzoom: 11`.

### CCRS — county code is a string, not an integer

`data.ca.gov` changed the `County Code` column type from integer to text. The CKAN filter must use a string:
```python
# CORRECT
{"County Code": str(county_code)}

# WRONG — returns HTTP 409 (operator does not exist: text = integer)
{"County Code": county_code}
```

### CCRS — take the first matching resource only

The CCRS package exposes multiple resources with the same display name per year (e.g., three `Crashes_2024` entries). Only the **first** contains data; the others are empty duplicates.
```python
# CORRECT — take first match, never overwrite
if year not in crashes:
    crashes[year] = r["id"]

# WRONG — last iteration wins, ends up with an empty resource
crashes[year] = r["id"]
```
Apply the same pattern to `parties` and `victims` resource discovery.

### `load_dotenv(override=True)` is required

Plain `load_dotenv()` does NOT override inherited OS environment variables. If the shell already has `MAPILLARY_TOKEN` or `GOOGLE_MAPS_KEY` set (e.g., from a previous export), the `.env` file is silently ignored.

Always call `load_dotenv(override=True)` in `main.py`.

### Rankings — `crash_coords` is a JSON-encoded string

`crash_coords` in the rankings GeoJSON is stored as a **JSON-encoded string** (not a nested array) to keep file size manageable — each facility caps at 200 crash coordinates.

Frontend must decode before use:
```javascript
const coords = JSON.parse(p.crash_coords || "[]");
```

Do not assume it is a native JS array from `e.features[0].properties`.

## Phase 2 Scope (next)

- PostGIS database (SQLAlchemy + GeoAlchemy2)
- `@mapbox/mapbox-gl-draw` for drawing tools in the frontend
- Attribute forms + project persistence via FastAPI
- Status-driven dynamic layer styling

## Future Development Notes

- `data/enrichment/` is pre-created in the Dockerfile but currently unused. Reserved for AADT data (Caltrans PeMS) and SPF calibration files needed for Phase 3 crash-rate normalization and Empirical Bayes PSI scoring.
- `METHODOLOGY.md` documents the full rankings algorithm (data sources, spatial join, EPDO, bin classification, grade-separation gap, recommended improvements). Reference it before touching `build_safety_rankings.py`.
- The Raspberry Pi 5 at `raspberrypi.local` is a live deployment target. Always verify Docker images build and run correctly on arm64 before pushing.
- Cloudflare Tunnel is used to expose the Pi deployment publicly. Configuration lives on the Pi, not in this repo.
