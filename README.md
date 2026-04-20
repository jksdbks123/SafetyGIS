# SafetyGIS

**SafetyGIS** is an open-source web GIS platform for visualizing transportation safety infrastructure and crash data across California. It combines real CHP crash records, OpenStreetMap road infrastructure, Mapillary street-level imagery, and a statewide safety rankings engine into an interactive map for researchers, planners, and the public.

## Features

### Inspect Mode
- **Real CHP crash data** — pulls live records from the California CHP Crash Records System (CCRS) via the [data.ca.gov](https://data.ca.gov) open data API; all 60+ raw attributes preserved; all 58 counties supported
- **OSM infrastructure layers** — traffic signals, crossings, bus stops, bike lanes, footways, sidewalks, traffic calming devices, and street lamps fetched dynamically via Overpass API (3-mirror fallback)
- **Crash heatmap + individual point popups** — heatmap at low zoom; click individual crashes at zoom ≥ 13 for the full attribute sheet including parties and victims data
- **Street-level imagery** — Mapillary photo coverage as vector tiles; click any photo point for thumbnail + metadata; sign detection layers (regulatory, warning, info) with Mapillary sprite icons
- **Google Street View** — drag the yellow pegman to any map location to open an embedded Street View panorama
- **Polygon & rectangle selection tool** — draw a shape to select crash / OSM features; export as CSV or GeoJSON
- **Statistics panel** — analyze crash patterns by severity, year, collision type, weather, road condition, lighting, or day of week; bar and donut charts via Chart.js; scopes: viewport, selection, county, city
- **Basemap switching** — OpenFreeMap vector tiles (default, free, no key) + Esri World Imagery satellite

### Analysis Mode
- **County data manager** — click any of California's 58 counties to trigger background download of crash data + OSM tiles; per-county progress chips show readiness state
- **Safety rankings computation** — EPDO (Equivalent Property Damage Only) weighted scoring across all cached counties; configurable fatal/injury/PDO weights
- **Bin browser** — browse ranked facilities by type (intersection vs segment) and filter combinations (road class, speed, control type, leg count, lane count); purple chips = data available
- **Crash dashboard** — click any ranked facility to open a detailed panel: EPDO score, 5-year fatal/severe/total counts, severity breakdown bar chart, lighting/weather/day distributions, and crash point overlay on the map

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 · FastAPI · Uvicorn |
| Frontend | Vanilla JS · MapLibre GL JS 4.7.1 · Chart.js 4.4.3 |
| Basemap | OpenFreeMap (free, no key) |
| Crash data | CCRS / data.ca.gov CKAN API |
| OSM data | Overpass API (3-mirror fallback) |
| Street imagery | Mapillary vector tiles + REST API |
| Street View | Google Maps JavaScript API (optional) |
| Deployment | Docker · Raspberry Pi 5 · Cloudflare Tunnel |

No database required — all data is fetched on-demand and cached as local GeoJSON files.

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/jksdbks123/SafetyGIS.git
cd SafetyGIS
```

### 2. Create a virtual environment and install dependencies

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Windows (Command Prompt):**
```cmd
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> **Windows PowerShell note:** If script execution is blocked, run
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once, then retry.

### 3. Configure API keys

**macOS / Linux:**
```bash
cp .env.example .env
```

**Windows (Command Prompt):**
```cmd
copy .env.example .env
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in your keys:

| Key | Required | Where to get it |
|-----|----------|-----------------|
| `MAPILLARY_TOKEN` | Optional | [mapillary.com/app](https://www.mapillary.com/app/?pKey=signup) — free developer account |
| `GOOGLE_MAPS_KEY` | Optional | [Google Cloud Console](https://console.cloud.google.com/) — Maps JavaScript API |

The app runs fully without either key; Mapillary layers and Street View will be unavailable.

### 4. Run

**macOS / Linux (Makefile shortcut):**
```bash
make dev
# custom port:
make dev PORT=9000
```

**macOS / Linux (manual):**
```bash
source .venv/bin/activate
uvicorn main:app --reload
```

**Windows (Command Prompt):**
```cmd
.venv\Scripts\activate
uvicorn main:app --reload
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

> **Windows note:** `make` is not available by default on Windows. Either run `uvicorn`
> directly (shown above) or install GNU Make via [Chocolatey](https://chocolatey.org/)
> (`choco install make`) or run commands inside Git Bash / WSL.

Open **http://localhost:8000** in your browser.

### 5. Load data

Data loads automatically as you pan and zoom in Inspect mode. To pre-seed or compute rankings:

```bash
# Switch to Analysis mode in the app (header toggle) and click any county chip
# to trigger downloads, then click Compute Rankings.

# Or pre-fetch from the command line:
python scripts/fetch_crash_data.py      # CCRS crash data (Sacramento + Humboldt)
python scripts/fetch_osm.py             # OSM infrastructure tiles

# Run rankings standalone (after data is cached):
python scripts/build_safety_rankings.py
python scripts/build_safety_rankings.py --counties sacramento,alameda --min-osm-pct 0
```

## Docker Deployment

Docker is the recommended path for production deployments and Raspberry Pi hosting.
It works identically on macOS, Linux, and Windows (Docker Desktop with WSL2 backend).

```bash
# Build and start (detached)
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down

# Update code and rebuild
git pull && docker compose up -d --build
```

> **Windows note:** Docker Desktop for Windows requires WSL2. Install it from
> [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
> Once Docker Desktop is running, all `docker compose` commands work identically in
> PowerShell, Command Prompt, or WSL.

API keys are read from `.env` (never baked into the image). The `data/` directory is
mounted as a volume so the cache survives container rebuilds.

See [TUTORIAL.md](TUTORIAL.md) for Raspberry Pi 5 deployment, Cloudflare Tunnel setup, and the full deployment guide.

## Data Sources

| Source | License | Notes |
|--------|---------|-------|
| [CCRS / data.ca.gov](https://data.ca.gov/dataset/california-chp-traffic-crash-data) | CC BY | California CHP crash records 2019–2024 |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | ODbL | Road infrastructure via Overpass API |
| [OpenFreeMap](https://openfreemap.org) | MIT | Vector basemap tiles |
| [Mapillary](https://www.mapillary.com/developer/api-documentation) | CC BY-SA | Street-level photos — requires free API key |
| [Esri World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9) | Esri ToU | Satellite imagery basemap |

## Project Structure

```
SafetyGIS/
├── main.py                      # FastAPI backend — all API endpoints (~1,170 lines)
├── static/
│   ├── index.html               # Single-page app shell + CSS (~950 lines)
│   └── app.js                   # All map logic — layers, UI, Analysis mode (~3,040 lines)
├── scripts/
│   ├── build_safety_rankings.py # Compute EPDO safety rankings from cached data (~890 lines)
│   ├── fetch_crash_data.py      # Pre-fetch CCRS crash data by county
│   └── fetch_osm.py             # Pre-fetch OSM infrastructure tiles
├── data/                        # Runtime cache — gitignored, mounted as Docker volume
│   ├── crash_cache/             # {county}.geojson — generated on demand
│   ├── osm_cache/               # {z}_{x}_{y}.json — generated on demand
│   ├── rankings/                # Statewide rankings GeoJSON output
│   └── mapillary_cache/         # Mapillary tile/image cache
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
└── Makefile
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/osm/dynamic?bbox=W,S,E,N` | OSM features for viewport (z12 tile cache) |
| `GET /api/crashes/dynamic?bbox=W,S,E,N` | CCRS crashes — triggers background county download |
| `GET /api/crashes/stats` | Aggregated crash statistics (county/city/viewport/selection) |
| `GET /api/counties` | All 58 CA county names |
| `GET /api/data/county_status` | Per-county cache readiness: `{crash_ready, osm_pct, analysis_ready, …}` |
| `POST /api/data/county/{name}/fetch_crash` | Trigger background crash download for one county |
| `POST /api/data/county/{name}/fetch_osm` | Trigger background OSM tile download for one county |
| `POST /api/rankings/compute` | Start safety rankings computation (EPDO weights, county list) |
| `GET /api/rankings/status` | Rankings job status: `{status, progress 0-100, message}` |
| `GET /api/rankings/bins` | Available ranking bin keys |
| `GET /api/rankings/bin/{bin_key}` | Worst/best 10 facilities for a bin |
| `GET /api/mapillary/images?bbox=…` | Mapillary image metadata (proxied + cached) |
| `GET /api/mapillary/token` | Returns whether a Mapillary token is configured |
| `POST /api/ai/query` | AI assistant placeholder |

## Contributing

Contributions are welcome. Open an issue to discuss proposed changes before submitting a pull request.

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes and open a PR

## License

This project is licensed under the **MIT License** — see [LICENSE](LICENSE) for details.

Data licenses vary by source — see the [Data Sources](#data-sources) table above.

## Acknowledgements

- CHP / California OES for public CCRS crash data
- OpenStreetMap contributors
- OpenFreeMap for free vector tile hosting
- Mapillary for street-level imagery API
