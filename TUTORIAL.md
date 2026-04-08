# GIS-Track — 完整开发与部署教程

> 作者：Zhihui | Vibe Coding Exercise  
> 最后更新：2026-04-08

本教程记录 GIS-Track 项目从零开始的完整实践过程，包括本地开发、Raspberry Pi 部署、常见问题排查。

---

## 目录

1. [项目概述](#1-项目概述)
2. [本地开发环境搭建](#2-本地开发环境搭建)
3. [应用架构说明](#3-应用架构说明)
4. [运行与调试](#4-运行与调试)
5. [数据来源与获取方式](#5-数据来源与获取方式)
6. [Docker 打包](#6-docker-打包)
7. [Raspberry Pi 5 部署](#7-raspberry-pi-5-部署)
8. [公网访问：Cloudflare Tunnel](#8-公网访问cloudflare-tunnel)
9. [常见问题排查](#9-常见问题排查)
10. [代码更新流程](#10-代码更新流程)

---

## 1. 项目概述

GIS-Track 是一个基于浏览器的交通安全 GIS 工具，覆盖加州全境。

**数据层：**
| 图层 | 数据来源 | 加载方式 |
|------|---------|---------|
| 崩溃事故点 (Crash) | CHP CCRS via data.ca.gov | 按县懒加载，缓存到本地 |
| 道路基础设施 (OSM) | OpenStreetMap via Overpass API | 按 Z12 tile 懒加载，缓存到本地 |
| 路标识别 (Mapillary) | Mapillary API (Meta) | 实时请求，服务端代理 |
| 街景 (Street View) | Google Maps Platform | 实时请求，需 API Key |

**技术栈：**
- 后端：Python 3.12 + FastAPI + Uvicorn
- 前端：原生 JavaScript + MapLibre GL JS 4.7.1 + Chart.js 4.4.3
- 无前端构建工具（无 webpack/vite）
- 部署：Docker + Raspberry Pi 5

---

## 2. 本地开发环境搭建

### 前提条件
- macOS（本教程基于 macOS）
- Python 3.11 或 3.12（推荐；本项目本地使用 3.14 但 Docker 用 3.12）
- Git

### 步骤

```bash
# 1. 克隆仓库
git clone https://github.com/jksdbks123/SafetyGIS.git
cd SafetyGIS

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API Keys
cp .env.example .env
# 用文本编辑器打开 .env，填入：
#   MAPILLARY_TOKEN=MLY|...
#   GOOGLE_MAPS_KEY=AIza...
# 两个 key 都是可选的，不填则对应功能不可用
```

### API Key 获取方式

**Mapillary Token（免费）：**
1. 注册 mapillary.com 账号
2. 进入 Developer Settings → Create Application
3. 复制 Client Access Token

**Google Maps Key：**
1. 进入 console.cloud.google.com
2. 创建项目 → 启用 Maps JavaScript API
3. 创建 API Key（建议限制来源域名）

---

## 3. 应用架构说明

```
浏览器 (MapLibre GL JS + app.js)
    │
    ├── GET /api/osm/dynamic?bbox=     → Overpass tile fetch，Z12 缓存
    ├── GET /api/crashes/dynamic?bbox= → CCRS 按县缓存，返回 fetching/features
    ├── GET /api/crashes/stats         → 统计聚合（county/city/viewport）
    ├── GET /api/counties              → 加州 58 县列表
    ├── GET /api/mapillary/images      → Mapillary 代理（Token 不暴露前端）
    ├── GET /api/mapillary/token       → 仅返回 Token 是否可用
    ├── POST /api/ai/query             → AI 接口占位（待接入真实 LLM）
    └── GET /static/*                  → HTML/JS/CSS 静态文件

FastAPI 后端 (main.py)
    │
    └── 外部 API：Overpass, data.ca.gov (CCRS), Mapillary, Google Maps
```

### 关键数据结构（前端）

```javascript
CRASH_FEATURE_MAP   // Map<id, GeoJSON Feature> — 所有已加载崩溃事故
OSM_FEATURE_MAP     // Map<id, GeoJSON Feature> — 所有已加载 OSM 要素
G_selectionData     // GeoJSON FeatureCollection — 当前框选的要素
```

**为什么用 Map 而不直接读 MapLibre 属性？**  
MapLibre 内部将 GeoJSON 转换为 VectorTile 格式时只保留 paint/filter 用到的属性。
点击事件里 `e.features[0].properties` 可能是截断的。
因此所有完整属性必须存在 JS Map 中，点击时按 ID 查找。

### 崩溃数据懒加载流程

```
用户移动地图
    ↓
前端：GET /api/crashes/dynamic?bbox=west,south,east,north
    ↓
后端：计算 bbox 覆盖哪些县
    ↓
已缓存的县 → 直接返回 features
未缓存的县 → 返回 {fetching: ["sacramento", ...]}，启动后台线程
    ↓（后台线程）
调用 data.ca.gov CCRS API，按页获取（每页 5000 条）
写入 data/crash_cache/{county}.geojson
    ↓
前端：收到 fetching → 25 秒后重新请求（轮询）
```

---

## 4. 运行与调试

### 启动本地服务

```bash
source .venv/bin/activate
uvicorn main:app --reload
# 浏览器打开 http://localhost:8000
```

`--reload` 模式下修改 Python 文件会自动重启，修改 JS/HTML 刷新浏览器即可。

### 日志查看

```bash
# 后端实时日志（uvicorn 输出到终端）
# 崩溃数据 fetch 进度会打印到终端，例如：
#   [sacramento] county=34 year=2024… 117066 total

# 前端日志
# 打开浏览器开发者工具 → Console
```

### 手动预获取数据

```bash
# 获取崩溃数据（Sacramento + Humboldt）
python scripts/fetch_crash_data.py

# 获取 OSM 数据（Sacramento + Humboldt bbox）
python scripts/fetch_osm.py
```

---

## 5. 数据来源与获取方式

### CHP CCRS（崩溃数据）

- **来源**：California Highway Patrol，通过 data.ca.gov CKAN Datastore API 开放
- **覆盖**：加州 58 个县，2019–2024 年（已加入 2025）
- **License**：California Open Data，公共领域
- **API 端点**：`https://data.ca.gov/api/3/action/datastore_search`
- **分页**：每页 5000 条，按 offset 翻页
- **字段**：County Code（文本型，非整数！）、Latitude、Longitude、NumberKilled、NumberInjured 等

> **重要注意**：County Code 字段为**文本类型**。查询时必须传字符串 `"34"` 而非整数 `34`，否则返回 HTTP 409 类型不匹配错误。

> **重要注意**：同一年份可能存在多个同名资源（Crashes_2024 × 3）。只有第一个有数据，后续为空。代码中需用 `if year not in crashes` 防止覆盖。

### OpenStreetMap（基础设施）

- **来源**：OpenStreetMap contributors via Overpass API
- **License**：ODbL 1.0
- **查询方式**：Overpass QL，`out geom;` 格式（重要：不能用 `out body; >; out skel qt;`）
- **缓存**：Z12 tile 粒度，文件名 `12_{x}_{y}.json`
- **镜像链**：`overpass-api.de` → `overpass.kumi.systems` → `overpass.openstreetmap.ru`

> **重要注意**：必须使用 `out geom;`，它将坐标直接内嵌在每个元素中。如果用 `out body; >; out skel qt;` 会先返回 way 元素（仅有节点 ID），再返回节点坐标，单次解析时 nodes dict 还是空的，所有线要素会丢失。

### Mapillary（路标识别）

- **来源**：Mapillary API（Meta Platforms）
- **License**：CC BY-SA 4.0（影像）；AI 识别模型为 Mapillary 专有
- **认证**：需要 Mapillary Token，服务端代理（Token 不暴露给前端）
- **加载策略**：用户第一次开启对应图层时触发加载（懒加载）

---

## 6. Docker 打包

### 文件说明

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 定义镜像构建步骤 |
| `docker-compose.yml` | 定义服务、端口、volume、env |
| `.dockerignore` | 排除不进镜像的文件 |

### Dockerfile 关键设计

```dockerfile
FROM python:3.12-slim          # arm64 支持，约 130MB
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt   # 先安装依赖，利用层缓存
COPY main.py .
COPY static/ ./static/
COPY scripts/ ./scripts/
RUN mkdir -p data/crash_cache data/osm_cache data/mapillary_cache
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**为什么 `data/` 不 COPY 进镜像？**  
data 目录包含几百 MB 的缓存文件，且会持续增长。通过 volume mount 挂载到宿主机，容器重建后缓存不丢失。

### docker-compose.yml 关键设计

```yaml
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env                  # API keys 不进镜像
    volumes:
      - ./data:/app/data      # 缓存持久化到宿主机 ./data 目录
    restart: unless-stopped   # Pi 重启后自动拉起
```

### 本地测试 Docker

```bash
# 安装 Docker Desktop（macOS）
brew install --cask docker
# 启动 Docker Desktop 应用

# 构建并启动
docker compose up

# 后台运行
docker compose up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

---

## 7. Raspberry Pi 5 部署

### 兼容性

| 项目 | 状态 |
|------|------|
| CPU 架构 | ✅ arm64（Cortex-A76） |
| RAM 4GB | ✅ 应用稳定运行约 200–400 MB |
| Docker | ✅ 完整支持 |
| 所有 Python 依赖 | ✅ 均有 arm64 wheel |
| OS 要求 | ⚠️ 必须用 **64 位**系统 |
| 存储 | ⚠️ 建议 USB SSD 或 A2 级 SD 卡（缓存约 330 MB 且持续增长） |

### 刷机（Raspberry Pi Imager）

1. 下载 Raspberry Pi Imager：`brew install --cask raspberry-pi-imager`
2. 选择：Device → **Raspberry Pi 5**，OS → **Raspberry Pi OS (64-bit)**
3. 点 Next → Edit Settings，填入：
   - Hostname：`raspberrypi`
   - SSH：启用，密码认证
   - Username：`pi`，Password：自设
   - WiFi SSID & Password：家庭 WiFi 信息
   - Country：US
4. 写入 SD 卡

### 从 Mac 连接 Pi（无需显示器）

Pi 通电后约 60 秒连上 WiFi。

```bash
# 在 Mac 终端
ping -c 3 raspberrypi.local
# 成功则显示 192.168.1.x 的 IP

ssh pi@raspberrypi.local
# 或 ssh pi@192.168.1.x
```

### 设置免密 SSH（可选，便于自动化部署）

```bash
# Mac 上生成专用 key
ssh-keygen -t ed25519 -f ~/.ssh/id_pi -N ""

# 将公钥发送到 Pi（在 Pi 的 SSH 会话中执行）
mkdir -p ~/.ssh
chmod 700 ~/.ssh
echo "$(cat ~/.ssh/id_pi.pub 内容粘贴到这里)" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# 测试免密登录
ssh -i ~/.ssh/id_pi pi@raspberrypi.local "echo OK"
```

### 在 Pi 上部署

```bash
# 1. 在 Pi 上安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker pi
# 重新登录使 docker 组生效

# 2. 克隆项目
git clone https://github.com/jksdbks123/SafetyGIS.git
cd SafetyGIS

# 3. 配置 API Keys
cp .env.example .env
nano .env
# 填入 MAPILLARY_TOKEN 和 GOOGLE_MAPS_KEY

# 4. 启动服务（后台）
sudo docker compose up -d --build

# 5. 验证
curl http://localhost:8000/api/mapillary/token
# 应返回 {"token": "MLY|..."}

# 局域网访问
http://192.168.1.x:8000
```

### 日常管理命令（在 Pi 上）

```bash
# 查看状态
sudo docker ps
sudo docker compose -f ~/SafetyGIS/docker-compose.yml ps

# 实时日志
sudo docker compose -f ~/SafetyGIS/docker-compose.yml logs -f

# 停止
sudo docker compose -f ~/SafetyGIS/docker-compose.yml down

# 更新代码并重新构建
cd ~/SafetyGIS
git pull
sudo docker compose up -d --build
```

---

## 8. 公网访问：Cloudflare Tunnel

Cloudflare Tunnel 将本地服务暴露到公网，无需公网 IP、无需端口转发、免费。

### 在 Mac 上临时开启

```bash
# 安装
brew install cloudflare/cloudflare/cloudflared

# 启动（每次 URL 随机）
cloudflared tunnel --url http://localhost:8000
# 输出类似：https://random-name.trycloudflare.com
```

### 在 Pi 上持久运行

```bash
# 以 Docker 方式运行（无需在 Pi 上安装额外软件）
sudo docker run -d \
  --name cloudflared \
  --network host \
  --restart unless-stopped \
  cloudflare/cloudflared:latest \
  tunnel --url http://localhost:8000

# 查看公网 URL
sudo docker logs cloudflared 2>&1 | grep trycloudflare
```

> **注意**：免费 Tunnel 每次重启 URL 都会变。若需固定域名，需注册 Cloudflare 账号并配置 Named Tunnel。

---

## 9. 常见问题排查

### 崩溃数据不显示

**症状**：地图上看不到红色事故点，蓝色加载条一直转或消失后仍无数据。

**诊断步骤：**

```bash
# 1. 检查 API 是否可达
curl -s "https://data.ca.gov/api/3/action/package_show?id=ccrs" | python3 -c "import json,sys; print(json.load(sys.stdin)['success'])"
# 期望输出：True

# 2. 检查缓存文件
ls -la data/crash_cache/
# 如果文件只有 ~45 字节，说明 fetch 失败

# 3. 手动运行 fetch 脚本查看错误
python scripts/fetch_crash_data.py 2>&1 | head -30

# 常见错误：
# HTTP 409 → County Code 类型不匹配（需传字符串 "34" 不是整数 34）
# HTTP 404 → 资源 ID 失效（API 更新了资源，需检查 get_all_resources()）
# 0 records → 选中了空的重复资源（加 year not in crashes 判断）
```

### OSM 数据不显示

**症状**：基础设施图层（信号灯、人行横道等）不出现。

**诊断：**

```bash
# 检查 Overpass API 状态（3 个镜像）
curl -s -o /dev/null -w "%{http_code}" "https://overpass-api.de/api/interpreter" 

# 如果所有镜像都 504/timeout，是 Overpass 服务繁忙
# 等待几小时或次日重试
```

### SSH 无法连接 Pi

```bash
# 1. 确认 Pi 在网络上
ping -c 3 raspberrypi.local

# 2. 扫描 SSH 端口
nc -z -w 3 raspberrypi.local 22

# 3. 如果 mDNS 不工作，查路由器 DHCP 页找 IP
# 通常在 http://192.168.1.1

# 4. SSH 详细日志
ssh -v pi@raspberrypi.local
```

### Docker 容器启动失败

```bash
# 查看错误日志
sudo docker logs safetygis-app-1

# 常见原因：
# - .env 不存在 → cp .env.example .env
# - 端口 8000 被占用 → lsof -i :8000
# - requirements.txt 依赖安装失败 → 重新 docker compose build
```

### USB-C 直连 Pi（Gadget 模式）

Pi 5 的 USB-C 默认只供电。要让 Mac 通过 USB-C 识别 Pi 为网络设备，需要提前在 SD 卡的 `bootfs` 分区写入配置：

```bash
# SD 卡插入 Mac 后，在终端执行
echo "dtoverlay=dwc2,dr_mode=peripheral" >> /Volumes/bootfs/config.txt
sed -i '' 's/rootwait/rootwait modules-load=dwc2,g_ether/' /Volumes/bootfs/cmdline.txt
diskutil eject /Volumes/bootfs
```

写入后重新插回 Pi，USB-C 连接 Mac，约 30 秒后可用 `ssh pi@raspberrypi.local`。

---

## 10. 代码更新流程

### 开发 → GitHub → Pi 三步更新

```bash
# 步骤 1：本地开发，测试无误
source .venv/bin/activate
uvicorn main:app --reload

# 步骤 2：提交并推送
git add .
git commit -m "描述改动内容"
git push origin main

# 步骤 3：Pi 上拉取并重建（可通过 Mac SSH 远程执行）
ssh -i ~/.ssh/id_pi pi@192.168.1.157 \
  "cd ~/SafetyGIS && git pull && sudo docker compose up -d --build"
```

### 只改前端（JS/HTML/CSS）时

前端文件直接被 COPY 进镜像，改动后必须重新 build 容器。但因为 Python 依赖层没变，`docker compose up -d --build` 只需约 10 秒（layer cache 命中 pip install 步骤）。

### 环境变量（.env）变更后

```bash
# docker compose restart 不会重新读取 env_file
# 必须用 up -d 重建容器
sudo docker compose up -d
```

---

## 附录：文件结构

```
SafetyGIS/
├── main.py                  # FastAPI 后端，所有 API 端点
├── requirements.txt         # Python 依赖
├── .env.example             # 环境变量模板
├── .env                     # 实际 key（不进 git）
├── Dockerfile               # Docker 镜像定义
├── docker-compose.yml       # 服务编排
├── .dockerignore            # Docker 构建排除文件
├── static/
│   ├── index.html           # 单页应用 HTML + CSS
│   └── app.js               # 所有前端逻辑（~1800 行）
├── scripts/
│   ├── fetch_crash_data.py  # 手动预获取 CCRS 崩溃数据
│   └── fetch_osm.py         # 手动预获取 OSM 数据
└── data/                    # 运行时缓存（不进 git）
    ├── crash_cache/         # {county}.geojson，按需生成
    ├── osm_cache/           # {z}_{x}_{y}.json，按需生成
    └── mapillary_cache/     # Mapillary 影像缓存
```

