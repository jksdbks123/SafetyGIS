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
    ├─ GET  /api/osm/topology?node_id=&…    → intersection topology for clicked node (nearest-centroid fallback)
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

### OSM Tile Processing Pipeline (`main.py`)

Each z12 tile is processed by `_osm_tile_features()` using a 3-pass strategy:

```
Pass 1: collect all node coordinates (tagged + skel untagged) → nodes_dict
Pass 2: process ways → geometry, wtype, node_degree tracking, ways_lookup
Pass 3: parse relations (restrictions + future: routes) → restrictions[]

→ _compute_tile_topologies(nodes_dict, ways_lookup, node_degree, restrictions)
      ↳ per-node approach metadata (bearing, lanes, oneway, speed, name)
      ↳ configuration classification (ROUNDABOUT / CHANNELIZED_RT / DIVIDED / UNDIVIDED)
      ↳ conflict points (Garber & Hoel formula)
      ↳ roundabout compound grouping (ring nodes → one primary)
      ↳ embed lon/lat on each topology entry (needed for nearest-centroid fallback)

→ emit intersection_centroid Point features (skip roundabout secondaries)
→ write tile GeoJSON cache + relation cache (topologies dict)
```

Relation cache lives alongside the OSM tile cache:
- OSM tile: `data/osm_cache/{x}_{y}.json` — GeoJSON FeatureCollection
- Relation cache: `data/osm_cache/{x}_{y}_rel.json` — `{"topologies": {...}, "restrictions": [...]}`

**Key design choices:**
- No framework on the frontend — vanilla JS + MapLibre GL JS 4.7.1
- All crash properties from CCRS stored as-is in GeoJSON (full fidelity)
- OSM features include all raw tags (`...tags` spread) — visible in popups and GeoJSON downloads
- Dynamic OSM at z12 tile granularity; crash data cached per-county in background threads
- Basemap: OpenFreeMap (no API key); satellite fallback: Esri World Imagery
- Street View via pegman drag-drop only — not on arbitrary map click (prevents accidental opens)

## Frontend System (app.js)

### App Mode

`G_appMode = 'inspect' | 'analysis' | 'debug'` is the master switch:
- **Inspect mode:** Layers load lazily on viewport pan/zoom (`scheduleViewportLoad()`)
- **Analysis mode:** Viewport loading is suppressed; data is controlled by county chip downloads
- **Debug mode:** *(planned — Phase 2A)* Renders infra cell polygons, core markers, approach territories; no crash/rankings layers

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

### Topology Panel

Clicking any node on `intersections-pt-layer` or `signals-layer` opens the topology panel:
- Fires `GET /api/osm/topology?node_id={id}&lon={lon}&lat={lat}&tile_x={x}&tile_y={y}`
- Backend looks up the node in the relation cache; if not found, falls back to nearest primary
  intersection_centroid within 50 m using embedded `lon`/`lat` on each topology entry
- Panel renders: SVG approach diagram (bearing arrows), configuration badge, conflict points,
  per-approach table (name, highway class, lanes, bearing)
- Panel is draggable and closeable; `#topo-panel` in `index.html`
- Key functions: `_openTopologyPanel`, `_renderTopologyPanel`, `_renderTopologySVG`,
  `_renderApproachHighlight`, `closeTopoPanel`, `_ensureTopoPanelDraggable`

The `intersections-pt-layer` filter includes `intersection_centroid` (teal, 0.7 opacity) in
addition to `stop`, `give_way`, and `roundabout`. Roundabout secondary ring nodes are
suppressed — only the primary (most non-roundabout approaches) is rendered.

### Analysis Mode Components

- **County Data Manager** — chip grid, `_pollAnaCounty()` at 2 s interval during active downloads
  (accelerated from 4 s when `fetching_crash || fetching_osm`), `_countyChipClass()` for color state
- **Active Downloads Panel** (`_renderActiveDownloads`) — shown above the county grid when any
  county is downloading; per-county cards with OSM % bar, crash records count, real-time
  download speed (tiles/s for OSM, records/s for crash), and ETA estimate
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

### Roundabout compound grouping — `roundabout_primary` flag

Every node on a roundabout ring (`junction=roundabout`) that touches 2+ rankable ways would
naively become its own `intersection_centroid` — producing 4–10 dots per roundabout. To
collapse to one logical entity, `_compute_tile_topologies` groups all topology nodes sharing
the same roundabout way_id, elects the primary (node with most non-roundabout approaches),
merges leg-road approaches from secondaries into the primary, and sets `roundabout_primary`
on each secondary. Secondaries are then skipped in the `intersection_centroid` emission loop.

