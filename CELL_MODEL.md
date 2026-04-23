# Design: Composite Infrastructure Entity Model

> **Status: Conceptual — iterating toward implementation spec**
> This document is intentionally open-ended. Each revision should narrow the scope until
> the schema is stable enough to code against.

---

## Problem with the Current Model

Every ranked facility is either a **Point** (intersection) or a **LineString** (segment).
A point carries aggregate properties — `degree`, `control_type`, `conflict_points` — but
nothing about its immediate topological neighborhood. The surrounding road geometry,
approach-by-approach attributes, and relational constraints (turn restrictions, route
memberships) are discarded at ranking time.

Consequences:
- Two 4-leg signals look identical to the ranker even if one has 55 mph rural approaches
  and the other has 25 mph urban ones.
- EPDO rate normalization uses a single facility-level AADT; approach-level exposure
  is unavailable.
- The topology panel shows bearing + lane count, but that data never enters the ranking
  feature vector.
- Compound entities (roundabouts, divided intersections) are already grouped, but their
  merged approach attributes are never persisted to the ranking output.

---

## Core Concept: The Infra Cell

### Cell Metaphor

The road network is a tessellation of **mutually exclusive infra cells**. Each cell owns a
contiguous piece of the network — no overlap, no gap. Two adjacent cells share a boundary
at the midpoint of the way that connects them. Cells connect to each other through those
shared boundaries like tiles in a mosaic.

```
╔══════════════════╗     ╔══════════════════╗
║   Cell A         ║     ║   Cell B         ║
║                  ║     ║                  ║
║   [core A1]──────╫─────╫──[core B1]       ║
║       │          ║  ↑  ║      │           ║
║       │          ║ boundary   │           ║
║   [core A2]      ║(midpoint)  │           ║
║       │          ║     ║      │           ║
╚═══════╪══════════╝     ╚══════╪═══════════╝
        │                       │
      [Cell C]              [Cell D]
```

**Cell** = one infra entity (one ranked facility)
**Core** = one OSM nucleus node (or way). A cell may have **more than one core** —
a roundabout cell has 3–8 ring nodes all merged into one cell; a divided intersection
cell has two parallel nodes. The cores collectively define where the cell is.
**Cell territory** = the cores + the near-halves of all approach ways radiating from them.
**Cell boundary** = the midpoint of each approach way — the handoff point between adjacent cells.

This mutual exclusivity has a concrete consequence: when a crash is spatially matched,
it is assigned to exactly one cell. No double-counting across compound structures.

### Facility as a Graph Neighborhood

A composite infrastructure entity is a small subgraph of the OSM topology:

```
                    [Terminus B / Cell B boundary]
                         ╎
                   (approach way 2)  ← owned by this cell up to midpoint
                    speed=45  lanes=2
                         ╎
[Terminus A]━━━(approach way 1)━━━[CORE n12345]━━━(approach way 3)━━━[Terminus C]
               speed=35  lanes=4   ╔══════╗      speed=35  lanes=2
                              ╔════╣ CELL ╠════╗
                              ║    ╚══════╝    ║
                                       ╎
                                 (approach way 4)
                                  speed=45  lanes=1
                                       ╎
                                  [Terminus D / Cell D boundary]
```

The **core(s)** are the OSM node(s) at the heart of the cell (one or more for compound cells).
The **approaches** are the 1-hop ways directly incident to the cores.
The **relations** are OSM relations referencing the cores or their approaches.
The **cell boundary** on each approach is at `approach_length_m / 2` — midway to the next cell.

---

## Debug Mode

Understanding how the system partitions the road network into cells requires a dedicated
visual inspection tool — one that exposes the grouping logic directly on the map. This
motivates a **third app mode** alongside inspect and analysis:

```
G_appMode = 'inspect' | 'analysis' | 'debug'
```

### What Debug Mode Shows

| Layer | Purpose |
|---|---|
| **Cell polygons** | Convex hull (or buffered union) of each cell's cores + approach midpoints. Colored by configuration: teal=UNDIVIDED, orange=ROUNDABOUT, yellow=CHANNELIZED_RT, blue=DIVIDED. |
| **Core markers** | Dots at each nucleus node. Multi-core cells show all cores connected by a thin line, making compound grouping visible. |
| **Approach territory** | Each approach way rendered in two halves: the "near half" (owned by this cell) in the cell's color, the "far half" (owned by the neighboring cell) in that neighbor's color. The midpoint split is marked with a small tick. |
| **Cell ID label** | Short entity ID (`n12345`) overlaid near the centroid of each cell polygon. Click to open raw entity JSON. |
| **Orphan ways** | Ways not assigned to any cell shown in red — diagnostic for coverage gaps. |
| **Cell connections** | Thin lines between cell centroids along each shared boundary — makes the cell graph topology visible at a glance. |

