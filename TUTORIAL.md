# GIS-Track — Complete Development & Deployment Tutorial

> Author: Zhihui | Vibe Coding Exercise  
> Last updated: 2026-04-08

This tutorial documents the full hands-on workflow for GIS-Track — from local development
through Raspberry Pi deployment — including architecture explanations, data source details,
and troubleshooting guides.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Local Development Setup](#2-local-development-setup)
3. [Application Architecture](#3-application-architecture)
4. [Running & Debugging](#4-running--debugging)
5. [Data Sources & How They Are Fetched](#5-data-sources--how-they-are-fetched)
6. [Docker Packaging](#6-docker-packaging)
7. [Raspberry Pi 5 Deployment](#7-raspberry-pi-5-deployment)
8. [Public Access via Cloudflare Tunnel](#8-public-access-via-cloudflare-tunnel)
9. [Troubleshooting](#9-troubleshooting)
10. [Code Update Workflow](#10-code-update-workflow)

---

## 1. Project Overview

GIS-Track is a browser-based transportation safety GIS tool covering all of California.

**Data layers:**

| Layer | Source | Loading strategy |
|-------|--------|-----------------|
| Crash points | CHP CCRS via data.ca.gov | Lazy-loaded per county, cached locally |
| Road infrastructure | OpenStreetMap via Overpass API | Lazy-loaded per Z12 tile, cached locally |
| Street sign detection | Mapillary API (Meta) | Live requests, server-side proxy |
| Street View | Google Maps Platform | Live requests, optional API key |

**Tech stack:**

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12 + FastAPI + Uvicorn |
| Frontend | Vanilla JavaScript + MapLibre GL JS 4.7.1 + Chart.js 4.4.3 |
| No build tools | No webpack / vite — CDN only |
| Deployment | Docker + Raspberry Pi 5 |

---

## 2. Local Development Setup

### Prerequisites
- macOS (this tutorial is macOS-based)
- Python 3.11 or 3.12 (recommended; local dev uses 3.14 but Docker targets 3.12)
- Git

### Steps

```bash
# 1. Clone the repository
git clone https://github.com/jksdbks123/SafetyGIS.git
cd SafetyGIS

# 2. Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure API keys
cp .env.example .env
# Open .env in a text editor and fill in:
#   MAPILLARY_TOKEN=MLY|...
#   GOOGLE_MAPS_KEY=AIza...
# Both keys are optional — the app runs without them;
# Mapillary layers and Street View are simply unavailable.
```

### How to get API keys

**Mapillary Token (free):**
1. Register at mapillary.com
2. Go to Developer Settings → Create Application
3. Copy the Client Access Token

**Google Maps Key:**
1. Go to console.cloud.google.com
2. Create a project → Enable Maps JavaScript API
3. Create an API Key (recommended: restrict by HTTP referrer)

---

## 3. Application Architecture

```
Browser (MapLibre GL JS + app.js)
    │
    ├── GET /api/osm/dynamic?bbox=      → Overpass tile fetch, Z12 cache
    ├── GET /api/crashes/dynamic?bbox=  → CCRS per-county, returns fetching / features
    ├── GET /api/crashes/stats          → Statistical aggregation (county/city/viewport)
    ├── GET /api/counties               → List of all 58 California counties
    ├── GET /api/mapillary/images       → Mapillary proxy (token never sent to browser)
    ├── GET /api/mapillary/token        → Returns token availability only
    ├── POST /api/ai/query              → AI placeholder (wire real LLM here)
    └── GET /static/*                   → HTML / JS / CSS static files

FastAPI backend (main.py)
    │
    └── External APIs: Overpass, data.ca.gov (CCRS), Mapillary, Google Maps
```

### Key frontend data structures

```javascript
CRASH_FEATURE_MAP   // Map<id, GeoJSON Feature> — all loaded crash events
OSM_FEATURE_MAP     // Map<id, GeoJSON Feature> — all loaded OSM features
G_selectionData     // GeoJSON FeatureCollection — currently selected features
```

**Why use a JS `Map` instead of reading MapLibre properties directly?**  
MapLibre converts GeoJSON to VectorTile format internally, retaining only properties
referenced in `paint`/`filter` expressions. The `e.features[0].properties` in a click
handler may be truncated. Storing full features in a keyed `Map` and looking them up by
ID on click guarantees complete attribute access.

### Crash data lazy-loading flow

```
User pans the map
    ↓
Frontend: GET /api/crashes/dynamic?bbox=west,south,east,north
    ↓
Backend: determine which counties intersect the bbox
    ↓
Already cached counties → return features immediately
Uncached counties → return {fetching: ["sacramento", ...]}, start background thread
    ↓ (background thread)
Fetch from data.ca.gov CCRS API, paginated at 5,000 records/page
Write to data/crash_cache/{county}.geojson
    ↓
Frontend: received fetching → poll again after 25 seconds
```

---

## 4. Running & Debugging

### Start the local server

```bash
source .venv/bin/activate
uvicorn main:app --reload
# Open http://localhost:8000 in your browser
```

`--reload` watches Python files and restarts on changes. For JS/HTML/CSS changes,
just refresh the browser.

### Reading logs

```bash
# Backend logs stream to the terminal where uvicorn is running.
# Crash data fetch progress looks like:
#   [sacramento] county=34 year=2024… 117066 total

# Frontend logs: open browser DevTools → Console tab
```

### Manually pre-fetch data

```bash
# Pre-fetch crash data for Sacramento and Humboldt
python scripts/fetch_crash_data.py

# Pre-fetch OSM infrastructure for Sacramento and Humboldt bbox
python scripts/fetch_osm.py
```

---

## 5. Data Sources & How They Are Fetched

### CHP CCRS (Crash Data)

- **Source:** California Highway Patrol, via data.ca.gov CKAN Datastore API
- **Coverage:** All 58 California counties, years 2019–2024 (2025 added)
- **License:** California Open Data — public domain
- **API endpoint:** `https://data.ca.gov/api/3/action/datastore_search`
- **Pagination:** 5,000 records per page, offset-based

> **Critical:** The `County Code` field is stored as **text**, not integer.
> The filter must pass `"34"` (string), not `34` (integer).
> Passing an integer returns HTTP 409 with a PostgreSQL type-mismatch error.

> **Critical:** The same year (e.g., `Crashes_2024`) may appear as multiple resources
> in the API response. Only the **first** one contains data; subsequent entries are empty.
> The resource discovery loop uses `if year not in crashes` to keep the first match
> and ignore duplicates.

### OpenStreetMap (Infrastructure)

- **Source:** OpenStreetMap contributors via Overpass API
- **License:** Open Database License (ODbL) 1.0
- **Query format:** Overpass QL with `out geom;`
- **Tile cache:** Z12 granularity, filename pattern `12_{x}_{y}.json`
- **Mirror fallback chain:** `overpass-api.de` → `overpass.kumi.systems` → `overpass.openstreetmap.ru`

> **Critical:** Always use `out geom;`. Never use `out body; >; out skel qt;`.
>
> The `out body; >; out skel qt;` format returns way elements first (with only node ID
> references, no coordinates), then resolves nodes in a second pass. A single-pass parser
> will have an empty `nodes` dict when it encounters way elements, silently dropping all
> line features (roads, bike lanes, footways).
>
> `out geom;` embeds coordinates directly in each element — no `nodes` dict needed.

### Mapillary (Street Sign Detection)

- **Source:** Mapillary (Meta Platforms, Inc.)
- **License:** CC BY-SA 4.0 (imagery); Mapillary AI for sign detection
- **Auth:** Requires Mapillary API token; server-side proxy prevents token exposure
- **Loading:** Lazy — layers load only when first toggled on

---

## 6. Docker Packaging

### File overview

| File | Purpose |
|------|---------|
| `Dockerfile` | Defines image build steps |
| `docker-compose.yml` | Service orchestration: port, volume, env |
| `.dockerignore` | Excludes venv, caches, secrets from image |

### Dockerfile explained

```dockerfile
FROM python:3.12-slim          # ~130 MB, official arm64 support
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # separate layer — cached by Docker
COPY main.py .
COPY static/ ./static/
COPY scripts/ ./scripts/
RUN mkdir -p data/crash_cache data/osm_cache data/mapillary_cache
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Why is `data/` not copied into the image?**  
The cache directory holds hundreds of MB of GeoJSON that grows over time. It is provided
via a volume mount at runtime so it persists across container rebuilds. Baking it into
the image would bloat every build.

### docker-compose.yml explained

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env                  # API keys never baked into the image
    volumes:
      - ./data:/app/data      # cache persists on the host filesystem
    restart: unless-stopped   # auto-restarts after Pi reboots
```

### Testing Docker locally (macOS)

```bash
# Install Docker Desktop
brew install --cask docker
# Launch Docker Desktop from Applications

# Build and start
docker compose up

# Run in background
docker compose up -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

## 7. Raspberry Pi 5 Deployment

### Compatibility summary

| Item | Status | Notes |
|------|--------|-------|
| CPU architecture | ✅ arm64 (Cortex-A76) | python:3.12-slim has official arm64 image |
| RAM (4 GB model) | ✅ | App steady-state ~200–400 MB |
| Docker Engine | ✅ | Fully supported |
| All Python dependencies | ✅ | All packages have arm64 wheels |
| OS requirement | ⚠️ | Must use **64-bit OS** |
| Storage | ⚠️ | Crash cache ~330 MB and growing — use USB SSD or A2-class SD card |

### Step 1 — Flash the SD card (Raspberry Pi Imager)

1. Install: `brew install --cask raspberry-pi-imager`
2. Select: **Device → Raspberry Pi 5**, **OS → Raspberry Pi OS (64-bit)**
3. Click **Next → Edit Settings** and fill in:
   - Hostname: `raspberrypi`
   - Enable SSH → password authentication
   - Username: `pi` / Password: your choice
   - WiFi SSID & Password
   - Wireless LAN country: US
4. Write to SD card

### Step 2 — Connect from Mac (headless)

After powering on the Pi, wait ~60 seconds for WiFi to connect.

```bash
# Confirm Pi is on the network
ping -c 3 raspberrypi.local

# Connect
ssh pi@raspberrypi.local
# or: ssh pi@192.168.1.x  (find IP from your router's DHCP device list)
```

### Step 3 — Set up passwordless SSH (optional but recommended)

Allows automated deployments from your Mac without entering a password every time.

```bash
# On Mac — generate a dedicated key pair
ssh-keygen -t ed25519 -f ~/.ssh/id_pi -N ""

# On the Pi (run one line at a time in your SSH session)
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo "<paste content of ~/.ssh/id_pi.pub here>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Test from Mac
ssh -i ~/.ssh/id_pi pi@raspberrypi.local "echo OK"
```

### Step 4 — Deploy on the Pi

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker pi
# Log out and back in for the docker group to take effect

# 2. Clone the project
git clone https://github.com/jksdbks123/SafetyGIS.git
cd SafetyGIS

# 3. Configure API keys
cp .env.example .env
nano .env   # fill in MAPILLARY_TOKEN and GOOGLE_MAPS_KEY

# 4. Start the service (detached)
sudo docker compose up -d --build

# 5. Verify
curl http://localhost:8000/api/mapillary/token
# Expected: {"token": "MLY|..."}

# Access from any device on the LAN
http://192.168.1.x:8000
```

### Daily management commands (on the Pi)

```bash
# Check container status
sudo docker ps

# Follow live logs
sudo docker compose -f ~/SafetyGIS/docker-compose.yml logs -f

# Stop
sudo docker compose -f ~/SafetyGIS/docker-compose.yml down

# Update code and rebuild
cd ~/SafetyGIS && git pull && sudo docker compose up -d --build
```

---

## 8. Public Access via Cloudflare Tunnel

Cloudflare Tunnel exposes your local service to the public internet without a public IP,
port forwarding, or firewall changes. The free tier requires no account registration.

### Temporary tunnel from Mac

```bash
# Install
brew install cloudflare/cloudflare/cloudflared

# Start (URL is random and changes on each run)
cloudflared tunnel --url http://localhost:8000
# Output: https://random-name.trycloudflare.com
```

### Persistent tunnel on the Pi

```bash
sudo docker run -d \
  --name cloudflared \
  --network host \
  --restart unless-stopped \
  cloudflare/cloudflared:latest \
  tunnel --url http://localhost:8000

# Find the public URL
sudo docker logs cloudflared 2>&1 | grep trycloudflare
```

> **Note:** Free quick-tunnels get a new random subdomain on every restart.
> For a stable URL, register a Cloudflare account and configure a Named Tunnel.

---

## 9. Troubleshooting

### Crash data not loading

**Symptoms:** No red crash points on the map; loading banner disappears without data.

```bash
# 1. Test CCRS API reachability
curl -s "https://data.ca.gov/api/3/action/package_show?id=ccrs" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['success'])"
# Expected: True

# 2. Check cache file sizes (files ~45 bytes = empty = fetch failed)
ls -la data/crash_cache/

# 3. Run fetch script manually to see the error
python scripts/fetch_crash_data.py 2>&1 | head -30
```

**Common errors and fixes:**

| Error | Cause | Fix |
|-------|-------|-----|
| HTTP 409 | `County Code` filter sent as integer | Change to `str(county_code)` |
| 0 records, HTTP 200 | Empty duplicate resource selected | Add `if year not in crashes` guard |
| HTTP 404 | Resource ID changed upstream | Re-run resource discovery |

### OSM data not loading

```bash
# Test the Overpass mirror chain
curl -s -o /dev/null -w "%{http_code}" "https://overpass-api.de/api/interpreter"

# 504 or timeout on all mirrors = Overpass under heavy load; retry later
```

### Cannot SSH into the Pi

```bash
ping -c 3 raspberrypi.local          # is Pi on network?
nc -z -w 3 raspberrypi.local 22      # is SSH port open?
# If mDNS fails, check your router admin page for the Pi's IP
ssh -v pi@raspberrypi.local          # verbose output for auth debugging
```

### Docker container fails to start

```bash
sudo docker logs safetygis-app-1

# Common causes:
# .env missing              → cp .env.example .env
# Port 8000 in use          → lsof -i :8000
# Build failed              → docker compose build (check output)
```

### USB-C direct connection (Gadget Mode)

The Pi 5 USB-C port delivers power only by default. To use it as a USB Ethernet
interface, pre-configure the SD card boot partition before first boot:

```bash
# With SD card mounted on Mac as "bootfs"
echo "dtoverlay=dwc2,dr_mode=peripheral" >> /Volumes/bootfs/config.txt
sed -i '' 's/rootwait/rootwait modules-load=dwc2,g_ether/' /Volumes/bootfs/cmdline.txt
diskutil eject /Volumes/bootfs
```

After this change the Pi appears as `raspberrypi.local` on a `169.254.x.x`
link-local address when connected via USB-C.

---

## 10. Code Update Workflow

### Mac → GitHub → Pi pipeline

```bash
# Step 1: develop and test locally
source .venv/bin/activate
uvicorn main:app --reload

# Step 2: commit and push
git add .
git commit -m "describe the change"
git push origin main

# Step 3: deploy to Pi from Mac (no need to SSH manually)
ssh -i ~/.ssh/id_pi pi@192.168.1.157 \
  "cd ~/SafetyGIS && git pull && sudo docker compose up -d --build"
```

### Notes on rebuilding

- **Frontend-only changes (JS/HTML/CSS):** Require image rebuild, but Docker layer cache
  skips `pip install` (unchanged) — typically under 15 seconds.
- **After `.env` changes:** `docker compose restart` does NOT re-read `env_file`.
  Always use `docker compose up -d` to recreate the container.

---

## Appendix — Repository Structure

```
SafetyGIS/
├── main.py                  # FastAPI backend — all API endpoints
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
├── .env                     # Actual keys (gitignored)
├── Dockerfile               # Docker image definition
├── docker-compose.yml       # Service orchestration
├── .dockerignore            # Docker build exclusions
├── Plan.md                  # Project roadmap (Phases 1–3)
├── TUTORIAL.md              # This file — development & deployment guide
├── DEVLOG.md                # Session-by-session development history
├── static/
│   ├── index.html           # Single-page app HTML + CSS (~700 lines)
│   └── app.js               # All frontend logic (~1,800 lines)
├── scripts/
│   ├── fetch_crash_data.py  # Manual pre-fetch: CCRS crash data
│   └── fetch_osm.py         # Manual pre-fetch: OSM infrastructure
└── data/                    # Runtime cache (gitignored)
    ├── crash_cache/         # {county}.geojson — generated on demand
    ├── osm_cache/           # {z}_{x}_{y}.json — generated on demand
    └── mapillary_cache/     # Mapillary response cache
```
