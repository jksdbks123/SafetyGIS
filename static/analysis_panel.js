// ============================================================================
//  ANALYSIS MODE
// ============================================================================

// ---- State -----------------------------------------------------------------
let _anaCountyData       = null;   // { county_name: { crash_ready, osm_pct, ... } }
let _anaCountyPollTimers = {};     // { county_name: intervalId } - per-county download polls
let _anaComputePollTimer = null;
const _dlPrevState = {};           // { county_name: { oTiles, cRecords, ts } } for speed calc
let _anaHandoffShown     = false;
let _anaBinsData         = null;   // cached result of /api/rankings/bins
let _anaActiveBinKey     = null;   // currently selected bin key
let _anaBinTab           = 'int';  // 'int' | 'seg'
let _anaComputeCounties  = new Set(); // counties selected for computation
let _anaFacilityModelRanking = null;
let _anaFacilityModelActiveGroup = null;
const _anaFacilityModelExpanded = new Set();

// ---- Mode switch -----------------------------------------------------------
function setAppMode(mode) {
  if (G_appMode === mode) return;
  const prevMode = G_appMode;
  G_appMode = mode;
  const isAnalysis = mode === 'analysis';
  const isDebug    = mode === 'debug';

  // Header mode buttons
  const btnI = document.getElementById('btn-mode-inspect');
  const btnA = document.getElementById('btn-mode-analysis');
  const btnD = document.getElementById('btn-mode-debug');
  if (btnI) { btnI.style.background = (!isAnalysis && !isDebug) ? '#1d4ed8' : 'transparent'; btnI.style.color = (!isAnalysis && !isDebug) ? '#fff' : '#6b7280'; }
  if (btnA) { btnA.style.background = isAnalysis ? '#7c3aed' : 'transparent'; btnA.style.color = isAnalysis ? '#fff' : '#6b7280'; }
  if (btnD) { btnD.style.background = isDebug ? '#059669' : 'transparent'; btnD.style.color = isDebug ? '#fff' : '#6b7280'; }

  // Side panels
  const inspPanel    = document.getElementById('panel');
  const anaPanel     = document.getElementById('analysis-panel');
  const dbgSbPanel   = document.getElementById('debug-sandbox-panel');
  if (inspPanel)  inspPanel.style.display  = (isAnalysis || isDebug) ? 'none' : 'block';
  if (anaPanel)   anaPanel.classList.toggle('hidden', !isAnalysis);
  if (dbgSbPanel) dbgSbPanel.classList.toggle('hidden', !isDebug);

  // Debug hint banner (keep for legacy cell-click mode when not in sandbox)
  const hint = document.getElementById('debug-hint');
  if (hint) hint.classList.add('hidden');

  // Lazy-init analysis panel drag/resize on first show (can't init while hidden)
  if (isAnalysis && anaPanel && !anaPanel._piInited) {
    requestAnimationFrame(() => {
      _initPanelInteractive(anaPanel, document.getElementById('analysis-drag-bar'),
        { minW: 220, minH: 200 });
    });
  }

  if (isAnalysis) {
    clearTimeout(_viewportTimer);
    clearTimeout(_crashPollTimer);
    if (G_drawMode) cancelDraw();
    if (G_pegmanMode) cancelPegmanMode();
    if (prevMode === 'debug' && typeof dbgSandbox !== 'undefined') dbgSandbox.onDeactivate();
    _clearDebugHighlight();
    closeDebugInfoPanel();
    _loadAnalysisCountyStatus();
    _anaLoadFacilityModelRankingResult(true);
  } else if (isDebug) {
    if (G_drawMode) cancelDraw();
    if (G_pegmanMode) cancelPegmanMode();
    if (typeof dbgSandbox !== 'undefined') dbgSandbox.onActivate();
    if (G_dataReady) scheduleViewportLoad();
  } else {
    // Returning to Inspect (from analysis or debug)
    Object.keys(_anaCountyPollTimers).forEach(name => {
      clearInterval(_anaCountyPollTimers[name]);
      delete _anaCountyPollTimers[name];
    });
    if (_anaComputePollTimer) { clearInterval(_anaComputePollTimer); _anaComputePollTimer = null; }
    if (prevMode === 'debug' && typeof dbgSandbox !== 'undefined') dbgSandbox.onDeactivate();
    _clearFacilityOverlay();
    _clearDebugHighlight();
    closeDebugInfoPanel();
    document.getElementById('rank-dash-panel')?.classList.remove('open');
    G_lastFacilityId = null;
    if (G_dataReady) scheduleViewportLoad();
  }
}

// ---- County grid -----------------------------------------------------------
async function _loadAnalysisCountyStatus() {
  try {
    const resp = await fetch('/api/data/county_status');
    if (!resp.ok) return;
    _anaCountyData = await resp.json();

    // First open: show handoff banner if cached data exists from Inspect session
    if (!_anaHandoffShown) {
      _anaHandoffShown = true;
      const hasData = Object.values(_anaCountyData).some(i => i.crash_ready || i.osm_pct >= 20);
      if (hasData) {
        const readyCt = Object.values(_anaCountyData).filter(i => i.analysis_ready).length;
        const partCt  = Object.values(_anaCountyData).filter(i => !i.analysis_ready && (i.crash_ready || i.osm_pct >= 20)).length;
        const parts = [];
        if (readyCt)  parts.push(`${readyCt} ready`);
        if (partCt)   parts.push(`${partCt} partial`);
        const banner = document.getElementById('ana-handoff-banner');
        const msg    = document.getElementById('ana-handoff-msg');
        if (banner && msg) {
          msg.textContent = `\u2191 Session data carried over \u2014 ${parts.join(', ')} from Inspect mode.`;
          banner.classList.remove('hidden');
        }
      }
    }

    _renderAnaCountyGrid();
    // Seed compute-county selection with any county that has crash data
    if (_anaComputeCounties.size === 0) {
      Object.entries(_anaCountyData).forEach(([name, info]) => {
        if (info.crash_ready) _anaComputeCounties.add(name);
      });
    }
    _renderAnaComputeCountyPicker();
    _updateAnaComputeBtn();
  } catch (_) {}
}

// -- Download speed helpers ------------------------------------------------

function _fmtNum(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'k';
  return String(n);
}

function _fmtSpeed(perSec, unit) {
  if (perSec <= 0) return '';
  if (perSec >= 1) return `${perSec.toFixed(1)} ${unit}/s`;
  return `${(perSec * 60).toFixed(0)} ${unit}/min`;
}

