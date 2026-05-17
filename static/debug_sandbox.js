// =============================================================================
// debug_sandbox.js â€” Full OSM road view + consolidated intersection highlight
// Uses global click handler to avoid layer-ordering issues.
// =============================================================================

const dbgSandbox = (() => {
  let _dlPollTimer=null,_consolidatePollTimer=null,_bulkDlPollTimer=null;
  let _sacComplete=false,_consolidationLoaded=false,_consolidationResult=null;
  let _facilityModel=null,_facilityModelLoaded=false,_facilityClickBound=false;
  let _facilityVisibility={junctions:true,roads:true,ramps:true,diagnostics:true};
  let _highlightTimer=null,_highlightPhase=0,_osmRoadLayerReady=false,_globalClickBound=false;
  let _editElements={type:'FeatureCollection',features:[]},_manualLabels={type:'FeatureCollection',features:[]};
  let _extentMode=null,_extentActive=false,_extentStart=null,_extentPoints=[],_currentExtent=null,_selectedEditIds=[];

  function _log(msg){console.log('[dbg-sandbox] '+msg);}
  function _el(id){return document.getElementById(id);}
  function _setBar(id,pct){const e=_el(id);if(e)e.style.width=Math.min(100,Math.max(0,pct))+'%';}
  function _setText(id,txt){const e=_el(id);if(e)e.innerHTML=txt;}
  function _setDisabled(id,v){const e=_el(id);if(e)e.disabled=v;}
  function _setHidden(id,v){const e=_el(id);if(e)e.classList.toggle('hidden',v);}

  // â”€â”€ Sacramento download â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  async function _checkSacStatus(){try{const r=await fetch('/api/debug/sacramento/status');const d=await r.json();_updateDlUI(d.cached,d.total,false);_sacComplete=d.complete;if(d.cached>0){_setDisabled('facility-model-build-btn',false);}if(_sacComplete)_onSacComplete();}catch(e){_log('status error: '+e.message);}}
  function _updateDlUI(done,total,active,speed){const pct=total>0?done/total*100:0;_setBar('sac-dl-bar',pct);let label=done+' / '+total+' tiles';if(active&&speed>0)label+=' Â· '+speed.toFixed(2)+' tiles/s';_setText('sac-dl-label',label);const btn=_el('sac-dl-btn');if(!btn)return;if(done>=total){btn.textContent='Ready âœ“';btn.disabled=true;btn.classList.add('sac-dl-done');}else if(active){btn.textContent='Downloadingâ€¦';btn.disabled=true;btn.classList.remove('sac-dl-done');}else{btn.textContent='Download';btn.disabled=false;btn.classList.remove('sac-dl-done');}}
  async function downloadSacramento(){const btn=_el('sac-dl-btn');if(btn){btn.textContent='Startingâ€¦';btn.disabled=true;}try{const r=await fetch('/api/debug/sacramento/fetch',{method:'POST'});if(!r.ok){const e=await r.json().catch(()=>({}));_setText('sac-dl-label','Error: '+(e.detail||r.status));if(btn){btn.textContent='Download';btn.disabled=false;}return;}const d=await r.json();if(d.status==='already_complete'){_sacComplete=true;_updateDlUI(d.cached,80,false);_onSacComplete();return;}_startDlPoll();}catch(e){_log('download error: '+e.message);_setText('sac-dl-label','Network error');if(btn){btn.textContent='Download';btn.disabled=false;}}}
  function _startDlPoll(){clearInterval(_dlPollTimer);_dlPollTimer=setInterval(async()=>{try{const r=await fetch('/api/debug/sacramento/progress');const d=await r.json();_updateDlUI(d.done,d.total,d.active,d.speed||0);if(d.complete){clearInterval(_dlPollTimer);_dlPollTimer=null;_sacComplete=true;_onSacComplete();}}catch(e){}},1500);}

  // â”€â”€ Bulk download â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  async function downloadBulk(){const btn=_el('sac-bulk-btn');if(btn){btn.textContent='Startingâ€¦';btn.disabled=true;}const rBtn=_el('sac-dl-btn');if(rBtn)rBtn.disabled=true;_setHidden('sac-bulk-progress-wrap',false);_setBar('sac-bulk-bar',0);_setText('sac-bulk-label','Starting bulk downloadâ€¦');try{const r=await fetch('/api/debug/sacramento/fetch_bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({counties:['sacramento']})});if(!r.ok){const e=await r.json().catch(()=>({}));_setText('sac-bulk-label','Error: '+(e.detail||r.status));_resetBulkBtn();return;}_startBulkDlPoll();}catch(e){_log('bulk error: '+e.message);_setText('sac-bulk-label','Network error');_resetBulkBtn();}}
  function _startBulkDlPoll(){clearInterval(_bulkDlPollTimer);_bulkDlPollTimer=setInterval(async()=>{try{const r=await fetch('/api/debug/sacramento/fetch_bulk/progress');const d=await r.json();if(d.error){clearInterval(_bulkDlPollTimer);_bulkDlPollTimer=null;_setText('sac-bulk-label','Error: '+d.error);_resetBulkBtn();return;}_updateBulkDlUI(d);if(d.complete){clearInterval(_bulkDlPollTimer);_bulkDlPollTimer=null;_resetBulkBtn();_checkSacStatus();}}catch(e){}},1000);}
  function _updateBulkDlUI(d){const p=d.phase||'',dn=d.phase_done,tt=d.phase_total;if(p==='downloading_pbf'&&tt>0){_setBar('sac-bulk-bar',dn/tt*100);_setText('sac-bulk-label','Downloading PBF: '+(dn/1048576).toFixed(1)+' / '+(tt/1048576).toFixed(1)+' MB');}else if(p==='scanning_nodes'){_setBar('sac-bulk-bar',30);_setText('sac-bulk-label','Scanning nodes: '+(dn||0).toLocaleString()+' matched');}else if(p==='indexing_nodes'){_setBar('sac-bulk-bar',45);_setText('sac-bulk-label','Indexing nodes: '+(dn||0).toLocaleString()+' / '+(tt||0).toLocaleString());}else if(p==='scanning_ways'){_setBar('sac-bulk-bar',55);_setText('sac-bulk-label','Scanning ways & relations: '+(dn||0).toLocaleString()+' matched');}else if(p==='writing_tiles'&&tt>0){_setBar('sac-bulk-bar',55+(dn/tt*45));_setText('sac-bulk-label','Writing tile caches: '+dn+'/'+tt);}else if(p==='done'){_setBar('sac-bulk-bar',100);_setText('sac-bulk-label','Done - '+d.done+' tiles cached');}}
  function _resetBulkBtn(){const b=_el('sac-bulk-btn');if(b){b.textContent='Fast Download (PBF)';b.disabled=false;}const r=_el('sac-dl-btn');if(r)r.disabled=false;}
  function _onSacComplete(){_setDisabled('consolidate-compute-btn',false);_setDisabled('facility-model-build-btn',false);_fetchAndShowStats();}

  // â”€â”€ Element stats â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  async function _fetchAndShowStats(){try{const r=await fetch('/api/debug/sacramento/element_stats');if(!r.ok)return;const d=await r.json();const grid=_el('dbgsb-stats-grid'),box=_el('dbgsb-stats');if(!grid||!box)return;const nodes=d.nodes||{},ways=d.ways||{};const nRows=[['intersection_centroid','intersections (raw)'],['traffic_signals','traffic_signals'],['stop','stop signs'],['give_way','give_way'],['mini_roundabout','mini_roundabout'],['bus_stop','bus_stop']];const wRows=[['motorway','motorway'],['motorway_link','â†³ link'],['trunk','trunk'],['trunk_link','â†³ link'],['primary','primary'],['primary_link','â†³ link'],['secondary','secondary'],['secondary_link','â†³ link'],['tertiary','tertiary'],['tertiary_link','â†³ link'],['residential','residential'],['unclassified','unclassified'],['living_street','living_street'],['roundabout','roundabout'],['service','service'],['track','track'],['footway','footway'],['cycleway','cycleway'],['path','path'],['pedestrian','pedestrian']];let h='<div class="sec">Nodes</div>';for(const[k,l]of nRows){const v=nodes[k];if(v)h+='<div class="k">'+l+'</div><div class="v">'+v.toLocaleString()+'</div>';}h+='<div class="sec">Ways</div>';for(const[k,l]of wRows){const v=ways[k];if(v)h+='<div class="k">'+l+'</div><div class="v">'+v.toLocaleString()+'</div>';}grid.innerHTML=h;box.classList.remove('hidden');}catch(e){_log('stats error: '+e.message);}}

  // â”€â”€ Full OSM Road Layer â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _buildOsmRoadLayer() {
    if(!map)return false;
    try {
      // Remove old layer first
      if(map.getLayer('dbg-osm-roads'))map.removeLayer('dbg-osm-roads');

      const HW=[
        'motorway','#e892a2','motorway_link','#e892a2',
        'trunk','#fabb7e','trunk_link','#fabb7e',
        'primary','#fdd7a2','primary_link','#fdd7a2',
        'secondary','#f7fabf','secondary_link','#f7fabf',
        'tertiary','#d4f2da','tertiary_link','#d4f2da',
        'residential','#e0e0e0','unclassified','#e0e0e0',
        'living_street','#ededed','service','#e0e0e0',
        'track','#dcd49c','track_grade1','#dcd49c','track_grade2','#dcd49c',
        'pedestrian','#ddd','footway','#e8d6c3','cycleway','#b4b4fa','path','#e8d6c3',
        'roundabout','#f7fabf','unknown','#6b7280',
      ];

      map.addLayer({
        id:'dbg-osm-roads',
        type:'line',
        source:'osm',
        minzoom:11,
        filter:['==',['geometry-type'],'LineString'],
        paint:{
          'line-color':['match',['get','type'],...HW,'#9ca3af'],
          'line-width':['interpolate',['linear'],['zoom'],11,1,14,3,17,6],
          'line-opacity':0.72,
        },
      });
      _osmRoadLayerReady=true;
      _log('OSM road layer built OK');
      return true;
    } catch(e){
      _log('OSM road layer error: '+e.message);
      // Source might not exist â€” create it
      try{
        if(!map.getSource('osm')){
          map.addSource('osm',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
          _log('Created empty osm source');
          // Retry layer
          map.addLayer({
            id:'dbg-osm-roads',type:'line',source:'osm',minzoom:11,
            filter:['==',['geometry-type'],'LineString'],
            paint:{'line-color':'#9ca3af','line-width':1.5,'line-opacity':0.5},
          });
          _osmRoadLayerReady=true;
          return true;
        }
      } catch(e2){_log('Fallback also failed: '+e2.message);}
      return false;
    }
  }

  // â”€â”€ Consolidation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  async function computeConsolidation(){if(!_sacComplete)return;const tol=parseFloat(_el('consolidate-tolerance-slider')?.value||'10');_setHidden('consolidate-progress-wrap',false);_setBar('consolidate-bar',0);_setText('consolidate-progress-label','Computing consolidationâ€¦');_setDisabled('consolidate-compute-btn',true);_clearConsolidatedLayers();_consolidationLoaded=false;_consolidationResult=null;try{const r=await fetch('/api/debug/consolidated/compute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tolerance:tol})});if(!r.ok){const e=await r.json();_setText('consolidate-progress-label','Error: '+(e.detail||r.status));_setDisabled('consolidate-compute-btn',false);return;}_startConsolidatePoll();}catch(e){_log('compute error: '+e.message);_setDisabled('consolidate-compute-btn',false);}}
  function _startConsolidatePoll(){clearInterval(_consolidatePollTimer);let tick=0;_consolidatePollTimer=setInterval(async()=>{tick++;try{const r=await fetch('/api/debug/consolidated/progress');const d=await r.json();if(d.error){clearInterval(_consolidatePollTimer);_setText('consolidate-progress-label','Error: '+d.error);_setDisabled('consolidate-compute-btn',false);return;}if(d.complete){clearInterval(_consolidatePollTimer);_consolidatePollTimer=null;_setBar('consolidate-bar',100);_setText('consolidate-progress-label',d.num_intersections.toLocaleString()+' consolidated intersections (tol='+d.tolerance+'m)');_setText('consolidate-result-stat',d.num_intersections.toLocaleString()+' intersections');_setDisabled('consolidate-compute-btn',false);await _loadConsolidatedResult();}else if(tick>180){clearInterval(_consolidatePollTimer);_setText('consolidate-progress-label','Timed out');_setDisabled('consolidate-compute-btn',false);}else{_setBar('consolidate-bar',Math.min(90,tick*2));}}catch(e){}},2000);}
  async function _loadConsolidatedResult(){try{_setText('consolidate-progress-label','Loading intersection dataâ€¦');const r=await fetch('/api/debug/consolidated/intersections');if(!r.ok){_setText('consolidate-progress-label','Failed to load (HTTP '+r.status+')');return;}const d=await r.json();_log('Loaded '+d.num_intersections+' intersections');_consolidationResult={intersections:d.intersections,num_intersections:d.num_intersections,tolerance:d.tolerance};_consolidationLoaded=true;_setHidden('consolidate-results-section',false);_setText('consolidate-result-stat',d.num_intersections.toLocaleString()+' consolidated intersections Â· tol='+d.tolerance+'m');_buildConsolidatedLayers();}catch(e){_log('load error: '+e.message);_setText('consolidate-progress-label','Failed: '+e.message);}}

  // â”€â”€ Map Layers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _clearConsolidatedLayers(){if(!map)return;_stopHighlight();['dbg-cons-intersections','dbg-cons-detail-nodes','dbg-cons-detail-roads'].forEach(id=>{try{if(map.getLayer(id))map.removeLayer(id);}catch(e){}});['dbg-cons-intersections-src','dbg-cons-detail-src'].forEach(id=>{try{if(map.getSource(id))map.removeSource(id);}catch(e){}});}

  function _buildConsolidatedLayers(){if(!map||!_consolidationResult)return;_clearConsolidatedLayers();
    try{
      // Main intersection points â€” add BEFORE intersections-pt-layer so it's below (app.js layer stays on top)
      // But we use a GLOBAL click handler so ordering doesn't matter for clicks
      map.addSource('dbg-cons-intersections-src',{type:'geojson',data:_consolidationResult.intersections});
      map.addLayer({
        id:'dbg-cons-intersections',type:'circle',source:'dbg-cons-intersections-src',minzoom:10,
        paint:{
          'circle-radius':['interpolate',['linear'],['zoom'],10,4,12,7,14,10,16,15,18,20],
          'circle-color':['case',['>',['get','merged_count'],1],'#f97316','#3b82f6'],
          'circle-stroke-color':'#ffffff',
          'circle-stroke-width':['case',['>',['get','merged_count'],1],3,1.5],
          'circle-opacity':['case',['>',['get','merged_count'],1],0.92,0.7],
        },
      });
      // Detail source
      map.addSource('dbg-cons-detail-src',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      map.addLayer({id:'dbg-cons-detail-nodes',type:'circle',source:'dbg-cons-detail-src',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':['interpolate',['linear'],['zoom'],10,10,14,18,18,26],'circle-color':'#fbbf24','circle-stroke-color':'#ffffff','circle-stroke-width':3.5,'circle-opacity':0.9}});
      map.addLayer({id:'dbg-cons-detail-roads',type:'line',source:'dbg-cons-detail-src',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#fbbf24','line-width':4.5,'line-opacity':0.85}});

      // Bind GLOBAL click handler (not per-layer) to avoid ordering issues
      _bindGlobalClickHandler();
      _log('Layers built: '+_consolidationResult.num_intersections+' intersection points');
    } catch(e){_log('Layer error: '+e.message);console.error(e);}
  }

  function _bindGlobalClickHandler(){
    if(_globalClickBound||!map)return;
    map.on('click',_onGlobalClick);
    map.on('mouseenter','dbg-cons-intersections',()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave','dbg-cons-intersections',()=>{map.getCanvas().style.cursor='';});
    _globalClickBound=true;
    _log('Global click handler bound');
  }

  // â”€â”€ GLOBAL click handler â€” catches clicks on consolidated intersections â”€â”€â”€â”€â”€
  let _currentClusterId = null;

  async function _onGlobalClick(e){
    if(!map||!_consolidationLoaded)return;
    const features=map.queryRenderedFeatures(e.point,{layers:['dbg-cons-intersections']});
    if(!features||features.length===0){
      // Click not on a consolidated intersection â€” clear detail layers
      _clearDetailLayers();
      return;
    }

    const feat=features[0];
    const p=feat.properties;
    const centroid=feat.geometry.coordinates;
    const members=p.member_details||[];
    const mergedCount=p.merged_count||0;
    const incidentIds=p.incident_osm_way_ids||[];
    _currentClusterId=p.cluster_id;
    _log('Cluster #'+p.cluster_id+' ('+mergedCount+' nodes, '+incidentIds.length+' incident ways)');

    e.originalEvent?.preventDefault();
    e.originalEvent?.stopPropagation();

    // Build detail features: member nodes (points)
    const detailFeats=[];
    for(const m of members){
      if(m.lon==null||m.lat==null)continue;
      if(Math.abs(m.lat)>90||Math.abs(m.lon)>180)continue;
      detailFeats.push({
        type:'Feature',
        geometry:{type:'Point',coordinates:[m.lon,m.lat]},
        properties:{node_id:m.node_id,kind:'member_node'}
      });
    }

    // Fetch connected roads from backend
    let roadFeats=[];
    try{
      const r=await fetch('/api/debug/consolidated/node/'+p.cluster_id+'/roads');
      if(r.ok){
        const d=await r.json();
        roadFeats=d.features||[];
        // Add road features to detail layer (tagged as roads)
        for(const rf of roadFeats){
          detailFeats.push({
            type:'Feature',
            geometry:rf.geometry,
            properties:{...rf.properties,kind:'incident_road'}
          });
        }
      }
    }catch(err){
      _log('Road fetch error: '+err.message);
    }

    // Update detail layer source
    const src=map.getSource('dbg-cons-detail-src');
    if(src)src.setData({type:'FeatureCollection',features:detailFeats});

    // Popup
    const color=mergedCount>1?'#f97316':'#3b82f6';
    new maplibregl.Popup({maxWidth:'420px',className:'dbg-cons-popup'})
      .setLngLat(centroid)
      .setHTML(_buildClusterPopupHTML(p,mergedCount,members,roadFeats,color))
      .addTo(map);

    _startHighlight();
    // Adjust road layer to show incident roads with different styling
    _updateDetailRoadStyle(roadFeats.length>0);
  }

  function _buildClusterPopupHTML(props,mergedCount,members,roadFeats,color){
    var safeMembers=Array.isArray(members)?members:[];
    // Node rows: show up to 30 member nodes
    var nodeRows='';
    var show=safeMembers.slice(0,30);
    for(var i=0;i<show.length;i++){
      var m=show[i];
      var mlon=(m.lon!=null)?Number(m.lon).toFixed(5):'?';
      var mlat=(m.lat!=null)?Number(m.lat).toFixed(5):'?';
      nodeRows+='<div style="font-family:monospace;font-size:0.55rem;color:#d1d5db;padding:1px 0">'+String(m.node_id||'?')+' <span style="color:#6b7280">'+mlon+','+mlat+'</span></div>';
    }
    var moreNote=safeMembers.length>30?'<div style="color:#6b7280;font-size:0.5rem;margin-top:1px">+ '+(safeMembers.length-30)+' more nodes</div>':'';
    // Road rows: summarize incident roads
    var roadRows='';
    var showRoads=roadFeats.slice(0,15);
    for(var j=0;j<showRoads.length;j++){
      var rp=showRoads[j].properties||{};
      var rname=rp.name||'unnamed';
      var rhw=rp.highway||rp.type||'';
      var rid=rp.id||'';
      roadRows+='<div style="font-family:monospace;font-size:0.55rem;color:#fbbf24;padding:1px 0">'+_esc(String(rname))+' <span style="color:#9ca3af">'+_esc(rhw)+'</span> <span style="color:#6b7280">#'+rid+'</span></div>';
    }
    var roadMore=roadFeats.length>15?'<div style="color:#6b7280;font-size:0.5rem;margin-top:1px">+ '+(roadFeats.length-15)+' more roads</div>':'';

    return '<div style="font-size:0.78rem;font-weight:600;color:#e5e7eb;">'+
      '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:'+color+';margin-right:5px;"></span>Intersection #'+props.cluster_id+
      '</div>'+
      '<div style="font-size:0.65rem;color:#9ca3af;margin-top:3px;">'+
      '<strong>'+mergedCount+'</strong> original nodes merged Â· <strong>'+roadFeats.length+'</strong> incident roads'+
      '</div>'+
      (safeMembers.length>0?'<div style="margin-top:6px;font-size:0.58rem;color:#f97316;font-weight:600;">â— Original OSM Nodes</div><div style="margin-top:2px;">'+nodeRows+moreNote+'</div>':'')+
      (roadFeats.length>0?'<div style="margin-top:6px;font-size:0.58rem;color:#fbbf24;font-weight:600;">â”€ Incident Road Segments</div><div style="margin-top:2px;">'+roadRows+roadMore+'</div>':'');
  }

  function _updateDetailRoadStyle(hasRoads){
    if(!map)return;
    try{
      if(map.getLayer('dbg-cons-detail-roads')){
        map.setPaintProperty('dbg-cons-detail-roads','line-color',
          hasRoads?['match',['get','kind'],'incident_road','#fbbf24','#6b7280']:'#6b7280');
        map.setPaintProperty('dbg-cons-detail-roads','line-width',
          hasRoads?['match',['get','kind'],'incident_road',3.5,1]:1);
      }
    }catch(e){}
  }

  function _clearDetailLayers(){
    _stopHighlight();
    _currentClusterId=null;
    const src=map?map.getSource('dbg-cons-detail-src'):null;
    if(src)src.setData({type:'FeatureCollection',features:[]});
  }

  function _startHighlight(){
    _stopHighlight();
    if(!map||!map.getLayer('dbg-cons-detail-nodes'))return;
    _highlightTimer=setInterval(()=>{
      _highlightPhase=(_highlightPhase+1)%4;
      const o=[0.95,0.3,0.95,0.5];
      const s=[3.5,1.5,3.5,2];
      try{
        map.setPaintProperty('dbg-cons-detail-nodes','circle-opacity',o[_highlightPhase]);
        map.setPaintProperty('dbg-cons-detail-nodes','circle-stroke-width',s[_highlightPhase]);
        map.setPaintProperty('dbg-cons-detail-roads','line-opacity',o[_highlightPhase]);
      }catch(e){}
    },400);
  }
  function _stopHighlight(){
    if(_highlightTimer){clearInterval(_highlightTimer);_highlightTimer=null;}
    _highlightPhase=0;
  }

  // â”€â”€ Tolerance slider â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function onToleranceSliderInput(val){_setText('consolidate-tolerance-label',val+' m');}

  // Facility model demo
  async function buildFacilityModel(){
    if(!_sacComplete)return;
    const btn=_el('facility-model-build-btn');
    if(btn){btn.disabled=true;btn.textContent='Building...';}
    _setHidden('facility-model-progress-wrap',false);
    _setBar('facility-model-bar',15);
    _setText('facility-model-label','Building facility model...');
    _clearFacilityModelLayers();
    try{
      const r=await fetch('/api/debug/facility_model/compute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force_refresh:true,tolerance_m:15})});
      if(!r.ok){
        const e=await r.json().catch(()=>({detail:r.statusText}));
        _setText('facility-model-label','Error: '+(e.detail||r.status));
        return;
      }
      _setBar('facility-model-bar',70);
      await _loadFacilityModelResult();
      _setBar('facility-model-bar',100);
      _setText('facility-model-label','Facility model ready.');
    }catch(e){
      _setText('facility-model-label','Network error: '+e.message);
    }finally{
      if(btn){btn.disabled=false;btn.textContent='Build Facility Model';}
    }
  }

  async function _loadFacilityModelResult(){
    const r=await fetch('/api/debug/facility_model/result');
    if(!r.ok){
      const e=await r.json().catch(()=>({detail:r.statusText}));
      throw new Error(e.detail||('HTTP '+r.status));
    }
    _facilityModel=await r.json();
    _facilityModelLoaded=true;
    _setHidden('facility-model-toggle-grid',false);
    _setHidden('facility-model-stat',false);
    const c=_facilityModel.metadata?.counts||{};
    _setText('facility-model-stat',
      (c.junction_candidates||0).toLocaleString()+' junctions Â· '+
      (c.road_segments||0).toLocaleString()+' roads Â· '+
      (c.ramp_segments||0).toLocaleString()+' ramps');
    _buildFacilityModelLayers();
  }

  async function _loadFacilityModelResultSilent(){
    try{await _loadFacilityModelResult();_setHidden('facility-model-progress-wrap',true);}
    catch(e){_log('Facility model silent load: '+e.message);}
  }

  function _clearFacilityModelLayers(){
    if(!map)return;
    ['dbg-fm-junctions','dbg-fm-roads','dbg-fm-ramps','dbg-fm-diagnostics'].forEach(id=>{try{if(map.getLayer(id))map.removeLayer(id);}catch(e){}});
    ['dbg-fm-junctions-src','dbg-fm-roads-src','dbg-fm-ramps-src','dbg-fm-diagnostics-src'].forEach(id=>{try{if(map.getSource(id))map.removeSource(id);}catch(e){}});
  }

  function _buildFacilityModelLayers(){
    if(!map||!_facilityModel)return;
    _clearFacilityModelLayers();
    try{
      map.addSource('dbg-fm-roads-src',{type:'geojson',data:_facilityModel.road_segments||{type:'FeatureCollection',features:[]}});
      map.addSource('dbg-fm-ramps-src',{type:'geojson',data:_facilityModel.ramp_segments||{type:'FeatureCollection',features:[]}});
      map.addSource('dbg-fm-diagnostics-src',{type:'geojson',data:_facilityModel.diagnostics||{type:'FeatureCollection',features:[]}});
      map.addSource('dbg-fm-junctions-src',{type:'geojson',data:_facilityModel.junction_candidates||{type:'FeatureCollection',features:[]}});
      map.addLayer({id:'dbg-fm-roads',type:'line',source:'dbg-fm-roads-src',minzoom:10,paint:{'line-color':'#64748b','line-width':['interpolate',['linear'],['zoom'],10,1,14,2.5,18,5],'line-opacity':0.72}});
      map.addLayer({id:'dbg-fm-ramps',type:'line',source:'dbg-fm-ramps-src',minzoom:10,paint:{'line-color':'#f97316','line-width':['interpolate',['linear'],['zoom'],10,1.5,14,3.5,18,7],'line-opacity':0.88}});
      map.addLayer({id:'dbg-fm-diagnostics',type:'circle',source:'dbg-fm-diagnostics-src',minzoom:10,paint:{'circle-radius':['interpolate',['linear'],['zoom'],10,4,14,7,18,11],'circle-color':'#e11d48','circle-stroke-color':'#fff','circle-stroke-width':1.2,'circle-opacity':0.85}});
      map.addLayer({id:'dbg-fm-junctions',type:'circle',source:'dbg-fm-junctions-src',minzoom:10,paint:{'circle-radius':['interpolate',['linear'],['zoom'],10,5,14,9,18,15],'circle-color':['match',['get','subtype'],'roundabout','#a855f7','ramp_terminal','#f97316','#14b8a6'],'circle-stroke-color':'#fff','circle-stroke-width':2,'circle-opacity':0.92}});
      _applyFacilityVisibility();
      _bindFacilityModelClickHandler();
    }catch(e){_log('Facility model layer error: '+e.message);}
  }

  function _applyFacilityVisibility(){
    if(!map)return;
    const pairs=[['dbg-fm-junctions','junctions'],['dbg-fm-roads','roads'],['dbg-fm-ramps','ramps'],['dbg-fm-diagnostics','diagnostics']];
    for(const [layer,key] of pairs){
      try{if(map.getLayer(layer))map.setLayoutProperty(layer,'visibility',_facilityVisibility[key]?'visible':'none');}catch(e){}
    }
  }

  function _setupFacilityToggles(){
    const grid=_el('facility-model-toggle-grid');
    if(!grid||grid._dbgFacilitySetup)return;
    grid._dbgFacilitySetup=true;
    grid.addEventListener('click',e=>{
      const row=e.target.closest('[data-facility-layer]');
      if(!row)return;
      const key=row.dataset.facilityLayer;
      _facilityVisibility[key]=!_facilityVisibility[key];
      row.classList.toggle('on',_facilityVisibility[key]);
      row.classList.toggle('off',!_facilityVisibility[key]);
      _applyFacilityVisibility();
    });
  }

  function _bindFacilityModelClickHandler(){
    if(!map||_facilityClickBound)return;
    map.on('click',_onFacilityModelClick);
    _facilityClickBound=true;
  }

  function _onFacilityModelClick(e){
    if(!_facilityModelLoaded||!map)return;
    const layers=['dbg-fm-junctions','dbg-fm-diagnostics','dbg-fm-ramps','dbg-fm-roads'].filter(id=>map.getLayer(id)&&map.getLayoutProperty(id,'visibility')!=='none');
    if(!layers.length)return;
    const feats=map.queryRenderedFeatures(e.point,{layers});
    if(!feats.length)return;
    const feat=feats[0];
    const p=feat.properties||{};
    const rows=['facility_id','facility_type','subtype','confidence','geometric_configuration','traffic_control_type','traffic_control_subtype','structural_grade','leg_count','approach_count','approach_highways','highway','name','length_m','begin_junction_id','end_junction_id','member_node_count','incident_way_count','control_point_count','osm_way_id','control_osm_ids','incident_osm_way_ids','diagnostic_flags','reason']
      .filter(k=>p[k]!=null&&String(p[k])!=='')
      .map(k=>'<div class="dbg-kv"><span class="k">'+_esc(k)+'</span><span class="v">'+_esc(String(p[k]))+'</span></div>')
      .join('');
    new maplibregl.Popup({maxWidth:'420px'})
      .setLngLat(e.lngLat)
      .setHTML('<div style="font-size:0.74rem;color:#e5e7eb;font-weight:700">Facility Model</div>'+rows)
      .addTo(map);
    e.originalEvent?.preventDefault();
    e.originalEvent?.stopPropagation();
  }

  // â”€â”€ OSM Layer Toggles â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  function _setupLayerToggles(){
    const grid=_el('dbgsb-toggle-grid');
    if(!grid||grid._dbgSetup)return;
    grid._dbgSetup=true;
    grid.addEventListener('click',e=>{
      const row=e.target.closest('.dbgsb-tgl-row');
      if(!row)return;
      const key=row.dataset.key;
      if(!key)return;
      // Use the global toggleLayer from app.js
      if(typeof toggleLayer==='function')toggleLayer(key);
      _syncOneToggle(row,key);
    });
    // Initial sync from LAYER_VISIBILITY (global from app.js)
    _syncAllToggles();
  }
  function _syncOneToggle(row,key){
    const on=(typeof LAYER_VISIBILITY!=='undefined')?!!LAYER_VISIBILITY[key]:false;
    row.classList.toggle('on',on);
    row.classList.toggle('off',!on);
  }
  function _syncAllToggles(){
    const grid=_el('dbgsb-toggle-grid');
    if(!grid)return;
    grid.querySelectorAll('.dbgsb-tgl-row').forEach(row=>{
      const key=row.dataset.key;
      if(key)_syncOneToggle(row,key);
    });
  }

  // â”€â”€ Lifecycle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  // OSM edit sandbox
  function _bboxString(){const b=map.getBounds();return[b.getWest(),b.getSouth(),b.getEast(),b.getNorth()].map(v=>v.toFixed(6)).join(',');}
  async function loadEditElements(){if(!map)return;const btn=_el('sandbox-load-btn');if(btn){btn.disabled=true;btn.textContent='Loading...';}_setText('sandbox-load-label','Loading OSM edit elements...');try{const r=await fetch('/api/debug/osm/edit_elements?bbox='+encodeURIComponent(_bboxString()));if(!r.ok){const e=await r.json().catch(()=>({detail:r.statusText}));_setText('sandbox-load-label','Error: '+(e.detail||r.status));return;}_editElements=await r.json();_buildEditSandboxLayers();_updateSelectedEditElements();const c=_editElements.metadata?.counts||{};_setText('sandbox-load-label',(_editElements.features||[]).length.toLocaleString()+' features Â· '+(c.nodes||0)+' nodes, '+(c.ways||0)+' ways, '+(c.relations||0)+' relations');}catch(e){_setText('sandbox-load-label','Network error: '+e.message);}finally{if(btn){btn.disabled=false;btn.textContent='Load Current View';}}}
  function _clearEditSandboxLayers(){if(!map)return;['dbg-edit-points','dbg-edit-relation-points','dbg-edit-lines','dbg-edit-relation-lines','dbg-edit-selected-lines','dbg-edit-selected-points','dbg-edit-label-fill','dbg-edit-label-outline','dbg-edit-extent-fill','dbg-edit-extent-outline','dbg-edit-vertices'].forEach(id=>{try{if(map.getLayer(id))map.removeLayer(id);}catch(e){}});['dbg-edit-elements-src','dbg-edit-selected-src','dbg-edit-extent-src','dbg-edit-labels-src'].forEach(id=>{try{if(map.getSource(id))map.removeSource(id);}catch(e){}});}
  function _buildEditSandboxLayers(){if(!map)return;const extent=_currentExtent?{type:'FeatureCollection',features:[_currentExtent]}:{type:'FeatureCollection',features:[]};try{_clearEditSandboxLayers();map.addSource('dbg-edit-elements-src',{type:'geojson',data:_editElements});map.addSource('dbg-edit-selected-src',{type:'geojson',data:{type:'FeatureCollection',features:[]}});map.addSource('dbg-edit-extent-src',{type:'geojson',data:extent});map.addSource('dbg-edit-labels-src',{type:'geojson',data:_manualLabels});map.addLayer({id:'dbg-edit-label-fill',type:'fill',source:'dbg-edit-labels-src',paint:{'fill-color':'#f59e0b','fill-opacity':0.08}});map.addLayer({id:'dbg-edit-label-outline',type:'line',source:'dbg-edit-labels-src',paint:{'line-color':'#f59e0b','line-width':2,'line-opacity':0.75}});map.addLayer({id:'dbg-edit-lines',type:'line',source:'dbg-edit-elements-src',filter:['all',['==',['geometry-type'],'LineString'],['!=',['get','osm_type'],'relation']],paint:{'line-color':['match',['get','element_kind'],'motorway','#ef4444','trunk','#f97316','primary','#f59e0b','secondary','#84cc16','tertiary','#38bdf8','residential','#cbd5e1','#64748b'],'line-width':['interpolate',['linear'],['zoom'],14,1,17,4],'line-opacity':0.78}});map.addLayer({id:'dbg-edit-relation-lines',type:'line',source:'dbg-edit-elements-src',filter:['all',['==',['geometry-type'],'LineString'],['==',['get','osm_type'],'relation']],paint:{'line-color':'#d946ef','line-width':['interpolate',['linear'],['zoom'],14,1.5,17,4.5],'line-opacity':0.78,'line-dasharray':[2,1]}});map.addLayer({id:'dbg-edit-points',type:'circle',source:'dbg-edit-elements-src',filter:['all',['==',['geometry-type'],'Point'],['!=',['get','osm_type'],'relation']],paint:{'circle-radius':['interpolate',['linear'],['zoom'],14,3,18,7],'circle-color':'#22c55e','circle-stroke-color':'#052e16','circle-stroke-width':1.2,'circle-opacity':0.88}});map.addLayer({id:'dbg-edit-relation-points',type:'circle',source:'dbg-edit-elements-src',filter:['all',['==',['geometry-type'],'Point'],['==',['get','osm_type'],'relation']],paint:{'circle-radius':['interpolate',['linear'],['zoom'],14,4,18,8],'circle-color':'#d946ef','circle-stroke-color':'#581c87','circle-stroke-width':1.5,'circle-opacity':0.9}});map.addLayer({id:'dbg-edit-selected-lines',type:'line',source:'dbg-edit-selected-src',filter:['==',['geometry-type'],'LineString'],paint:{'line-color':'#fbbf24','line-width':6,'line-opacity':0.9}});map.addLayer({id:'dbg-edit-selected-points',type:'circle',source:'dbg-edit-selected-src',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':9,'circle-color':'#fbbf24','circle-stroke-color':'#fff','circle-stroke-width':2}});map.addLayer({id:'dbg-edit-extent-fill',type:'fill',source:'dbg-edit-extent-src',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':'#3b82f6','fill-opacity':0.12}});map.addLayer({id:'dbg-edit-extent-outline',type:'line',source:'dbg-edit-extent-src',filter:['==',['geometry-type'],'Polygon'],paint:{'line-color':'#60a5fa','line-width':2,'line-dasharray':[3,2]}});map.addLayer({id:'dbg-edit-vertices',type:'circle',source:'dbg-edit-extent-src',filter:['==',['geometry-type'],'Point'],paint:{'circle-radius':4,'circle-color':'#60a5fa','circle-stroke-color':'#fff','circle-stroke-width':1.5}});_bindEditElementPopup();}catch(e){_log('edit sandbox layer error: '+e.message);}}
  function _bindEditElementPopup(){if(!map||map._dbgEditPopupBound)return;map._dbgEditPopupBound=true;const layers=['dbg-edit-points','dbg-edit-relation-points','dbg-edit-lines','dbg-edit-relation-lines'];map.on('click',e=>{if(_extentActive)return;const live=layers.filter(id=>map.getLayer(id));if(!live.length)return;const feats=map.queryRenderedFeatures(e.point,{layers:live});if(!feats.length)return;const p=feats[0].properties||{};const rows=['id','osm_type','element_kind','highway','junction','type','route','restriction','member_role','name','ref'].filter(k=>p[k]!=null&&p[k]!==''&&!String(p[k]).startsWith('[')).map(k=>'<div class="dbg-kv"><span class="k">'+_esc(k)+'</span><span class="v">'+_esc(p[k])+'</span></div>').join('');new maplibregl.Popup({maxWidth:'360px'}).setLngLat(e.lngLat).setHTML('<div style="font-size:0.72rem;color:#e5e7eb;font-weight:700">OSM Edit Element</div>'+rows).addTo(map);});}
  function startExtentRect(){_startExtentDraw('rect');}
  function startExtentPoly(){_startExtentDraw('poly');}
  function _startExtentDraw(mode){if(!map)return;_extentMode=mode;_extentActive=true;_extentStart=null;_extentPoints=[];_currentExtent=null;_selectedEditIds=[];_setText('sandbox-save-label',mode==='rect'?'Drag a rectangle on the map.':'Click vertices, double-click to finish.');_setDisabled('sandbox-save-btn',true);_setHidden('sandbox-cancel-btn',false);_el('sandbox-rect-btn')?.classList.toggle('active',mode==='rect');_el('sandbox-poly-btn')?.classList.toggle('active',mode==='poly');map.dragPan.disable();map.dragRotate.disable();if(mode==='poly')map.doubleClickZoom.disable();map.getCanvas().style.cursor='crosshair';_setExtentSource({type:'FeatureCollection',features:[]});}
  function cancelExtentDraw(){_finishExtentDraw(true);}
  function _finishExtentDraw(clear){const mode=_extentMode;_extentMode=null;_extentActive=false;_extentStart=null;_extentPoints=[];if(map){map.dragPan.enable();map.dragRotate.enable();if(mode==='poly')map.doubleClickZoom.enable();map.getCanvas().style.cursor='';}_setHidden('sandbox-cancel-btn',true);_el('sandbox-rect-btn')?.classList.remove('active');_el('sandbox-poly-btn')?.classList.remove('active');if(clear){_currentExtent=null;_selectedEditIds=[];_updateSelectedEditElements();_setExtentSource({type:'FeatureCollection',features:[]});_setText('sandbox-save-label','No manual extent selected.');_setDisabled('sandbox-save-btn',true);}}
  function _makeRect(sw,ne){const w=Math.min(sw.lng,ne.lng),e=Math.max(sw.lng,ne.lng),s=Math.min(sw.lat,ne.lat),n=Math.max(sw.lat,ne.lat);return{type:'Feature',geometry:{type:'Polygon',coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]},properties:{kind:'manual_intersection_extent'}};}
  function _makePoly(points){return{type:'Feature',geometry:{type:'Polygon',coordinates:[points]},properties:{kind:'manual_intersection_extent'}};}
  function _setExtentSource(fc){const src=map?map.getSource('dbg-edit-extent-src'):null;if(src)src.setData(fc);}
  function _updateExtentFeature(feature){_currentExtent=feature;_setExtentSource({type:'FeatureCollection',features:[feature]});_updateSelectedEditElements();_setDisabled('sandbox-save-btn',false);}
  function _pointInRingLocal(pt,ring){let inside=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){const xi=ring[i][0],yi=ring[i][1],xj=ring[j][0],yj=ring[j][1];if((yi>pt[1])!==(yj>pt[1])&&pt[0]<(xj-xi)*(pt[1]-yi)/(yj-yi)+xi)inside=!inside;}return inside;}
  function _featureTouchesRing(f,ring){const g=f.geometry||{};if(g.type==='Point')return _pointInRingLocal(g.coordinates,ring);if(g.type==='LineString'){const coords=g.coordinates||[];if(coords.some(c=>_pointInRingLocal(c,ring)))return true;if(coords.length)return _pointInRingLocal(coords[Math.floor(coords.length/2)],ring);}return false;}
  function _updateSelectedEditElements(){const src=map?map.getSource('dbg-edit-selected-src'):null;if(!_currentExtent||!_editElements.features){_selectedEditIds=[];if(src)src.setData({type:'FeatureCollection',features:[]});return;}const ring=_currentExtent.geometry.coordinates[0];const feats=_editElements.features.filter(f=>_featureTouchesRing(f,ring));_selectedEditIds=feats.map(f=>String(f.properties?.id||''));if(src)src.setData({type:'FeatureCollection',features:feats});_setText('sandbox-save-label',feats.length.toLocaleString()+' OSM edit elements inside extent.');}
  async function saveCurrentExtent(){if(!_currentExtent)return;const body={geometry:_currentExtent.geometry,selected_element_ids:_selectedEditIds,label:{label:_el('sandbox-label-input')?.value||'intersection_candidate',facility_model:'cell_model',control:_el('sandbox-control-input')?.value||'',notes:_el('sandbox-notes-input')?.value||''}};_setDisabled('sandbox-save-btn',true);_setText('sandbox-save-label','Saving manual label...');try{const r=await fetch('/api/debug/osm/intersection_labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok){const e=await r.json().catch(()=>({detail:r.statusText}));_setText('sandbox-save-label','Error: '+(e.detail||r.status));_setDisabled('sandbox-save-btn',false);return;}const d=await r.json();_manualLabels.features.push(d.feature);map.getSource('dbg-edit-labels-src')?.setData(_manualLabels);_setText('sandbox-save-label','Saved '+d.count.toLocaleString()+' manual labels.');_currentExtent=null;_selectedEditIds=[];_setExtentSource({type:'FeatureCollection',features:[]});_updateSelectedEditElements();}catch(e){_setText('sandbox-save-label','Network error: '+e.message);_setDisabled('sandbox-save-btn',false);}}
  async function _loadManualLabels(){try{const r=await fetch('/api/debug/osm/intersection_labels');if(r.ok){_manualLabels=await r.json();map?.getSource('dbg-edit-labels-src')?.setData(_manualLabels);}}catch(e){}}
  function _wireExtentDrawEvents(){if(!map||map._dbgExtentEventsBound)return;map._dbgExtentEventsBound=true;const canvas=map.getCanvas();canvas.addEventListener('mousedown',e=>{if(_extentMode!=='rect'||!_extentActive)return;e.stopPropagation();_extentStart=map.unproject([e.offsetX,e.offsetY]);});canvas.addEventListener('mousemove',e=>{if(_extentMode!=='rect'||!_extentActive||!_extentStart)return;const cur=map.unproject([e.offsetX,e.offsetY]);_setExtentSource({type:'FeatureCollection',features:[_makeRect(_extentStart,cur)]});});canvas.addEventListener('mouseup',e=>{if(_extentMode!=='rect'||!_extentActive||!_extentStart)return;const cur=map.unproject([e.offsetX,e.offsetY]);_updateExtentFeature(_makeRect(_extentStart,cur));_finishExtentDraw(false);});map.on('click',e=>{if(_extentMode!=='poly'||!_extentActive)return;e.originalEvent?.stopPropagation();_extentPoints.push([e.lngLat.lng,e.lngLat.lat]);const pts=_extentPoints.map(p=>({type:'Feature',geometry:{type:'Point',coordinates:p},properties:{}}));if(_extentPoints.length>=2)pts.push(_makePoly([..._extentPoints,_extentPoints[0]]));_setExtentSource({type:'FeatureCollection',features:pts});_setText('sandbox-save-label',_extentPoints.length+' vertices. Double-click to finish.');});map.on('dblclick',e=>{if(_extentMode!=='poly'||!_extentActive||_extentPoints.length<3)return;e.preventDefault();e.originalEvent?.stopPropagation();_extentPoints.pop();if(_extentPoints.length<3)return;_extentPoints.push([..._extentPoints[0]]);_updateExtentFeature(_makePoly(_extentPoints));_finishExtentDraw(false);});}

  function onActivate(){
    _log('onActivate');
    _setupLayerToggles();
    _setupFacilityToggles();
    _syncAllToggles();
    _checkSacStatus();
    _wireExtentDrawEvents();
    _loadManualLabels().then(()=>_buildEditSandboxLayers());
    if(map)map.on('moveend',_onMoveEnd);
    // Delay OSM road layer to ensure source exists
    setTimeout(()=>_buildOsmRoadLayer(),500);
    setTimeout(()=>_buildOsmRoadLayer(),1500);
    _loadConsolidatedResultSilent();
    _loadFacilityModelResultSilent();
  }
  async function _loadConsolidatedResultSilent(){try{const r=await fetch('/api/debug/consolidated/intersections');if(!r.ok){_log('Silent load HTTP '+r.status);return;}const d=await r.json();_log('Silent load: '+d.num_intersections+' intersections');_consolidationResult={intersections:d.intersections,num_intersections:d.num_intersections,tolerance:d.tolerance};_consolidationLoaded=true;_setHidden('consolidate-results-section',false);_setText('consolidate-result-stat',d.num_intersections.toLocaleString()+' intersections Â· tol='+d.tolerance+'m');_buildConsolidatedLayers();}catch(e){_log('Silent load error: '+e.message);}}
  function _onMoveEnd(){if(!_osmRoadLayerReady)_buildOsmRoadLayer();}

  function onDeactivate(){
    _log('onDeactivate');
    clearInterval(_dlPollTimer);_dlPollTimer=null;
    clearInterval(_consolidatePollTimer);_consolidatePollTimer=null;
    clearInterval(_bulkDlPollTimer);_bulkDlPollTimer=null;
    _stopHighlight();_osmRoadLayerReady=false;
    if(map){
      map.off('moveend',_onMoveEnd);
      map.off('click',_onGlobalClick);
      map.off('click',_onFacilityModelClick);
      map.off('mouseenter','dbg-cons-intersections');
      map.off('mouseleave','dbg-cons-intersections');
    }
    _globalClickBound=false;
    _facilityClickBound=false;
    _clearConsolidatedLayers();
    _clearFacilityModelLayers();
    _clearEditSandboxLayers();
    cancelExtentDraw();
    try{if(map&&map.getLayer('dbg-osm-roads'))map.removeLayer('dbg-osm-roads');}catch(e){}
  }

  function rebuildAfterStyleLoad(){
    if(!map||typeof G_appMode==='undefined'||G_appMode!=='debug')return;
    _log('rebuildAfterStyleLoad');
    _syncAllToggles();
    _buildEditSandboxLayers();
    setTimeout(()=>_buildOsmRoadLayer(),300);
    if(_consolidationLoaded){_buildConsolidatedLayers();_bindGlobalClickHandler();}
    if(_facilityModelLoaded){_buildFacilityModelLayers();_bindFacilityModelClickHandler();}
  }

  return {downloadSacramento,downloadBulk,computeConsolidation,buildFacilityModel,onActivate,onDeactivate,rebuildAfterStyleLoad,onToleranceSliderInput,loadEditElements,startExtentRect,startExtentPoly,cancelExtentDraw,saveCurrentExtent};
})();
