// =============================================================================
// GIS-Track  ·  app.js  (v2 — dynamic CA crashes, draw/select, AI placeholder)
// =============================================================================
// Architecture:
//   • styledata + isStyleLoaded() — permanent style-switch handler
//   • map.once('idle') in switchBasemap — fallback for inline satellite style
//   • Mapillary layers are lazy-loaded: sources added only on first toggle ON
//   • Google Street View shown in side panel on any map click
//   • Dynamic crash loading for all California counties (cached per-county)
//   • Draw tool: rectangle or polygon → select visible features → download GeoJSON
// =============================================================================

// ---- Constants ---------------------------------------------------------------

const SPRITE_URL = 'https://cdn.jsdelivr.net/npm/mapillary_sprite_source@1.8.0/sprites/sprites';

const BASEMAP_STYLES = {
  map: 'https://tiles.openfreemap.org/styles/liberty',
  satellite: {
    version: 8,
    glyphs: 'https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf',
    sources: {
      'esri-sat': {
        type: 'raster',
        tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        attribution: '© Esri, Maxar, Earthstar Geographics',
        maxzoom: 19,
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': '#0a0a0a' } },
      { id: 'sat-raster',  type: 'raster',     source: 'esri-sat' },
    ],
  },
};

const AREAS = {
  sacramento: { center: [-121.469, 38.555], zoom: 12 },
  humboldt:   { center: [-124.00,  40.80],  zoom: 10 },
};

// California rough bounding box (used to skip crash loads outside CA)
const CA_BBOX = { west: -124.48, south: 32.53, east: -114.13, north: 42.01 };

// ---- Global state ------------------------------------------------------------

let G_osmData        = null;
let G_crashData      = { type: 'FeatureCollection', features: [] };
let G_mlyToken       = null;
let G_hasMly         = false;
let G_dataReady      = false;
let G_currentBasemap = 'map';

let G_googleMapsKey   = '';
let G_hasGoogleMaps   = false;
let G_googleMapsReady = false;

// Accumulators — deduplicated feature stores
const OSM_FEATURE_MAP   = new Map();   // String(id) → feature
const CRASH_FEATURE_MAP = new Map();   // String(id) → feature

// Viewport load timers
let _crashPollTimer = null;    // polls after background county fetch

// Draw tool state
let G_drawMode      = null;    // null | 'rect' | 'poly'
let G_drawActive    = false;
let G_rectStart     = null;    // maplibregl.LngLat (mousedown start)
let G_polyPoints    = [];      // [[lng,lat], ...]
let G_drawShape     = null;    // saved GeoJSON Feature (survives basemap switch)
let G_selectionData = null;    // last selection FeatureCollection
let _lastClickTime  = 0;       // for dblclick debounce in poly mode

// ---- Layer visibility --------------------------------------------------------
// Mapillary-dependent layers start OFF — lazy-loaded on first toggle.

const LAYER_VISIBILITY = {
  signals:            true,
  crossings:          true,
  bus:                true,
  bike:               true,
  roads:              false,
  footway:            true,
  calming:            false,
  streetlamp:         false,
  heatmap:            true,
  crashes:            true,
  'asset-regulatory': false,
  'asset-warning':    false,
  'asset-info':       false,
  'asset-crosswalks': false,
};

// toggle key → MapLibre layer IDs it controls
const LAYER_IDS = {
  signals:            ['signals-layer'],
  crossings:          ['crossings-layer'],
  bus:                ['bus-layer'],
  bike:               ['bike-layer'],
  roads:              ['roads-layer'],
  footway:            ['footway-layer'],
  calming:            ['calming-layer'],
  streetlamp:         ['streetlamp-layer'],
  heatmap:            ['heatmap-layer'],
  crashes:            ['crashes-layer'],
  'asset-regulatory': ['asset-regulatory-layer'],
  'asset-warning':    ['asset-warning-layer'],
  'asset-info':       ['asset-info-layer'],
  'asset-crosswalks': ['asset-crosswalks-layer'],
  'rankings-worst':   ['rankings-worst-layer', 'rankings-worst-label'],
  'rankings-best':    ['rankings-best-layer',  'rankings-best-label'],
};

// source id → owned layer IDs (must remove layers before source)
const SOURCE_LAYERS = {
  osm:              ['signals-layer', 'crossings-layer', 'bus-layer', 'bike-layer',
                     'roads-layer', 'footway-layer', 'calming-layer', 'streetlamp-layer'],
  crashes:          ['heatmap-layer', 'crashes-layer'],
  'mly-signs-vt':   ['asset-regulatory-layer', 'asset-warning-layer', 'asset-info-layer'],
  'mly-objects-vt': ['asset-crosswalks-layer'],
  'rankings-worst': ['rankings-worst-layer', 'rankings-worst-label'],
  'rankings-best':  ['rankings-best-layer',  'rankings-best-label'],
};

// Tracks which Mapillary sources are currently in the active style.
const MLY_ADDED = {
  'mly-signs-vt':   false,
  'mly-objects-vt': false,
};

// ---- Map init ----------------------------------------------------------------

const map = new maplibregl.Map({
  container: 'map',
  style:     BASEMAP_STYLES.map,
  center:    AREAS.sacramento.center,
  zoom:      AREAS.sacramento.zoom,
});

map.addControl(new maplibregl.NavigationControl(), 'top-right');
map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');

map.on('zoom', () => {
  document.getElementById('zoom-val').textContent = map.getZoom().toFixed(1);
});

map.on('moveend', () => {
  if (G_dataReady) scheduleViewportLoad();
});

// ---- Permanent styledata handler ---------------------------------------------
// 'styledata' fires multiple times during a style load. The double-guard
// (isStyleLoaded + getSource check) ensures rebuildLayers runs exactly once
// per style switch, after the style is fully committed.

map.on('styledata', () => {
  if (!G_dataReady) return;
  if (!map.isStyleLoaded()) return;
  if (map.getSource('osm')) return;
  rebuildLayers();
});

// ---- Initial bootstrap -------------------------------------------------------

// ---- HTML escape helper (used in popup builders) ----------------------------

function _esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

map.on('load', async () => {
  document.getElementById('zoom-val').textContent = map.getZoom().toFixed(1);
  try {
    await loadData();
  } catch (err) {
    console.error('Data load failed:', err);
  }
  G_dataReady = true;
  rebuildLayers();
  setupPopups();
  setupDraw();
  setupPegman();
  // Wire up AI input Enter key
  document.getElementById('ai-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendAiQuery(); }
  });
  // Wire up Escape to cancel pegman mode
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && G_pegmanMode) cancelPegmanMode();
  });
  document.getElementById('loading').classList.add('hidden');
  scheduleViewportLoad();   // load OSM + crashes for initial viewport
});

// ---- Data loading ------------------------------------------------------------

async function loadData() {
  const [config, gmConfig] = await Promise.all([
    fetch('/api/config').then(r => r.json()),
    fetch('/api/googlemaps/config').then(r => r.json()),
  ]);

  // Seed OSM with pre-fetched area files (fast initial load)
  const [sacOsm, humOsm] = await Promise.all([
    fetch('/api/osm/sacramento').then(r => r.json()),
    fetch('/api/osm/humboldt').then(r => r.json()),
  ]);
  for (const f of [...sacOsm.features, ...humOsm.features]) {
    OSM_FEATURE_MAP.set(String(f.properties.id), f);
  }
  G_osmData = { type: 'FeatureCollection', features: [...OSM_FEATURE_MAP.values()] };

  // Crash data starts empty — dynamically loaded by loadCrashesForViewport()
  G_crashData = { type: 'FeatureCollection', features: [] };

  G_hasMly = config.has_mapillary;
  if (G_hasMly) {
    const { token } = await fetch('/api/mapillary/token').then(r => r.json());
    G_mlyToken = token;
    setMlyStatus('Token ready — toggle layers to load', '');
  } else {
    setMlyStatus('Add MAPILLARY_TOKEN to .env', 'error');
  }

  G_hasGoogleMaps = gmConfig.has_google_maps;
  G_googleMapsKey = gmConfig.key || '';
  if (G_hasGoogleMaps) {
    loadGoogleMapsAPI(G_googleMapsKey);
  } else {
    document.getElementById('sv-hint').textContent = 'Add GOOGLE_MAPS_KEY to .env';
  }

  // Load county list for Statistics panel
  try {
    const counties = await fetch('/api/counties').then(r => r.json());
    _buildCountySelect(counties);
  } catch (_) {}

  updateStats();
}

// ---- Dynamic viewport loading (OSM + Crashes) --------------------------------

let _viewportTimer = null;

function scheduleViewportLoad() {
  clearTimeout(_viewportTimer);
  clearTimeout(_crashPollTimer);   // cancel any pending poll when user moves
  _viewportTimer = setTimeout(() => {
    loadOsmForViewport();
    loadCrashesForViewport();
  }, 600);
}

async function loadOsmForViewport() {
  // z12 tile cache — at map zoom < 12 the viewport spans too many z12 tiles
  if (map.getZoom() < 12) return;
  const b    = map.getBounds();
  const bbox = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
  const statusEl = document.getElementById('crash-load-status');

  // Show loading — reuse the status bar
  if (statusEl && !statusEl.textContent) statusEl.textContent = 'Loading infrastructure…';

  let data;
  try {
    const resp = await fetch(`/api/osm/dynamic?bbox=${bbox}`);
    if (!resp.ok) {
      if (statusEl) statusEl.textContent = '';
      return;
    }
    data = await resp.json();
  } catch (_) {
    if (statusEl) statusEl.textContent = '';
    return;
  }

  if (statusEl && statusEl.textContent === 'Loading infrastructure…') statusEl.textContent = '';

  if (!data.features || !data.features.length) return;

  let added = 0;
  for (const f of data.features) {
    const key = String(f.properties.id);
    if (!OSM_FEATURE_MAP.has(key)) { OSM_FEATURE_MAP.set(key, f); added++; }
  }
  if (added === 0) return;

  G_osmData = { type: 'FeatureCollection', features: [...OSM_FEATURE_MAP.values()] };
  if (map.getSource('osm')) {
    map.getSource('osm').setData(G_osmData);
    updateStats();
  }
}

async function loadCrashesForViewport() {
  clearTimeout(_crashPollTimer);
  if (map.getZoom() < 9) return;

  const b = map.getBounds();
  // Skip if viewport doesn't overlap California at all
  if (b.getEast() < CA_BBOX.west || b.getWest() > CA_BBOX.east ||
      b.getNorth() < CA_BBOX.south || b.getSouth() > CA_BBOX.north) return;

  const bbox     = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
  const statusEl = document.getElementById('crash-load-status');

  let data;
  try {
    const resp = await fetch(`/api/crashes/dynamic?bbox=${bbox}`);
    if (!resp.ok) { if (statusEl) statusEl.textContent = ''; return; }
    data = await resp.json();
  } catch (_) { if (statusEl) statusEl.textContent = ''; return; }

  // Add newly arrived features
  let added = 0;
  for (const f of (data.features || [])) {
    const key = String(f.properties.id);
    if (!CRASH_FEATURE_MAP.has(key)) { CRASH_FEATURE_MAP.set(key, f); added++; }
  }
  if (added > 0) {
    G_crashData = { type: 'FeatureCollection', features: [...CRASH_FEATURE_MAP.values()] };
    if (map.getSource('crashes')) {
      map.getSource('crashes').setData(G_crashData);
      updateStats();
    }
  }

  // Backend is still fetching some counties — show status and poll
  const pending = data.fetching || [];
  if (pending.length > 0) {
    const names = pending.map(n => n.replace(/_/g, ' ')).join(', ');
    if (statusEl) statusEl.textContent = `Downloading crash data: ${names}…`;
    // Poll every 25 s until those counties are cached
    _crashPollTimer = setTimeout(() => loadCrashesForViewport(), 25000);
  } else {
    if (statusEl && statusEl.textContent.startsWith('Downloading crash')) {
      statusEl.textContent = '';
    }
  }
}