// Return {oSpeed, cSpeed} tiles/s and records/s from previous snapshot; update snapshot.
function _dlComputeSpeeds(name, info) {
  const prev = _dlPrevState[name];
  const now  = Date.now();
  let oSpeed = 0, cSpeed = 0;
  if (prev && prev.ts) {
    const dt = (now - prev.ts) / 1000;
    if (dt >= 0.5) {
      const dTiles = (info.osm_tile_cached || 0) - (prev.oTiles || 0);
      const dRec   = (info.crash_records_fetched || 0) - (prev.cRecords || 0);
      oSpeed = dTiles >= 0 ? dTiles / dt : 0;
      cSpeed = dRec   >= 0 ? dRec   / dt : 0;
    }
  }
  _dlPrevState[name] = {
    oTiles:   info.osm_tile_cached        || 0,
    cRecords: info.crash_records_fetched  || 0,
    ts: now,
  };
  return { oSpeed, cSpeed };
}

function _renderActiveDownloads() {
  const wrap = document.getElementById('ana-active-downloads');
  if (!wrap || !_anaCountyData) return;

  const active = Object.entries(_anaCountyData)
    .filter(([, info]) => info.fetching_crash || info.fetching_osm);

  if (active.length === 0) {
    if (wrap.classList.contains('has-items')) {
      wrap.classList.remove('has-items');
      wrap.innerHTML = '';
    }
    return;
  }

  wrap.classList.add('has-items');
  wrap.innerHTML = active.map(([name, info]) => {
    const label  = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const osmPct = Math.min(info.osm_pct || 0, 100);

    const { oSpeed, cSpeed } = _dlComputeSpeeds(name, info);

    // -- OSM row --
    const pbf = info.pbf_progress || {};
    const usingPbf = info.fetching_osm && info.osm_fetch_method === 'pbf';
    const osmSpeedStr = info.fetching_osm && !usingPbf ? _fmtSpeed(oSpeed, 't') : '';
    const remaining   = Math.max(0, (info.osm_tile_total || 0) - (info.osm_tile_cached || 0));
    let osmStatus;
    let osmBarPct = osmPct;
    if (info.osm_pct >= 95 && !info.fetching_osm) {
      osmStatus = '<span style="color:#4ade80">OK done</span>';
      osmBarPct = 100;
    } else if (usingPbf) {
      const phase = pbf.phase || 'starting';
      const phaseDone = pbf.phase_done || 0;
      const phaseTotal = pbf.phase_total || 0;
      const phasePct = phaseTotal > 0 ? Math.max(0, Math.min(100, phaseDone / phaseTotal * 100)) : 0;
      if (phase === 'downloading_pbf' && pbf.phase_total > 0) {
        osmStatus = `PBF ${Math.round(phasePct)}%`;
        osmBarPct = Math.max(4, Math.min(35, phasePct * 0.35));
      } else if (phase === 'scanning_nodes') {
        const matched = pbf.phase_done ? pbf.phase_done.toLocaleString() : '';
        osmStatus = matched ? `PBF nodes ${matched}` : 'PBF nodes';
        osmBarPct = Math.max(osmPct, 42);
      } else if (phase === 'indexing_nodes') {
        osmStatus = pbf.phase_total
          ? `PBF node index ${pbf.phase_done.toLocaleString()}/${pbf.phase_total.toLocaleString()}`
          : 'PBF node index';
        osmBarPct = Math.max(osmPct, 48);
      } else if (phase === 'scanning_ways') {
        const matched = pbf.phase_done ? pbf.phase_done.toLocaleString() : '';
        osmStatus = matched ? `PBF ways ${matched}` : 'PBF ways';
        osmBarPct = Math.max(osmPct, 58);
      } else if (phase === 'writing_tiles' && pbf.phase_total > 0) {
        osmStatus = `PBF tiles ${pbf.phase_done}/${pbf.phase_total}`;
        osmBarPct = Math.max(osmPct, 65 + phasePct * 0.35);
      } else if (phase === 'done') {
        osmStatus = '<span style="color:#4ade80">OK done</span>';
        osmBarPct = 100;
      } else if (pbf.error) {
        osmStatus = `<span style="color:#fca5a5">${pbf.error}</span>`;
        osmBarPct = Math.max(osmPct, 8);
      } else {
        osmStatus = 'PBF starting';
        osmBarPct = Math.max(osmPct, 8);
      }
    } else if (info.fetching_osm) {
      const parts = [`${osmPct.toFixed(0)}%`];
      if (remaining > 0) parts.push(`${remaining} left`);
      if (osmSpeedStr)   parts.push(`<span class="ana-dl-speed">${osmSpeedStr}</span>`);
      osmStatus = parts.join('  -  ');
    } else {
      osmStatus = `${osmPct.toFixed(0)}%`;
    }
    osmBarPct = Math.max(0, Math.min(100, osmBarPct));
    const osmBarClass = osmBarPct >= 95 ? 'ana-dl-bar-fill ana-dl-bar-done' : 'ana-dl-bar-fill ana-dl-bar-osm';

    // -- Crash row --
    const cRec  = info.crash_records_fetched || 0;
    const cYear = info.crash_current_year    || 0;
    // Crash bar: year-index out of 6 gives rough estimate
    const crashBarPct = info.crash_ready ? 100
      : (cYear >= 2019 ? Math.round((cYear - 2018) / 6 * 100) : (cRec > 0 ? 15 : 0));
    const crashSpeedStr = info.fetching_crash ? _fmtSpeed(cSpeed, 'rec') : '';
    let crashStatus;
    if (info.crash_ready) {
      crashStatus = '<span style="color:#4ade80">OK done</span>';
    } else if (info.fetching_crash) {
      const parts = [];
      if (cRec > 0)       parts.push(`${_fmtNum(cRec)} rec`);
      if (cYear >= 2019)  parts.push(`yr ${cYear}`);
      if (crashSpeedStr)  parts.push(`<span class="ana-dl-speed">${crashSpeedStr}</span>`);
      crashStatus = parts.length ? parts.join('  -  ') : 'fetching\u2026';
    } else {
      crashStatus = '\u2014';
    }
    const crashBarClass = info.crash_ready ? 'ana-dl-bar-fill ana-dl-bar-done' : 'ana-dl-bar-fill ana-dl-bar-crash';

    // ETA hint (tiles remaining / speed)
    let etaStr = '';
    if (info.fetching_osm && oSpeed > 0.1 && remaining > 0) {
      const secs = remaining / oSpeed;
      etaStr = secs < 60  ? `~${Math.round(secs)}s`
             : secs < 3600 ? `~${Math.round(secs/60)}m`
             : `~${(secs/3600).toFixed(1)}h`;
    }

    return `<div class="ana-dl-card">
  <div class="ana-dl-title">
    <span class="ana-dl-name">${label}</span>
    <span class="ana-dl-eta">${etaStr}</span>
  </div>
  <div class="ana-dl-row">
    <span class="ana-dl-tag ana-dl-tag-osm">OSM</span>
    <div class="ana-dl-bar-wrap"><div class="${osmBarClass}" style="width:${osmBarPct}%"></div></div>
    <span class="ana-dl-status">${osmStatus}</span>
  </div>
  <div class="ana-dl-row">
    <span class="ana-dl-tag ana-dl-tag-crash">CRS</span>
    <div class="ana-dl-bar-wrap"><div class="${crashBarClass}" style="width:${crashBarPct}%"></div></div>
    <span class="ana-dl-status">${crashStatus}</span>
  </div>
</div>`;
  }).join('');
}

