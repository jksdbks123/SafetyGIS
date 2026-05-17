# AGENTS.md

## Language

- Internal reasoning: English.
- Responses to the user: Chinese.
- Product output: English only. This includes code, comments, docstrings,
  UI labels, logs, README-style docs, and API responses.

## Current Project Direction

SafetyGIS is a FastAPI and vanilla MapLibre web GIS for California transportation
safety analysis. The broad old roadmap is obsolete.

The active priority is the intersection and roadway model. Do not spend effort on
PostGIS, drawing tools, Tailwind polish, AI query features, Empirical Bayes, or
new deployment work until the facility model is settled.

Use `PROJECT_STATE.md` as the current source of truth.

## Core App

- `main.py`: FastAPI app assembly.
- `app/config.py`: paths, environment, county bounds, and modeling constants.
- `app/osm/`: OSM fetching, tile parsing, topology, PBF, and consolidation.
- `app/crash/`: CCRS crash fetching, caches, enrichment, and stats.
- `app/rankings/`: EPDO ranking endpoints.
- `app/debug/`: modeling sandbox endpoints.
- `static/app.js`: main MapLibre UI.
- `static/analysis_panel.js`: Analysis mode UI.
- `static/debug_sandbox.js`: debug sandbox UI.
- `scripts/build_safety_rankings.py`: EPDO ranking computation.

## Development Rules

- Prefer existing patterns and keep edits scoped.
- Use `rg` for search.
- Preserve user changes; do not reset or revert unrelated work.
- Use `apply_patch` for manual file edits.
- Generated caches belong under `data/` and should stay out of git.
- Before touching `scripts/build_safety_rankings.py`, read `METHODOLOGY.md`.

## Context Budget Rules

- Start each task with `PROJECT_STATE.md` and this file, then read only the
  2-4 implementation files needed for the task.
- For facility-model work, prefer `app/facility_model/`, `app/debug/`, and
  `tests/test_facility_*` before broader app files.
- Do not read `static/app.js`, `scripts/build_safety_rankings.py`, or
  `METHODOLOGY.md` unless the task directly touches the main map shell, legacy
  statewide/bin rankings, or EPDO methodology.
- Treat `data/` as runtime cache, not project context, unless the user asks to
  inspect a specific generated file.
- New work should create small module boundaries and focused tests instead of
  expanding large files.
- Keep legacy statewide/bin ranking code runnable, but prioritize
  facility-model ranking for future development.

## Run

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
uvicorn main:app --reload
```

macOS/Linux:

```bash
source .venv/bin/activate
uvicorn main:app --reload
```

Open `http://localhost:8000`.

## Modeling Focus

The next useful work should produce a small, testable facility-model layer:

- stable intersection candidates,
- stable roadway segments,
- diagnostic GeoJSON outputs,
- explicit handling for divided roads, ramps, slip lanes, roundabouts, and grade
  separation.

Debug mode is the current sandbox for this work.
