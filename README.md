# SafetyGIS

SafetyGIS is a FastAPI and vanilla MapLibre GIS application for reviewing
California transportation safety data. It combines CCRS crash records, OSM road
infrastructure, optional Mapillary/Street View imagery, and an EPDO rankings
workflow.

## Current Priority

The project is paused on broad feature expansion. The active work is the
intersection and roadway facility model.

The existing app remains useful as a baseline:

- Inspect mode: viewport-based crash and OSM exploration.
- Analysis mode: county cache management and EPDO rankings.
- Debug mode: Sacramento OSM sandbox for consolidated intersection modeling.

See `PROJECT_STATE.md` for the accepted current state and deferred work.

## Stack

- Backend: Python, FastAPI, Uvicorn.
- Frontend: plain HTML, vanilla JavaScript, MapLibre GL JS, Chart.js.
- Data: local GeoJSON/JSON caches under `data/`.
- Optional services: Mapillary and Google Street View.

## Run Locally

```powershell
.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

Open `http://localhost:8000`.

For macOS/Linux:

```bash
source .venv/bin/activate
uvicorn main:app --reload
```

## Environment

Copy `.env.example` to `.env`.

```text
MAPILLARY_TOKEN=
GOOGLE_MAPS_KEY=
```

Both keys are optional. The app runs without them, but Mapillary and Street View
features will be disabled.

## Main Structure

```text
main.py                    FastAPI app assembly
app/config.py              paths, env, county bounds, modeling constants
app/osm/                   OSM tile processing, topology, consolidation
app/crash/                 CCRS fetch/cache/stats APIs
app/rankings/              safety ranking APIs
app/debug/                 modeling sandbox APIs
static/index.html          single-page app shell
static/app.js              main map UI
static/analysis_panel.js   Analysis mode UI
static/debug_sandbox.js    debug-mode modeling UI
scripts/                   data fetch and ranking scripts
data/                      runtime caches; not committed
```

## Data Scripts

```bash
python scripts/fetch_crash_data.py
python scripts/fetch_osm.py
python scripts/build_safety_rankings.py
```

AADT scripts are preserved for later use, but AADT expansion is deferred until
the facility model is stable.

## Docker

```bash
docker compose up -d --build
```

The `data/` directory is mounted as a volume and is treated as disposable runtime
cache from the repository perspective.