function _renderAnaCountyGrid() {
  const grid = document.getElementById('ana-county-grid');
  if (!grid || !_anaCountyData) return;

  // Sort: analysis_ready first, then partial, then rest - alphabetical within group
  const entries = Object.entries(_anaCountyData).sort(([an, ai], [bn, bi]) => {
    const aScore = ai.analysis_ready ? 2 : (ai.crash_ready || ai.osm_pct > 0 ? 1 : 0);
    const bScore = bi.analysis_ready ? 2 : (bi.crash_ready || bi.osm_pct > 0 ? 1 : 0);
    return bScore - aScore || an.localeCompare(bn);
  });

  grid.innerHTML = entries.map(([name, info]) => {
    const label     = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const chipClass = _countyChipClass(info);
    const remaining = Math.max(0, (info.osm_tile_total || 0) - (info.osm_tile_cached || 0));
    let titleText;
    if (chipClass === 'ready')   titleText = `${label}: analysis-ready (crash \u2713, OSM ${info.osm_pct}%)`;
    else if (chipClass === 'loading') titleText = `${label}: downloading\u2026 OSM ${info.osm_pct}% (${remaining} tiles left)`;
    else if (chipClass === 'partial') titleText = `${label}: partial \u2014 crash ${info.crash_ready ? '\u2713' : '\u2717'}, OSM ${info.osm_pct}% \u2014 click to complete`;
    else                         titleText = `${label}: no data \u2014 click to download`;

    // Ready counties are not clickable (nothing to do)
    const clickAttr = info.analysis_ready
      ? ''
      : `onclick="_anaClickCounty('${name}')"`;

    return `<span class="county-chip ${chipClass}" id="ana-chip-${name}"
               title="${titleText}" ${clickAttr}>${label}</span>`;
  }).join('');
}

async function _anaClickCounty(name) {
  if (!_anaCountyData) return;
  const info = _anaCountyData[name];
  if (!info || info.analysis_ready) return;

  // Update chip to loading state immediately
  const chip = document.getElementById(`ana-chip-${name}`);
  if (chip) { chip.className = 'county-chip loading'; chip.onclick = null; }

  try {
    await Promise.all([
      fetch(`/api/data/county/${name}/fetch_crash`, { method: 'POST' }).catch(() => {}),
      fetch(`/api/data/county/${name}/fetch_osm_pbf`, { method: 'POST' }).catch(() => {}),
    ]);
  } catch (_) {}

  // Show download card immediately without waiting for first poll
  if (_anaCountyData[name]) {
    _anaCountyData[name].fetching_crash = true;
    _anaCountyData[name].fetching_osm = true;
    _anaCountyData[name].osm_fetch_method = 'pbf';
    _anaCountyData[name].pbf_progress = {
      active: true,
      complete: false,
      counties: [name],
      phase: 'starting',
      phase_done: 0,
      phase_total: 0,
      error: '',
    };
  }
  _renderActiveDownloads();

  // Poll until this county is ready
  if (_anaCountyPollTimers[name]) clearInterval(_anaCountyPollTimers[name]);
  _anaCountyPollTimers[name] = setInterval(() => _pollAnaCounty(name), 2000);
}

async function _pollAnaCounty(name) {
  try {
    const resp = await fetch(`/api/data/county/${name}/status`);
    if (!resp.ok) return;
    const fresh = await resp.json();
    // Only re-render if this county's state actually changed
    const prev = _anaCountyData?.[name];
    const next = fresh[name];
    const changed = !prev
      || prev.analysis_ready !== next?.analysis_ready
      || prev.osm_pct       !== next?.osm_pct
      || prev.crash_ready   !== next?.crash_ready
      || prev.fetching_crash !== next?.fetching_crash
      || prev.fetching_osm  !== next?.fetching_osm
      || prev.pbf_progress?.phase !== next?.pbf_progress?.phase
      || prev.pbf_progress?.phase_done !== next?.pbf_progress?.phase_done
      || prev.pbf_progress?.phase_total !== next?.pbf_progress?.phase_total
      || prev.pbf_progress?.error !== next?.pbf_progress?.error;
    _anaCountyData = { ...(_anaCountyData || {}), ...fresh };
    if (changed) { _renderAnaCountyGrid(); _updateAnaComputeBtn(); }
    _renderActiveDownloads();

    const info = _anaCountyData[name];
    const stillActive = info && (info.fetching_crash || info.fetching_osm);
    if (!info || info.analysis_ready || !stillActive) {
      clearInterval(_anaCountyPollTimers[name]);
      delete _anaCountyPollTimers[name];
      _renderActiveDownloads(); // clear card now that download finished
    }
  } catch (_) {}
}

function _updateAnaComputeBtn() {
  const btn = document.getElementById('ana-compute-btn');
  if (!btn || !_anaCountyData) return;
  const hasSelection = [..._anaComputeCounties].some(n => _anaCountyData[n]?.crash_ready);
  btn.disabled = !hasSelection;
  btn.title = hasSelection ? '' : 'Select at least one county with crash data above';
}