function updateStats() {
  const signals   = G_osmData?.features.filter(f => f.properties.type === 'traffic_signals') ?? [];
  const crossings = G_osmData?.features.filter(f => f.properties.type === 'crossing') ?? [];
  const fatal     = G_crashData?.features.filter(f => f.properties.severity === 'fatal') ?? [];
  document.getElementById('stat-signals').textContent       = signals.length.toLocaleString();
  document.getElementById('stat-crossings').textContent     = crossings.length.toLocaleString();
  document.getElementById('stat-crashes-total').textContent = (G_crashData?.features.length ?? 0).toLocaleString();
  document.getElementById('stat-fatal').textContent         = fatal.length.toLocaleString();
  // Refresh chart if panel is open and on a live scope
  _maybeRefreshStatsOnDataUpdate();
}

// ---- Core layer rebuild (called after every style switch) --------------------

function rebuildLayers() {
  Object.keys(MLY_ADDED).forEach(k => { MLY_ADDED[k] = false; });

  addOsmLayers();
  addCrashLayers();
  addDrawLayers();
  addRankingsLayers();

  if (G_hasMly && G_mlyToken) {
    try { map.addSprite('mly', SPRITE_URL); } catch (_) {}
    if (LAYER_VISIBILITY['asset-regulatory'] ||
        LAYER_VISIBILITY['asset-warning']    ||
        LAYER_VISIBILITY['asset-info'])       addSignLayers();
    if (LAYER_VISIBILITY['asset-crosswalks']) addCrosswalkLayer();
  } else if (!G_hasMly) {
    ['asset-regulatory', 'asset-warning', 'asset-info', 'asset-crosswalks'].forEach(k => {
      document.getElementById(`toggle-${k}`)?.classList.add('disabled');
      const r = document.getElementById(`row-${k}`);
      if (r) r.style.pointerEvents = 'none';
    });
  }

  applyVisibilityState();
}

// ---- Safe layer/source removal -----------------------------------------------

function removeSafe(sourceId) {
  (SOURCE_LAYERS[sourceId] || []).forEach(id => {
    if (map.getLayer(id)) map.removeLayer(id);
  });
  if (map.getSource(sourceId)) map.removeSource(sourceId);
}

// ---- OSM layers --------------------------------------------------------------

function addOsmLayers() {
  removeSafe('osm');
  map.addSource('osm', { type: 'geojson', data: G_osmData });

  map.addLayer({
    id: 'signals-layer', type: 'circle', source: 'osm',
    filter: ['==', ['get', 'type'], 'traffic_signals'],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 10, 3, 15, 7],
      'circle-color':        '#facc15',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#78350f',
    },
  });

  map.addLayer({
    id: 'crossings-layer', type: 'circle', source: 'osm',
    filter: ['==', ['get', 'type'], 'crossing'],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 10, 2, 15, 5],
      'circle-color':        '#60a5fa',
      'circle-stroke-width': 1,
      'circle-stroke-color': '#1e3a5f',
    },
  });

  map.addLayer({
    id: 'bus-layer', type: 'circle', source: 'osm',
    filter: ['in', ['get', 'type'], ['literal', ['bus_stop', 'bus_station']]],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 10, 3, 15, 7],
      'circle-color':        '#34d399',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#064e3b',
    },
  });

  map.addLayer({
    id: 'bike-layer', type: 'line', source: 'osm',
    filter: ['==', ['get', 'type'], 'cycleway'],
    paint: {
      'line-color':   '#a78bfa',
      'line-width':   ['interpolate', ['linear'], ['zoom'], 10, 1.5, 15, 3],
      'line-opacity': 0.85,
    },
  });

  // Road network classified by highway type
  map.addLayer({
    id: 'roads-layer', type: 'line', source: 'osm',
    minzoom: 11,
    filter: ['in', ['get', 'type'], ['literal', [
      'motorway','motorway_link','trunk','trunk_link',
      'primary','primary_link','secondary','secondary_link',
      'tertiary','tertiary_link','residential','unclassified','living_street',
    ]]],
    paint: {
      'line-color': ['match', ['get', 'type'],
        'motorway',       '#e82727',
        'motorway_link',  '#e82727',
        'trunk',          '#f97316',
        'trunk_link',     '#f97316',
        'primary',        '#f59e0b',
        'primary_link',   '#f59e0b',
        'secondary',      '#84cc16',
        'secondary_link', '#84cc16',
        'tertiary',       '#94a3b8',
        'tertiary_link',  '#94a3b8',
        'residential',    '#cbd5e1',
        'unclassified',   '#cbd5e1',
        'living_street',  '#e2e8f0',
        '#6b7280'
      ],
      'line-width': ['interpolate', ['linear'], ['zoom'],
        11, ['match', ['get', 'type'],
          ['motorway','trunk'], 2,
          ['primary','secondary'], 1.5,
          1
        ],
        16, ['match', ['get', 'type'],
          ['motorway','trunk'], 6,
          ['primary','secondary'], 4,
          ['tertiary','tertiary_link'], 3,
          2
        ],
      ],
      'line-opacity': 0.8,
    },
  });

  // Sidewalks / footways / pedestrian paths — only at z14+ where OSM geometry is meaningful
  map.addLayer({
    id: 'footway-layer', type: 'line', source: 'osm',
    minzoom: 14,
    filter: ['==', ['get', 'type'], 'footway'],
    paint: {
      'line-color':       '#e2e8f0',
      'line-width':       ['interpolate', ['linear'], ['zoom'], 14, 1, 17, 3],
      'line-opacity':     0.8,
      'line-dasharray':   [2, 1],
    },
  });

  // Traffic calming devices (speed bumps, chicanes, etc.)
  map.addLayer({
    id: 'calming-layer', type: 'circle', source: 'osm',
    filter: ['==', ['get', 'type'], 'traffic_calming'],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 12, 4, 16, 8],
      'circle-color':        '#fb923c',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#7c2d12',
      'circle-opacity':      0.9,
    },
  });

  // Street lamps
  map.addLayer({
    id: 'streetlamp-layer', type: 'circle', source: 'osm',
    filter: ['==', ['get', 'type'], 'street_lamp'],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 13, 2, 17, 5],
      'circle-color':        '#fde68a',
      'circle-stroke-width': 1,
      'circle-stroke-color': '#78350f',
      'circle-opacity':      0.85,
    },
  });
}

// ---- Crash layers ------------------------------------------------------------

function addCrashLayers() {
  removeSafe('crashes');
  map.addSource('crashes', { type: 'geojson', data: G_crashData });

  map.addLayer({
    id: 'heatmap-layer', type: 'heatmap', source: 'crashes', maxzoom: 15,
    paint: {
      'heatmap-weight':    ['match', ['get', 'severity'],
        'fatal', 1.0, 'severe_injury', 0.6, 'other_injury', 0.3, 0.1],
      'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 6, 0.5, 14, 2],
      'heatmap-radius':    ['interpolate', ['linear'], ['zoom'], 6, 15, 14, 35],
      'heatmap-opacity':   ['interpolate', ['linear'], ['zoom'], 12, 0.8, 15, 0.2],
      'heatmap-color': [
        'interpolate', ['linear'], ['heatmap-density'],
        0,   'rgba(0,0,0,0)',
        0.4, 'rgba(253,174,97,0.6)',
        0.7, 'rgba(244,109,67,0.85)',
        1.0, 'rgba(165,0,38,1)',
      ],
    },
  });

  map.addLayer({
    id: 'crashes-layer', type: 'circle', source: 'crashes', minzoom: 12,
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 12, 4, 14, 7, 17, 11],
      'circle-color':        ['match', ['get', 'severity'],
        'fatal', '#dc2626', 'severe_injury', '#f97316', 'other_injury', '#fbbf24', '#9ca3af'],
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#1f2937',
      'circle-opacity':      ['interpolate', ['linear'], ['zoom'], 12, 0.5, 14, 0.9],
    },
  });
}

// ---- Traffic sign layers -----------------------------------------------------

function addSignLayers() {
  removeSafe('mly-signs-vt');
  map.addSource('mly-signs-vt', {
    type:    'vector',
    tiles:   [`https://tiles.mapillary.com/maps/vtp/mly_map_feature_traffic_sign/2/{z}/{x}/{y}?access_token=${G_mlyToken}`],
    minzoom: 12,
    maxzoom: 14,
  });

  const signLayout = {
    'icon-image':            ['concat', 'mly:', ['get', 'value']],
    'icon-size':             ['interpolate', ['linear'], ['zoom'], 13, 0.35, 16, 0.6, 19, 0.9],
    'icon-allow-overlap':    true,
    'icon-ignore-placement': true,
    'icon-optional':         false,
    'icon-padding':          2,
  };

  map.addLayer({
    id: 'asset-regulatory-layer', type: 'symbol',
    source: 'mly-signs-vt', 'source-layer': 'traffic_sign',
    minzoom: 13,
    filter: ['any',
      ['in', 'regulatory--',    ['get', 'value']],
      ['in', 'complementary--', ['get', 'value']],
      ['in', 'route--',         ['get', 'value']],
    ],
    layout: signLayout,
  });

  map.addLayer({
    id: 'asset-warning-layer', type: 'symbol',
    source: 'mly-signs-vt', 'source-layer': 'traffic_sign',
    minzoom: 13,
    filter: ['in', 'warning--', ['get', 'value']],
    layout: signLayout,
  });

  map.addLayer({
    id: 'asset-info-layer', type: 'symbol',
    source: 'mly-signs-vt', 'source-layer': 'traffic_sign',
    minzoom: 13,
    filter: ['in', 'information--', ['get', 'value']],
    layout: signLayout,
  });

  MLY_ADDED['mly-signs-vt'] = true;
}

// ---- Crosswalk markings ------------------------------------------------------

function addCrosswalkLayer() {
  removeSafe('mly-objects-vt');
  map.addSource('mly-objects-vt', {
    type:    'vector',
    tiles:   [`https://tiles.mapillary.com/maps/vtp/mly_map_feature_point/2/{z}/{x}/{y}?access_token=${G_mlyToken}`],
    minzoom: 12,
    maxzoom: 14,
  });
  map.addLayer({
    id: 'asset-crosswalks-layer', type: 'circle',
    source: 'mly-objects-vt', 'source-layer': 'point',
    minzoom: 13,
    filter: ['in', 'crosswalk', ['get', 'value']],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 13, 5, 18, 10],
      'circle-color':        '#38bdf8',
      'circle-stroke-width': 1.5,
      'circle-stroke-color': '#fff',
      'circle-opacity':      0.92,
    },
  });
  MLY_ADDED['mly-objects-vt'] = true;
}

// ---- Draw layers (selection polygon/rectangle) -------------------------------

function addDrawLayers() {
  // Always remove + re-add so they survive basemap switches
  ['draw-vertices', 'draw-outline', 'draw-fill',
   'selection-hl-point', 'selection-hl-line'].forEach(id => {
    if (map.getLayer(id)) map.removeLayer(id);
  });
  if (map.getSource('draw-selection'))    map.removeSource('draw-selection');
  if (map.getSource('selection-highlight')) map.removeSource('selection-highlight');

  map.addSource('selection-highlight', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });

  map.addLayer({
    id: 'selection-hl-line', type: 'line', source: 'selection-highlight',
    filter: ['==', ['geometry-type'], 'LineString'],
    paint: { 'line-color': '#38bdf8', 'line-width': 5, 'line-opacity': 0.75 },
  });
  map.addLayer({
    id: 'selection-hl-point', type: 'circle', source: 'selection-highlight',
    filter: ['==', ['geometry-type'], 'Point'],
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 10, 7, 16, 12],
      'circle-color':        'transparent',
      'circle-stroke-width': 3,
      'circle-stroke-color': '#38bdf8',
    },
  });
  _updateHighlight();

  const initData = G_drawShape
    ? { type: 'FeatureCollection', features: [G_drawShape] }
    : { type: 'FeatureCollection', features: [] };

  map.addSource('draw-selection', { type: 'geojson', data: initData });

  map.addLayer({
    id: 'draw-fill', type: 'fill', source: 'draw-selection',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': '#3b82f6', 'fill-opacity': 0.12 },
  });
  map.addLayer({
    id: 'draw-outline', type: 'line', source: 'draw-selection',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'line-color': '#3b82f6', 'line-width': 2, 'line-dasharray': [4, 2] },
  });
  map.addLayer({
    id: 'draw-vertices', type: 'circle', source: 'draw-selection',
    filter: ['==', ['geometry-type'], 'Point'],
    paint: {
      'circle-radius':       5,
      'circle-color':        '#3b82f6',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#fff',
    },
  });
}

