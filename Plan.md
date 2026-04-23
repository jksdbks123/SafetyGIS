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
- ✅ Intersection topology panel — click any signal/stop/intersection → draggable panel with
    SVG approach diagram, configuration badge (ROUNDABOUT/DIVIDED/etc.), conflict points,
    per-approach table; nearest-centroid fallback for infrastructure nodes
- ✅ `intersection_centroid` features — teal dots for computed topological intersection
    centers; roundabout ring nodes collapsed to single primary via compound grouping

### Analysis Mode (rankings computation)
- ✅ County data manager — click to trigger download, per-county progress chips
- ✅ Active downloads panel — real-time OSM % progress bars, crash record counts,
    download speed (tiles/s, records/s), ETA estimate; updates every 2 s
- ✅ Safety rankings computation — EPDO-weighted scoring via `build_safety_rankings.py`
- ✅ Configurable EPDO weights (fatal / injury / PDO)
- ✅ Bin browser — intersections and segments, multi-dimension filter chips
- ✅ Crash dashboard — EPDO score, severity charts, crash coords overlay on map
- ✅ Worst / best 10 ranked facilities per bin, map visualization

### Deployment
- ✅ Docker packaging (arm64-compatible, Raspberry Pi 5 tested)
- ✅ Cloudflare Tunnel for public access

---

## Phase 2A — Composite Infra Entity / Cell Model (Active)

**Goal:** Model each infrastructure facility as a composite entity (nucleus + approaches +
relations) rather than a bare point/line. See `CELL_MODEL.md` for full design spec.

**Key concept:** The road network is a tessellation of mutually exclusive **infra cells**.
Each cell = one ranked facility. A cell may have multiple cores (roundabout, divided intersection).
Cell boundaries lie at the midpoint of each approach way.

- [ ] P0 — Debug mode: third app mode visualizing cell polygons, core markers, approach
      territories, cell connections; JSON inspector panel on click (frontend only)
- [ ] PA — Extend approach attributes in `_compute_tile_topologies`:
      `speed_mph`, `surface`, `sidewalk`, `bicycle`, `turn:lanes` → `turn_lanes_list`,
      `approach_length_m`, `has_bike_lane`, `has_sidewalk`
- [ ] PB — Route relation parsing: `type=route` (bus/bicycle/foot/road) in Pass 3
- [ ] PC — Rankings enrichment: composite approach attributes in facility features;
      `sum(approach_aadt)` as entering-volume denominator; richer bin key
- [ ] PD — New `GET /api/osm/facility/{fid}` endpoint for full composite entity JSON
- [ ] PE — Frontend topology panel upgraded to use `/api/osm/facility/{fid}`;
      approach polylines on map; per-approach speed/lanes/AADT table

## Phase 2B — Core Interaction & Tracking (Deferred)

**Goal:** Make the map interactive — support drawing, editing, and persisting project records.

- [ ] 2B.1 PostGIS database design — SQLAlchemy + GeoAlchemy2 models
- [ ] 2B.2 Frontend drawing tools — `@mapbox/mapbox-gl-draw`
- [ ] 2B.3 Attribute forms & API — HTML forms → FastAPI → PostGIS
- [ ] 2B.4 Status-driven styling — layer colors by project phase/status

---

## Phase 3 — Polish & AI Features

**Goal:** Refine UX and explore AI-powered capabilities.

- [ ] 3.1 UI/UX polish — Tailwind CSS via CDN; refined layer control panel
- [ ] 3.2 Performance optimization — backend vector tile serving for large datasets
- [ ] 3.3 AI features (exploratory)
  - AADT integration from Caltrans PeMS for exposure-normalized rankings
  - Empirical Bayes PSI scoring (requires calibrated SPFs)
  - Natural language map queries ("show me all uncontrolled intersections in Sacramento")
  - Before/after safety analysis tools