// ---- Compute rankings ------------------------------------------------------
async function anaComputeRankings() {
  const btn = document.getElementById('ana-compute-btn');
  if (btn) { btn.disabled = true; btn.textContent = '\u23f3 Computing\u2026'; }
  _anaShowProgress(0, 'Starting rankings computation\u2026');

  const wF = parseFloat(document.getElementById('ana-w-fatal')?.value)  || 10;
  const wI = parseFloat(document.getElementById('ana-w-injury')?.value) || 2;
  const wP = parseFloat(document.getElementById('ana-w-pdo')?.value)    || 0.2;

  const allowIncomplete = document.getElementById('ana-allow-incomplete')?.checked;
  const minOsm = allowIncomplete ? 0 : 80;

  // Build selected county list (only those with crash data)
  const countyList = [..._anaComputeCounties].filter(n => _anaCountyData?.[n]?.crash_ready).join(',');

  const params = new URLSearchParams({ weights: `${wF},${wI},${wP}`, min_osm_pct: minOsm });
  if (countyList) params.set('counties', countyList);

  try {
    const resp = await fetch(`/api/rankings/compute?${params}`, { method: 'POST' });
    if (resp.status === 409) { _anaSetProgress(0, 'Already running \u2014 polling\u2026'); }
    else if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.detail || resp.statusText); }
    if (_anaComputePollTimer) clearInterval(_anaComputePollTimer);
    _anaComputePollTimer = setInterval(_pollAnaCompute, 1500);
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = '\u25b6 Compute Rankings'; }
    _anaSetProgress(0, `Error: ${e.message}`);
  }
}

async function _pollAnaCompute() {
  try {
    const resp = await fetch('/api/rankings/status');
    if (!resp.ok) return;
    const data = await resp.json();
    _anaSetProgress(data.progress ?? 0, data.message || '');

    if (data.status === 'done') {
      clearInterval(_anaComputePollTimer);
      _anaComputePollTimer = null;
      _anaSetProgress(100, 'Done! Loading available bins\u2026');
      const btn = document.getElementById('ana-compute-btn');
      if (btn) { btn.disabled = false; btn.textContent = '\u2713 Recompute'; }
      await _anaLoadBins();
    } else if (data.status === 'error') {
      clearInterval(_anaComputePollTimer);
      _anaComputePollTimer = null;
      _anaSetProgress(0, `Error: ${data.message}`);
      const btn = document.getElementById('ana-compute-btn');
      if (btn) { btn.disabled = false; btn.textContent = '\u25b6 Compute Rankings'; }
    }
  } catch (_) {}
}

async function anaComputeFacilityModelRanking() {
  const btn = document.getElementById('ana-fm-compute-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Computing...'; }
  _anaSetFacilityModelProgress(10, 'Computing facility-model ranking...');
  try {
    const resp = await fetch('/api/rankings/facility_model/compute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ county: 'sacramento', force_refresh: true }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || resp.statusText);
    }
    _anaSetFacilityModelProgress(75, 'Loading ranked facilities...');
    await _anaLoadFacilityModelRankingResult(false);
    _anaSetFacilityModelProgress(100, 'Facility-model ranking ready.');
  } catch (err) {
    _anaSetFacilityModelProgress(0, `Error: ${err.message}`);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Compute Facility Model Ranking'; }
  }
}

function _anaSetFacilityModelProgress(pct, msg) {
  document.getElementById('ana-fm-progress-wrap')?.classList.remove('hidden');
  const bar = document.getElementById('ana-fm-progress-bar');
  const msgEl = document.getElementById('ana-fm-progress-msg');
  if (bar) bar.style.width = Math.min(100, Math.max(0, pct)) + '%';
  if (msgEl) msgEl.textContent = msg;
}

async function _anaLoadFacilityModelRankingResult(silent = false) {
  try {
    const resp = await fetch('/api/rankings/facility_model/result');
    if (!resp.ok) {
      if (!silent) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || resp.statusText);
      }
      return;
    }
    _anaFacilityModelRanking = await resp.json();
    _anaRenderFacilityModelRanking();
  } catch (err) {
    if (!silent) _anaSetFacilityModelProgress(0, `Error: ${err.message}`);
  }
}

function _rankDisplayFeatures(features) {
  return (features || []).map((feat, idx) => ({
    ...feat,
    properties: {
      ...(feat.properties || {}),
      rank_worst: idx + 1,
      county: feat.properties?.county || 'sacramento',
      total_5yr: feat.properties?.total_5yr ?? feat.properties?.crash_total ?? 0,
      fatal_5yr: feat.properties?.fatal_5yr ?? feat.properties?.fatal ?? 0,
      severe_5yr: feat.properties?.severe_5yr ?? feat.properties?.severe_injury ?? 0,
      epdo_weights: feat.properties?.epdo_weights || _anaFacilityModelRanking?.metadata?.epdo_weights || {},
      year_window: feat.properties?.year_window || _anaFacilityModelRanking?.metadata?.year_window || 5,
    },
  }));
}

function _anaRenderFacilityModelRanking() {
  const data = _anaFacilityModelRanking;
  if (!data) return;
  const wrap = document.getElementById('ana-fm-results-wrap');
  const meta = document.getElementById('ana-fm-meta');
  const groupsEl = document.getElementById('ana-fm-groups');
  const results = document.getElementById('ana-fm-results');
  const labelEl = document.getElementById('ana-fm-group-label');
  if (wrap) wrap.classList.remove('hidden');
  const m = data.metadata || {};
  if (meta) {
    meta.textContent = `${(m.ranked_nonzero_count || 0).toLocaleString()} matched facilities · ${(m.crash_count || 0).toLocaleString()} crashes · ${(Object.keys(data.peer_groups || {}).length).toLocaleString()} peer groups`;
  }
  if (!groupsEl || !results) return;
  _anaSeedFacilityModelTree(data.group_tree);
  groupsEl.innerHTML = `<div class="ana-rank-col-hdr" style="color:#9ca3af">Classification Tree</div>` +
    _anaRenderFacilityModelTree(data.group_tree, 0, 'fm');
  const firstKey = _anaFacilityModelActiveGroup || _anaFirstRankedFacilityModelGroup(data.group_tree);
  if (firstKey) _anaLoadFacilityModelGroup(firstKey, true);
  else {
    if (labelEl) labelEl.textContent = '';
    results.innerHTML = '<div style="font-size:0.65rem;color:#6b7280;padding:8px 0">No peer groups have matched crashes yet.</div>';
    _renderRankingsMap([]);
  }
}