// ---- Draw tool ---------------------------------------------------------------

function setupDraw() {
  const canvas = map.getCanvas();

  // Rectangle: canvas mouse events
  canvas.addEventListener('mousedown', e => {
    if (G_drawMode !== 'rect' || !G_drawActive) return;
    e.stopPropagation();
    G_rectStart = map.unproject([e.offsetX, e.offsetY]);
  });

  canvas.addEventListener('mousemove', e => {
    if (G_drawMode !== 'rect' || !G_drawActive || !G_rectStart) return;
    const cur = map.unproject([e.offsetX, e.offsetY]);
    _updateDrawSource(_makeRect(G_rectStart, cur));
  });

  canvas.addEventListener('mouseup', e => {
    if (G_drawMode !== 'rect' || !G_drawActive || !G_rectStart) return;
    const end   = map.unproject([e.offsetX, e.offsetY]);
    const shape = _makeRect(G_rectStart, end);
    G_drawShape = shape;
    _updateDrawSource(shape);
    _endDraw();
    finalizeSelection(shape.geometry.coordinates[0]);
  });

  // Polygon: map click adds points
  map.on('click', e => {
    if (G_drawMode !== 'poly' || !G_drawActive) return;
    const now = Date.now();
    if (now - _lastClickTime < 350) return;  // ignore 2nd click of a dblclick
    _lastClickTime = now;
    G_polyPoints.push([e.lngLat.lng, e.lngLat.lat]);
    _updatePolyPreview();
    const n = G_polyPoints.length;
    document.getElementById('draw-hint').textContent =
      `${n} point${n > 1 ? 's' : ''}. Double-click to finish. Esc to cancel.`;
  });

  // Polygon: double-click closes polygon
  map.on('dblclick', e => {
    if (G_drawMode !== 'poly' || !G_drawActive || G_polyPoints.length < 3) return;
    e.preventDefault();
    // Remove the extra point added by the last click event (part of this dblclick)
    G_polyPoints.pop();
    if (G_polyPoints.length < 3) return;
    G_polyPoints.push([...G_polyPoints[0]]);  // close ring
    const shape = _makePolygon(G_polyPoints);
    G_drawShape = shape;
    _updateDrawSource(shape);
    _endDraw();
    finalizeSelection(shape.geometry.coordinates[0]);
  });

  // Escape cancels
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && G_drawMode) cancelDraw();
  });
}

function startDrawRect() {
  G_drawMode   = 'rect';
  G_drawActive = true;
  G_rectStart  = null;
  clearDrawSelection();
  map.dragPan.disable();
  map.dragRotate.disable();
  map.getCanvas().style.cursor = 'crosshair';
  document.getElementById('draw-hint').textContent   = 'Click and drag to draw a rectangle.';
  document.getElementById('btn-draw-cancel').style.display = 'flex';
  document.getElementById('btn-draw-rect').classList.add('active');
  document.getElementById('selection-result').classList.add('hidden');
}

function startDrawPoly() {
  G_drawMode   = 'poly';
  G_drawActive = true;
  G_polyPoints = [];
  clearDrawSelection();
  map.dragPan.disable();
  map.dragRotate.disable();
  map.doubleClickZoom.disable();
  map.getCanvas().style.cursor = 'crosshair';
  document.getElementById('draw-hint').textContent   = 'Click to add points. Double-click to finish.';
  document.getElementById('btn-draw-cancel').style.display = 'flex';
  document.getElementById('btn-draw-poly').classList.add('active');
  document.getElementById('selection-result').classList.add('hidden');
}

function cancelDraw() {
  _endDraw();
  clearSelection();
}

function _endDraw() {
  const mode   = G_drawMode;
  G_drawMode   = null;
  G_drawActive = false;
  G_rectStart  = null;
  G_polyPoints = [];
  map.dragPan.enable();
  map.dragRotate.enable();
  if (mode === 'poly') map.doubleClickZoom.enable();
  map.getCanvas().style.cursor = '';
  document.getElementById('draw-hint').textContent = '';
  document.getElementById('btn-draw-cancel').style.display = 'none';
  document.getElementById('btn-draw-rect').classList.remove('active');
  document.getElementById('btn-draw-poly').classList.remove('active');
}

function clearDrawSelection() {
  G_drawShape = null;
  const src = map.getSource('draw-selection');
  if (src) src.setData({ type: 'FeatureCollection', features: [] });
}

function _updateHighlight() {
  const src = map.getSource('selection-highlight');
  if (!src) return;
  const features = G_selectionData ? G_selectionData.features : [];
  src.setData({ type: 'FeatureCollection', features });
  document.getElementById('sel-count').textContent = features.length.toLocaleString();
  document.getElementById('selection-result').classList.toggle('hidden', features.length === 0);
  _statsOnSelectionChange(features.length);
}

function _applyClickSelection(feat, append) {
  if (!feat) return;
  if (append && G_selectionData) {
    const existingIds = new Set(G_selectionData.features.map(f => f.properties.id));
    if (!existingIds.has(feat.properties.id)) {
      G_selectionData.features.push(feat);
    }
  } else {
    G_selectionData = { type: 'FeatureCollection', features: [feat] };
  }
  _updateHighlight();
}

function clearSelection() {
  clearDrawSelection();
  G_selectionData = null;
  _updateHighlight();
}

function _makeRect(sw, ne) {
  const west  = Math.min(sw.lng, ne.lng), east  = Math.max(sw.lng, ne.lng);
  const south = Math.min(sw.lat, ne.lat), north = Math.max(sw.lat, ne.lat);
  return {
    type: 'Feature',
    geometry: {
      type: 'Polygon',
      coordinates: [[[west,south],[east,south],[east,north],[west,north],[west,south]]],
    },
    properties: {},
  };
}

function _makePolygon(points) {
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [points] },
    properties: {},
  };
}

function _updateDrawSource(feature) {
  const src = map.getSource('draw-selection');
  if (!src) return;
  src.setData({ type: 'FeatureCollection', features: [feature] });
}

function _updatePolyPreview() {
  const src = map.getSource('draw-selection');
  if (!src) return;
  const pts = G_polyPoints.map(([lng, lat]) => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [lng, lat] }, properties: {},
  }));
  const feats = [...pts];
  if (G_polyPoints.length >= 2) feats.push(_makePolygon([...G_polyPoints, G_polyPoints[0]]));
  src.setData({ type: 'FeatureCollection', features: feats });
}

// ---- Selection & Download ----------------------------------------------------

function finalizeSelection(ring) {
  const features = [];

  // OSM features — respect per-layer visibility
  if (G_osmData) {
    for (const feat of G_osmData.features) {
      const type = feat.properties.type;
      let layerKey;
      if      (type === 'traffic_signals')                    layerKey = 'signals';
      else if (type === 'crossing')                           layerKey = 'crossings';
      else if (type === 'bus_stop' || type === 'bus_station') layerKey = 'bus';
      else if (type === 'cycleway')                           layerKey = 'bike';
      else if (['motorway','motorway_link','trunk','trunk_link','primary','primary_link',
                'secondary','secondary_link','tertiary','tertiary_link',
                'residential','unclassified','living_street'].includes(type)) layerKey = 'roads';
      else if (type === 'footway')                            layerKey = 'footway';
      else if (type === 'traffic_calming')                    layerKey = 'calming';
      else if (type === 'street_lamp')                        layerKey = 'streetlamp';
      else continue;
      if (!LAYER_VISIBILITY[layerKey]) continue;

      let pt;
      if      (feat.geometry.type === 'Point')      pt = feat.geometry.coordinates;
      else if (feat.geometry.type === 'LineString') pt = feat.geometry.coordinates[Math.floor(feat.geometry.coordinates.length / 2)];
      else continue;

      if (_pointInRing(pt, ring)) features.push(feat);
    }
  }

  // Crash features — require 'crashes' or 'heatmap' visible
  if (G_crashData && (LAYER_VISIBILITY['crashes'] || LAYER_VISIBILITY['heatmap'])) {
    for (const feat of G_crashData.features) {
      if (feat.geometry.type !== 'Point') continue;
      if (_pointInRing(feat.geometry.coordinates, ring)) features.push(feat);
    }
  }

  G_selectionData = { type: 'FeatureCollection', features };
  _updateHighlight();
}

function _pointInRing([px, py], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i], [xj, yj] = ring[j];
    if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi)
      inside = !inside;
  }
  return inside;
}

function _triggerDownload(content, filename, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function downloadSelection() {
  if (!G_selectionData || G_selectionData.features.length === 0) return;
  const out = {
    type:     'FeatureCollection',
    features: G_selectionData.features,
    metadata: { source: 'GIS-Track', timestamp: new Date().toISOString(), count: G_selectionData.features.length },
  };
  _triggerDownload(JSON.stringify(out, null, 2), `gistrack_${new Date().toISOString().slice(0, 10)}.geojson`, 'application/geo+json');
}

async function downloadSelectionFull() {
  if (!G_selectionData || G_selectionData.features.length === 0) return;
  const crashIds = G_selectionData.features
    .filter(f => f.properties.severity !== undefined)
    .map(f => String(f.properties.id));
  let detail = {};
  if (crashIds.length) {
    try {
      detail = await fetch(`/api/crashes/detail?ids=${crashIds.join(',')}`).then(r => r.json());
    } catch (_) {}
  }
  const enriched = G_selectionData.features.map(f => {
    const cid = String(f.properties.id);
    if (!detail[cid]) return f;
    return { ...f, properties: { ...f.properties, parties: detail[cid].parties, victims: detail[cid].victims } };
  });
  const out = {
    type: 'FeatureCollection',
    features: enriched,
    metadata: { source: 'GIS-Track', timestamp: new Date().toISOString(), count: enriched.length },
  };
  _triggerDownload(JSON.stringify(out, null, 2), `gistrack_full_${new Date().toISOString().slice(0, 10)}.geojson`, 'application/geo+json');
}

function applyCrashFilter() {
  const filters = ['all'];
  if (document.getElementById('cf-ped')?.checked)  filters.push(['==', ['get', 'motorvehicleinvolvedwithcode'], 'B']);
  if (document.getElementById('cf-bike')?.checked) filters.push(['==', ['get', 'motorvehicleinvolvedwithcode'], 'E']);
  const f = filters.length > 1 ? filters : null;
  ['crashes-layer', 'heatmap-layer'].forEach(id => {
    if (map.getLayer(id)) map.setFilter(id, f);
  });
}

// ---- AI Analytics (placeholder) ----------------------------------------------

async function sendAiQuery() {
  const input    = document.getElementById('ai-input');
  const question = (input.value || '').trim();
  if (!question) return;

  const respEl = document.getElementById('ai-response');
  respEl.textContent = 'Thinking…';
  respEl.classList.remove('hidden');

  const b = map.getBounds();
  const context = {
    total_crashes: G_crashData?.features.length ?? 0,
    fatal_crashes: G_crashData?.features.filter(f => f.properties.severity === 'fatal').length ?? 0,
    osm_features:  G_osmData?.features.length ?? 0,
    zoom:          parseFloat(map.getZoom().toFixed(1)),
    bbox:          `${b.getWest().toFixed(3)},${b.getSouth().toFixed(3)},${b.getEast().toFixed(3)},${b.getNorth().toFixed(3)}`,
  };

  try {
    const r    = await fetch('/api/ai/query', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ question, context }),
    });
    const data = await r.json();
    respEl.textContent = data.answer;
  } catch (_) {
    respEl.textContent = 'Connection error.';
  }
}

