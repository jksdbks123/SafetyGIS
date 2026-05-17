# Facility Model and Crash Pattern Dashboard Plan

## Purpose

SafetyGIS should move from overlapping experimental candidates toward a stable,
mutually exclusive facility model that can support defensible crash assignment,
peer-group ranking, and engineer-facing pattern discovery.

This plan covers two near-term product tracks:

1. A mutually exclusive facility model for junctions, roadway segments, ramps,
   and special configurations.
2. A classification-tree dashboard that helps engineers see how crash outcomes
   vary across facility attributes.

## Track 1: Mutually Exclusive Facility Model

### Current Problem

The current facility model can emit junction candidates and segment candidates
that spatially overlap. Crashes near an intersection can therefore be assigned
to both the junction and a roadway segment, which inflates counts and makes
facility-level rankings harder to trust.

### Modeling Principle

Every crash should have one primary facility assignment for ranking. Additional
nearby facilities can be retained as secondary context, but primary EPDO counts
must be mutually exclusive.

### Proposed Facility Hierarchy

Use a priority order for primary crash assignment:

1. Junction nucleus
2. Ramp terminal or ramp merge/diverge influence area
3. Roadway segment
4. Unassigned / out-of-network diagnostic bucket

This hierarchy prevents a crash from being counted in both a junction and an
adjacent segment. It also makes ambiguous cases inspectable instead of silently
duplicated.

### Junction Nucleus Definition

Represent each junction as a compact influence area rather than a single point.
The nucleus should include:

- Consolidated node cluster center.
- Member OSM nodes.
- Short connector geometry between member nodes.
- Approach cut lines where the junction transitions into roadway segments.

Candidate rules:

- Merge nearby at-grade graph nodes when they are connected by short internal
  links or represent divided-road intersection legs.
- Keep grade-separated crossings separate unless there is a connecting ramp,
  link, or shared node.
- Represent roundabouts as one junction nucleus using the roundabout ring and
  approach cut points.
- Represent slip lanes as part of a junction if they connect two approaches
  within the junction influence area and are below a length threshold.

### Roadway Segment Definition

Roadway segments should begin and end at facility boundaries:

- Junction cut lines.
- Ramp terminal cut lines.
- Meaningful changes in roadway classification attributes, if needed later.

Segment geometry should exclude any geometry consumed by junction nuclei or ramp
terminal areas. The remaining linework becomes rankable segment facilities.

### Ramp Handling

Treat ramps as first-class facilities, not generic roadway segments.

Recommended subtypes:

- Freeway entrance ramp.
- Freeway exit ramp.
- System ramp.
- Ramp terminal at surface street.
- Merge/diverge influence area.

Ramp crashes should be assigned to ramp facilities before generic road segments
when the crash lies on ramp geometry or within a ramp influence envelope.

### Assignment Algorithm

Build a deterministic crash-to-facility assignment table:

1. Precompute facility polygons or buffered envelopes in projected coordinates.
2. For each crash, find candidate facilities within a search radius.
3. Apply the primary assignment hierarchy.
4. Tie-break by distance to facility geometry, then by facility confidence, then
   by stable facility ID.
5. Store assignment diagnostics: candidate count, chosen facility, distance,
   hierarchy reason, and ambiguity flags.

Expected output:

- `primary_facility_id`
- `primary_facility_type`
- `assignment_distance_m`
- `assignment_method`
- `secondary_facility_ids`
- `assignment_flags`

### Diagnostics

Add GeoJSON diagnostics before treating the model as production:

- Junction nuclei polygons.
- Segment pieces excluded by junction envelopes.
- Ramp influence envelopes.
- Crashes with multiple candidate facilities.
- Crashes left unassigned.
- Segment/junction overlap heat layer.

### Test Strategy

Create focused fixtures for:

- Divided arterial crossing another divided arterial.
- Ramp terminal next to a signalized intersection.
- Slip lane at a surface intersection.
- Roundabout with multiple approaches.
- Grade-separated crossing with no connection.
- Closely spaced intersections along a corridor.

Each fixture should assert:

- Facility IDs are stable.
- Junction and segment geometries do not overlap beyond allowed tolerance.
- Each crash receives at most one primary assignment.
- Assignment flags expose ambiguous cases.

## Track 2: Classification-Tree Crash Pattern Dashboard

### Product Goal

The dashboard should help an engineer quickly answer:

- Which crash types are unusually common in this peer group?
- Which facility attributes correlate with severity or conflict type?
- Which branch of the classification tree explains the difference?
- Which facilities are outliers within otherwise similar contexts?

### Dashboard Structure

Use the classification tree as the main navigation model. Each node should show
counts and distributions for its descendants, with drill-down from broad classes
to leaf peer groups.

Recommended views:

- Tree overview: facility count, crash count, EPDO, and top crash type per node.
- Branch comparison: side-by-side distributions across sibling nodes.
- Leaf peer group: ranked facilities plus crash-pattern breakdown.
- Facility detail: assigned crashes, facility attributes, topology, and nearby
  secondary context.

### Metrics

Compute summaries at every tree node:

- Facility count.
- Crash count.
- EPDO total and EPDO per facility.
- Fatal, severe injury, other injury, PDO.
- Pedestrian, bicycle, motorcycle, rear-end, broadside, sideswipe, hit-object.
- Night, wet-road, alcohol/drug involvement where available.
- Percentile bands within the node and within the leaf.

### Correlation-Oriented Comparisons

For each tree split, show how crash patterns change between children:

- Difference in crash-type share.
- Difference in severe/fatal share.
- Difference in EPDO per facility.
- Minimum sample-size warning.
- Top overrepresented crash categories.

This should be descriptive rather than causal. The UI should use language like
"overrepresented in this branch" instead of implying that an attribute causes a
crash pattern.

### Suggested Visuals

- Compact tree table with sparklines.
- Stacked bars for crash type and severity.
- Small multiples for sibling branches.
- Scatterplot of EPDO percentile versus crash count.
- Facility list filtered by selected branch and crash type.
- Map highlight synchronized with the selected tree node.

### Data Contract

Create a backend summary endpoint that is independent from the current UI:

- `/api/analysis/classification_tree/summary`
- `/api/analysis/classification_tree/node/{node_id}`
- `/api/analysis/facility/{facility_id}/crash_profile`

The summary payload should be pre-aggregated enough for fast UI navigation and
include node IDs that map directly to classification-tree paths.

### Implementation Sequence

1. Finish mutually exclusive facility assignment first.
2. Emit a crash assignment table and diagnostics.
3. Build node-level aggregation from assigned crashes.
4. Add a simple tree summary endpoint.
5. Build an initial dashboard in Analysis mode.
6. Add branch comparison and facility-detail refinements.

## Near-Term Deliverables

1. Facility model fixtures and overlap tests.
2. Primary crash assignment module with diagnostics.
3. Facility model cache schema that stores facilities and assignments together.
4. Classification-tree aggregation module.
5. First dashboard view: tree summary, sibling comparison, and selected-node
   facility list.
