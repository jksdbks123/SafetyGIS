# GIS-Track — Project Plan

**Core Architecture:**
- **Frontend:** Plain HTML + Vanilla JavaScript + MapLibre GL JS
- **Backend:** Python 3 + FastAPI
- **Database:** PostgreSQL + PostGIS (Phase 2)
- **AI Coding Partner:** Claude Code

---

## Vibe Coding Workflow

1. **Set system context first.** Start each session by stating the stack: "Python backend, plain JS + MapLibre frontend, no React."
2. **Break features into the smallest possible tasks.** Instead of "build a save-drawing feature," say: "Step 1 — add a Save button to the HTML. Step 2 — write a JS function to read the current drawn coordinates. Step 3 — write a FastAPI endpoint to receive them."
3. **Data before database (Dummy Data first).** Before wiring any database, have the AI generate static GeoJSON fixtures so the frontend renders correctly. Connect real data only after the display layer is confirmed working.
4. **Feed errors directly to the AI.** When the browser console or Python throws an error, paste the full traceback and ask: "Why does this happen? How do I fix it?"

---

## Phase 1 — Urban Digital Base (Background & Data Layers) ✅ Complete

**Goal:** A read-only "god view" that aggregates multiple data sources onto a single interactive map.

- **Task 1.1 — Base map & server skeleton**
  FastAPI serves `index.html`; MapLibre GL JS renders OpenFreeMap vector tiles (free, no API key).
- **Task 1.2 — OSM infrastructure layers**
  Python script calls Overpass API; result converted to GeoJSON and rendered as independent layers (signals, crossings, bus stops, bike lanes, footways, street lamps, traffic calming).
- **Task 1.3 — Crash data layer**
  Real CHP CCRS data via data.ca.gov CKAN API; all 58 counties supported; per-county lazy loading with local GeoJSON cache.
- **Task 1.4 — Mapillary integration**
  Street-level imagery coverage as vector tiles; sign detection layers; side-panel popup with thumbnail + metadata.

---

## Phase 2 — Core Interaction & Tracking (Next)

**Goal:** Make the map interactive — support drawing, editing, and persisting infrastructure project records.

- **Task 2.1 — PostGIS database design**
  SQLAlchemy + GeoAlchemy2 models for project geometries and attributes.
- **Task 2.2 — Frontend drawing tools**
  `@mapbox/mapbox-gl-draw` for points, lines, and polygons.
- **Task 2.3 — Attribute forms & API**
  Native HTML forms submit project metadata via `fetch` to FastAPI → stored in PostGIS.
- **Task 2.4 — Status-driven styling**
  Layer colors driven by project phase/status field.

---

## Phase 3 — Polish & AI Features

**Goal:** Refine UX and explore AI-powered capabilities.

- **Task 3.1 — UI/UX polish**
  Tailwind CSS via CDN; refined layer control panel.
- **Task 3.2 — Performance optimization**
  Backend vector tile serving for large datasets.
- **Task 3.3 — AI features (exploratory)**
  - Dangerous intersection detection using crash density analysis.
  - Natural language map queries ("show me all sidewalks under construction").