// ---- Google Street View ------------------------------------------------------

// Layers that have their own click popups (used by pegman handler)
const POPUP_LAYERS = [
  'signals-layer', 'crossings-layer', 'bus-layer', 'bike-layer',
  'crashes-layer',
  'asset-regulatory-layer', 'asset-warning-layer', 'asset-info-layer',
  'asset-crosswalks-layer',
];

let G_pegmanMode        = false;
let G_pegmanClickHandler = null;

function loadGoogleMapsAPI(key) {
  document.getElementById('sv-hint').textContent = 'Loading Google Maps…';
  window._onGoogleMapsLoaded = () => {
    G_googleMapsReady = true;
    const btn = document.getElementById('pegman-btn');
    if (btn) btn.style.display = 'flex';
    document.getElementById('sv-hint').textContent = 'Drag 🟡 person to map for Street View';
  };
  const s = document.createElement('script');
  s.src = `https://maps.googleapis.com/maps/api/js?key=${key}&callback=_onGoogleMapsLoaded`;
  s.async = true;
  s.onerror = () => {
    document.getElementById('sv-hint').textContent = 'Google Maps failed to load';
  };
  document.head.appendChild(s);
}

function setupPegman() {
  const btn = document.getElementById('pegman-btn');
  if (!btn) return;

  // Click pegman to toggle placement mode
  btn.addEventListener('click', () => {
    if (!G_hasGoogleMaps || !G_googleMapsReady) return;
    G_pegmanMode ? cancelPegmanMode() : activatePegmanMode();
  });

  // HTML5 drag: drag pegman onto map canvas
  btn.setAttribute('draggable', 'true');
  btn.addEventListener('dragstart', e => {
    if (!G_hasGoogleMaps || !G_googleMapsReady) { e.preventDefault(); return; }
    e.dataTransfer.setData('text/plain', 'pegman');
    e.dataTransfer.effectAllowed = 'copy';
    activatePegmanMode();
  });

  const canvas = map.getCanvas();
  canvas.addEventListener('dragover', e => {
    if (!G_pegmanMode) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });
  canvas.addEventListener('drop', e => {
    if (!G_pegmanMode) return;
    e.preventDefault();
    const lngLat = map.unproject([e.offsetX, e.offsetY]);
    cancelPegmanMode();
    showStreetView(lngLat.lat, lngLat.lng);
  });
}

function activatePegmanMode() {
  G_pegmanMode = true;
  map.getCanvas().style.cursor = 'crosshair';
  document.getElementById('sv-hint').textContent = 'Click map to place. Esc to cancel.';
  const btn = document.getElementById('pegman-btn');
  if (btn) { btn.classList.add('active'); btn.title = 'Click to cancel'; }

  G_pegmanClickHandler = e => {
    if (!G_pegmanMode) return;
    if (G_drawMode) return;
    const hit = map.queryRenderedFeatures(e.point, { layers: POPUP_LAYERS.filter(id => map.getLayer(id)) });
    if (hit.length > 0) return;  // let feature popup handle it
    cancelPegmanMode();
    showStreetView(e.lngLat.lat, e.lngLat.lng);
  };
  map.once('click', G_pegmanClickHandler);
}

function cancelPegmanMode() {
  G_pegmanMode = false;
  map.getCanvas().style.cursor = '';
  if (G_pegmanClickHandler) { map.off('click', G_pegmanClickHandler); G_pegmanClickHandler = null; }
  const btn = document.getElementById('pegman-btn');
  if (btn) { btn.classList.remove('active'); btn.title = 'Drag or click to place Street View'; }
  const hint = document.getElementById('sv-hint');
  if (hint) hint.textContent = G_googleMapsReady ? 'Drag 🟡 person to map for Street View' : 'Add GOOGLE_MAPS_KEY to .env';
}

function showStreetView(lat, lng) {
  const panel       = document.getElementById('mly-panel');
  const panoDiv     = document.getElementById('sv-pano');
  const placeholder = document.getElementById('sv-placeholder');

  panel.classList.add('open');
  panoDiv.innerHTML         = '';
  placeholder.style.display = 'flex';
  placeholder.textContent   = 'Loading Street View…';

  const sv = new google.maps.StreetViewService();
  sv.getPanorama({ location: { lat, lng }, radius: 50 }, (data, status) => {
    if (status === google.maps.StreetViewStatus.OK) {
      placeholder.style.display = 'none';
      new google.maps.StreetViewPanorama(panoDiv, {
        pano:                  data.location.pano,
        pov:                   { heading: 0, pitch: 0 },
        zoom:                  1,
        motionTracking:        false,
        motionTrackingControl: false,
        fullscreenControl:     false,
      });
    } else {
      placeholder.textContent = 'No Street View available at this location';
    }
  });
}

function closeSidePanel() {
  document.getElementById('mly-panel').classList.remove('open');
}

function openHelpPanel() {
  document.getElementById('help-panel').classList.add('open');
}

function closeHelpPanel() {
  document.getElementById('help-panel').classList.remove('open');
}

// ---- Stats ribbon ------------------------------------------------------------

function toggleStatsRibbon() {
  document.getElementById('stats-ribbon').classList.toggle('open');
}

// ---- Crash detail (Parties & Victims) helpers --------------------------------

const INJURY_DEGREE = {
  'Fatal':          'Fatal',
  'SuspectSerious': 'Suspect Serious Injury',
  'SuspectMinor':   'Suspect Minor Injury',
  'PossibleInjury': 'Possible Injury',
};
const INJURY_DEGREE_COLOR = {
  'Fatal':          '#dc2626',
  'SuspectSerious': '#f97316',
  'SuspectMinor':   '#fbbf24',
  'PossibleInjury': '#9ca3af',
};

const PARTY_TOP_KEYS = new Set([
  'PartyNumber', 'PartyType', 'IsAtFault', 'StatedAge', 'GenderDescription',
  'SobrietyDrugPhysicalDescription1', 'Vehicle1Make', 'Vehicle1Year',
  'MovementPrecCollDescription', 'NumberKilledParty', 'NumberInjuredParty', 'CollisionId',
]);
const VICTIM_TOP_KEYS = new Set([
  'InjuredWitPassId', 'InjuredPersonType', 'ExtentOfInjuryCode', 'Ejected',
  'SafetyEquipmentDescription', 'SeatPositionDescription', 'StatedAge', 'Gender Desc',
  'CollisionId', 'PartyNumber',
]);

async function _fetchCrashDetail(ids, years) {
  try {
    const yearParam = years && years.length ? `&years=${years.join(',')}` : '';
    const data = await fetch(`/api/crashes/detail?ids=${ids.join(',')}${yearParam}`).then(r => r.json());
    const allParties = ids.flatMap(id => (data[id]?.parties || []));
    const allVictims = ids.flatMap(id => (data[id]?.victims || []));
    const partiesEl = document.getElementById('ptab-parties');
    const victimsEl = document.getElementById('ptab-victims');
    if (partiesEl) {
      partiesEl.innerHTML = allParties.length
        ? allParties.map(_partyCardHTML).join('<hr class="popup-divider">')
        : '<div class="popup-loading">No party data available</div>';
    }
    if (victimsEl) {
      victimsEl.innerHTML = allVictims.length
        ? allVictims.map(_victimCardHTML).join('<hr class="popup-divider">')
        : '<div class="popup-loading">No victim data available</div>';
    }
    // Update tab button counts
    document.querySelectorAll('.ptab').forEach(t => {
      if (t.dataset.tab === 'parties') t.textContent = `Parties (${allParties.length})`;
      if (t.dataset.tab === 'victims') t.textContent = `Victims (${allVictims.length})`;
    });
  } catch (_) {}
}

function _partyCardHTML(p) {
  const type     = p['PartyType'] || '—';
  const atFault  = p['IsAtFault'];
  const age      = p['StatedAge'];
  const sex      = p['GenderDescription'];
  const sobriety = p['SobrietyDrugPhysicalDescription1'];
  const make     = p['Vehicle1Make'];
  const yr       = p['Vehicle1Year'];
  const move     = p['MovementPrecCollDescription'];
  const killed   = parseInt(p['NumberKilledParty']  || 0, 10);
  const injured  = parseInt(p['NumberInjuredParty'] || 0, 10);
  const extra = Object.entries(p)
    .filter(([k, v]) => !PARTY_TOP_KEYS.has(k) && v !== null && v !== undefined && String(v).trim() !== '')
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `<div class="popup-row"><span class="popup-key" style="font-size:0.64rem">${_esc(k)}</span><span style="font-size:0.66rem;word-break:break-all">${_esc(String(v))}</span></div>`)
    .join('');
  return `
    <div class="popup-card-title">Party ${_esc(String(p['PartyNumber'] || ''))}: ${_esc(type)}</div>
    ${atFault  ? `<div class="popup-row"><span class="popup-key">At Fault</span><span style="color:${atFault==='True'?'#f97316':'#34d399'}">${_esc(atFault)}</span></div>` : ''}
    ${(age||sex) ? `<div class="popup-row"><span class="popup-key">Age / Sex</span><span>${_esc(String(age||'—'))} / ${_esc(String(sex||'—'))}</span></div>` : ''}
    ${sobriety ? `<div class="popup-row"><span class="popup-key">Sobriety</span><span style="font-size:0.7rem">${_esc(sobriety)}</span></div>` : ''}
    ${(make||yr) ? `<div class="popup-row"><span class="popup-key">Vehicle</span><span>${_esc(String(yr||''))} ${_esc(String(make||''))}</span></div>` : ''}
    ${move ? `<div class="popup-row"><span class="popup-key">Movement</span><span style="font-size:0.7rem">${_esc(move)}</span></div>` : ''}
    ${killed  > 0 ? `<div class="popup-row"><span class="popup-key">Killed</span><span style="color:#dc2626">${killed}</span></div>` : ''}
    ${injured > 0 ? `<div class="popup-row"><span class="popup-key">Injured</span><span>${injured}</span></div>` : ''}
    ${extra}
  `;
}

function _victimCardHTML(v) {
  const role     = v['InjuredPersonType'] || '—';
  const deg      = String(v['ExtentOfInjuryCode'] ?? '');
  const degLabel = INJURY_DEGREE[deg] || deg || '—';
  const degColor = INJURY_DEGREE_COLOR[deg] || '#9ca3af';
  const ejected  = v['Ejected'];
  const eq       = v['SafetyEquipmentDescription'];
  const age      = v['StatedAge'];
  const sex      = v['Gender Desc'];
  const seat     = v['SeatPositionDescription'];
  const extra = Object.entries(v)
    .filter(([k, val]) => !VICTIM_TOP_KEYS.has(k) && val !== null && val !== undefined && String(val).trim() !== '')
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, val]) => `<div class="popup-row"><span class="popup-key" style="font-size:0.64rem">${_esc(k)}</span><span style="font-size:0.66rem;word-break:break-all">${_esc(String(val))}</span></div>`)
    .join('');
  return `
    <div class="popup-card-title">Victim ${_esc(String(v['InjuredWitPassId'] || ''))}: ${_esc(role)}</div>
    <div class="popup-row"><span class="popup-key">Injury</span><span style="color:${degColor};font-weight:600">${_esc(degLabel)}</span></div>
    ${ejected && ejected !== 'NotEjected' ? `<div class="popup-row"><span class="popup-key">Ejected</span><span style="color:#f97316">${_esc(ejected)}</span></div>` : ''}
    ${eq ? `<div class="popup-row"><span class="popup-key">Safety Equip</span><span style="font-size:0.7rem">${_esc(eq)}</span></div>` : ''}
    ${(age||sex) ? `<div class="popup-row"><span class="popup-key">Age / Sex</span><span>${_esc(String(age||'—'))} / ${_esc(String(sex||'—'))}</span></div>` : ''}
    ${seat ? `<div class="popup-row"><span class="popup-key">Seat Position</span><span>${_esc(seat)}</span></div>` : ''}
    ${extra}
  `;
}

