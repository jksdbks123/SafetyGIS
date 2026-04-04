# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**GIS-Track** — A web-based GIS application for tracking transportation safety infrastructure and crash data across all of California. Phase 1 is complete; Phase 2 (PostGIS + drawing tools) is next.

## Running the App

```bash
source .venv/bin/activate
uvicorn main:app --reload
# Open http://localhost:8000
```

## Data Scripts

```bash
python scripts/fetch_crash_data.py  # Pull real CHP CCRS crash data for specified counties
python scripts/fetch_osm.py         # Re-fetch OSM infrastructure from Overpass API
```

The `fetch_osm.py` script has a 3-mirror fallback chain (`overpass-api.de` → `overpass.kumi.systems` → `overpass.openstreetmap.ru`).

Crash data is also fetched on-demand via the backend: `GET /api/crashes/dynamic?bbox=…` starts a background thread per county and returns immediately with a `fetching` list; the frontend polls every 25s until all counties are cached in `data/crash_cache/`.

## Environment

Copy `.env.example` to `.env` and fill in `MAPILLARY_TOKEN` (free Mapillary developer account). The backend proxies all Mapillary API calls — the token is never sent to the frontend.

## Architecture

```
Browser (MapLibre GL JS + app.js)
    │
    ├─ GET /api/osm/dynamic?bbox=   → Overpass tile fetch, cached at z12 in data/osm_cache/
    ├─ GET /api/osm/{area}          → serves data/{area}_osm.geojson (pre-seeding SAC/HUM)
    ├─ GET /api/crashes/dynamic?bbox= → CCRS per-county, cached in data/crash_cache/
    ├─ GET /api/mapillary/images    → proxied + cached to data/mapillary_cache/
    ├─ GET /api/mapillary/token     → returns token availability only
    ├─ POST /api/ai/query           → placeholder; wire real LLM here
    └─ GET /static/*                → HTML/JS/CSS

FastAPI backend (main.py)
    │
    └─ External: Overpass API (OSM), CCRS/data.ca.gov (crashes), Mapillary API
```

**Key design choices:**
- No framework on the frontend — vanilla JS + MapLibre GL JS 4.7.1
- All crash properties from CCRS are stored as-is in GeoJSON properties (full fidelity)
- OSM features include all raw tags (`...tags` spread) — visible in popups and GeoJSON downloads
- Dynamic OSM at z12 tile granularity; crash data cached per-county in background threads
- Basemap: OpenFreeMap (no API key); satellite fallback: Esri World Imagery
- Street View opened via pegman drag-drop or click-to-activate, not on arbitrary map click

## Frontend Layer System (app.js)

All map layers are defined in `static/app.js`. The layer toggle buttons in the UI call `toggleLayer()` by group name. On basemap switch, `switchBasemap()` rebuilds all layers since MapLibre replaces the entire style.

Mapillary sign layers are lazy-loaded on first toggle ON. Google Street View requires dragging the pegman (`#pegman-btn`) or clicking it to enter placement mode — this prevents accidental SV opens on map clicks.

## Lessons Learned

### Overpass API — always use `out geom;`

**Never** use `out body; >; out skel qt;` for queries that include `way` elements.

That format returns way elements first (with node ID references but no coordinates), then resolves the referenced nodes afterward. A single-pass parser will have an empty `nodes` dict when it hits the way elements, silently dropping every line feature.

**Always use `out geom;`** — it embeds coordinates directly in each element:
```
# CORRECT
out geom;

# WRONG — breaks single-pass parsing, drops all ways
out body;
>;
out skel qt;
```

With `out geom;`, way coordinate extraction is:
```python
coords = [(g["lon"], g["lat"]) for g in el.get("geometry", []) if g]
```
No `nodes` dict needed at all.

### MapLibre — popup properties may be truncated

MapLibre GL JS internally converts GeoJSON to VectorTile format. Only properties referenced in `paint`/`filter` expressions are reliably preserved in `e.features[0].properties`.

**Always store full features in a JS `Map` keyed by ID** (`CRASH_FEATURE_MAP`, `OSM_FEATURE_MAP`) and look them up by ID in click handlers instead of reading from `e.features`.

### OSM sidewalk/footway zoom levels

`highway=footway` and `footway=sidewalk` ways are offset from the road centerline in OSM (mapped as actual on-ground positions). At zoom < 14 they appear misaligned against the basemap's road rendering.

Always set `minzoom: 14` on footway layers. Road network layers can start at `minzoom: 11`.

## Phase 2 Scope (next)

- PostGIS database (SQLAlchemy + GeoAlchemy2)
- `@mapbox/mapbox-gl-draw` for drawing tools in the frontend
- Attribute forms + project persistence
- Status-driven dynamic layer styling