### Debug Panel (click a cell)

Opens a raw JSON inspector panel showing the full composite entity:
- `entity_id`, `entity_type`, `derived.configuration`
- Core(s) with coordinates
- Approaches list (way_id, bearing, speed, lanes, territory_m)
- Relations list
- Assigned crash count (from last rankings run, if available)

### Mutual Exclusivity Visualization

A key invariant to verify: every approach way midpoint belongs to exactly one cell.
Debug mode should highlight any approach whose midpoint falls within two overlapping
cell polygons — this would signal a compound grouping conflict.

### Mode Transition

```
[Inspect] ──→ [Analysis] ──→ [Debug]
                                 ↑
                   triggered by new toolbar button or keyboard shortcut 'D'
```

Debug mode suppresses the crash and rankings layers. It loads only OSM topology data
(reuses the existing `_osm_tile_features` cache). It does not require the analysis
pipeline to have run.

---

## OSM Data Model Alignment

OSM already encodes this topology. The three primitive types map naturally:

| OSM primitive | Role in composite entity | Current use |
|---|---|---|
| **Node** | Nucleus of intersection; terminus of each approach | ✅ stored as `intersection_centroid` |
| **Way** | Each approach road (geometry + tags); nucleus of segment | ✅ stored individually; ⚠️ not linked to nucleus |
| **Relation** | Turn restrictions; route memberships; administrative | ⚠️ only `type=restriction` parsed; routes ignored |

---

## Proposed Composite Entity Schema

```jsonc
{
  // ── Identity ────────────────────────────────────────────────────────────
  "entity_id":   "n12345",               // "n{node_id}" | "w{way_id}"
  "entity_type": "intersection",         // "intersection" | "segment"

  // ── Nucleus ─────────────────────────────────────────────────────────────
  // The central OSM element (node for intersection, way for segment)
  "nucleus": {
    "osm_id":    12345,
    "osm_type":  "node",                 // "node" | "way"
    "geometry":  { "type": "Point", "coordinates": [-121.5, 39.7] },
    "tags": {
      "highway":          "traffic_signals",
      "traffic_signals":  "signal",
      "crossing":         "traffic_signals",
      "bicycle":          "yes"
      // ... all raw OSM tags preserved
    }
  },

  // ── Approaches ──────────────────────────────────────────────────────────
  // One entry per OSM way directly incident to the nucleus.
  // For segment entities this is the two endpoint ways; or a broader leg set.
  "approaches": [
    {
      "way_id":     456789,
      "geometry": {
        "type": "LineString",
        "coordinates": [[-121.500, 39.700], [-121.495, 39.701]]
        // trimmed to ~120 m from nucleus — enough for sight-line estimation
      },
      "tags": {
        "name":           "Main Street",
        "highway":        "secondary",
        "oneway":         "no",
        "lanes":          "4",
        "maxspeed":       "35 mph",
        "surface":        "asphalt",
        "sidewalk":       "both",
        "bicycle":        "lane",
        "turn:lanes":     "left|through|through|right",
        "cycleway:right": "lane"
      },
      // Derived / computed
      "bearing":          92.2,          // degrees from nucleus toward terminus
      "oneway_flag":      0,             // +1 outbound-only, -1 inbound-only, 0 bidirectional
      "lanes_int":        4,             // parsed integer (default 1)
      "speed_mph":        35,            // parsed numeric (road-class default if missing)
      "aadt":             12500,         // from pre-computed lookup (null if missing)
      "terminus_node_id": 789012,        // far-end node of this approach way
      "approach_length_m": 118.4,        // haversine to terminus
      "has_bike_lane":    true,
      "has_sidewalk":     true,
      "turn_lanes_list":  ["left", "through", "through", "right"]
    }
    // ... one entry per approach
  ],

  // ── Relations ───────────────────────────────────────────────────────────
  "relations": [
    {
      "rel_id":        111222,
      "type":          "restriction",
      "restriction":   "no_left_turn",
      "from_way_id":   456789,
      "to_way_id":     567890
    },
    {
      "rel_id":        222333,
      "type":          "route",
      "route":         "bus",
      "ref":           "42",
      "operator":      "Sacramento RT"
    }
  ],

  // ── Derived intersection-level attributes ───────────────────────────────
  "derived": {
    "configuration":           "UNDIVIDED",    // ROUNDABOUT|CHANNELIZED_RT|DIVIDED|UNDIVIDED
    "conflict_points":         12,
    "leg_count":               4,
    "control_type":            "signal",        // signal|stop|yield|uncontrolled
    "compound_nodes":          [],
    "max_approach_speed_mph":  45,
    "min_approach_speed_mph":  35,
    "speed_variance_mph":      5,
    "approach_lane_total":     10,
    "has_bike_lane":           true,
    "has_sidewalk":            true,
    "has_bus_route":           true,
    "turn_restriction_count":  1
  }
}
```