// ---- Popups (registered once — layer-click listeners survive setStyle) -------

function setupPopups() {
  const popup = new maplibregl.Popup({ closeButton: true, closeOnClick: true, maxWidth: '320px' });

  // OSM feature popups — shows all non-empty OSM tags
  const OSM_LABEL = {
    'signals-layer':   'Traffic Signal',
    'crossings-layer': 'Crosswalk (OSM)',
    'bus-layer':       'Bus Stop',
    'bike-layer':      'Bike Lane',
  };
  const OSM_SKIP_KEYS = new Set(['id', 'type']);

  // New OSM layers also need popups
  const OSM_LABEL_EXT = Object.assign({}, OSM_LABEL, {
    'roads-layer':      'Road',
    'footway-layer':    'Sidewalk / Footway',
    'calming-layer':    'Traffic Calming',
    'streetlamp-layer': 'Street Lamp',
  });

  ['signals-layer', 'crossings-layer', 'bus-layer', 'bike-layer',
   'roads-layer', 'footway-layer', 'calming-layer', 'streetlamp-layer'].forEach(layerId => {
    map.on('click', layerId, e => {
      if (G_drawActive) return;
      // MapLibre may truncate properties in e.features — look up full feature from OSM_FEATURE_MAP
      const renderedId = String(e.features[0].properties.id ?? '');
      const fullFeature = OSM_FEATURE_MAP.get(renderedId);
      const p = fullFeature ? fullFeature.properties : e.features[0].properties;
      const label = OSM_LABEL_EXT[layerId] || 'OSM Feature';
      const allRows = Object.entries(p)
        .filter(([k, v]) => !OSM_SKIP_KEYS.has(k) && v !== null && v !== undefined && String(v).trim() !== '')
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `<div class="popup-row"><span class="popup-key">${_esc(k.replace(/_/g,' '))}</span><span style="font-size:0.72rem;word-break:break-all">${_esc(String(v))}</span></div>`)
        .join('');
      popup.setLngLat(e.lngLat).setHTML(`
        <div class="popup-title">${label}</div>
        <div class="popup-scroll">
          ${allRows || '<div style="color:#6b7280;font-size:0.72rem">No additional tags</div>'}
          <div class="popup-row" style="opacity:0.45;font-size:0.65rem;margin-top:4px"><span class="popup-key">OSM ID</span><span>${_esc(String(p.id))}</span></div>
        </div>
      `).addTo(map);
      // Click to select — Cmd/Ctrl+click appends, plain click replaces
      const selFeat = fullFeature || { type: 'Feature', geometry: e.features[0].geometry, properties: p };
      _applyClickSelection(selFeat, e.originalEvent.metaKey || e.originalEvent.ctrlKey);
    });
    map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = ''; });
  });

  // Crash point popup — scrollable, shows all overlapping crashes
  const SEVERITY_LABEL = {
    fatal:         'Fatal',
    severe_injury: 'Severe Injury',
    other_injury:  'Injury',
    pdo:           'Property Damage',
  };
  const SEVERITY_COLOR = {
    fatal: '#dc2626', severe_injury: '#f97316', other_injury: '#fbbf24', pdo: '#9ca3af',
  };

  // Keys shown prominently at top of crash popup — skipped in the "all fields" section below
  const CRASH_TOP_KEYS = new Set([
    'id', 'collision_id', 'severity', 'year', 'killed', 'injured', 'date',
    'crash_date_time', 'collision_type', 'collision_type_description',
    'special_cond', 'special_condition', 'latitude', 'longitude', 'county_code',
  ]);

  map.on('click', 'crashes-layer', e => {
    if (G_drawActive) return;
    // MapLibre only preserves properties used in paint/filter expressions.
    // Resolve each rendered feature to its full record from CRASH_FEATURE_MAP.
    const feats = e.features.map(f => {
      const id = String(f.properties.id ?? f.properties.collision_id ?? '');
      return CRASH_FEATURE_MAP.get(id) || f;
    });
    const total = feats.length;

    // Build Crash tab HTML (same layout as before)
    const crashRows = feats.slice(0, 8).map((feat, i) => {
      const p       = feat.properties;
      const sev     = p.severity || 'pdo';
      const color   = SEVERITY_COLOR[sev] || '#9ca3af';
      const label   = SEVERITY_LABEL[sev] || sev;
      const typeStr = (p.collision_type || p.collision_type_description || 'unknown').replace(/_/g, ' ');
      const dateStr = p.date || (p.year ? String(p.year) : '—');
      const cond    = p.special_cond || p.special_condition || '';
      const extraRows = Object.entries(p)
        .filter(([k, v]) => !CRASH_TOP_KEYS.has(k) && v !== null && v !== undefined && String(v).trim() !== '')
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => `<div class="popup-row"><span class="popup-key" style="font-size:0.66rem">${_esc(k.replace(/_/g,' '))}</span><span style="font-size:0.68rem;word-break:break-all">${_esc(String(v))}</span></div>`)
        .join('');
      return `
        ${i > 0 ? '<hr class="popup-divider">' : ''}
        ${total > 1 ? `<div class="popup-seq">${i + 1} / ${Math.min(total, 8)}${total > 8 ? '+' : ''}</div>` : ''}
        <div class="popup-row"><span class="popup-key">Severity</span><span style="color:${color};font-weight:600">${_esc(label)}</span></div>
        <div class="popup-row"><span class="popup-key">Type</span><span>${_esc(typeStr)}</span></div>
        <div class="popup-row"><span class="popup-key">Date</span><span>${_esc(dateStr)}</span></div>
        ${p.killed  > 0 ? `<div class="popup-row"><span class="popup-key">Killed</span><span style="color:#dc2626">${p.killed}</span></div>` : ''}
        ${p.injured > 0 ? `<div class="popup-row"><span class="popup-key">Injured</span><span>${p.injured}</span></div>` : ''}
        ${cond ? `<div class="popup-row"><span class="popup-key">Condition</span><span style="font-size:0.7rem">${_esc(cond)}</span></div>` : ''}
        ${extraRows}
        <div class="popup-row" style="opacity:0.45;font-size:0.65rem;margin-top:4px"><span class="popup-key">ID</span><span>${_esc(String(p.id))}</span></div>
      `;
    }).join('');

    popup.setLngLat(e.lngLat).setHTML(`
      <div class="popup-title">
        Traffic Crash${total > 1 ? ` <span style="font-size:0.75rem;color:#6b7280">(${total} here)</span>` : ''}
      </div>
      <div class="popup-tabs">
        <button class="ptab active" data-tab="crash">Crash</button>
        <button class="ptab" data-tab="parties">Parties</button>
        <button class="ptab" data-tab="victims">Victims</button>
      </div>
      <div id="ptab-crash"   class="ptab-content popup-scroll">${crashRows}</div>
      <div id="ptab-parties" class="ptab-content hidden popup-scroll"><div class="popup-loading">Loading…</div></div>
      <div id="ptab-victims" class="ptab-content hidden popup-scroll"><div class="popup-loading">Loading…</div></div>
    `).addTo(map);

    // Tab switching — delegate to popup content div (recreated each addTo call)
    const content = document.querySelector('.maplibregl-popup-content');
    if (content) {
      content.addEventListener('click', evt => {
        const btn = evt.target.closest('.ptab');
        if (!btn) return;
        content.querySelectorAll('.ptab').forEach(b => b.classList.remove('active'));
        content.querySelectorAll('.ptab-content').forEach(c => c.classList.add('hidden'));
        btn.classList.add('active');
        document.getElementById(`ptab-${btn.dataset.tab}`)?.classList.remove('hidden');
      });
    }

    // Lazy-load parties and victims (pass year hints to narrow CKAN search)
    const ids   = feats.slice(0, 8).map(f => String(f.properties.id));
    const years = [...new Set(feats.slice(0, 8).map(f => String(f.properties.year || '')).filter(Boolean))];
    _fetchCrashDetail(ids, years);

    // Click to select — Cmd/Ctrl+click appends, plain click replaces
    const append = e.originalEvent.metaKey || e.originalEvent.ctrlKey;
    if (!append || !G_selectionData) G_selectionData = { type: 'FeatureCollection', features: [] };
    const existingIds = new Set(G_selectionData.features.map(f => f.properties.id));
    feats.forEach(f => {
      if (f && !existingIds.has(f.properties.id)) {
        G_selectionData.features.push(f);
        existingIds.add(f.properties.id);
      }
    });
    _updateHighlight();
  });
  map.on('mouseenter', 'crashes-layer', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'crashes-layer', () => { map.getCanvas().style.cursor = ''; });

  // Traffic sign popups
  ['asset-regulatory-layer', 'asset-warning-layer', 'asset-info-layer'].forEach(layerId => {
    map.on('click', layerId, e => {
      const p     = e.features[0].properties;
      const parts = (p.value || '').split('--');
      const cat   = parts[0] || '—';
      const type  = (parts[1] || '—').replace(/-/g, ' ');
      const icon  = cat === 'regulatory' ? '🛑' : cat === 'warning' ? '⚠️' : 'ℹ️';
      const fmt   = ms => ms ? new Date(parseInt(ms)).toLocaleDateString('en-US', { year: 'numeric', month: 'short' }) : '—';
      popup.setLngLat(e.lngLat).setHTML(`
        <div class="popup-title">${icon} ${type}</div>
        <div class="popup-row"><span class="popup-key">Category</span><span>${cat}</span></div>
        <div class="popup-row"><span class="popup-key">First detected</span><span>${fmt(p.first_seen_at)}</span></div>
        <div class="popup-row"><span class="popup-key">Last seen</span><span>${fmt(p.last_seen_at)}</span></div>
        <div class="popup-row" style="font-size:0.68rem;margin-top:4px">
          <span class="popup-key">Value</span>
          <span style="word-break:break-all">${p.value || '—'}</span>
        </div>
      `).addTo(map);
    });
    map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = ''; });
  });

  // Crosswalk markings
  map.on('click', 'asset-crosswalks-layer', e => {
    const p    = e.features[0].properties;
    const type = (p.value || '').split('--').slice(1).join(' › ');
    const fmt  = ms => ms ? new Date(parseInt(ms)).toLocaleDateString('en-US', { year: 'numeric', month: 'short' }) : '—';
    popup.setLngLat(e.lngLat).setHTML(`
      <div class="popup-title">🚶 ${type || 'Crosswalk'}</div>
      <div class="popup-row"><span class="popup-key">First detected</span><span>${fmt(p.first_seen_at)}</span></div>
      <div class="popup-row" style="font-size:0.68rem;margin-top:4px">
        <span class="popup-key">Value</span>
        <span style="word-break:break-all">${p.value || '—'}</span>
      </div>
    `).addTo(map);
  });
  map.on('mouseenter', 'asset-crosswalks-layer', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'asset-crosswalks-layer', () => { map.getCanvas().style.cursor = ''; });
}

// ---- Mapillary status --------------------------------------------------------

function setMlyStatus(msg, type) {
  const el = document.getElementById('mly-status');
  el.textContent = msg;
  el.className   = type || '';
}

// ---- Layer toggle ------------------------------------------------------------

function toggleLayer(key) {
  if (!LAYER_IDS[key]) return;
  LAYER_VISIBILITY[key] = !LAYER_VISIBILITY[key];

  if (LAYER_VISIBILITY[key] && G_hasMly && G_mlyToken) {
    if (['asset-regulatory', 'asset-warning', 'asset-info'].includes(key) && !MLY_ADDED['mly-signs-vt']) {
      try { map.addSprite('mly', SPRITE_URL); } catch (_) {}
      addSignLayers();
      // FIX: all three sign layers were just added as visible-by-default;
      // applyVisibilityState syncs them all to their correct ON/OFF state.
      applyVisibilityState();
      return;
    }
    if (key === 'asset-crosswalks' && !MLY_ADDED['mly-objects-vt']) {
      addCrosswalkLayer();
      applyVisibilityState();
      return;
    }
  }

  _syncLayerVisibility(key);
  document.getElementById(`row-${key}`)?.classList.toggle('off',  !LAYER_VISIBILITY[key]);
  document.getElementById(`toggle-${key}`)?.classList.toggle('on', LAYER_VISIBILITY[key]);

  if (key === 'crashes') {
    document.getElementById('crash-filters')?.classList.toggle('hidden', !LAYER_VISIBILITY[key]);
    if (!LAYER_VISIBILITY[key]) applyCrashFilter(); // clear filters when layer hidden
  }
}