The `roundabout_primary` field is also the signal used by the nearest-centroid fallback to
skip secondary nodes during the spatial search.

### `_compute_tile_topologies` — pass ordering is critical

Topology computation must happen **before** intersection_centroid emission, because
`roundabout_primary` flags (set during topology) gate which nodes are emitted. If the
order is swapped, all ring nodes appear as individual centroids.

Current order in `_osm_tile_features()`:
1. Pass 1 (nodes) + Pass 2 (ways) + Pass 3 (relations)
2. `_compute_tile_topologies(...)` — sets `roundabout_primary` on secondaries
3. Embed `lon`/`lat` on each topology entry
4. Emit `intersection_centroid` features (skip any node with `roundabout_primary`)
5. Write GeoJSON tile cache
6. Write relation cache (`{x}_{y}_rel.json`)

### Rankings — `crash_coords` is a JSON-encoded string

`crash_coords` in the rankings GeoJSON is stored as a **JSON-encoded string** (not a nested array) to keep file size manageable — each facility caps at 200 crash coordinates.

Frontend must decode before use:
```javascript
const coords = JSON.parse(p.crash_coords || "[]");
```

Do not assume it is a native JS array from `e.features[0].properties`.

## Phase 2 Scope (active)

Phase 2 has diverged from the original PostGIS/drawing-tools plan. The current direction
is richer infrastructure entity modeling (see `CELL_MODEL.md`).

### Phase 2A — Composite Infra Entity ("Cell Model")

The road network is modeled as a tessellation of mutually exclusive **infra cells**.
Each cell = one ranked facility = a nucleus node/way + its approach ways + OSM relations.
See `CELL_MODEL.md` for full design spec, schema, and open questions.

**Phase 0** (frontend only — no backend changes):
- Add `'debug'` as a third app mode
- `_renderDebugCells()`: draw cell polygons (convex hull of nucleus + approach midpoints),
  core dots, compound-node connector lines, colored by configuration type
- Click a cell → raw entity JSON inspector panel
- Validates cell model visually before any backend schema changes

**Phase A** (backend — extend approach attributes):
- Add `speed_mph`, `surface`, `sidewalk`, `bicycle`, `turn:lanes` to each approach entry
  in `_compute_tile_topologies`
- Compute `approach_length_m` (haversine nucleus → terminus)
- Parse `turn:lanes` → `turn_lanes_list`

**Phase B** (backend — route relations):
- Add `type=route` (bus/bicycle/foot/road) to Pass 3 relation parsing

**Phase C** (rankings enrichment):
- Attach composite approach attributes to each ranked facility
- Use `sum(approach_aadt)` as entering-volume denominator for intersections
- Extend bin key with `max_approach_speed_bin`

**Phase D** (new API endpoint):
- `GET /api/osm/facility/{fid}` — return full composite entity JSON on demand

**Phase E** (frontend):
- Replace topology panel `/api/osm/topology` call with `/api/osm/facility/{fid}`
- Render approach ways as colored polylines; per-approach speed/lanes/AADT table

### Original Phase 2 Items (deferred to Phase 2B)

- PostGIS database (SQLAlchemy + GeoAlchemy2)
- `@mapbox/mapbox-gl-draw` for drawing tools in the frontend
- Attribute forms + project persistence via FastAPI
- Status-driven dynamic layer styling

## Future Development Notes

- `CELL_MODEL.md` — the active design document for the composite infra entity model.
  Read before touching `_compute_tile_topologies`, `build_safety_rankings.py`, or the
  topology panel. This document lives in the repo and is the source of truth for Phase 2A.
- `data/enrichment/` is pre-created in the Dockerfile but currently unused. Reserved for AADT data (Caltrans PeMS) and SPF calibration files needed for Phase 3 crash-rate normalization and Empirical Bayes PSI scoring.
- `METHODOLOGY.md` documents the full rankings algorithm (data sources, spatial join, EPDO, bin classification, grade-separation gap, recommended improvements). Reference it before touching `build_safety_rankings.py`.
- The Raspberry Pi 5 at `raspberrypi.local` is a live deployment target. Always verify Docker images build and run correctly on arm64 before pushing.
- Cloudflare Tunnel is used to expose the Pi deployment publicly. Configuration lives on the Pi, not in this repo.
