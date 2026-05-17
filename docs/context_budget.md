# Context Budget Guidelines

SafetyGIS should stay easy to reason about as the facility model evolves. These
guidelines define how future work should limit context consumption while keeping
the app runnable.

## Default Reading Order

Start with:

- `AGENTS.md`
- `PROJECT_STATE.md`
- The smallest relevant implementation area

For facility-model work, that usually means:

- `app/facility_model/`
- `app/debug/`
- `tests/test_facility_model.py`
- `tests/test_facility_ranking.py`

Avoid opening broad files until they are directly relevant.

## Large Files

Large files are not default context. Read them only when the task explicitly
touches their surface:

- `static/app.js`: main map shell, shared map state, rank dashboard, topology
  panel, and inspect-mode behavior.
- `static/analysis_panel.js`: Analysis mode county downloads, legacy ranking
  controls, and facility-model ranking tree UI.
- `static/debug_sandbox.js`: Debug-mode sandbox map layers and manual labels.
- `scripts/build_safety_rankings.py`: legacy statewide/bin EPDO rankings.
- `METHODOLOGY.md`: EPDO methodology and ranking assumptions.

## Runtime Data

The `data/` directory is runtime cache. Do not use it as project context unless
the task asks for a specific generated artifact or a cache-related bug requires
inspection.

Generated cache examples include crash GeoJSON, OSM tiles, PBF downloads,
debug facility-model outputs, ranking outputs, party/victim enrichment, and
Mapillary caches.

## Development Shape

Prefer small modules with clear ownership:

- Facility extraction and classification logic belongs in `app/facility_model/`.
- Debug-only orchestration belongs in `app/debug/`.
- Analysis-mode UI belongs in `static/analysis_panel.js`.
- Main map shell behavior belongs in `static/app.js`.

New behavior should include focused tests. Avoid adding more unrelated behavior
to large files when a small module or helper can carry the change.

## Legacy Baseline

The old statewide/bin ranking workflow remains runnable as a baseline, but it is
not the active development direction. Future work should use facility-model
ranking unless the user explicitly asks to modify the legacy workflow.