function applyVisibilityState() {
  Object.keys(LAYER_VISIBILITY).forEach(key => {
    _syncLayerVisibility(key);
    document.getElementById(`row-${key}`)?.classList.toggle('off',  !LAYER_VISIBILITY[key]);
    document.getElementById(`toggle-${key}`)?.classList.toggle('on',  LAYER_VISIBILITY[key]);
  });
}

function _syncLayerVisibility(key) {
  const vis = LAYER_VISIBILITY[key] ? 'visible' : 'none';
  (LAYER_IDS[key] || []).forEach(id => {
    if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', vis);
  });
}

// ---- Basemap switch ----------------------------------------------------------

function switchBasemap(mode) {
  if (G_currentBasemap === mode) return;
  G_currentBasemap = mode;
  map.setStyle(BASEMAP_STYLES[mode]);
  map.once('idle', () => {
    if (G_dataReady && !map.getSource('osm')) rebuildLayers();
  });
  document.getElementById('btn-basemap-map').classList.toggle('active',       mode === 'map');
  document.getElementById('btn-basemap-satellite').classList.toggle('active', mode === 'satellite');
}

// ---- Statistics Analysis Panel -----------------------------------------------

let _statChart     = null;
let _statSource    = 'crashes';   // 'crashes' | 'osm'
let _statChartType = 'bar';       // 'bar' | 'pie'
let G_lastStatsData = null;       // { groups, total } — used for CSV export

const SEVERITY_COLORS = {
  'fatal':         '#dc2626',
  'severe_injury': '#f97316',
  'other_injury':  '#fbbf24',
  'pdo':           '#6b7280',
};
const STAT_PALETTE = [
  '#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6',
  '#06b6d4','#ec4899','#84cc16','#f97316','#6366f1',
  '#14b8a6','#a78bfa','#fb923c','#22d3ee','#4ade80',
];

function toggleStatsPanel() {
  const body  = document.getElementById('stat-panel-body');
  const arrow = document.getElementById('stats-panel-arrow');
  const open  = body.classList.contains('hidden');
  body.classList.toggle('hidden', !open);
  arrow.innerHTML = open ? '&#9660;' : '&#9658;';
  if (open) {
    _buildCityList();
    refreshStats();
  }
}

function setStatSource(src) {
  _statSource = src;
  document.getElementById('stat-src-crashes').classList.toggle('active', src === 'crashes');
  document.getElementById('stat-src-osm').classList.toggle('active',     src === 'osm');
  document.getElementById('stat-scope-row').classList.toggle('hidden',   src === 'osm');
  document.getElementById('stat-groupby-row').classList.toggle('hidden', src === 'osm');
  document.getElementById('stat-year-row').classList.toggle('hidden',    src === 'osm');
  refreshStats();
}

function setChartType(type) {
  _statChartType = type;
  document.getElementById('stat-chart-bar').classList.toggle('active', type === 'bar');
  document.getElementById('stat-chart-pie').classList.toggle('active', type === 'pie');
  if (G_lastStatsData) _renderChart(G_lastStatsData);
}

function _getStatScope() {
  for (const r of document.querySelectorAll('input[name="stat-scope"]')) {
    if (r.checked) return r.value;
  }
  return 'viewport';
}

async function refreshStats() {
  const body = document.getElementById('stat-panel-body');
  if (!body || body.classList.contains('hidden')) return;

  const totalEl = document.getElementById('stat-total');
  totalEl.textContent = 'Computing…';

  if (_statSource === 'osm') {
    const scope = _getStatScope();
    const features = (scope === 'selection' && G_selectionData)
      ? G_selectionData.features.filter(f => f.properties.severity === undefined)
      : [...OSM_FEATURE_MAP.values()];
    _renderStatsFromFeatures(features, 'type', '', `OSM features${scope === 'selection' ? ' (selection)' : ' (viewport)'}`);
    return;
  }

  const scope   = _getStatScope();
  const groupBy = document.getElementById('stat-groupby').value;
  const year    = document.getElementById('stat-year').value;

  if (scope === 'selection') {
    const features = (G_selectionData?.features || []).filter(f => f.properties.severity !== undefined);
    _renderStatsFromFeatures(features, groupBy, year, 'crashes (selection)');
    return;
  }

  if (scope === 'viewport') {
    _renderStatsFromFeatures([...CRASH_FEATURE_MAP.values()], groupBy, year, 'crashes (viewport)');
    return;
  }

  // county or city — call backend
  let url;
  if (scope === 'county') {
    const cc = document.getElementById('stat-county-select').value;
    if (!cc) { totalEl.textContent = 'Select a county above'; return; }
    url = `/api/crashes/stats?scope=county&county_code=${cc}&group_by=${groupBy}${year ? '&year=' + year : ''}`;
  } else {
    const city = document.getElementById('stat-city-input').value.trim();
    if (!city) { totalEl.textContent = 'Enter a city name above'; return; }
    url = `/api/crashes/stats?scope=city&city_name=${encodeURIComponent(city)}&group_by=${groupBy}${year ? '&year=' + year : ''}`;
  }

  try {
    const data = await fetch(url).then(r => r.json());
    if (data.fetching) {
      _destroyChart();
      totalEl.textContent = 'County not loaded yet — zoom in to load it first.';
      return;
    }
    G_lastStatsData = data.groups;
    const label = `${(data.total || 0).toLocaleString()} crashes${data.display_name ? ' · ' + data.display_name : ''}`;
    totalEl.textContent = label;
    _renderChart(data.groups);
  } catch (_) {
    totalEl.textContent = 'Error loading stats';
  }
}

function _renderStatsFromFeatures(features, groupBy, yearFilter, label) {
  const counts = {};
  let total = 0;
  for (const f of features) {
    if (yearFilter && String(f.properties.year) !== yearFilter) continue;
    let val = f.properties[groupBy];
    val = (val !== undefined && val !== null && String(val).trim()) ? String(val).trim() : 'Unknown';
    counts[val] = (counts[val] || 0) + 1;
    total++;
  }
  const groups = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 15)
    .map(([lbl, cnt]) => ({ label: lbl, count: cnt }));
  G_lastStatsData = groups;
  document.getElementById('stat-total').textContent = `${total.toLocaleString()} ${label}`;
  _renderChart(groups);
}

function _destroyChart() {
  if (_statChart) { _statChart.destroy(); _statChart = null; }
}