function _anaSeedFacilityModelTree(root) {
  if (!root || _anaFacilityModelExpanded.size) return;
  for (const child of root.children || []) {
    _anaFacilityModelExpanded.add(`fm|${child.key || ''}:${child.value || ''}`);
  }
  const expandRankedPath = (node, path) => {
    for (const child of node.children || []) {
      const childPath = `${path}|${child.key || ''}:${child.value || ''}`;
      const hasRanked = !!(_anaFacilityModelRanking?.group_rankings?.[child.group_key]?.features || []).length;
      if (hasRanked || expandRankedPath(child, childPath)) {
        _anaFacilityModelExpanded.add(childPath);
        return true;
      }
    }
    return false;
  };
  expandRankedPath(root, 'fm');
}

function _anaFirstRankedFacilityModelGroup(node) {
  if (!node) return null;
  if (node.is_leaf && node.group_key && (_anaFacilityModelRanking?.group_rankings?.[node.group_key]?.features || []).length) {
    return node.group_key;
  }
  for (const child of node.children || []) {
    const found = _anaFirstRankedFacilityModelGroup(child);
    if (found) return found;
  }
  return null;
}

function _anaRenderFacilityModelTree(node, depth, path) {
  if (!node) return '';
  const children = node.children || [];
  if (!children.length) return '';
  return children.map((child, idx) => {
    const childPath = `${path}|${child.key || depth}:${child.value || idx}`;
    const hasRanked = !!(_anaFacilityModelRanking?.group_rankings?.[child.group_key]?.features || []).length;
    const isLeaf = !!child.is_leaf;
    const expanded = _anaFacilityModelExpanded.has(childPath);
    const active = child.group_key && child.group_key === _anaFacilityModelActiveGroup;
    const indent = depth * 12;
    const count = `${(child.nonzero_count || 0).toLocaleString()}/${(child.facility_count || 0).toLocaleString()}`;
    const risk = `Max ${(child.max_epdo || 0).toFixed(1)} · P95 ${(child.p95_epdo || 0).toFixed(1)}`;
    if (isLeaf) {
      const cls = hasRanked ? `available${active ? ' selected' : ''}` : 'sparse';
      const onclick = hasRanked ? `onclick="_anaLoadFacilityModelGroup('${_escAttr(child.group_key || '')}')"` : '';
      return `<div class="bin-tree-leaf ${cls}" style="padding-left:${indent}px" ${onclick} title="${_esc(child.group_key || '')}">
        <span class="bin-tree-bullet" style="color:${hasRanked ? '#b91c1c' : '#374151'}">&#9679;</span>
        <span class="bin-tree-label">${_esc(child.label || child.value || '')}</span>
        <span class="bin-tree-count">${count} · ${risk}</span>
      </div>`;
    }
    return `<div class="bin-tree-node">
      <div class="bin-tree-row ${child.nonzero_count ? 'has-data' : 'no-data'}" style="padding-left:${indent}px" onclick="_anaToggleFacilityModelNode('${_escAttr(childPath)}')">
        <span class="bin-tree-arrow">${expanded ? '&#9660;' : '&#9654;'}</span>
        <span class="bin-tree-label">${_esc(child.label || child.value || '')}</span>
        <span class="bin-tree-count">${count} · ${risk}</span>
      </div>
      ${expanded ? _anaRenderFacilityModelTree(child, depth + 1, childPath) : ''}
    </div>`;
  }).join('');
}

function _anaToggleFacilityModelNode(path) {
  if (_anaFacilityModelExpanded.has(path)) _anaFacilityModelExpanded.delete(path);
  else _anaFacilityModelExpanded.add(path);
  _anaRenderFacilityModelRanking();
}

function _anaFacilityGroupLabel(key) {
  const stats = _anaFacilityModelRanking?.peer_groups?.[key];
  if (stats?.label) return stats.label;
  return String(key || '').split('|').join(' / ');
}

