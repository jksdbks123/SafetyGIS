# GIS-Track Development Log

## Project Overview
**Name:** GIS-Track — Transportation Infrastructure Project Tracking System
**Stack:** FastAPI + Vanilla JS + MapLibre GL JS + PostgreSQL/PostGIS
**Scope:** Sacramento & Humboldt County, California (Phase 1 focus)
**AI Coding Partner:** Claude Code

---

## Developer Preferences & Work Habits

- **Language:** Conversation in Chinese (mixed with English); all code, comments, and product output must be in **English**.
- **Coding style:** Vibe Coding approach — break large features into the smallest possible tasks, data-first (dummy data before DB connection), paste errors directly to AI for debugging.
- **Stack constraints:** Python backend only; no React — plain HTML + Vanilla JS on the frontend.
- **Dependency management:** Use `python3 -m venv .venv` for virtual environments on macOS (system Python is externally managed).
- **Overpass API:** Use mirror fallback list (`overpass-api.de` → `overpass.kumi.systems` → `overpass.openstreetmap.ru`) as primary mirror is unreliable.

---

## Development History

### Phase 1 — Urban Digital Base (Background & Data Layers)

#### Session 1 — 2026-03-31

**Goal:** Build a working Phase 1 demo for Sacramento and Humboldt County.

**Tasks completed:**

| Task | Status | Notes |
|------|--------|-------|
| 1.1 FastAPI base server + MapLibre base map | ✅ Done | OpenFreeMap vector tiles (free, no API key required) |
| 1.2 OSM infrastructure layer | ✅ Done | Traffic signals, crossings, bus stops, bike lanes via Overpass API |
| 1.3 Crash data layer | ✅ Done | Dummy SWITRS-style data (300 Sacramento + 120 Humboldt records) |
| 1.4 Mapillary integration | ⬜ Pending | Deferred to later session |

**Data summary:**

| Area | OSM Features | Crash Records |
|------|-------------|---------------|
| Sacramento | 22,500 | 300 (dummy) |
| Humboldt County | 3,189 | 120 (dummy) |

**Key decisions:**
- Used OpenFreeMap (`tiles.openfreemap.org/styles/liberty`) — completely free, no API key.
- Crash data is synthetic (random seed 42) with realistic severity distribution: 3% fatal, 10% severe injury, 37% other injury, 50% PDO.
- Heatmap weighted by severity; individual crash points appear at zoom ≥ 13.
- Layer toggle panel with live stats bar at bottom.

**How to run:**
```bash
source .venv/bin/activate
uvicorn main:app --reload
# Open http://localhost:8000
```

---

#### Session 2 — 2026-03-31

**Goal:** English UI + Mapillary street view integration with dynamic loading & caching.

**Tasks completed:**

| Task | Status | Notes |
|------|--------|-------|
| Full English UI | ✅ Done | All labels, panels, popups, stats bar |
| Mapillary vector tile coverage | ✅ Done | Lines (zoom 6+) and photo points (zoom 14+) via Mapillary public VT |
| Mapillary dynamic image loading | ✅ Done | Fetches image metadata on `moveend` when zoom ≥ 13 |
| Backend tile cache | ✅ Done | Cached at z14 tile granularity under `data/mapillary_cache/` |
| Mapillary side panel | ✅ Done | Thumbnail + metadata + link to Mapillary; slides in on photo click |
| Token security | ✅ Done | Token never hardcoded in JS — backend serves it via `/api/mapillary/token` |

**Key decisions:**
- Mapillary vector tiles (`tiles.mapillary.com`) used for coverage rendering — zero API cost, browser-cached.
- Image metadata (`/api/mapillary/images`) fetched dynamically on map move, deduplicated by z14 tile key on both frontend and backend.
- Backend cache: per-tile JSON files under `data/mapillary_cache/`; individual image metadata cached as `img_{id}.json`.
- Panorama images highlighted in yellow on the map.
- `python-dotenv` added; token stored in `.env` (not committed).