---

## What This Unlocks for Safety Ranking

### 1. Richer peer-group bins

Current bin key for intersections:
```
int | {control} | {road_class} | {speed_bin} | {leg_bin}
```

With approach attributes, we can add dimensions:
```
int | {control} | {max_approach_class} | {max_approach_speed_bin} | {leg_bin} | {has_ped}
```

This separates a 4-leg signal with two 55 mph arterials from one with four 25 mph local streets.

### 2. Approach-level AADT exposure

Rate normalization is currently: `epdo_score / (facility_aadt × length × years)`.
For intersections, facility-level AADT is a single-approach estimate. With approach-level
AADT, the entering-vehicle count is the sum across all approaches — the correct denominator
for intersection conflict frequency models (FHWA HSM Part C).

```
epdo_rate = epdo_score / sum(approach_aadt_i × approach_volume_weight_i)
```

### 3. Turn-movement-specific analysis

Angle crashes (the most common fatal intersection type) correlate with crossing-movement
conflict points. With individual approach-level `oneway_flag` and `turn_lanes_list`, we
can count crossing-movement pairs instead of using the generic Garber & Hoel formula.

### 4. Multimodal exposure

Bus route relations → elevated pedestrian exposure estimate.
`bicycle=lane` on approaches → cyclist conflict potential.
Currently both are zero in the feature vector.

### 5. Compound entity EPDO merge

For roundabouts and divided intersections, the current system groups nodes but only
keeps the primary node's crash data. With a composite entity, the secondary nodes'
crash coords can be merged into the primary's list before EPDO summation.

---

## Open Questions (to resolve before implementation)

1. **Approach length cutoff** — how many meters of approach geometry to store per way?
   - Short (30–50 m): just the turning movement zone
   - Medium (100–150 m): includes deceleration lane geometry
   - Long (to next intersection): allows approach-level speed profiling
   
   **Leaning toward:** 120 m (covers deceleration distance at 55 mph; avoids full way storage)

2. **Segment entity approaches** — for a road segment, what are the "approaches"?
   - Option A: The two OSM nodes at each end of the way (intersection endpoints)
   - Option B: Parallel way (opposing direction, for divided highways)
   - Option C: Both
   
   **Leaning toward:** Option A first (endpoint intersection topology), Option C eventually

3. **Relation scope** — which relation types to include?
   - `type=restriction` — already partially done; include fully ✅
   - `type=route` with `route=bus|bicycle|foot` — useful for exposure weighting ✅
   - `type=route` with `route=road` (Caltrans state routes) — useful for road classification ✅
   - Administrative boundaries, signage, etc. — probably not yet ❌

4. **Storage strategy** — where does the composite entity live?
   - Option A: Extend the existing relation cache JSON per tile
   - Option B: Separate `entity_cache/` directory with one JSON per facility ID
   - Option C: Embed compact version in rankings GeoJSON; full entity on-demand via API
   
   **Leaning toward:** Option C — compact summary in rankings; full entity served by
   `GET /api/osm/facility/{fid}` which assembles on demand from the tile cache

5. **Backward compatibility** — existing `data/rankings/` files won't have the new fields.
   The frontend should degrade gracefully (show topology panel without approach detail
   if entity endpoint returns 404).