function _escAttr(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function _anaLoadFacilityModelGroup(groupKey, preserveTree = false) {
  const data = _anaFacilityModelRanking;
  if (!data) return;
  _anaFacilityModelActiveGroup = groupKey;
  if (!preserveTree) _anaRenderFacilityModelRanking();
  const features = _rankDisplayFeatures(data.group_rankings?.[groupKey]?.features || []);
  _renderRankingsMap(features);
  if (typeof LAYER_VISIBILITY !== 'undefined') {
    LAYER_VISIBILITY['rankings-worst'] = true;
    _syncLayerVisibility?.('rankings-worst');
  }
  _fitRankedFacilities(features);
  const labelEl = document.getElementById('ana-fm-group-label');
  const results = document.getElementById('ana-fm-results');
  const stats = data.peer_groups?.[groupKey] || {};
  if (labelEl) {
    labelEl.innerHTML = `<div style="font-family:inherit;color:#9ca3af">${_esc(_anaFacilityGroupLabel(groupKey))}</div>
      <div style="margin-top:3px;color:#6b7280">n=${(stats.facility_count || 0).toLocaleString()} · matched=${(stats.nonzero_count || 0).toLocaleString()} · mean=${(stats.mean_epdo || 0).toFixed(1)} · P50=${(stats.p50_epdo || 0).toFixed(1)} · P75=${(stats.p75_epdo || 0).toFixed(1)} · P90=${(stats.p90_epdo || 0).toFixed(1)} · P95=${(stats.p95_epdo || 0).toFixed(1)}</div>`;
  }
  if (!results) return;
  results.innerHTML = `<div class="ana-rank-col-hdr" style="color:#9ca3af">Ranked Within Selected Peer Group</div>` +
    features.map((feat, idx) => {
      const p = feat.properties || {};
      const epdo = typeof p.epdo_score === 'number' ? p.epdo_score.toFixed(1) : '-';
      const pct = p.epdo_percentile != null ? Math.round(p.epdo_percentile) : null;
      const band = p.epdo_band || '';
      const config = p.geometric_configuration || p.subtype || p.highway || '';
      const speed = p.speed_mph ? `${p.speed_mph} mph` : (p.approach_max_speed_mph ? `${p.approach_max_speed_mph} mph` : '');
      const lanes = p.lanes ? `${p.lanes} lanes` : (p.approach_max_lanes ? `${p.approach_max_lanes} lanes` : '');
      const context = [p.facility_type, config, speed, lanes].filter(Boolean).join(' · ');
      return `<div class="ana-rank-row" onclick="openRankDash('${p.facility_id}')" title="Click to open crash dashboard">
        <span class="ana-rank-num">#${p.peer_rank || idx + 1}</span> ${_rankFacilityLabel(p)}<br>
        <span class="ana-rank-epdo worst">EPDO ${epdo}</span>
        <span style="color:#6b7280;font-size:0.58rem"> ${_rankFatal(p)}K ${_rankSevere(p)}S ${_rankTotal(p)} total</span><br>
        <span style="color:#6b7280;font-size:0.58rem">${_esc(context)} ${pct != null ? `· P${pct} ${band}` : ''}</span>
      </div>`;
    }).join('');
}
function _fitRankedFacilities(features) {
  if (!features?.length || !map) return;
  const coords = [];
  features.slice(0, 80).forEach(feat => {
    const geom = feat.geometry || {};
    if (geom.type === 'Point' && Array.isArray(geom.coordinates)) coords.push(geom.coordinates);
    if (geom.type === 'LineString') (geom.coordinates || []).forEach(c => coords.push(c));
  });
  const lngs = coords.map(c => c[0]).filter(Number.isFinite);
  const lats = coords.map(c => c[1]).filter(Number.isFinite);
  if (!lngs.length) return;
  map.fitBounds(
    [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
    { padding: 70, maxZoom: 12, duration: 900 }
  );
}

function _anaShowProgress(pct, msg) {
  document.getElementById('ana-progress-wrap')?.classList.remove('hidden');
  _anaSetProgress(pct, msg);
}

function _anaSetProgress(pct, msg) {
  const bar = document.getElementById('ana-progress-bar');
  const msgEl = document.getElementById('ana-progress-msg');
  if (bar)   bar.style.width = Math.min(100, pct) + '%';
  if (msgEl) msgEl.textContent = msg;
}

// ---- Bin browser -----------------------------------------------------------
async function _anaLoadBins() {
  try {
    const resp = await fetch('/api/rankings/bins');
    if (!resp.ok) return;
    _anaBinsData = await resp.json();
    _anaRenderBinChips();

    const section = document.getElementById('ana-bins-section');
    if (section) section.style.display = 'block';

    // Update meta label
    const meta = document.getElementById('ana-bins-meta');
    if (meta && _anaBinsData) {
      const total  = Object.keys(_anaBinsData.bins || {}).length;
      const avail  = Object.values(_anaBinsData.bins || {}).filter(b => b.has_data).length;
      meta.textContent = `${avail}/${total} bins with data`;
    }
  } catch (_) {}
}

function _anaSetBinTab(tab) {
  _anaBinTab = tab;
  document.getElementById('ana-tab-int')?.classList.toggle('active', tab === 'int');
  document.getElementById('ana-tab-seg')?.classList.toggle('active', tab === 'seg');
  document.getElementById('ana-bins-int')?.classList.toggle('hidden', tab !== 'int');
  document.getElementById('ana-bins-seg')?.classList.toggle('hidden', tab !== 'seg');
}

// ---- Bin tree constants & state --------------------------------------------

// Taxonomy: defines tree level order and human-readable value labels per facility type.
// To add a new bin dimension: add one entry to the relevant array here - no other
// tree-building logic needs to change.
const BIN_TAXONOMY = {
  seg: [
    { key: 'road_class',  labels: { highway: 'Highway', arterial: 'Arterial', collector: 'Collector', local: 'Local' } },
    { key: 'speed_bin',   labels: {} },   // raw value used as label (e.g. "<=25mph")
    { key: 'lane_bin',    labels: { '1-2': '1-2 lanes', '3-4': '3-4 lanes', '5+': '5+ lanes' } },
  ],
  int: [
    { key: 'control_type', labels: { signal: 'Signal', stop: 'All-Way Stop', give_way: 'Yield', uncontrolled: 'Uncontrolled' } },
    { key: 'road_class',   labels: { highway: 'Highway', arterial: 'Arterial', collector: 'Collector', local: 'Local' } },
    { key: 'speed_bin',    labels: {} },
    { key: 'leg_bin',      labels: { 'T-int': '3-leg', '4-leg': '4-leg', multi: '5+-leg' } },
  ],
};

// Tracks which tree node paths are expanded.  Path format: "seg|highway" or "int|stop|arterial".
// Default on load: first-level nodes are expanded (seeded in _anaRenderBinChips).
const _binTreeExpanded = new Set();

function _anaBinLabel(key) {
  // Convert bin_key to human-readable label (still used by anaLoadRanking bin-label display)
  // int|signal|arterial|26-40mph|4-leg  ->  Signal  -  Arterial  -  26-40mph  -  4-leg
  // seg|arterial|26-40mph|1-2           ->  Arterial  -  26-40mph  -  1-2 lanes
  const prefix = key.split('|')[0];
  const tax    = BIN_TAXONOMY[prefix] || [];
  return key.split('|').slice(1).map((val, i) => {
    const lvl = tax[i];
    return (lvl?.labels?.[val]) || val;
  }).join(' \u00b7 ');
}

// ---- Tree builder -----------------------------------------------------------

/**
 * Build a nested tree from a flat bins dict.
 * Returns: { [val]: { _count, _hasData, _children: { ... }, _leaf?: {key,info} } }
 * _leaf is set only at the deepest level (the actual bin).
 */
function _buildBinTree(bins, prefix) {
  const taxonomy = BIN_TAXONOMY[prefix] || [];
  const root = {};

  for (const [binKey, info] of Object.entries(bins)) {
    if (!binKey.startsWith(prefix + '|')) continue;
    const parts = binKey.split('|').slice(1);   // e.g. ['local','<=25mph','1-2']
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const val = parts[i];
      if (!node[val]) node[val] = { _count: 0, _hasData: false, _children: {} };
      node[val]._count   += info.count || 0;
      node[val]._hasData  = node[val]._hasData || !!info.has_data;
      if (i === parts.length - 1) {
        node[val]._leaf = { key: binKey, info };
      }
      node = node[val]._children;
    }
  }
  return root;
}

// ---- Tree renderer ----------------------------------------------------------

/**
 * Recursively render tree nodes into HTML.
 * @param {object} node     - current level { val: {_count,_hasData,_children,_leaf?} }
 * @param {Array}  taxonomy - BIN_TAXONOMY level definitions (for labels)
 * @param {number} depth    - current depth (0 = first level)
 * @param {string} path     - parent path, e.g. "seg|highway"
 */
function _renderBinTree(node, taxonomy, depth, path) {
  const indent = depth * 12;
  let html = '';

  // Sort entries: has_data nodes first, then by count desc
  const entries = Object.entries(node).sort(([,a],[,b]) => {
    if (a._hasData !== b._hasData) return a._hasData ? -1 : 1;
    return (b._count || 0) - (a._count || 0);
  });

  for (const [val, meta] of entries) {
    const levelDef  = taxonomy[depth] || {};
    const label     = levelDef.labels?.[val] || val;
    const nodePath  = path ? `${path}|${val}` : val;
    const isLeaf    = !!meta._leaf;
    const countStr  = meta._count ? meta._count.toLocaleString() : '0';

    if (isLeaf) {
      // Leaf: clickable bin
      const isActive = meta._leaf.key === _anaActiveBinKey;
      const cls      = meta._hasData
        ? `available${isActive ? ' selected' : ''}`
        : 'sparse';
      const onclick  = meta._hasData
        ? `onclick="anaLoadRanking('${meta._leaf.key}')"`
        : '';
      html += `<div class="bin-tree-leaf ${cls}" style="padding-left:${indent}px" ${onclick} title="${meta._leaf.key}">
        <span class="bin-tree-bullet" style="color:${meta._hasData ? '#7c3aed' : '#374151'}">&#9679;</span>
        <span class="bin-tree-label">${label}</span>
        <span class="bin-tree-count">${countStr}</span>
      </div>`;
    } else {
      // Parent: collapsible
      const isExpanded = _binTreeExpanded.has(nodePath);
      const arrow      = isExpanded ? '&#9660;' : '&#9654;';
      const dataClass  = meta._hasData ? 'has-data' : 'no-data';
      html += `<div class="bin-tree-node">
        <div class="bin-tree-row ${dataClass}" style="padding-left:${indent}px"
             onclick="_toggleBinNode('${nodePath}')">
          <span class="bin-tree-arrow">${arrow}</span>
          <span class="bin-tree-label">${label}</span>
          <span class="bin-tree-count">${countStr}</span>
        </div>`;
      if (isExpanded) {
        html += _renderBinTree(meta._children, taxonomy, depth + 1, nodePath);
      }
      html += `</div>`;
    }
  }
  return html;
}

function _toggleBinNode(path) {
  if (_binTreeExpanded.has(path)) {
    _binTreeExpanded.delete(path);
  } else {
    _binTreeExpanded.add(path);
  }
  _anaRenderBinChips();
}

// ---- Main render entry point ------------------------------------------------

function _anaRenderBinChips() {
  if (!_anaBinsData?.bins) return;

  const intEl = document.getElementById('ana-bins-int');
  const segEl = document.getElementById('ana-bins-seg');
  if (!intEl || !segEl) return;

  const bins = _anaBinsData.bins;

  // Seed first-level expansions on very first render
  if (_binTreeExpanded.size === 0) {
    for (const key of Object.keys(bins)) {
      const prefix = key.split('|')[0];
      const firstVal = key.split('|')[1];
      if (firstVal) _binTreeExpanded.add(`${prefix}|${firstVal}`);
    }
  }

  // Build and render segment tree
  const segTree = _buildBinTree(bins, 'seg');
  segEl.innerHTML = Object.keys(segTree).length
    ? _renderBinTree(segTree, BIN_TAXONOMY.seg, 0, 'seg')
    : '<div style="font-size:0.65rem;color:#4b5563">No segment bins computed yet.</div>';

  // Build and render intersection tree
  const intTree = _buildBinTree(bins, 'int');
  intEl.innerHTML = Object.keys(intTree).length
    ? _renderBinTree(intTree, BIN_TAXONOMY.int, 0, 'int')
    : '<div style="font-size:0.65rem;color:#4b5563">No intersection bins computed yet.</div>';
}

// ---- Load a specific bin's rankings ----------------------------------------
async function anaLoadRanking(binKey) {
  if (!binKey) return;
  _anaActiveBinKey = binKey;

  // Re-render chips so selected state updates
  _anaRenderBinChips();

  // Switch to correct tab
  const tab = binKey.startsWith('int|') ? 'int' : 'seg';
  if (_anaBinTab !== tab) _anaSetBinTab(tab);

  // Show loading state
  const wrap = document.getElementById('ana-rank-results-wrap');
  const results = document.getElementById('ana-rank-results');
  const binLabel = document.getElementById('ana-bin-label');
  if (wrap) wrap.classList.remove('hidden');
  if (results) results.innerHTML = '<div style="font-size:0.65rem;color:#6b7280;padding:8px 0">Loading\u2026</div>';
  if (binLabel) binLabel.textContent = binKey;

  try {
    const resp = await fetch(`/api/rankings/bin/${encodeURIComponent(binKey)}`);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      if (results) results.innerHTML = `<div style="font-size:0.65rem;color:#f87171;padding:8px 0">${err.detail || 'Error loading rankings.'}</div>`;
      return;
    }
    const data = await resp.json();

    if (data.insufficient_data) {
      if (results) results.innerHTML = '<div style="font-size:0.65rem;color:#6b7280;padding:8px 0">Not enough facilities (&lt;20) for this bin.</div>';
      return;
    }

    // Render on map
    const facilities = data.top_by_epdo || [];
    _renderRankingsMap(facilities);
    if (typeof LAYER_VISIBILITY !== 'undefined') {
      LAYER_VISIBILITY['rankings-worst'] = true;
      if (typeof _syncLayerVisibility === 'function') {
        _syncLayerVisibility('rankings-worst');
      }
    }

    // Fly to bounding box of shown facilities
    if (facilities.length) {
      const lngs = facilities.map(f => f.geometry?.coordinates?.[0]).filter(Number.isFinite);
      const lats = facilities.map(f => f.geometry?.coordinates?.[1]).filter(Number.isFinite);
      if (lngs.length) {
        map.fitBounds(
          [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
          { padding: 60, maxZoom: 13, duration: 900 }
        );
      }
    }

    // Render table
    _anaRenderRankTable(facilities, data.group_stats || {});

    // Update bin label with count
    if (binLabel && data.facility_count) {
      binLabel.textContent = `${binKey}  \u00b7  ${data.facility_count} facilities`;
    }

    // Scroll the results into view within the analysis panel
    setTimeout(() => {
      document.getElementById('ana-rank-results-wrap')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, 200);
  } catch (err) {
    if (results) results.innerHTML = `<div style="font-size:0.65rem;color:#f87171;padding:8px 0">Error: ${err.message}</div>`;
  }
}

const _BAND_COLORS  = { critical: '#ef4444', high_priority: '#f97316', elevated: '#facc15',
                        typical: '#9ca3af', low: '#4b5563',
                        above_median: '#9ca3af', below_median: '#4b5563' };
const _BAND_SHORT   = { critical: 'Critical', high_priority: 'High', elevated: 'Elevated',
                        typical: 'Typical', low: 'Low',
                        above_median: 'Above Median', below_median: 'Below Median' };
const _BAND_LABELS  = { critical: 'Critical (top 5%)', high_priority: 'High Priority (top 10%)',
                        elevated: 'Elevated (top 25%)', typical: 'Typical', low: 'Low',
                        above_median: 'Above Median',
                        below_median: 'Below Median' };
const _DBAND_BG     = { critical: '#7f1d1d', high_priority: '#431407', elevated: '#422006',
                        typical: '#1f2937', low: '#111827',
                        above_median: '#1f2937', below_median: '#111827' };
const _DBAND_FG     = { critical: '#fca5a5', high_priority: '#fed7aa', elevated: '#fde68a',
                        typical: '#9ca3af', low: '#6b7280',
                        above_median: '#9ca3af', below_median: '#6b7280' };

function _anaRenderRankTable(facilities, groupStats) {
  const el = document.getElementById('ana-rank-results');
  if (!el) return;

  // Group stats summary bar
  let statsHtml = '';
  if (groupStats && groupStats.n) {
    const gs = groupStats;
    statsHtml = `<div style="font-size:0.6rem;color:#6b7280;background:#0f1117;border:1px solid #1f2937;border-radius:4px;padding:6px 8px;margin-bottom:8px;line-height:1.8">
      <span style="color:#9ca3af;font-weight:600">Peer group (n=${gs.n.toLocaleString()})</span> &nbsp; - &nbsp;
      Mean <span style="color:#d1d5db">${gs.mean}</span> &nbsp; - &nbsp;
      P50 <span style="color:#d1d5db">${gs.p50}</span> &nbsp; - &nbsp;
      P75 <span style="color:#facc15">${gs.p75}</span> &nbsp; - &nbsp;
      P90 <span style="color:#f97316">${gs.p90}</span> &nbsp; - &nbsp;
      P95 <span style="color:#ef4444">${gs.p95}</span>
    </div>`;
  }

  function rowHtml(feat) {
    if (!feat) return '';
    const p    = feat.properties ?? {};
    const name = p.name || (p.facility_id ? p.facility_id.replace(/^[nw]/, '#') : '-');
    const epdo = typeof p.epdo_score === 'number' ? p.epdo_score.toFixed(1) : '-';
    const f    = p.fatal_5yr ?? 0;
    const s    = p.severe_5yr ?? 0;
    const fid  = p.facility_id || '';
    const pct  = p.epdo_percentile != null ? Math.round(p.epdo_percentile) : null;
    const band = p.epdo_band || '';
    const pctLabel = pct != null
      ? `<span style="color:${_BAND_COLORS[band] || '#9ca3af'};font-size:0.5rem;font-weight:600">P${pct} ${_BAND_SHORT[band] || ''}</span>`
      : '';
    return `<div class="ana-rank-row" onclick="openRankDash('${fid}')" title="Click to open crash dashboard">
      ${name}<br>
      <span class="ana-rank-epdo worst">EPDO ${epdo}</span>
      <span style="color:#6b7280;font-size:0.58rem"> ${f}K ${s}S &nbsp;</span>${pctLabel}
    </div>`;
  }

  el.innerHTML = statsHtml + `<div>
    <div class="ana-rank-col-hdr" style="color:#9ca3af">Top ${facilities.length} by EPDO Score</div>
    ${facilities.map(f => rowHtml(f)).join('')}
  </div>`;
}

// ---- Compute county picker --------------------------------------------------
function _renderAnaComputeCountyPicker() {
  const el = document.getElementById('ana-compute-county-list');
  if (!el || !_anaCountyData) return;

  // Only counties that have crash data are eligible
  const eligible = Object.entries(_anaCountyData)
    .filter(([, info]) => info.crash_ready)
    .sort(([an, ai], [bn, bi]) => {
      // Sort: analysis_ready first, then by osm_pct desc, then alpha
      if (ai.analysis_ready !== bi.analysis_ready) return ai.analysis_ready ? -1 : 1;
      return (bi.osm_pct - ai.osm_pct) || an.localeCompare(bn);
    });

  if (!eligible.length) {
    el.innerHTML = '<div style="font-size:0.62rem;color:#6b7280">No counties with crash data yet. Download crash data above first.</div>';
    const cntEl = document.getElementById('ana-compute-county-count');
    if (cntEl) cntEl.textContent = '0 selected';
    return;
  }

  el.innerHTML = eligible.map(([name, info]) => {
    const label   = name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const checked = _anaComputeCounties.has(name) ? 'checked' : '';
    const osmTxt  = info.analysis_ready
      ? '<span style="color:#4ade80">ready</span>'
      : `<span style="color:#f59e0b">${info.osm_pct.toFixed(0)}% OSM</span>`;
    return `<label class="ana-compute-county-row">
      <input type="checkbox" ${checked} onchange="_anaToggleComputeCounty('${name}',this.checked)">
      <span class="ana-compute-county-name">${label}</span>
      ${osmTxt}
    </label>`;
  }).join('');

  _updateAnaComputeCountyCount();
}

function _anaToggleComputeCounty(name, checked) {
  if (checked) _anaComputeCounties.add(name);
  else _anaComputeCounties.delete(name);
  _updateAnaComputeCountyCount();
  _updateAnaComputeBtn();
}

function _updateAnaComputeCountyCount() {
  const el = document.getElementById('ana-compute-county-count');
  if (el) el.textContent = `${_anaComputeCounties.size} selected`;
}

function _anaComputeSelectAll() {
  if (!_anaCountyData) return;
  Object.entries(_anaCountyData).forEach(([name, info]) => {
    if (info.crash_ready) _anaComputeCounties.add(name);
  });
  _renderAnaComputeCountyPicker();
  _updateAnaComputeBtn();
}

function _anaComputeClearAll() {
  _anaComputeCounties.clear();
  _renderAnaComputeCountyPicker();
  _updateAnaComputeBtn();
}