**To activate Mapillary:**
1. Get a free token at [mapillary.com/developer](https://www.mapillary.com/developer)
2. Add to `.env`: `MAPILLARY_TOKEN=your_token_here`
3. Restart the server

---

#### Session 3 — 2026-04-01

**Goal:** Bug fixes + traffic sign icon integration + basemap switch refactor.

**Bugs fixed:**

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| All layers disappear on basemap switch | `map.once('style.load', addAllLayers)` stacks multiple handlers when clicked quickly | Replaced with permanent `map.on('style.load', ...)` handler |
| Duplicate `moveend` listeners after rebuild | Each `addAllLayers` called `map.on('moveend')` without removing previous | Now uses `map.off() + map.on()` pattern in `rebuildLayers()` |
| `switchBasemap` accepted unused `event` arg | Legacy signature | Removed; HTML onclick updated |
| Sign layers showed colored circles, not real icons | Used `circle` type with no sprites | Switched to `symbol` type; added Mapillary sprite via `map.addSprite()` |

**Architecture changes:**
- `map.on('style.load', onStyleLoaded)` — single permanent handler, fires on both initial load and `setStyle()`. Eliminates all `once()` hacks.
- `rebuildLayers()` replaces `addAllLayers()` — called from the style.load handler.
- `switchBasemap(mode)` — now just calls `map.setStyle()` + updates button state; no `once` listener.
- `setupPopups()` — confirmed called once only; MapLibre layer-click event listeners survive `setStyle()`.
- Guard in `switchBasemap` prevents redundant style reloads if same mode clicked twice.

**Traffic sign icons:**
- Added Mapillary sprite via `map.addSprite('mly', 'https://cdn.jsdelivr.net/npm/mapillary_sprite_source@1.8.0/sprites/sprites')`.
- Sign layers (regulatory, warning, information) switched from `circle` to `symbol` type.
- `icon-image` uses `['concat', 'mly:', ['get', 'value']]` → maps each sign value directly to its sprite icon.
- Unknown icons silently skipped by MapLibre; `icon-allow-overlap: true` to handle dense sign areas.
- Sprite added each time `rebuildLayers()` runs (setStyle clears previous sprites).

---

## Roadmap

### Phase 2 — Core Business Interaction & Tracking
- [ ] 2.1 PostGIS database design (SQLAlchemy + GeoAlchemy2)
- [ ] 2.2 Frontend drawing tools (`@mapbox/mapbox-gl-draw`)
- [ ] 2.3 Attribute forms + FastAPI data persistence
- [ ] 2.4 Status-driven dynamic layer styling

### Phase 3 — Polish & AI Features
- [ ] 3.1 UI/UX with Tailwind CSS (CDN)
- [ ] 3.2 Vector tiles performance optimization
- [ ] 3.3 AI-powered dangerous intersection detection
- [ ] 3.3 Natural language map queries

---

## Session 2026-04-06 — Statistics Analysis Panel

### Added
- **`GET /api/counties`** — returns `{county_name: county_code}` for all 58 CA counties
- **`GET /api/crashes/stats`** — aggregates crash counts from cached county GeoJSON files by scope (county or city) and group_by field (severity, year, collision_type, weather, road_condition, lighting, involved_with, day_of_week)
- **Statistics panel** in left sidebar — collapsible, sits between AI Analytics and Street View
  - Data source toggle: Crashes vs OSM Infrastructure
  - Scope: Selection (auto-activates when polygon/rect selection exists) / Viewport / County / City
  - Group by: 8 crash fields + OSM feature type
  - Year filter
  - Chart type: Bar / Donut (Chart.js 4.4.3 via CDN)
  - Export CSV
  - Auto-refresh on: viewport moveend, new data loaded, selection change

### Architecture decisions
- Viewport and Selection scopes are client-side (CRASH_FEATURE_MAP / OSM_FEATURE_MAP)
- County/City scopes call backend which reads cached .geojson files directly
- Selection scope auto-activates when features are selected via draw tool or click
- Chart.js loaded via CDN (no build step)

### Field verification
- `city_name` confirmed present in cached features
- `lightingdescription` (no space, not `lighting_description`) confirmed
- `weather_1`, `road_condition_1`, `day_of_week`, `motorvehicleinvolvedwithcode` all confirmed

---

## Session 2026-04-10 — Analysis Mode, Safety Rankings, Street View & Code Quality

### Added: Analysis Mode (Inspect / Analysis toggle)

A new `Analysis` button in the header switches the left panel from Inspect mode (viewport lazy loading) to Analysis mode (controlled county-level downloads + rankings). Key design:

- `G_appMode = 'inspect' | 'analysis'` guards `scheduleViewportLoad()` so panning does not trigger data loads while in Analysis mode
- The Inspect panel and Analysis panel are independent DOM elements; toggling hides/shows each
- All polling timers (county download + rankings compute) are correctly cancelled on mode switch

### Added: County Data Manager (Analysis Mode)

- County chip grid shows all 58 California counties with color-coded readiness state (ready / partial / downloading / no data)
- Click any chip to trigger parallel background download of crash + OSM data for that county
- Per-county polling (`_pollAnaCounty`) updates chip state every 4 seconds; stops when download completes
- Session handoff: counties cached during Inspect mode browsing are pre-seeded — entering Analysis mode shows their state immediately
- `GET /api/data/county_status` is the single source of truth: returns `{crash_ready, osm_pct, osm_tile_cached, osm_tile_total, analysis_ready, fetching_crash, fetching_osm}` per county

### Added: Safety Rankings Computation (Analysis Mode)

- Compute button fires `POST /api/rankings/compute?weights=F,I,P&counties=…&min_osm_pct=N`
- Progress bar polls `GET /api/rankings/status` at 1.5 s intervals
- On completion, auto-loads `GET /api/rankings/bins` and renders the bin browser
- EPDO weights configurable (fatal default 10, injury 2, PDO 0.2)
- "Allow incomplete OSM" checkbox sets `min_osm_pct=0` for counties with crash data only

### Added: Bin Browser (Analysis Mode)

- `GET /api/rankings/bins` returns all available bin keys with facility counts
- Chips grouped by intersection vs segment; purple = data available, gray = sparse (<20 facilities)
- Clicking a chip calls `GET /api/rankings/bin/{key}` and renders worst/best 10 tables
- Map layers `rankings-worst` (red circles) and `rankings-best` (green circles) are updated via shared `_renderRankingsMap()`

### Added: Crash Dashboard (`openRankDash`)

Clicking any ranked facility (from map circle or table row) opens a detail panel:
- EPDO score, 5-year fatal / severe / total counts in a 4-cell score strip
- Lighting, PCF, weather, day-of-week, collision type, road condition distribution bars
- Crash point overlay on the main map (facility buffer + individual crash coords)
- `crash_coords` stored in rankings GeoJSON as a JSON-encoded string (max 200 per facility) to minimize GeoJSON size; decoded with `JSON.parse()` on the frontend

### Added: `build_safety_rankings.py` enhancements

- New crash fields: `collision_type`, `road_cond`, `mveh` (motor vehicle involved), `hour` (derived from `crash_time_description`)
- Hour-of-day distribution (24 bins) added to EPDO output
- `--counties` flag accepts comma-separated list; `--min-osm-pct` sets readiness threshold
- `PYTHONIOENCODING=utf-8` set for Windows `cp1252` compatibility
- Empty cache written for failed Overpass tiles so `osm_pct` advances correctly

### Fixed: Google Street View showing world map instead of Street View

Root cause: an iframe fallback I had added earlier used `https://maps.google.com/maps?layer=c&…&output=embed`, which opens a regular map embed, not a Street View panorama.

Fix: removed iframe fallback entirely; reverted to `StreetViewService.getPanorama()` approach. Added a poll-wait (200 ms intervals, 8 s timeout) for the edge case where the user drags the pegman before the Google Maps JS API finishes loading.

### Fixed: Mapillary layers permanently grayed out on page load

Root cause: `fetch('/api/osm/sacramento')` returned 404 (endpoint removed in an earlier refactor). The error was not caught; it propagated up through `loadData()`, aborting config loading, the Mapillary token fetch, and the Google Maps API initialization before they ran. Result: `G_hasMly` stayed `false`, `rebuildLayers()` applied `.disabled` CSS and `pointerEvents:none` to all Mapillary toggles permanently.

Fix: wrapped the non-critical OSM preload in an isolated try-catch. Failures silently fall through to an empty `G_osmData`; viewport loading populates OSM on first pan as normal.

### Fixed: `load_dotenv()` not overriding inherited OS environment variables

Fix: `load_dotenv(override=True)` in `main.py` so `.env` values always win over any inherited process environment.

### Removed: Inspect-mode Rankings panel (dead code)

The old Inspect-mode Rankings panel (compute button, county scope dropdown, output dir picker, rankings table) was moved to Analysis mode in a prior session but its JS functions were left behind. Removed 299 lines of dead code: `toggleRankingsPanel`, `_loadRankingsConfig`, `setRankingsDir`, `onRankFacTypeChange`, `_buildBinKey`, `loadSelectedRanking`, `_rankStatus`, `_renderRankingsTable`, `pickRankingsDir`, `startRankingsCompute`, `_startRankPoll`, `_pollRankStatus`, `_setRankProgress`, `_resetComputeBtn`, `downloadRankings`, `clearRankings`.

### Simplified: Code quality pass

- `_renderAnaCountyGrid()` now reuses `_countyChipClass()` instead of inlining duplicate chip class logic
- `_pollAnaCounty()` now compares previous vs fresh county state before re-rendering the grid (avoids unnecessary DOM repaints every 4 seconds)
- `_anaComputePollTimer` now correctly cancelled when switching back to Inspect mode

---

## Session 2026-04-08 — Metadata, Disclaimer, Docker, Raspberry Pi 5 Deployment

### Added: Data metadata & disclaimer

- **Info buttons (ⓘ)** on each layer group heading in the left panel; clicking opens a modal with source, license, coverage, and API notes for that data layer
- **Disclaimer & Data Credits** section added to the Help panel (open by default)
- **Footer disclaimer bar** across the bottom of the app with data attribution links
- **Header** updated: "Developed by Zhihui · Vibe Coding Exercise" + ⚠ Disclaimer button
- **Page title** updated to "GIS-Track — California Transportation Safety"

### Added: Docker packaging

Three new files for containerized deployment:

| File | Purpose |
|------|---------|
| `Dockerfile` | `python:3.12-slim` base, arm64-compatible, copies source only (data via volume) |
| `docker-compose.yml` | Port 8000, `./data` volume mount, `env_file`, `restart: unless-stopped` |
| `.dockerignore` | Excludes `.venv/`, `data/`, `.env`, `*.md` from build context |

Key decision: `data/` is excluded from the image and provided via volume mount so the
GeoJSON cache persists across container rebuilds.

### Raspberry Pi 5 deployment

- RPi5 confirmed arm64-compatible with all five Python dependencies
- OS: Raspberry Pi OS (64-bit), flashed via Raspberry Pi Imager with WiFi + SSH pre-configured
- Pi discovered at `192.168.1.157` via mDNS (`raspberrypi.local`)
- Passwordless SSH set up using `~/.ssh/id_pi` ed25519 key pair
- Docker 29.4.0 installed on Pi via `get.docker.com`
- Container built natively on arm64 — no emulation needed
- API keys configured in `~/SafetyGIS/.env` on the Pi

### Bug fix: CCRS crash data returns 0 records (HTTP 409 / empty resource)

Two separate issues discovered and fixed in `main.py` and `scripts/fetch_crash_data.py`:

**Issue 1 — Type mismatch (HTTP 409):**  
`data.ca.gov` changed the `County Code` column type from integer to text. Our filter
was sending `{"County Code": 34}` (integer); the Postgres backend rejected it with
`operator does not exist: text = integer`.  
Fix: `json.dumps({"County Code": str(county_code)})`

**Issue 2 — Empty duplicate resource selected:**  
The CCRS package now exposes multiple resources with the same name per year
(e.g., three `Crashes_2024` entries). Only the first has data; the others are empty.
Our loop used `crashes[year] = r["id"]`, overwriting on each iteration and ending up
with the last (empty) resource.  
Fix: `if year not in crashes: crashes[year] = r["id"]`  
Same fix applied to `parties` and `victims` resource discovery.

**Verification:** Sacramento 86,338 crashes (851 fatal), Humboldt 9,723 (129 fatal) — confirmed on Pi.

### Documentation

- `TUTORIAL.md` rewritten in English — covers local setup, architecture, Docker, Pi deployment, Cloudflare Tunnel, troubleshooting, and update workflow
- `Plan.md` translated to English
- `DEVLOG.md` updated with this session
