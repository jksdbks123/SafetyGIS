# SafetyGIS — Project Plan

**Core Architecture:**
- **Frontend:** Plain HTML + Vanilla JavaScript + MapLibre GL JS
- **Backend:** Python 3 + FastAPI
- **Database:** None (Phase 1); PostgreSQL + PostGIS (Phase 2)
- **AI Coding Partner:** Claude Code

---

## Vibe Coding Workflow

1. **Set system context first.** Start each session by stating the stack: "Python backend, plain JS + MapLibre frontend, no React."
2. **Break features into the smallest possible tasks.** Instead of "build a save-drawing feature," say: "Step 1 — add a Save button to the HTML. Step 2 — write a JS function to read the current drawn coordinates. Step 3 — write a FastAPI endpoint to receive them."
3. **Data before database (Dummy Data first).** Before wiring any database, have the AI generate static GeoJSON fixtures so the frontend renders correctly. Connect real data only after the display layer is confirmed working.
4. **Feed errors directly to the AI.** When the browser console or Python throws an error, paste the full traceback and ask: "Why does this happen? How do I fix it?"

---

## Phase 1 — Urban Digital Base ✅ Complete

**Goal:** A read-only "god view" that aggregates multiple data sources onto a single interactive map.

### Inspect Mode (read-only map exploration)
- ✅ FastAPI server + MapLibre base map (OpenFreeMap, free)
- ✅ OSM infrastructure layers — traffic signals, crossings, bus stops, bike lanes, footways, sidewalks, street lamps, traffic calming
- ✅ Real CHP CCRS crash data — all 58 counties, lazy viewport loading, local GeoJSON cache
- ✅ Crash heatmap + individual popups with parties/victims data
- ✅ Mapillary integration — vector tile coverage, photo popups, sign detection layers
- ✅ Google Street View — pegman drag-and-drop
- ✅ Polygon/rectangle selection tool — CSV/GeoJSON export
- ✅ Statistics panel — Chart.js bar/donut, 8 group-by fields, 4 scopes
- ✅ Basemap switching — OpenFreeMap / Esri satellite
- ✅ Data source info modals + disclaimer

### Analysis Mode (rankings computation)
- ✅ County data manager — click to trigger download, per-county progress chips
- ✅ Safety rankings computation — EPDO-weighted scoring via `build_safety_rankings.py`
- ✅ Configurable EPDO weights (fatal / injury / PDO)
- ✅ Bin browser — intersections and segments, multi-dimension filter chips
- ✅ Crash dashboard — EPDO score, severity charts, crash coords overlay on map
- ✅ Worst / best 10 ranked facilities per bin, map visualization

### Deployment
- ✅ Docker packaging (arm64-compatible, Raspberry Pi 5 tested)
- ✅ Cloudflare Tunnel for public access

---

## Phase 2 — Core Interaction & Tracking (Next)

**Goal:** Make the map interactive — support drawing, editing, and persisting infrastructure project records.

- [ ] 2.1 PostGIS database design — SQLAlchemy + GeoAlchemy2 models for project geometries and attributes
- [ ] 2.2 Frontend drawing tools — `@mapbox/mapbox-gl-draw` for points, lines, and polygons
- [ ] 2.3 Attribute forms & API — HTML forms submit project metadata via `fetch` → FastAPI → PostGIS
- [ ] 2.4 Status-driven styling — layer colors driven by project phase/status field

---

## Phase 3 — Polish & AI Features

**Goal:** Refine UX and explore AI-powered capabilities.

- [ ] 3.1 UI/UX polish — Tailwind CSS via CDN; refined layer control panel
- [ ] 3.2 Performance optimization — backend vector tile serving for large datasets
- [ ] 3.3 AI features (exploratory)
  - AADT integration from Caltrans PeMS for exposure-normalized rankings
  - Natural language map queries ("show me all uncontrolled intersections in Sacramento")
  - Before/after safety analysis tools