6. **Cell boundary assignment** — when two intersection cells share an approach way, how
   is the way split?
   - **Midpoint** (simple, symmetric): each cell owns half the way length
   - **Functional split** (traffic engineering): the "influence zone" extends to where
     queue spillback typically ends — roughly the deceleration distance from the nucleus.
     At 55 mph that's ~120 m; at 25 mph that's ~30 m. The cell boundary is NOT at midpoint
     but at distance `d = v²/(2a)` from the nucleus (a ≈ 3.5 m/s²).
   - For now: **midpoint** for simplicity; speed-based influence zone can be a later refinement.

7. **OSM tag coverage gaps** — many approaches lack `maxspeed`, `lanes`, or `turn:lanes`.
   - Default by road class (already done for `speed_mph` and `lanes`)
   - Or flag explicitly as `"estimated": true` to distinguish real from imputed data
   - Recommendation: add `"estimated_speed": bool` and `"estimated_lanes": bool` flags

---

## Implementation Phases (rough, to be refined)

### Phase 0 — Debug Mode (frontend only, no backend changes needed)
Build the debug mode UI using only data already available in the topology cache:
- Add `'debug'` as a valid `G_appMode` value; add toolbar button + `'D'` shortcut
- On mode entry: load OSM tile features for viewport (reuses existing `scheduleViewportLoad`)
- For each `intersection_centroid` feature: fetch topology via existing `/api/osm/topology`
- Draw cell polygon: convex hull of `[nucleus_lon/lat] + [approach midpoints]`
- Color by `topo.configuration`; draw core dots; connect compound_nodes with lines
- Click → raw JSON inspector panel
- This phase is independently deployable and validates the cell model visually
  before any backend changes are made

### Phase A — Extend approach attributes in `_compute_tile_topologies`
- Add `speed_mph`, `surface`, `sidewalk`, `bicycle`, `turn:lanes` to each approach entry
- Parse `turn:lanes` string into `turn_lanes_list`
- Compute `approach_length_m` (haversine nucleus → terminus node)
- Add terminus node geometry to `nodes_dict` lookup
- Store `has_bike_lane`, `has_sidewalk` booleans on each approach
- Add `has_bus_route` to derived section (from relation scan)

### Phase B — Extend relation parsing to include route relations
- Current: only `type=restriction` parsed in Pass 3 of `_osm_tile_features`
- Add: `type=route` with `route ∈ {bus, bicycle, foot, road}` — store as relation list

### Phase C — Composite entity assembly in `build_safety_rankings.py`
- For each ranked facility, assemble composite entity from approach attributes
- Add `max_approach_speed_mph`, `approach_lane_total`, `has_bus_route` to facility props
- Use `sum(approach_aadt)` as intersection entering-volume denominator where AADT known
- Extend bin key to include `max_approach_speed_bin` for intersection bins

### Phase D — `GET /api/osm/facility/{fid}` endpoint in `main.py`
- Load tile cache for tile containing `fid`
- Assemble full composite entity JSON on demand
- Return 404 if tile not cached (frontend triggers lazy load)

### Phase E — Frontend: topology panel uses composite entity
- Replace `GET /api/osm/topology?node_id=…` call with `GET /api/osm/facility/{fid}`
- Render approach ways as colored polylines in the SVG diagram
- Show per-approach speed, lanes, AADT in the approach table

---

## Critical Files (tentative)

| File | Change |
|---|---|
| `static/app.js` — `setAppMode`, toolbar | Phase 0: add `'debug'` mode |
| `static/app.js` — new `_renderDebugCells` | Phase 0: cell polygon + core + connection layers |
| `static/index.html` | Phase 0: debug toolbar button, JSON inspector panel |
| `main.py` — `_compute_tile_topologies` | Phase A + B |
| `main.py` — `GET /api/osm/facility/{fid}` | Phase D (new endpoint) |
| `scripts/build_safety_rankings.py` | Phase C |
| `static/app.js` — `_renderTopologyPanel` | Phase E |
| `METHODOLOGY.md` | Document cell model + debug mode |

---

## Verification (when ready to code)

1. Pick a known complex intersection (e.g., a signalized 5-leg in Sacramento)
2. `GET /api/osm/facility/n{node_id}` → confirm approaches list has speed, lanes, AADT
3. Check `derived.max_approach_speed_mph` and `derived.has_bus_route` are correct
4. Rerun `build_safety_rankings.py` for Sacramento → confirm new bin key separates
   high-speed vs low-speed 4-leg signals
5. Frontend: click intersection → topology panel shows approach polylines + per-approach table
