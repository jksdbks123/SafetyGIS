# GIS-Track — Complete Development & Deployment Tutorial

> Author: Zhihui | Vibe Coding Exercise  
> Last updated: 2026-04-19

This tutorial documents the full hands-on workflow for GIS-Track — from local development
through production deployment — including architecture explanations, data source details,
and troubleshooting guides. Platform-specific instructions are provided for macOS, Linux,
Windows, Docker, and Raspberry Pi 5.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Local Development Setup](#2-local-development-setup)
3. [Application Architecture](#3-application-architecture)
4. [Running & Debugging](#4-running--debugging)
5. [Data Sources & How They Are Fetched](#5-data-sources--how-they-are-fetched)
6. [Analysis Mode & Safety Rankings](#6-analysis-mode--safety-rankings)
7. [Docker Packaging](#7-docker-packaging)
8. [Raspberry Pi 5 Deployment](#8-raspberry-pi-5-deployment)
9. [Public Access via Cloudflare Tunnel](#9-public-access-via-cloudflare-tunnel)
10. [Troubleshooting](#10-troubleshooting)
11. [Code Update Workflow](#11-code-update-workflow)

---

## 1. Project Overview

GIS-Track is a browser-based transportation safety GIS tool covering all of California.

**Two operating modes:**

| Mode | Purpose |
|------|---------|
| **Inspect** | Viewport-driven lazy loading; map exploration; selection tools; statistics; export |
| **Analysis** | County-level downloads; EPDO safety rankings computation; bin browser; crash dashboard |

**Data layers:**

| Layer | Source | Loading strategy |
|-------|--------|-----------------|
| Crash points | CHP CCRS via data.ca.gov | Lazy-loaded per county in Inspect; bulk download in Analysis |
| Road infrastructure | OpenStreetMap via Overpass API | Lazy-loaded per Z12 tile, cached locally |
| Street sign detection | Mapillary API (Meta) | Live requests, server-side proxy |
| Street View | Google Maps Platform | On-demand, optional API key |

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

| Item | macOS | Linux | Windows |
|------|-------|-------|---------|
| Python | 3.11–3.12 recommended | 3.11–3.12 recommended | 3.11–3.12 recommended |
| Git | `brew install git` or Xcode CLT | `apt install git` / `dnf install git` | [git-scm.com](https://git-scm.com/download/win) |
| Make (optional) | pre-installed | `apt install make` | `choco install make` or use Git Bash |

Docker targets Python 3.12. Local dev works with 3.11–3.14.

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/jksdbks123/SafetyGIS.git
cd SafetyGIS
```

---

### Step 2 — Create a virtual environment

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows — Command Prompt:**
```cmd
python -m venv .venv
.venv\Scripts\activate
```

**Windows — PowerShell:**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> **PowerShell execution policy:** If `.ps1` scripts are blocked, run once:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

---

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

(Same command on all platforms once the venv is activated.)

---

### Step 4 — Configure API keys

**macOS / Linux:**
```bash
cp .env.example .env
```

**Windows — Command Prompt:**
```cmd
copy .env.example .env
```

**Windows — PowerShell:**
```powershell
Copy-Item .env.example .env
```

Open `.env` in any text editor and fill in:

```
MAPILLARY_TOKEN=MLY|...
GOOGLE_MAPS_KEY=AIza...
```

Both keys are optional — the app runs without them; Mapillary layers and Street View are simply unavailable.

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
    ├── GET /api/osm/dynamic?bbox=             → Overpass tile fetch, Z12 cache
    ├── GET /api/crashes/dynamic?bbox=         → CCRS per-county, returns fetching / features
    ├── GET /api/crashes/stats                 → Statistical aggregation (county/city/viewport)
    ├── GET /api/counties                      → List of all 58 California counties
    ├── GET /api/data/county_status            → Per-county cache readiness (Analysis mode)
    ├── POST /api/data/county/{name}/fetch_*   → Trigger background crash / OSM download
    ├── POST /api/rankings/compute             → Start EPDO rankings computation
    ├── GET /api/rankings/status               → Job progress 0–100
    ├── GET /api/rankings/bins                 → Available ranking bin keys
    ├── GET /api/rankings/bin/{key}            → Worst/best 10 facilities for a bin
    ├── GET /api/mapillary/images              → Mapillary proxy (token never sent to browser)
    ├── GET /api/mapillary/token               → Returns token availability only
    ├── POST /api/ai/query                     → AI placeholder (wire real LLM here)
    └── GET /static/*                          → HTML / JS / CSS static files

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

**macOS / Linux — Makefile shortcut (recommended):**
```bash
make dev            # starts on port 8000
make dev PORT=9000  # custom port — kills any process already on that port first
```

**macOS / Linux — manual:**
```bash
source .venv/bin/activate
uvicorn main:app --reload
```

**Windows — Command Prompt:**
```cmd
.venv\Scripts\activate
uvicorn main:app --reload
```

**Windows — PowerShell:**
```powershell
.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

> **Windows + Makefile:** `make` is not installed by default on Windows.
> To use Makefile shortcuts, either:
> - Install GNU Make via Chocolatey: `choco install make`
> - Run commands inside **Git Bash** (ships with Git for Windows) or **WSL**
> - Or just run `uvicorn` directly as shown above.

`--reload` watches Python files and restarts on changes. For JS/HTML/CSS changes,
just refresh the browser.

Open **http://localhost:8000** in your browser.

---

### Reading logs

```
# Backend logs stream to the terminal where uvicorn is running.
# Crash data fetch progress looks like:
#   [sacramento] county=34 year=2024… 117066 total

# Frontend logs: open browser DevTools → Console tab (F12)
```

---

### Killing a stuck port

**macOS / Linux:**
```bash
lsof -ti:8000 | xargs kill -9
```

**Windows — Command Prompt:**
```cmd
FOR /F "tokens=5" %P IN ('netstat -ano ^| findstr :8000') DO taskkill /F /PID %P
```

**Windows — PowerShell:**
```powershell
Get-NetTCPConnection -LocalPort 8000 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

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

## 6. Analysis Mode & Safety Rankings

Analysis mode computes EPDO (Equivalent Property Damage Only) safety scores for every intersection and road segment in the cached counties, then ranks them worst-to-best within peer groups (same road class, speed range, control type, etc.).

### Step 1 — Download county data

Click the **Analysis** button in the header. The county grid shows all 58 counties.

- **Green chip** — crash data + OSM tiles both ready; can be ranked immediately
- **Yellow chip** — partial data (crash only, or OSM < 100%); may still be rankable
- **Gray chip** — no data; click to trigger download

Click any gray/yellow chip to start a background download. The chip animates while downloading and turns green when complete. Data cached during Inspect-mode browsing carries over automatically.

### Step 2 — Configure and compute

1. In the **Compute Rankings** section, check/uncheck counties to include
2. Optionally adjust EPDO weights (default: fatal = 10, injury = 2, PDO = 0.2)
3. Check **Allow incomplete OSM** if you want to rank counties with crash data but partial OSM coverage
4. Click **Compute Rankings** — a progress bar tracks the subprocess

Computation runs `scripts/build_safety_rankings.py` as a subprocess. Runtime scales with the number of facilities: Sacramento (~20k OSM nodes/ways + 86k crashes) takes roughly 2–3 minutes.

You can also run rankings from the command line:

```bash
# All cached counties
python scripts/build_safety_rankings.py

# Specific counties
python scripts/build_safety_rankings.py --counties sacramento,alameda,los_angeles

# Override EPDO weights (fatal, injury, pdo)
python scripts/build_safety_rankings.py --weights 9,3,1

# Allow partial OSM coverage
python scripts/build_safety_rankings.py --min-osm-pct 0

# Output to a custom directory
RANKINGS_DIR=/path/to/output python scripts/build_safety_rankings.py
```

### Step 3 — Browse results

After computation, the **Browse Rankings** section auto-opens with bin chips:

- **Intersections** tab — filtered by control type (signal/stop/roundabout/…), road class, speed range, leg count
- **Segments** tab — filtered by road class, speed range, lane count
- **Purple chip** — at least 20 facilities ranked in this bin; click to load
- **Gray chip** — fewer than 20 facilities (insufficient for reliable ranking)

Clicking a chip loads the worst/best 10 facilities and draws red/green circles on the map.

### Step 4 — Crash dashboard

Click any circle on the map or any row in the worst/best table to open the **Crash Dashboard**:

- EPDO score and 5-year crash counts (fatal / severe / total)
- Distribution charts: lighting conditions, primary collision factor, weather, day of week, collision type, road condition
- Individual crash points overlaid on the map at the facility location

### How EPDO scoring works

```
EPDO score = Σ (fatal × w_fatal) + (severe_injury × w_injury) + (other_injury × w_injury×0.5) + (pdo × w_pdo)
```

Facilities are ranked within peer groups (bins) so that a rural two-lane road is not compared against a signalized urban arterial. The bin key encodes the filter dimensions, e.g. `int|signal|arterial|26-40mph`.

---

## 7. Docker Packaging

Docker is the recommended way to run SafetyGIS in production. The image is
multi-arch (`linux/amd64` + `linux/arm64`) and runs identically on macOS,
Linux, Windows, and Raspberry Pi 5.

### File overview

| File | Purpose |
|------|---------|
| `Dockerfile` | Image build steps |
| `docker-compose.yml` | Service orchestration: port, volume, env, healthcheck |
| `.dockerignore` | Excludes `.venv/`, `data/`, `.env` from image build context |

### Dockerfile explained

```dockerfile
FROM python:3.12-slim          # ~130 MB base; official arm64 + amd64 support
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # separate layer — skipped on code-only changes
COPY main.py .
COPY static/ ./static/
COPY scripts/ ./scripts/
RUN mkdir -p data/crash_cache data/osm_cache data/mapillary_cache data/rankings data/enrichment
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/mapillary/token')"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Why is `data/` not copied into the image?**  
The cache grows to hundreds of MB. It is provided via a volume mount at runtime
so it persists across container rebuilds without bloating every image layer.

### docker-compose.yml explained

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env                  # API keys loaded at runtime, never baked into the image
    volumes:
      - ./data:/app/data      # cache persists on the host filesystem
    restart: unless-stopped   # auto-restarts after reboots
```

---

### Installing Docker

**macOS:**
```bash
brew install --cask docker
# Launch Docker Desktop from Applications, wait for the whale icon in the menu bar
```

**Linux (Debian / Ubuntu):**
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in for the docker group to take effect
```

**Linux (Fedora / RHEL / CentOS):**
```bash
sudo dnf install docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

**Windows:**  
Download and install [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/).  
Requires Windows 10 64-bit (build 19041+) with WSL2 enabled.

```powershell
# Enable WSL2 first (run as Administrator, then reboot)
wsl --install
# After reboot, install Docker Desktop and enable the WSL2 backend in Settings
```

Once Docker Desktop is running, all `docker compose` commands below work identically
in PowerShell, Command Prompt, and WSL.

---

### Common Docker commands (all platforms)

```bash
# Build and start in the foreground (shows logs)
docker compose up

# Build and start detached (background)
docker compose up -d

# View live logs
docker compose logs -f

# Stop and remove containers (data volume is preserved)
docker compose down

# Update code, rebuild image, restart
git pull && docker compose up -d --build

# Check container health
docker ps
```

> **After `.env` changes:** `docker compose restart` does NOT re-read `env_file`.
> Always use `docker compose up -d` (recreates the container) when `.env` changes.

---

## 8. Raspberry Pi 5 Deployment

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

## 9. Public Access via Cloudflare Tunnel

Cloudflare Tunnel exposes your local service to the public internet without a public IP,
port forwarding, or firewall changes. The free quick-tunnel tier requires no account.

### Temporary tunnel — macOS

```bash
# Install
brew install cloudflare/cloudflare/cloudflared

# Start (URL is random and changes on each run)
cloudflared tunnel --url http://localhost:8000
# Output: https://random-name.trycloudflare.com
```

### Temporary tunnel — Linux

```bash
# Download the binary (amd64 example)
wget https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -O /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# Start
cloudflared tunnel --url http://localhost:8000
```

### Temporary tunnel — Windows

```powershell
# Option 1 — winget
winget install Cloudflare.cloudflared

# Option 2 — Chocolatey
choco install cloudflared

# Start
cloudflared tunnel --url http://localhost:8000
```

### Persistent tunnel on the Raspberry Pi (Docker)

```bash
sudo docker run -d \
  --name cloudflared \
  --network host \
  --restart unless-stopped \
  cloudflare/cloudflared:latest \
  tunnel --url http://localhost:8000

# Find the public URL in the logs
sudo docker logs cloudflared 2>&1 | grep trycloudflare
```

> **Note:** Free quick-tunnels get a new random subdomain on every restart.
> For a stable URL, register a Cloudflare account and configure a Named Tunnel.

---

## 10. Troubleshooting

### Crash data not loading

**Symptoms:** No red crash points on the map; loading banner disappears without data.

**macOS / Linux:**
```bash
# 1. Test CCRS API reachability
curl -s "https://data.ca.gov/api/3/action/package_show?id=ccrs" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['success'])"
# Expected: True

# 2. Check cache file sizes (~45 bytes = empty = fetch failed)
ls -la data/crash_cache/

# 3. Run fetch script manually to see the error
python scripts/fetch_crash_data.py 2>&1 | head -30
```

**Windows:**
```powershell
# 1. Test CCRS API reachability
Invoke-WebRequest "https://data.ca.gov/api/3/action/package_show?id=ccrs" | Select-Object -ExpandProperty Content | python -c "import json,sys; print(json.load(sys.stdin)['success'])"

# 2. Check cache files
dir data\crash_cache

# 3. Run fetch script
python scripts\fetch_crash_data.py
```

**Common errors and fixes:**

| Error | Cause | Fix |
|-------|-------|-----|
| HTTP 409 | `County Code` filter sent as integer | Change to `str(county_code)` |
| 0 records, HTTP 200 | Empty duplicate resource selected | Add `if year not in crashes` guard |
| HTTP 404 | Resource ID changed upstream | Re-run resource discovery |

---

### OSM data not loading

**macOS / Linux:**
```bash
curl -s -o /dev/null -w "%{http_code}" "https://overpass-api.de/api/interpreter"
```

**Windows:**
```powershell
(Invoke-WebRequest "https://overpass-api.de/api/interpreter").StatusCode
```

504 or timeout on all mirrors = Overpass under heavy load; retry later.

---

### Port 8000 already in use

**macOS / Linux:**
```bash
lsof -ti:8000 | xargs kill -9
```

**Windows — Command Prompt:**
```cmd
FOR /F "tokens=5" %P IN ('netstat -ano ^| findstr :8000') DO taskkill /F /PID %P
```

**Windows — PowerShell:**
```powershell
Get-NetTCPConnection -LocalPort 8000 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

### Cannot SSH into the Pi

```bash
ping -c 3 raspberrypi.local          # is Pi on network?
nc -z -w 3 raspberrypi.local 22      # is SSH port open?
# If mDNS fails, check your router admin page for the Pi's IP
ssh -v pi@raspberrypi.local          # verbose output for auth debugging
```

---

### Docker container fails to start

```bash
sudo docker logs safetygis-app-1

# Common causes:
# .env missing              → cp .env.example .env  (Linux/macOS)
#                             copy .env.example .env  (Windows CMD)
# Port 8000 in use          → see "Port 8000 already in use" above
# Build failed              → docker compose build (check output)
# WSL2 not enabled (Win)    → wsl --install, then reboot
```

---

### Windows: `.venv\Scripts\activate` not recognized

```powershell
# If you see "cannot be loaded because running scripts is disabled":
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# If python is not found:
# Install Python 3.12 from https://www.python.org/downloads/
# Check "Add Python to PATH" during installation
```

---

### USB-C direct connection to Pi (Gadget Mode)

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

## 11. Code Update Workflow

### Develop locally → push to GitHub → deploy to Pi

**macOS / Linux:**
```bash
# Step 1: develop and test locally
source .venv/bin/activate
uvicorn main:app --reload

# Step 2: commit and push
git add -p          # stage changes interactively
git commit -m "describe the change"
git push origin main

# Step 3: deploy to Pi from your machine (no manual SSH session needed)
ssh -i ~/.ssh/id_pi pi@192.168.1.157 \
  "cd ~/SafetyGIS && git pull && sudo docker compose up -d --build"
```

**Windows — PowerShell:**
```powershell
# Step 1: develop and test locally
.venv\Scripts\Activate.ps1
uvicorn main:app --reload

# Step 2: commit and push
git add -p
git commit -m "describe the change"
git push origin main

# Step 3: deploy to Pi from Windows PowerShell
ssh -i $HOME\.ssh\id_pi pi@192.168.1.157 "cd ~/SafetyGIS && git pull && sudo docker compose up -d --build"
```

> SSH is built into Windows 10/11. If it is missing, install it via
> **Settings → Apps → Optional features → OpenSSH Client**.

### Notes on rebuilding

- **Frontend-only changes (JS/HTML/CSS):** Require image rebuild, but Docker layer cache
  skips `pip install` (unchanged) — typically under 15 seconds.
- **After `.env` changes:** `docker compose restart` does NOT re-read `env_file`.
  Always use `docker compose up -d` (recreates the container) when `.env` changes.
- **After `requirements.txt` changes:** The `pip install` layer is invalidated; full
  rebuild required — expect 2–5 minutes on Pi, under 1 minute on a fast desktop.

---

## Appendix — Repository Structure

```
SafetyGIS/
├── main.py                       # FastAPI backend — all API endpoints (~1,170 lines)
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variable template
├── .env                          # Actual keys (gitignored)
├── Dockerfile                    # Docker image definition
├── docker-compose.yml            # Service orchestration
├── .dockerignore                 # Docker build exclusions
├── Makefile                      # Dev shortcuts: make dev, make fetch-crashes, …
├── CLAUDE.md                     # AI coding partner instructions
├── Plan.md                       # Project roadmap (Phases 1–3)
├── TUTORIAL.md                   # This file — development & deployment guide
├── DEVLOG.md                     # Session-by-session development history
├── static/
│   ├── index.html                # Single-page app HTML + CSS (~950 lines)
│   └── app.js                    # All frontend logic — Inspect + Analysis modes (~3,040 lines)
├── scripts/
│   ├── build_safety_rankings.py  # EPDO safety rankings computation (~890 lines)
│   ├── fetch_crash_data.py       # Manual pre-fetch: CCRS crash data
│   └── fetch_osm.py              # Manual pre-fetch: OSM infrastructure
└── data/                         # Runtime cache (gitignored, mounted as Docker volume)
    ├── crash_cache/              # {county}.geojson — generated on demand
    ├── osm_cache/                # {z}_{x}_{y}.json — generated on demand
    ├── rankings/                 # Rankings GeoJSON output from build_safety_rankings.py
    └── mapillary_cache/          # Mapillary response cache
```
