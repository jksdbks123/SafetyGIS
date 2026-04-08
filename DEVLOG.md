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