function _renderChart(groups) {
  if (!groups || groups.length === 0) {
    _destroyChart();
    if (!document.getElementById('stat-total').textContent) {
      document.getElementById('stat-total').textContent = 'No data';
    }
    return;
  }
  const labels = groups.map(g => g.label);
  const values = groups.map(g => g.count);
  const total  = values.reduce((a, b) => a + b, 0);
  const colors = groups.map((g, i) =>
    (_statSource === 'crashes' && SEVERITY_COLORS[g.label])
      ? SEVERITY_COLORS[g.label]
      : STAT_PALETTE[i % STAT_PALETTE.length]
  );

  _destroyChart();
  const ctx = document.getElementById('stat-chart').getContext('2d');
  const isBar = _statChartType === 'bar';
  _statChart = new Chart(ctx, {
    type: isBar ? 'bar' : 'doughnut',
    data: {
      labels,
      datasets: [{
        data:            values,
        backgroundColor: isBar ? colors.map(c => c + 'bb') : colors,
        borderColor:     colors,
        borderWidth:     isBar ? 0 : 1,
        borderRadius:    isBar ? 3 : 0,
      }],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: !isBar,
          labels:  { color: '#9ca3af', font: { size: 9 }, boxWidth: 10, padding: 6 },
        },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.raw.toLocaleString()} (${((ctx.raw / total) * 100).toFixed(1)}%)`,
          },
        },
      },
      scales: isBar ? {
        x: { ticks: { color: '#6b7280', font: { size: 8 }, maxRotation: 40 }, grid: { color: '#1f2937' } },
        y: { ticks: { color: '#6b7280', font: { size: 8 } },                  grid: { color: '#1f2937' } },
      } : {},
    },
  });
}

function exportStatsCsv() {
  if (!G_lastStatsData || !G_lastStatsData.length) return;
  const total = G_lastStatsData.reduce((s, g) => s + g.count, 0);
  const rows  = [['Label', 'Count', 'Percent']];
  for (const g of G_lastStatsData) {
    rows.push([g.label, g.count, ((g.count / total) * 100).toFixed(2) + '%']);
  }
  rows.push(['Total', total, '100%']);
  const csv = rows.map(r => r.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
  _triggerDownload(csv, `gistrack_stats_${new Date().toISOString().slice(0, 10)}.csv`, 'text/csv');
}

function _buildCountySelect(counties) {
  const sel = document.getElementById('stat-county-select');
  if (!sel) return;
  const sorted = Object.entries(counties).sort((a, b) => a[0].localeCompare(b[0]));
  for (const [name, code] of sorted) {
    const opt = document.createElement('option');
    opt.value       = code;
    opt.textContent = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    sel.appendChild(opt);
  }
}

let _cityListSize = 0;

function _buildCityList() {
  if (CRASH_FEATURE_MAP.size === _cityListSize) return;
  _cityListSize = CRASH_FEATURE_MAP.size;
  const cities = new Set();
  for (const f of CRASH_FEATURE_MAP.values()) {
    const c = f.properties.city_name;
    if (c) cities.add(String(c).trim());
  }
  const dl = document.getElementById('stat-city-list');
  if (!dl) return;
  dl.innerHTML = '';
  for (const c of [...cities].sort()) {
    const opt = document.createElement('option');
    opt.value = c;
    dl.appendChild(opt);
  }
}

function _statsOnSelectionChange(selCount) {
  const selRadio    = document.getElementById('stat-scope-selection');
  const selCountEl  = document.getElementById('stat-sel-count');
  if (!selRadio) return;
  const hasSelection = selCount > 0;
  selRadio.disabled = !hasSelection;
  if (selCountEl) selCountEl.textContent = hasSelection ? `(${selCount.toLocaleString()})` : '';
  if (hasSelection) {
    selRadio.checked = true;
  } else if (selRadio.checked) {
    document.getElementById('stat-scope-viewport').checked = true;
  }
  const body = document.getElementById('stat-panel-body');
  if (body && !body.classList.contains('hidden')) refreshStats();
}

function _maybeRefreshStatsOnDataUpdate() {
  const body = document.getElementById('stat-panel-body');
  if (!body || body.classList.contains('hidden')) return;
  const scope = _getStatScope();
  if (scope === 'viewport' || _statSource === 'osm') refreshStats();
}

// =============================================================================
// Data Source Info Modal
// =============================================================================

const _DATA_META = {
  basemap: {
    title: 'Basemap Tiles',
    rows: [
      ['Map style',   'OpenFreeMap — liberty style'],
      ['Attribution', '© OpenStreetMap contributors'],
      ['License',     'Open Database License (ODbL) 1.0'],
      ['Satellite',   'Esri World Imagery'],
      ['Sat. credit', '© Esri, Maxar, Earthstar Geographics'],
      ['Usage',       'Display only — no routing or geocoding'],
    ],
  },
  osm: {
    title: 'OpenStreetMap Infrastructure',
    rows: [
      ['Source',      'OpenStreetMap contributors'],
      ['License',     'Open Database License (ODbL) 1.0'],
      ['API',         'Overpass API (3-mirror fallback)'],
      ['Tile cache',  'Zoom-12 tiles, cached on first viewport visit'],
      ['Update lag',  'Hours to days behind real-world edits'],
      ['Coverage',    'California — dynamic, viewport-based'],
      ['More info',   'openstreetmap.org/copyright'],
    ],
  },
  crash: {
    title: 'CHP Crash Data (CCRS)',
    rows: [
      ['Source',    'California Highway Patrol (CHP)'],
      ['Dataset',   'Crash Cause Reporting System (CCRS)'],
      ['Portal',    'data.ca.gov (CKAN Datastore API)'],
      ['License',   'California Open Data — Public Domain'],
      ['Coverage',  'All 58 CA counties, years 2019–2024'],
      ['Refresh',   'API updated daily; app caches per county on first view'],
      ['Note',      'Geocoding accuracy varies; some records lack coordinates and are excluded'],
    ],
  },
  mapillary: {
    title: 'Mapillary Street Signs',
    rows: [
      ['Provider',   'Mapillary (Meta Platforms, Inc.)'],
      ['Imagery',    'CC BY-SA 4.0'],
      ['Detection',  'Mapillary AI computer vision model'],
      ['Auth',       'Requires Mapillary API token (server-side proxy)'],
      ['Coverage',   'Varies by location; urban areas better covered'],
      ['Terms',      'mapillary.com/terms'],
    ],
  },
  streetview: {
    title: 'Street View',
    rows: [
      ['Provider', 'Google Maps Platform'],
      ['APIs',     'Maps JavaScript API — Street View Service'],
      ['Auth',     'Requires Google Maps API key'],
      ['Terms',    'Google Maps Platform Terms of Service'],
      ['Note',     'Key usage subject to Google billing; not embedded — opens in side panel'],
    ],
  },
};

function showDataInfo(key) {
  const meta = _DATA_META[key];
  if (!meta) return;
  let rows = meta.rows.map(([k, v]) =>
    `<tr><td style="color:#9ca3af;padding:3px 8px 3px 0;white-space:nowrap;vertical-align:top">${k}</td>` +
    `<td style="color:#e0e0e0;padding:3px 0;line-height:1.5">${v}</td></tr>`
  ).join('');
  document.getElementById('data-info-title').textContent = meta.title;
  document.getElementById('data-info-table').innerHTML = rows;
  document.getElementById('data-info-modal').classList.remove('hidden');
}

function closeDataInfo() {
  document.getElementById('data-info-modal').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Safety Rankings
// ---------------------------------------------------------------------------

// Empty GeoJSON sources for worst/best ranked facilities
const EMPTY_FC = { type: 'FeatureCollection', features: [] };

function addRankingsLayers() {
  removeSafe('rankings-worst');
  removeSafe('rankings-best');

  map.addSource('rankings-worst', { type: 'geojson', data: EMPTY_FC });
  map.addSource('rankings-best',  { type: 'geojson', data: EMPTY_FC });

  map.addLayer({
    id: 'rankings-worst-layer', type: 'circle', source: 'rankings-worst', minzoom: 8,
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 10, 8, 16, 14],
      'circle-color':        '#ef4444',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#7f1d1d',
      'circle-opacity':      0.85,
    },
  });
  map.addLayer({
    id: 'rankings-worst-label', type: 'symbol', source: 'rankings-worst', minzoom: 8,
    layout: {
      'text-field':  ['to-string', ['get', 'rank_worst']],
      'text-size':   ['interpolate', ['linear'], ['zoom'], 10, 8, 16, 12],
      'text-anchor': 'center',
      'text-font':   ['Open Sans Bold', 'Arial Unicode MS Bold'],
    },
    paint: { 'text-color': '#fff' },
  });

  map.addLayer({
    id: 'rankings-best-layer', type: 'circle', source: 'rankings-best', minzoom: 8,
    paint: {
      'circle-radius':       ['interpolate', ['linear'], ['zoom'], 10, 8, 16, 14],
      'circle-color':        '#22c55e',
      'circle-stroke-width': 2,
      'circle-stroke-color': '#14532d',
      'circle-opacity':      0.85,
    },
  });
  map.addLayer({
    id: 'rankings-best-label', type: 'symbol', source: 'rankings-best', minzoom: 8,
    layout: {
      'text-field':  ['to-string', ['get', 'rank_best']],
      'text-size':   ['interpolate', ['linear'], ['zoom'], 10, 8, 16, 12],
      'text-anchor': 'center',
      'text-font':   ['Open Sans Bold', 'Arial Unicode MS Bold'],
    },
    paint: { 'text-color': '#fff' },
  });

  // Hover popup for ranked facilities
  const rankPopup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });

  ['rankings-worst-layer', 'rankings-best-layer'].forEach(layerId => {
    map.on('mouseenter', layerId, e => {
      map.getCanvas().style.cursor = 'pointer';
      const p = e.features[0]?.properties ?? {};
      const fid = p.facility_id;
      // Look up full props from stored features
      const feat = G_rankWorstMap.get(fid) ?? G_rankBestMap.get(fid);
      const props = feat?.properties ?? p;
      rankPopup.setLngLat(e.lngLat)
        .setHTML(_rankPopupHtml(props))
        .addTo(map);
    });
    map.on('mouseleave', layerId, () => {
      map.getCanvas().style.cursor = '';
      rankPopup.remove();
    });
  });
}

// Stores for full feature props (needed because MapLibre truncates properties in tiles)
const G_rankWorstMap = new Map();  // facility_id → GeoJSON Feature
const G_rankBestMap  = new Map();

function _rankPopupHtml(p) {
  const name  = p.name || p.facility_id || '—';
  const epdo  = typeof p.epdo_score === 'number' ? p.epdo_score.toFixed(1) : '—';
  const fatal = p.fatal_5yr ?? '—';
  const sev   = p.severe_5yr ?? '—';
  const tot   = p.total_5yr ?? '—';
  const cls   = p.road_class || '—';
  const ctrl  = p.control_type || '—';
  const county = p.county || '—';
  return `<div style="font-size:0.72rem;line-height:1.6;min-width:160px">
    <b style="color:#fff">${name}</b><br>
    <span style="color:#6b7280">${county} · ${cls} · ${ctrl}</span><br>
    <span style="color:#ef4444">EPDO: ${epdo}</span> &nbsp;
    <span style="color:#fca5a5">Fatal: ${fatal}</span> &nbsp;
    <span style="color:#fdba74">Severe: ${sev}</span><br>
    <span style="color:#9ca3af">Crashes (5yr): ${tot}</span>
  </div>`;
}

function toggleRankingsPanel() {
  const body  = document.getElementById('rank-panel-body');
  const arrow = document.getElementById('rank-panel-arrow');
  const open  = body.classList.contains('hidden');
  body.classList.toggle('hidden', !open);
  arrow.innerHTML = open ? '&#9660;' : '&#9658;';
  if (open) _loadRankingsConfig();
}

async function _loadRankingsConfig() {
  try {
    const [cfgResp, statusResp] = await Promise.all([
      fetch('/api/rankings/config'),
      fetch('/api/data/county_status'),
    ]);
    if (!cfgResp.ok) return;
    const data = await cfgResp.json();

    // Populate output dir placeholder
    const dirInput = document.getElementById('rank-output-dir');
    dirInput.placeholder = data.active_dir || '/data/rankings';

    // Populate county dropdown with analysis-ready counties
    const sel = document.getElementById('rank-county-scope');
    sel.innerHTML = '<option value="all">All analysis-ready counties</option>';

    let readyCount = 0;
    if (statusResp.ok) {
      const statusData = await statusResp.json();
      Object.entries(statusData)
        .filter(([, info]) => info.analysis_ready)
        .sort(([a], [b]) => a.localeCompare(b))
        .forEach(([c]) => {
          readyCount++;
          const opt = document.createElement('option');
          opt.value = c;
          opt.textContent = c.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
          sel.appendChild(opt);
        });
    } else {
      // Fallback: use crash-cached counties from config
      (data.cached_counties || []).forEach(c => {
        const opt = document.createElement('option');
        opt.value = c;
        opt.textContent = c.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
        sel.appendChild(opt);
        readyCount++;
      });
    }

    // Gate compute button: disabled if no analysis-ready county
    const btn = document.getElementById('rank-compute-btn');
    if (btn) {
      btn.disabled = readyCount === 0;
      btn.title = readyCount === 0
        ? 'Download at least one county (crash + OSM) before computing'
        : '';
    }
  } catch (_) {}
}

async function setRankingsDir() {
  const dir = document.getElementById('rank-output-dir').value.trim();
  if (!dir) return;
  try {
    const resp = await fetch('/api/rankings/set_dir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ output_dir: dir }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      _rankStatus(`Cannot load: ${err.detail || resp.statusText}`);
      return;
    }
    _rankStatus('Path set. Use Load Rankings to visualize.');
  } catch (e) {
    _rankStatus(`Error: ${e.message}`);
  }
}

function onRankFacTypeChange() {
  const isInt = document.getElementById('rank-fac-type').value === 'int';
  document.getElementById('rank-int-filters').classList.toggle('hidden', !isInt);
  document.getElementById('rank-seg-filters').classList.toggle('hidden', isInt);
  clearRankings();
}

function _buildBinKey() {
  const facType = document.getElementById('rank-fac-type').value;
  if (facType === 'int') {
    const ctrl  = document.getElementById('rank-control').value;
    const cls   = document.getElementById('rank-int-road-class').value;
    const speed = document.getElementById('rank-int-speed').value;
    const legs  = document.getElementById('rank-legs').value;
    // Build partial key — skip empty dimensions
    const parts = [ctrl, cls, speed, legs].filter(Boolean);
    if (!parts.length) return null;
    return 'int|' + parts.join('|');
  } else {
    const cls   = document.getElementById('rank-seg-road-class').value;
    const speed = document.getElementById('rank-seg-speed').value;
    const lanes = document.getElementById('rank-lanes').value;
    const parts = [cls, speed, lanes].filter(Boolean);
    if (!parts.length) return null;
    return 'seg|' + parts.join('|');
  }
}

async function loadSelectedRanking() {
  const binKey = _buildBinKey();
  if (!binKey) {
    _rankStatus('Select at least one filter to load rankings.');
    return;
  }

  _rankStatus('Loading…');
  document.getElementById('rank-table-wrap').classList.add('hidden');
  document.getElementById('rank-bin-label').classList.add('hidden');

  try {
    const encoded = encodeURIComponent(binKey);
    const resp = await fetch(`/api/rankings/bin/${encoded}`);
    if (resp.status === 404) {
      const err = await resp.json().catch(() => ({}));
      if (err.detail?.includes('not found')) {
        _rankStatus(`No bin matches "${binKey}". Try different filters.`);
      } else {
        _rankStatus('Rankings not computed yet. Run scripts/build_safety_rankings.py first.');
      }
      return;
    }
    if (!resp.ok) throw new Error(resp.statusText);
    const data = await resp.json();

    if (data.insufficient_data) {
      _rankStatus(`Insufficient data (< 20 facilities) for bin: ${binKey}`);
      return;
    }

    _rankStatus('');
    _renderRankingsMap(data.worst || [], data.best || []);
    _renderRankingsTable(data.worst || [], data.best || [], binKey, data.facility_count);
  } catch (err) {
    _rankStatus(`Error: ${err.message}`);
  }
}

function _rankStatus(msg) {
  const el = document.getElementById('rank-status');
  if (msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

function _renderRankingsMap(worst, best) {
  G_rankWorstMap.clear();
  G_rankBestMap.clear();
  worst.forEach(f => G_rankWorstMap.set(f.properties?.facility_id, f));
  best.forEach(f  => G_rankBestMap.set(f.properties?.facility_id, f));

  const worstSrc = map.getSource('rankings-worst');
  const bestSrc  = map.getSource('rankings-best');
  if (worstSrc) worstSrc.setData({ type: 'FeatureCollection', features: worst });
  if (bestSrc)  bestSrc.setData({ type: 'FeatureCollection', features: best });
}

function _renderRankingsTable(worst, best, binKey, facilityCount) {
  document.getElementById('rank-bin-label').textContent =
    `${binKey}  ·  ${facilityCount ?? '?'} facilities statewide`;
  document.getElementById('rank-bin-label').classList.remove('hidden');

  const maxRows = Math.max(worst.length, best.length);
  if (!maxRows) { _rankStatus('No ranked facilities returned.'); return; }

  function rowHtml(feat, rankProp, colorClass) {
    if (!feat) return '<div class="rank-row" style="min-height:36px"></div>';
    const p    = feat.properties ?? {};
    const rank = p[rankProp] ?? '—';
    const name = p.name || p.facility_id || '—';
    const epdo = typeof p.epdo_score === 'number' ? p.epdo_score.toFixed(1) : '—';
    const f    = p.fatal_5yr ?? 0;
    const s    = p.severe_5yr ?? 0;
    const fid  = p.facility_id;
    return `<div class="rank-row" onclick="openRankDash('${fid}')">
      <span class="rn">#${rank}</span> ${name}<br>
      <span class="re ${colorClass}">EPDO ${epdo}</span>
      <span class="re" style="color:#9ca3af"> ${f}K/${s}S</span>
    </div>`;
  }

  let worstHtml = '', bestHtml = '';
  for (let i = 0; i < maxRows; i++) {
    worstHtml += rowHtml(worst[i], 'rank_worst', 'worst');
    bestHtml  += rowHtml(best[i],  'rank_best',  'best');
  }
  document.getElementById('rank-worst-list').innerHTML = worstHtml;
  document.getElementById('rank-best-list').innerHTML  = bestHtml;
  document.getElementById('rank-table-wrap').classList.remove('hidden');
}

function _flyToRankFacility(facilityId) {
  const feat = G_rankWorstMap.get(facilityId) ?? G_rankBestMap.get(facilityId);
  if (!feat) return;
  const coords = feat.geometry?.type === 'Point'
    ? feat.geometry.coordinates
    : feat.geometry?.coordinates?.[0];
  if (coords) map.flyTo({ center: coords, zoom: Math.max(map.getZoom(), 14), duration: 800 });
}

// ---- Rankings compute + download -------------------------------------------

let _rankPollTimer = null;

async function pickRankingsDir() {
  try {
    const resp = await fetch('/api/system/pick_dir');
    if (!resp.ok) return;
    const { path } = await resp.json();
    if (path) document.getElementById('rank-output-dir').value = path;
  } catch (_) {}
}

async function startRankingsCompute() {
  const btn       = document.getElementById('rank-compute-btn');
  const outputDir = document.getElementById('rank-output-dir').value.trim();
  const county    = document.getElementById('rank-county-scope').value;
  const wFatal  = parseFloat(document.getElementById('rank-w-fatal').value)  || 10;
  const wInjury = parseFloat(document.getElementById('rank-w-injury').value) || 2;
  const wPdo    = parseFloat(document.getElementById('rank-w-pdo').value)    || 0.2;

  btn.disabled = true;
  btn.textContent = '⏳ Running…';
  _setRankProgress(0, 'Starting computation…');
  document.getElementById('rank-progress-wrap').classList.remove('hidden');

  try {
    const params = new URLSearchParams();
    if (county && county !== 'all') params.set('county', county);
    if (outputDir) params.set('output_dir', outputDir);
    params.set('weights', `${wFatal},${wInjury},${wPdo}`);

    const resp = await fetch('/api/rankings/compute?' + params, { method: 'POST' });
    if (resp.status === 409) {
      _setRankProgress(0, 'Already running — polling…');
    } else if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || resp.statusText);
    }
    _startRankPoll();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '▶ Compute';
    _setRankProgress(0, `Error: ${e.message}`);
  }
}

function _startRankPoll() {
  if (_rankPollTimer) return;
  _rankPollTimer = setInterval(_pollRankStatus, 1500);
}

async function _pollRankStatus() {
  try {
    const resp = await fetch('/api/rankings/status');
    if (!resp.ok) return;
    const data = await resp.json();

    _setRankProgress(data.progress ?? 0, data.message || '');

    if (data.status === 'done') {
      clearInterval(_rankPollTimer);
      _rankPollTimer = null;
      _setRankProgress(100, 'Done! Rankings ready.');
      _resetComputeBtn();
      _loadRankingsConfig();   // refresh active_dir placeholder + county list
    } else if (data.status === 'error') {
      clearInterval(_rankPollTimer);
      _rankPollTimer = null;
      _setRankProgress(0, `Error: ${data.message}`);
      _resetComputeBtn();
    }
  } catch (_) {}
}

function _setRankProgress(pct, msg) {
  document.getElementById('rank-progress-bar').style.width = pct + '%';
  document.getElementById('rank-progress-msg').textContent = msg;
}

function _resetComputeBtn() {
  const btn = document.getElementById('rank-compute-btn');
  btn.disabled = false;
  btn.textContent = '▶ Compute';
}

function downloadRankings() {
  window.location.href = '/api/rankings/download';
}

function clearRankings() {
  G_rankWorstMap.clear();
  G_rankBestMap.clear();
  const worstSrc = map.getSource('rankings-worst');
  const bestSrc  = map.getSource('rankings-best');
  if (worstSrc) worstSrc.setData(EMPTY_FC);
  if (bestSrc)  bestSrc.setData(EMPTY_FC);
  document.getElementById('rank-table-wrap').classList.add('hidden');
  document.getElementById('rank-bin-label').classList.add('hidden');
  _rankStatus('');
}

// ---------------------------------------------------------------------------
// Crash Dashboard Panel
// ---------------------------------------------------------------------------

function openRankDash(facilityId) {
  const feat = G_rankWorstMap.get(facilityId) ?? G_rankBestMap.get(facilityId);
  if (!feat) return;
  _flyToRankFacility(facilityId);

  const p     = feat.properties ?? {};
  const dists = typeof p.crash_dists === 'string'
    ? JSON.parse(p.crash_dists)
    : (p.crash_dists ?? {});
  const total = p.total_5yr || 0;
  const fatal = p.fatal_5yr || 0;
  const sev   = p.severe_5yr || 0;
  const pdo   = Math.max(0, total - fatal - sev);

  const title = p.name || p.facility_id || 'Facility';
  document.getElementById('rank-dash-title').textContent = title;

  let html = `
    <div class="dash-stat-row"><span class="dk">County</span><span class="dv">${p.county || '—'}</span></div>
    <div class="dash-stat-row"><span class="dk">Road Class</span><span class="dv">${p.road_class || '—'}</span></div>
    <div class="dash-stat-row"><span class="dk">Control</span><span class="dv">${p.control_type || p.road_type || '—'}</span></div>
    <div class="dash-stat-row"><span class="dk">Speed</span><span class="dv">${p.speed_mph ? p.speed_mph + ' mph' : '—'}</span></div>
    <div class="dash-stat-row"><span class="dk">EPDO Score</span><span class="dv" style="color:#ef4444">${typeof p.epdo_score === 'number' ? p.epdo_score.toFixed(1) : '—'}</span></div>
    <div class="dash-stat-row"><span class="dk">Crashes (5yr)</span><span class="dv">${total}</span></div>
    <div class="dash-chart-title">Severity</div>
    ${_dashBar('Fatal',   fatal, total, '#ef4444')}
    ${_dashBar('Severe',  sev,   total, '#f97316')}
    ${_dashBar('PDO',     pdo,   total, '#6b7280')}`;

  if (total > 0) {
    html += `<div class="dash-chart-title">Special Conditions</div>
    ${_dashBar('Pedestrian', dists.ped || 0, total, '#60a5fa')}
    ${_dashBar('Cyclist',    dists.cyc || 0, total, '#a78bfa')}
    ${_dashBar('Impaired',   dists.imp || 0, total, '#f59e0b')}`;

    const lighting = dists.lighting || {};
    if (Object.keys(lighting).length) {
      html += `<div class="dash-chart-title">Lighting</div>`;
      const lightTotal = Object.values(lighting).reduce((a, b) => a + b, 0);
      Object.entries(lighting).slice(0, 5).forEach(([k, v]) => {
        html += _dashBar(k, v, lightTotal, '#38bdf8');
      });
    }

    const pcf = dists.pcf || {};
    if (Object.keys(pcf).length) {
      html += `<div class="dash-chart-title">Primary Collision Factor</div>`;
      const pcfTotal = Object.values(pcf).reduce((a, b) => a + b, 0);
      Object.entries(pcf).slice(0, 5).forEach(([k, v]) => {
        html += _dashBar(k, v, pcfTotal, '#fb7185');
      });
    }
  }

  document.getElementById('rank-dash-body').innerHTML = html;
  document.getElementById('rank-dash-panel').classList.add('open');
}

function _dashBar(label, count, total, color) {
  const pct = total > 0 ? Math.round(count / total * 100) : 0;
  return `<div class="dash-bar-row">
    <span class="dash-bar-label" title="${label}">${label}</span>
    <div class="dash-bar-bg"><div class="dash-bar-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="dash-bar-count">${count}</span>
  </div>`;
}

function closeRankDash() {
  document.getElementById('rank-dash-panel').classList.remove('open');
}

// ---------------------------------------------------------------------------
// County Status Panel
// ---------------------------------------------------------------------------

let _countyStatusData = null;

async function toggleCountyPanel() {
  const body  = document.getElementById('county-panel-body');
  const arrow = document.getElementById('county-panel-arrow');
  const open  = body.classList.contains('hidden');
  body.classList.toggle('hidden', !open);
  arrow.innerHTML = open ? '&#9660;' : '&#9658;';
  if (open && !_countyStatusData) await _loadCountyStatus();
}

async function _loadCountyStatus() {
  try {
    const resp = await fetch('/api/data/county_status');
    if (!resp.ok) return;
    _countyStatusData = await resp.json();
    _renderCountyGrid();
  } catch (_) {}
}

function _countyChipClass(info) {
  if (info.fetching_crash || info.fetching_osm) return 'loading';
  if (info.analysis_ready) return 'ready';
  if (info.crash_ready || info.osm_tile_cached > 0) return 'partial';
  return 'uncached';
}

function _renderCountyGrid() {
  if (!_countyStatusData) return;
  const grid = document.getElementById('county-grid');
  grid.innerHTML = Object.entries(_countyStatusData)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, info]) => {
      const label   = name.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
      const cls     = _countyChipClass(info);
      const crashSt = info.crash_ready ? '✓ crash' : '– crash';
      const osmSt   = info.osm_tile_total > 0
        ? `OSM ${info.osm_tile_cached}/${info.osm_tile_total}`
        : 'OSM –';
      const title = `${label}: ${crashSt} | ${osmSt}`;
      return `<div class="county-chip ${cls}" id="chip-${name}" title="${title}"
                   onclick="_loadCountyData('${name}')">${label}</div>`;
    }).join('');
}

async function _loadCountyData(countyName) {
  const chip = document.getElementById(`chip-${countyName}`);
  if (!chip || chip.classList.contains('ready') || chip.classList.contains('loading')) return;
  chip.classList.remove('uncached', 'partial');
  chip.classList.add('loading');

  const info = _countyStatusData?.[countyName];
  if (!info) return;
  const [s, w, n, e] = info.bbox;
  map.flyTo({ center: [(w + e) / 2, (s + n) / 2], zoom: 9, duration: 1200 });

  try {
    // Start crash + OSM downloads in parallel (each returns immediately, runs in background)
    await Promise.all([
      fetch(`/api/data/county/${countyName}/fetch_crash`, { method: 'POST' }),
      fetch(`/api/data/county/${countyName}/fetch_osm`,   { method: 'POST' }),
    ]);

    // Poll until analysis_ready (crash done AND ≥95% OSM tiles)
    const poll = setInterval(async () => {
      try {
        const r = await fetch('/api/data/county_status');
        if (!r.ok) return;
        const d = await r.json();
        _countyStatusData = d;
        _renderCountyGrid();  // update all chips (shows tile progress)
        if (d[countyName]?.analysis_ready) {
          clearInterval(poll);
        }
      } catch (_) {}
    }, 5000);
  } catch (_) {
    chip.classList.remove('loading');
    chip.classList.add('uncached');
  }
}

