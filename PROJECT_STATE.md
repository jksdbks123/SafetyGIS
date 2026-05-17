# SafetyGIS Current State

Last reviewed: 2026-05-17

## Product Core

SafetyGIS is a FastAPI and vanilla MapLibre web GIS for California transportation
safety review. The working product has three useful surfaces:

- Inspect mode: lazy viewport loading for CCRS crashes, OSM infrastructure,
  Mapillary, Street View, selection/export, and crash statistics.
- Analysis mode: county cache management and EPDO safety rankings from cached
  CCRS/OSM data.
- Debug mode: a Sacramento-focused sandbox for OSM road inspection and
  consolidated intersection experiments.

## Current Architecture

- `main.py`: FastAPI app assembly and static file serving.
- `app/config.py`: paths, environment variables, county bounds, OSM query
  constants, and modeling defaults.
- `app/osm/`: Overpass/PBF tile processing, topology lookup, cache helpers, and
  geometric intersection consolidation.
- `app/crash/`: CCRS fetch, crash cache, party/victim enrichment, and stats.
- `app/rankings/`: safety-ranking API wrapper around `scripts/build_safety_rankings.py`.
- `app/debug/`: sandbox endpoints for Sacramento tile downloads, PBF processing,
  and consolidated-intersection exploration.
- `static/app.js`: main map UI.
- `static/analysis_panel.js`: Analysis-mode county download and ranking UI.
- `static/debug_sandbox.js`: debug-mode consolidation UI.

## Accepted Progress

The read-only GIS and rankings workflow are useful enough to keep as a baseline.
The important active work is not PostGIS, drawing tools, Tailwind, or AI query
features. Those are deferred until the modeling foundation is clearer.

## Product Goal

The target workflow is an engineer-facing traffic facility ranking tool. An
engineer may not know what improvement strategy is appropriate for a specific
intersection, ramp, or road segment. SafetyGIS should help by finding facilities
with the same operating context and physical attributes, ranking them only within
that comparable peer group, and exposing crash patterns and facility design
attributes so engineers can infer which designs or controls are associated with
higher crash burden.

Ranking must therefore remain leaf-bin based, not a single flat global list. The
classification tree should preserve dimensions such as facility type, road class,
speed, lane count, geometric configuration, traffic control type, structural
grade, ramp/road subtype, and length bin. The comparison happens at the leaf.

The legacy statewide/bin ranking workflow is kept runnable as a baseline, but
future ranking work should prefer the facility-model ranking path unless a task
explicitly targets legacy bins.

The current modeling path is geometric consolidation:

1. Build a graph from OSM way endpoints.
2. Filter graph nodes by degree.
3. Project to meters.
4. Buffer candidate nodes by a tolerance.
5. Union overlapping buffers into clusters.
6. Emit one consolidated intersection point per cluster.
7. Preserve member nodes and incident OSM way IDs for inspection.

This is implemented in `app/osm/consolidate.py` and surfaced through
`app/debug/router.py` plus `static/debug_sandbox.js`.

## Main Problem

Intersection and roadway modeling remains unresolved.

Current facility-model ranking work is moving toward this target:

- OSM/PBF-derived facility candidates for junctions, road segments, and ramps.
- Tree-based peer grouping in Analysis mode.
- EPDO percentile/rank computed within leaf peer groups.
- Facility click-through should keep the comprehensive dashboard: crash overlay,
  severity and conflict distributions, percentile chart, topology diagram for
  junctions, and facility design attributes.
- Analysis-mode OSM downloads should prefer the high-speed California PBF path
  instead of slow per-tile Overpass fetching.

The current consolidation is useful as a sandbox, but it is not yet a production
facility model. The next model needs to define:

- What counts as an intersection nucleus.
- How divided roads, slip lanes, ramps, roundabouts, and grade separation are
  represented.
- How a roadway segment is bounded between intersections.
- Whether crash assignment should target nodes, consolidated clusters, roadway
  segments, approaches, or movements.
- Which model outputs become stable facility IDs for rankings and later storage.

## Deferred

The following are explicitly out of scope until modeling is settled:

- PostGIS persistence.
- Drawing and project-management workflows.
- Tailwind UI polish.
- Natural-language / AI map queries.
- Empirical Bayes or SPF work.
- Broad AADT integration beyond preserving existing scripts/reference data.

## Repository Hygiene

Runtime data belongs under `data/` and should stay out of git unless a file is a
small, intentional fixture. Large caches and generated artifacts are disposable:

- CCRS crash caches.
- OSM tile and relation caches.
- party/victim caches.
- Mapillary caches.
- rankings outputs.
- consolidated debug caches.
- local PBF downloads.

Archived cell-model experiments, notebooks, screenshots, and old visual examples
are no longer part of the working project context.

## Next Development Direction

Keep the app runnable, but treat the debug sandbox as the primary workbench.
The next implementation step should be a small, explicit facility-model package
that can turn OSM roads into stable intersection and roadway candidates, with
diagnostic GeoJSON outputs before any UI or database expansion.

## Context Budget

Use `docs/context_budget.md` as the standing context-control rulebook. In short:
read the project state first, then only the smallest relevant implementation
area; avoid `data/` and legacy ranking files unless the task directly requires
them.
