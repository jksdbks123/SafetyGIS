# SafetyGIS

**SafetyGIS** is an open-source web GIS platform for visualizing transportation safety infrastructure and crash data across California. It combines real CHP crash records, OpenStreetMap road infrastructure, and Mapillary street-level imagery into an interactive map for researchers, planners, and the public.

![SafetyGIS screenshot](docs/screenshot.png)

## Features

- **Real CHP crash data** — pulls live records from the California CHP Crash Records System (CCRS) via the [data.ca.gov](https://data.ca.gov) open data API; all 60+ raw attributes preserved
- **OSM infrastructure layers** — traffic signals, crossings, bus stops, bike lanes, footways, sidewalks, traffic calming devices, and street lamps fetched dynamically via Overpass API
- **Crash heatmap + individual point popups** — heatmap at low zoom; click individual crashes at zoom ≥ 13 for the full attribute sheet
- **Street-level imagery** — Mapillary photo coverage rendered as vector tiles; click any photo point for thumbnail + metadata; drag the pegman to open Google Street View at any location
- **Viewport-driven data loading** — OSM and crash data auto-load as you pan/zoom; z12 tile caching keeps re-fetches minimal
- **Polygon & rectangle selection tool** — draw a shape to download all selected crash / OSM features as GeoJSON
- **Basemap switching** — OpenFreeMap vector tiles (default, no API key) + Esri World Imagery satellite toggle
- **Stats ribbon** — collapsible bottom bar with live layer statistics

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python · FastAPI · Uvicorn |
| Frontend | Vanilla JS · MapLibre GL JS 4.7.1 |
| Basemap | OpenFreeMap (free, no key) |
| Crash data | CCRS / data.ca.gov CKAN API |
| OSM data | Overpass API (3-mirror fallback) |
| Street imagery | Mapillary vector tiles + REST API |
| Street View | Google Maps JavaScript API (optional) |

No database required for Phase 1 — all data is fetched on-demand and cached as local GeoJSON files.

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/SafetyGIS.git
cd SafetyGIS

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

| Key | Required | Where to get it |
|-----|----------|-----------------|
| `MAPILLARY_TOKEN` | Optional | [mapillary.com/app](https://www.mapillary.com/app/?pKey=signup) — free developer account |
| `GOOGLE_MAPS_KEY` | Optional | [Google Cloud Console](https://console.cloud.google.com/) — Maps JavaScript API |

The app runs fully without either key; Mapillary layers and Street View will simply be unavailable.

### 3. Run

```bash
make dev
# or: source .venv/bin/activate && uvicorn main:app --reload
```

Open **http://localhost:8000**.

### 4. Load data

On first run the map is empty. Data loads automatically as you pan/zoom California. To pre-seed specific counties:

```bash
# Fetch real CHP crash data (Sacramento + Humboldt by default)
make fetch-crashes

# Fetch OSM infrastructure for a viewport
make fetch-osm
```

## Data Sources

| Source | License | Notes |
|--------|---------|-------|
| [CCRS / data.ca.gov](https://data.ca.gov/dataset/california-chp-traffic-crash-data) | CC BY | California CHP crash records 2015–present |
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | ODbL | Road infrastructure via Overpass API |
| [OpenFreeMap](https://openfreemap.org) | MIT | Vector basemap tiles |
| [Mapillary](https://www.mapillary.com/developer/api-documentation) | CC BY-SA | Street-level photos — requires free API key |
| [Esri World Imagery](https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9) | Esri ToU | Satellite imagery basemap |

## Project Structure

```
SafetyGIS/
├── main.py               # FastAPI app — all API endpoints
├── static/
│   ├── index.html        # Single-page app shell + CSS
│   └── app.js            # All map logic (MapLibre, layers, popups, UI)
├── scripts/
│   ├── fetch_crash_data.py   # Pull CCRS crash records by county
│   └── fetch_osm.py          # Pre-fetch OSM for a bounding box
├── data/
│   ├── crash_cache/      # Per-county crash GeoJSON (gitignored)
│   ├── osm_cache/        # z12 tile OSM JSON (gitignored)
│   └── mapillary_cache/  # Mapillary tile/image cache (gitignored)
├── requirements.txt
├── Makefile
└── .env.example
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/osm/dynamic?bbox=W,S,E,N` | OSM features for viewport (z12 tile cache) |
| `GET /api/crashes/dynamic?bbox=W,S,E,N` | CCRS crashes — triggers background county download |
| `GET /api/mapillary/images?bbox=…` | Mapillary image metadata (proxied + cached) |
| `GET /api/mapillary/token` | Returns whether a Mapillary token is configured |
| `POST /api/ai/query` | AI assistant placeholder — wire your LLM here |

## Roadmap

### Phase 2 (planned)
- [ ] PostGIS database (SQLAlchemy + GeoAlchemy2)
- [ ] Drawing tools (`@mapbox/mapbox-gl-draw`) for project polygons
- [ ] Attribute forms + project persistence
- [ ] Status-driven dynamic layer styling

### Phase 3 (future)
- [ ] Statewide crash coverage (all 58 California counties)
- [ ] Before/after safety analysis tools
- [ ] Shareable project links

## Contributing

Contributions are welcome! Please open an issue to discuss proposed changes before submitting a pull request. For large features, describe the use case so we can coordinate.

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
