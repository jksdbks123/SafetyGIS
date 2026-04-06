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
};

// source id → owned layer IDs (must remove layers before source)
const SOURCE_LAYERS = {
  osm:              ['signals-layer', 'crossings-layer', 'bus-layer', 'bike-layer',
                     'roads-layer', 'footway-layer', 'calming-layer', 'streetlamp-layer'],
  crashes:          ['heatmap-layer', 'crashes-layer'],
  'mly-signs-vt':   ['asset-regulatory-layer', 'asset-warning-layer', 'asset-info-layer'],
  'mly-objects-vt': ['asset-crosswalks-layer'],
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
}

// ---- Core layer rebuild (called after every style switch) --------------------

function rebuildLayers() {
  Object.keys(MLY_ADDED).forEach(k => { MLY_ADDED[k] = false; });

  addOsmLayers();
  addCrashLayers();
  addDrawLayers();

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

function downloadSelection() {
  if (!G_selectionData || G_selectionData.features.length === 0) return;
  const out = {
    type:     'FeatureCollection',
    features: G_selectionData.features,
    metadata: {
      source:    'GIS-Track',
      timestamp: new Date().toISOString(),
      count:     G_selectionData.features.length,
    },
  };
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/geo+json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `gistrack_${new Date().toISOString().slice(0, 10)}.geojson`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
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
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: 'application/geo+json' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `gistrack_full_${new Date().toISOString().slice(0, 10)}.geojson`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function applyCrashFilter() {
  const filters = ['all'];
  if (document.getElementById('cf-ped')?.checked)      filters.push(['==', ['get', 'has_pedestrian'], true]);
  if (document.getElementById('cf-bike')?.checked)     filters.push(['==', ['get', 'has_cyclist'],    true]);
  if (document.getElementById('cf-impaired')?.checked) filters.push(['==', ['get', 'has_impaired'],   true]);
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
  '1': 'Killed', '2': 'Severe Injury', '3': 'Other Visible Injury',
  '4': 'Complaint of Pain', '5': 'Possible Injury', '6': 'No Apparent Injury', '0': 'Not a Victim',
};
const INJURY_DEGREE_COLOR = {
  '1': '#dc2626', '2': '#f97316', '3': '#fbbf24', '4': '#9ca3af', '6': '#374151',
};

const PARTY_TOP_KEYS = new Set([
  'Party Number', 'Party Type', 'At Fault', 'Party Age', 'Party Sex',
  'Party Sobriety', 'Vehicle Make', 'Vehicle Year', 'Movement Preceding Crash',
  'Party Number Killed', 'Party Number Injured', 'Collision Id', 'County Code',
]);
const VICTIM_TOP_KEYS = new Set([
  'Victim Number', 'Victim Role', 'Victim Degree of Injury', 'Victim Ejected',
  'Victim Safety Equipment 1', 'Victim Safety Equipment 2', 'Victim Age', 'Victim Sex',
  'Victim Seating Position', 'Collision Id', 'Party Number', 'County Code',
]);

async function _fetchCrashDetail(ids) {
  try {
    const data = await fetch(`/api/crashes/detail?ids=${ids.join(',')}`).then(r => r.json());
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
  const type     = p['Party Type'] || '—';
  const atFault  = p['At Fault'];
  const age      = p['Party Age'];
  const sex      = p['Party Sex'];
  const sobriety = p['Party Sobriety'];
  const make     = p['Vehicle Make'];
  const yr       = p['Vehicle Year'];
  const move     = p['Movement Preceding Crash'];
  const killed   = parseInt(p['Party Number Killed']  || 0, 10);
  const injured  = parseInt(p['Party Number Injured'] || 0, 10);
  const extra = Object.entries(p)
    .filter(([k, v]) => !PARTY_TOP_KEYS.has(k) && v !== null && v !== undefined && String(v).trim() !== '')
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `<div class="popup-row"><span class="popup-key" style="font-size:0.64rem">${_esc(k)}</span><span style="font-size:0.66rem;word-break:break-all">${_esc(String(v))}</span></div>`)
    .join('');
  return `
    <div class="popup-card-title">Party ${_esc(String(p['Party Number'] || ''))}: ${_esc(type)}</div>
    ${atFault  ? `<div class="popup-row"><span class="popup-key">At Fault</span><span style="color:${atFault==='Y'?'#f97316':'#34d399'}">${_esc(atFault)}</span></div>` : ''}
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
  const role  = v['Victim Role'] || '—';
  const deg   = String(v['Victim Degree of Injury'] ?? '');
  const degLabel = INJURY_DEGREE[deg] || deg || '—';
  const degColor = INJURY_DEGREE_COLOR[deg] || '#9ca3af';
  const ejected  = v['Victim Ejected'];
  const eq1      = v['Victim Safety Equipment 1'];
  const eq2      = v['Victim Safety Equipment 2'];
  const age      = v['Victim Age'];
  const sex      = v['Victim Sex'];
  const seat     = v['Victim Seating Position'];
  const extra = Object.entries(v)
    .filter(([k, val]) => !VICTIM_TOP_KEYS.has(k) && val !== null && val !== undefined && String(val).trim() !== '')
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, val]) => `<div class="popup-row"><span class="popup-key" style="font-size:0.64rem">${_esc(k)}</span><span style="font-size:0.66rem;word-break:break-all">${_esc(String(val))}</span></div>`)
    .join('');
  return `
    <div class="popup-card-title">Victim ${_esc(String(v['Victim Number'] || ''))}: ${_esc(role)}</div>
    <div class="popup-row"><span class="popup-key">Injury</span><span style="color:${degColor};font-weight:600">${_esc(degLabel)}</span></div>
    ${ejected && ejected !== 'Not Ejected' ? `<div class="popup-row"><span class="popup-key">Ejected</span><span style="color:#f97316">${_esc(ejected)}</span></div>` : ''}
    ${eq1 ? `<div class="popup-row"><span class="popup-key">Safety Equip 1</span><span style="font-size:0.7rem">${_esc(eq1)}</span></div>` : ''}
    ${eq2 ? `<div class="popup-row"><span class="popup-key">Safety Equip 2</span><span style="font-size:0.7rem">${_esc(eq2)}</span></div>` : ''}
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

    // Lazy-load parties and victims
    const ids = feats.slice(0, 8).map(f => String(f.properties.id));
    _fetchCrashDetail(ids);

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

