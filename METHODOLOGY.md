# GIS-Track Safety Rankings — Methodology

**Version:** 1.0 (2026-04-10)  
**Author:** Zhihui Chen  
**System:** GIS-Track — California Transportation Safety (SafetyGIS)

---

## 1. Overview

The GIS-Track rankings engine identifies potentially hazardous road facilities by spatially joining police-reported crash records to OpenStreetMap road infrastructure and computing an EPDO-weighted severity score for each facility. Facilities are then ranked **within homogeneous peer groups** (bins) so that comparisons are only made between structurally similar locations.

**What the engine does:**
- Builds a registry of intersections and road segments from OSM data
- Matches each crash record to the nearest intersection and/or segment within defined radii
- Computes a 5-year EPDO score and crash-attribute distributions per facility
- Stratifies facilities into peer-group bins and ranks worst/best within each bin

**What it does not do (current limitations):**
- It does not detect grade separations — overpasses and underpasses are invisible to the 2D spatial join
- It does not normalize by traffic volume (AADT) — frequency bias favors high-volume facilities
- It does not apply Empirical Bayes correction for regression to the mean

**Audience:** Transportation planners, traffic engineers, and safety analysts evaluating high-injury network locations in California.

---

## 2. Data Sources

| Source | Content Used | Known Limitations |
|--------|-------------|-------------------|
| **CHP CCRS** via `data.ca.gov` | Crash point coordinates, severity (KABCO), date/time, lighting, weather, road condition, primary collision factor, motor vehicle involved with, collision type | Police-reported; under-reports ~50% of injury crashes (NHTSA CRSS); ~10% of records lack precise GPS coordinates and are placed at the nearest intersection centroid |
| **OpenStreetMap** via Overpass API | Node control type (`highway=traffic_signals/stop/give_way`), way geometry, highway class, speed limit, lane count | Volunteer-maintained; completeness varies by county; bridge/tunnel/layer tags **not currently fetched** |

**Analysis window:** 5 years (constant `YEAR_WINDOW = 5`). This is consistent with HSM network screening practice — long enough to average out year-to-year random variation, short enough to reflect current infrastructure conditions. FHWA's Highway Safety Improvement Program (HSIP) manual recommends a minimum of 3 years; 5 years is the standard for high-confidence ranking (*FHWA-SA-18-020*, 2018).

---

## 3. Facility Registry

### 3.1 Intersections

Included node types:

| OSM Tag | Control Type | Conflict Points |
|---------|-------------|-----------------|
| `highway=traffic_signals` | Signalized | All movement conflicts resolved by signal |
| `highway=stop` | All-way stop | All movements yield |
| `highway=give_way` | Yield / priority | Minor road yields to major |

**Excluded node types:** `highway=crossing`, `highway=bus_stop`, `highway=traffic_calming`, `highway=mini_roundabout`, untagged nodes. These are excluded either because they do not represent vehicle-vehicle conflict points or because OSM tagging for them is too inconsistent to support reliable classification.

### 3.2 Road Segments

All OSM ways with a `highway` value in the motorized road class set:

```
motorway, motorway_link, trunk, trunk_link,
primary, primary_link, secondary, secondary_link,
tertiary, tertiary_link,
residential, unclassified, living_street
```

Excluded: `cycleway`, `footway`, `path`, `pedestrian`, `service`, `track`.

### 3.3 Road Class Hierarchy

Used for bin classification and peer-group `similar_best` matching:

| Bin Class | OSM `highway` Values |
|-----------|----------------------|
| `highway` | motorway, motorway_link, trunk, trunk_link |
| `arterial` | primary, primary_link, secondary, secondary_link |
| `collector` | tertiary, tertiary_link |
| `local` | residential, unclassified, living_street |

For intersections, the highest-priority road class among ways within 50 m of the node determines the bin class.

### 3.4 Speed Defaults by Class

Where OSM `maxspeed` is absent, the following defaults are applied:

| Road Class | Default Speed |
|------------|--------------|
| highway | 65 mph |
| arterial | 45 mph |
| collector | 35 mph |
| local | 25 mph |

### 3.5 Tile Deduplication

OSM data is fetched at z12 tile granularity via the Overpass API. Ways straddling tile boundaries appear in multiple tiles. Deduplication keeps the version with the most vertices (longest cached geometry) and discards shorter fragments. This ensures segment length calculations are accurate and prevents double-counting a single way.

---

## 4. Spatial Matching Algorithm

### 4.1 Coordinate Projection

All distance calculations are performed in a local equidistant projection centered on the county centroid, derived from the bounding box of all cached crash records. This converts geographic coordinates (degrees) to planar metres, giving accurate Euclidean distances without the distortion of a global projection.

```python
# Pseudocode
lon_0, lat_0 = county_centroid()
crs = CRS.from_proj4(f"+proj=aeqd +lat_0={lat_0} +lon_0={lon_0} +units=m")
```

### 4.2 Two-Phase Matching

Both phases run independently using a Shapely `STRtree` spatial index for performance.

**Phase 1 — Intersection match (radius = 50 m)**

For each crash, the STRtree returns all intersection nodes within 50 m. The crash is assigned to the single nearest node (minimum Euclidean distance). If no node exists within 50 m, the crash is unmatched for intersections.

**Phase 2 — Segment match (radius = 30 m)**

For each crash, the STRtree returns all way geometries within 30 m. The crash is assigned to the single nearest way (minimum perpendicular distance from crash point to the polyline). If no way exists within 30 m, the crash is unmatched for segments.

### 4.3 Intentional Double-Counting

Both phases are independent. A crash within 50 m of a signalized node and within 30 m of the approaching segment is assigned to **both** facilities and contributes to both EPDO scores.

This is consistent with HSM network screening practice, which attributes "intersection-related" crashes (those occurring within the intersection influence area) to the intersection *and* allows the approach segment to be scored separately. The ITE defines the intersection influence area as extending up to approximately 76 m (250 ft) from the center; our 50 m radius is a conservative subset of this zone.

The practical consequence is that removing a hazardous facility from the worst list requires addressing both the intersection and its approaching segments — which is exactly the engineering intent.

### 4.4 Leg Count for Intersection Bins

For each intersection node, ways with any endpoint within 30 m are counted as legs. The result determines the leg-bin dimension:

| Leg Count | Bin Label |
|-----------|-----------|
| ≤ 3 | `T-int` |
| 4 | `4-leg` |
| ≥ 5 | `multi` |

### 4.5 Radii Rationale

- **50 m (intersection):** CHP CCRS GPS accuracy is typically 10–30 m; the 50 m radius provides a margin while staying well inside the ~76 m ITE influence-area definition. Wider radii (e.g., 100 m) risk capturing mid-block crashes as intersection crashes.
- **30 m (segment):** Accounts for GPS uncertainty and road width (typical urban arterial cross-section ≈ 15–20 m half-width) without over-attributing crashes on adjacent parallel roads.

---

## 5. Grade Separation: Current Gap and Consequences

### 5.1 The Problem

The current Overpass query does **not** fetch `bridge`, `tunnel`, or `layer` tags. The facility registry and spatial join have no awareness of vertical separation between roads.

**Current Overpass query (simplified):**
```
node["highway"~"traffic_signals|stop|give_way"](bbox);
way["highway"~"motorway|trunk|primary|..."](bbox);
```

`bridge=yes`, `tunnel=yes`, and `layer` are not included in the output fields.

### 5.2 OSM Grade Separation Tags

Per OSM wiki:

| Tag | Meaning |
|-----|---------|
| `bridge=yes` | Way crosses at higher elevation on a structure |
| `tunnel=yes` | Way passes through underground or covered passage |
| `layer=N` | Vertical ordering integer (default 0); bridge → +1, tunnel → −1 |

Ways that cross in 2D **without connecting** have no shared node. Ways that connect **at grade** share a node. The presence or absence of a shared node is the key structural difference — but our current pipeline has no logic to test this.

### 5.3 Consequences in Practice

**Scenario:** Interstate 5 crosses a local street via an overpass. OSM stores this as two non-connecting ways whose geometries overlap in 2D. A crash on the I-5 on-ramp is within 30 m of both the freeway way and the local street way below. The spatial join assigns the crash to both segments.

**More serious scenario:** Some OSM mappers add an intersection node at the 2D crossing point of a bridge and the road below, even though no physical intersection exists. If such a node exists and is tagged `highway=stop`, the rankings engine will treat it as a real controlled intersection, accumulate crash points from both the overpass and underpass approaches, and potentially rank it among the most dangerous intersections in the county.

This is a known limitation of all 2D crash-location systems. FHWA's "Using GIS for Crash Location and Analysis at State DOTs" (*FHWA-HRT-10-043*, 2010) lists grade-separation handling as one of the primary challenges in automated spatial crash referencing.

### 5.4 Proposed Fix (Future Work)

1. Add `bridge`, `tunnel`, `layer` to the Overpass query output fields
2. During facility registry construction, exclude ways tagged `bridge=yes` or `tunnel=yes` from **leg counting** at any intersection node (a bridge over a road is not a turning movement)
3. During spatial matching, apply a heuristic suppression: if all ways connecting to a node are tagged as bridges or tunnels, the node does not represent an at-grade conflict point and should be excluded from the intersection registry
4. Expose a new bin dimension `grade=at-grade|grade-separated` for segment classification

---

## 6. EPDO Scoring

### 6.1 Formula

```
EPDO_score = (w_F × N_fatal)
           + (w_SI × N_severe_injury)
           + (w_OI × N_other_injury)
           + (w_PDO × N_pdo)

Default weights (FHWA Rural/Local Roads Toolkit, normalized PDO = 1.0):
  w_F   = 9.5    (fatal — KABCO K)
  w_SI  = 3.5    (incapacitating injury — KABCO A)
  w_OI  = 1.0    (non-incapacitating / possible injury — KABCO B/C combined)
  w_PDO = 1.0    (property damage only — KABCO O)

Configurable via --weights fatal,injury,pdo (3 args, A and B/C equal)
                       or --weights fatal,severe,other,pdo (4 args, full control)
```

All crashes matched to the facility over the 5-year window are included.

### 6.2 Severity Mapping (CHP CCRS → KABCO)

| CHP CCRS `collision_severity` | KABCO | GIS-Track Category |
|-------------------------------|-------|-------------------|
| Fatal | K | `fatal` |
| Severe Injury | A | `severe_injury` |
| Other Injury | B / C | `other_injury` |
| Property Damage Only | O | `pdo` |

### 6.3 Academic Grounding

EPDO (Equivalent Property Damage Only) scoring was formalized in FHWA's HSIP guidance and is codified in the *Highway Safety Manual* (AASHTO, 2010, 1st ed.), Chapter 4 — "Network Screening." The method converts mixed-severity crash records into a single comparable score by weighting each crash by the relative societal cost of its severity.

The default GIS-Track weights come from the **FHWA "Improving Safety on Rural Local and Tribal Roads" Safety Toolkit** (FHWA-SA-14-073), Step 2 — Network Screening, which provides KABCO-based EPDO factors normalized to PDO = 1.0:

| KABCO | Severity | FHWA Toolkit Factor | GIS-Track Default |
|-------|----------|---------------------|-------------------|
| K | Fatal | 9.5 | 9.5 |
| A | Incapacitating injury | 3.5 | 3.5 |
| B | Non-incapacitating visible injury | 1.5 | — |
| C | Possible / complaint-of-pain injury | 1.0 | — |
| B/C | (CHP CCRS "Other Injury" — B+C undifferentiated) | — | 1.0 * |
| O | Property damage only | 1.0 | 1.0 |

*CHP CCRS does not distinguish KABCO B from C within its "Other Injury" category. GIS-Track uses 1.0 (the C lower bound) as a conservative choice. Agencies expecting primarily B-level injuries may raise this to 1.5 via `--weights 9.5,3.5,1.5,1.0`.*

These toolkit factors derive from NHTSA's societal crash cost estimates normalized relative to PDO = 1.0. They differ from the absolute 2020 FHWA crash costs (Fatal ≈ $11.7 M, PDO ≈ $12,800) but provide a stable, widely-used standard for network screening comparisons across agencies.

The weights are configurable via `--weights F,I,P` (3 args, A = B/C) or `--weights F,A,BC,P` (4 args, full KABCO control) to allow agencies to substitute locally calibrated cost ratios.

### 6.4 Attribute Distributions Stored per Facility

Beyond the EPDO score, each facility record stores the following distributions for crash pattern analysis:

- Lighting conditions
- Primary Collision Factor (PCF)
- Weather
- Day of week
- Collision type (angle, rear-end, sideswipe, head-on, pedestrian, etc.)
- Road condition
- Motor vehicle involved with (pedestrian, bicycle, fixed object, etc.)
- Hour of day (24-bin, "00"–"23")
- Flags: pedestrian involved, cyclist involved, DUI involved

These distributions power the crash dashboard in the Analysis Mode UI.

---

## 7. Peer-Group (Bin) Classification

### 7.1 Why Peer Groups

Raw crash frequency is not a valid comparison across facility types. A 6-lane signalized arterial intersection processes tens of thousands of vehicles per day and will accumulate more crashes than a residential stop sign regardless of its geometric design quality. Ranking them on a single list conflates volume with hazard.

The *Highway Safety Manual* (AASHTO, 2010) defines separate Safety Performance Functions (SPFs) for each facility type precisely because crash propensity varies by orders of magnitude across types. NCHRP Report 17-50 ("Implementation of the Highway Safety Manual") recommends agencies stratify network screening by facility type before any ranking is applied. GIS-Track implements this principle by grouping facilities into bins based on control type, road class, speed range, and geometry, then ranking only within each bin.

### 7.2 Bin Key Structure

**Intersection:**
```
int | <control> | <road_class> | <speed_bin> | <leg_bin>

Example: int|signal|arterial|41-55mph|4-leg
```

**Segment:**
```
seg | <road_class> | <speed_bin> | <lane_bin>

Example: seg|arterial|26-40mph|3-4
```

### 7.3 Dimension Boundaries

**Control type (intersections):**

| Value | Meaning |
|-------|---------|
| `signal` | `highway=traffic_signals` |
| `stop` | `highway=stop` |
| `yield` | `highway=give_way` |
| `uncontrolled` | no control tag (not currently in registry) |

**Speed bin:**

| Bin Label | Range |
|-----------|-------|
| `<=25mph` | 0–25 mph |
| `26-40mph` | 26–40 mph |
| `41-55mph` | 41–55 mph |
| `>55mph` | > 55 mph |

**Leg bin (intersections):**

| Bin Label | Leg Count |
|-----------|-----------|
| `T-int` | ≤ 3 |
| `4-leg` | 4 |
| `multi` | ≥ 5 |

**Lane bin (segments):**

| Bin Label | Lane Count |
|-----------|-----------|
| `1-2` | 1–2 lanes |
| `3-4` | 3–4 lanes |
| `5+` | 5 or more |

### 7.4 Bin Suppression

Bins with fewer than **20 facilities** are suppressed (`insufficient_data = True`) and do not appear in the UI. Below this threshold, rank ordering is unstable — adding or removing one or two facilities can entirely reorder the list. Some practitioners use ≥ 30 as the threshold; 20 is a conservative minimum that allows rural county bins to appear.

Facilities with fewer than **2 matched crashes** are excluded from the worst-10 list. Single-crash matches are more likely to reflect GPS snapping artifacts or one-time events than systematic hazards.

---

## 8. Current Metric: Limitations

The current ranking metric is an **absolute EPDO frequency score** — a severity-weighted crash count over 5 years, ranked within peer groups. It answers: *"Which facilities in this peer group have accumulated the most weighted crash harm?"*

### 8.1 Exposure Bias

The most significant limitation. A signalized arterial intersection processing 60,000 vehicles/day will almost always outscore a structurally identical intersection on a 5,000 vehicles/day collector, even if the latter has a demonstrably worse crash rate per entering vehicle. The current metric conflates high volume with high risk.

### 8.2 Regression to the Mean (RTM)

Sites selected for high EPDO in one 5-year period statistically tend to show lower scores in the next period, even without any treatment — this is purely a statistical artifact (regression to the mean), not an improvement. The HSM's Empirical Bayes (EB) method was specifically developed to correct for this (*HSM*, Chapter 4, Section 4.2). Without EB correction, agencies that treat top-ranked sites and re-evaluate in 3 years may overestimate the effectiveness of their interventions.

### 8.3 CCRS B/C Injury Indistinction

CHP CCRS does not distinguish KABCO B (non-incapacitating visible injury) from KABCO C (possible injury) within its "Other Injury" severity code. Both map to `other_injury` and receive weight 1.0. The FHWA toolkit assigns B = 1.5 and C = 1.0 separately. Since California cannot supply this distinction from police reports alone, the 1.0 conservative floor is used. Agencies with supplemental trauma registry data could split the CCRS "Other Injury" pool and apply the toolkit's differentiated weights.

### 8.4 No Temporal Trend Detection

A facility that was improved from 15 crashes/year to 2 crashes/year after signal installation still carries a high 5-year EPDO from its pre-treatment history. The metric does not distinguish improving from worsening trends.

### 8.5 No Crash Type Specificity

EPDO treats all crash types (angle, rear-end, sideswipe, run-off-road, pedestrian) identically. Different crash types correspond to different countermeasures; a site dominated by angle crashes warrants signal timing review, while a rear-end cluster suggests spacing or sight distance issues.

---

## 9. Recommended Improvements

### Tier 1 — Achievable with Current Data

**1. Exposure-normalized crash rate (requires AADT)**

For segments:
```
Crash Rate = (EPDO_score / (AADT × length_km × years × 365)) × 1,000,000
             [units: EPDO-equivalent crashes per million vehicle-kilometres]
```

For intersections:
```
Crash Rate = (EPDO_score / (entering_vehicles_per_day × years × 365)) × 1,000,000
             [units: EPDO-equivalent crashes per million entering vehicles]
```

Caltrans PeMS provides AADT for most California state highways. Local road counts require municipal data or traffic model estimates.

**2. Differentiate KABCO B from C when source data permits**

Current CCRS "Other Injury" conflates B and C. If a supplemental injury severity source (e.g., trauma registry) becomes available, split `other_injury` into `injury_b` (weight 1.5) and `injury_c` (weight 1.0) per the FHWA toolkit's full 5-level table.

**3. Grade separation filtering**

Add `bridge`, `tunnel`, `layer` to the Overpass query. Suppress bridge/tunnel ways from leg counting at intersection nodes. Exclude nodes where all connecting ways are grade-separated.

### Tier 2 — Requires Safety Performance Function Calibration

**4. Empirical Bayes PSI (Potential for Safety Improvement)**

The HSM's preferred network screening method (*HSM*, Chapter 4, Method 4):

```
EB_estimated = w × SPF_predicted + (1 − w) × observed
PSI = EB_estimated − SPF_predicted

where:
  w = 1 / (1 + k × SPF_predicted)    (overdispersion weight)
  k = SPF calibration parameter
  SPF_predicted = f(AADT, facility_type, geometry)
```

PSI directly measures how much worse a facility is than statistically expected for its type and volume. This is superior to raw EPDO because it (a) corrects for RTM, (b) removes the volume bias, and (c) produces a statistically defensible ranking.

Implementing this requires calibrated SPFs for California conditions. FHWA's HSIS (Highway Safety Information System) and the Caltrans statewide crash database are sufficient inputs for SPF regression.

**5. Crash type specificity**

Add a crash type dimension to the bin key (e.g., `angle`, `rear-end`, `run-off-road`, `pedestrian`) or as a secondary filter. This enables countermeasure-specific screening: agencies looking to reduce pedestrian fatalities query only pedestrian-involved EPDO; agencies targeting intersection angle crashes query by type.

### Tier 3 — Research / Exploratory

**6. MAIS-weighted severity**

The Maximum Abbreviated Injury Scale (MAIS) is a medical outcome measure more precise than KABCO. Cross-referencing crash records with trauma registry data (where available at the county level) produces MAIS-based unit crash costs closer to actual economic harm.

**7. Temporal trend analysis**

Fit a Poisson or negative binomial GLM to the annual crash time series per facility (5 data points). Rank by the positive trend coefficient — this surfaces facilities that are getting *worse*, regardless of their current absolute EPDO rank. This is particularly valuable for proactive intervention before a facility reaches the top of the EPDO list.

---

## 10. Verification

### Reproducing Rankings Locally

```bash
cd D:/SafetyGIS
python scripts/build_safety_rankings.py --counties sacramento
# Primary output: data/rankings/statewide.json
# Summary:        data/rankings/summary.txt
```

### Expected Bin Key Examples

| Bin Key | Interpretation |
|---------|----------------|
| `int\|signal\|arterial\|41-55mph\|4-leg` | 4-leg signalized arterial, 41–55 mph |
| `int\|stop\|local\|<=25mph\|T-int` | T-intersection, all-way stop, local road |
| `seg\|arterial\|26-40mph\|3-4` | Arterial segment, 26–40 mph, 3–4 lanes |
| `seg\|highway\|>55mph\|1-2` | Freeway/trunk, > 55 mph, 1–2 lanes |

### Checking for Grade-Separation Artifacts

In the rankings output, identify any intersection node ranked in the worst-5 whose geographic location corresponds to a known highway overpass (e.g., US-50 over Folsom Blvd, Sacramento; SR-99 over surface streets). If found, verify in OSM whether the node represents an at-grade crossing or a mapping error. Grade-separation artifacts will typically have:
- Multiple legs from different road classes (a local road and a freeway)
- Zero stop-line geometry visible in satellite imagery
- A `layer` tag mismatch on the connecting ways

---

## References

- AASHTO. (2010). *Highway Safety Manual*, 1st Edition. Washington, DC: American Association of State Highway and Transportation Officials.
- FHWA. (2018). *Highway Safety Improvement Program (HSIP) Manual*. FHWA-SA-18-020. Federal Highway Administration.
- FHWA. (2010). *Using GIS for Crash Location and Analysis at State DOTs*. FHWA-HRT-10-043. Federal Highway Administration.
- FHWA. (2020). *Crash Costs for Highway Safety Analysis*. FHWA-SA-20-021. Federal Highway Administration.
- NCHRP. (2013). *NCHRP Report 17-50: Implementation of the Highway Safety Manual*. Transportation Research Board.
- FHWA. (2014). *Improving Safety on Rural Local and Tribal Roads — Safety Toolkit*. FHWA-SA-14-073. Federal Highway Administration. Step 2: Conduct Network Screening. https://highways.dot.gov/safety/local-rural/improving-safety-rural-local-and-tribal-roads-safety-toolkit/step-2-conduct
- OSM Wiki. (2024). *Key:bridge*. OpenStreetMap Foundation. https://wiki.openstreetmap.org/wiki/Key:bridge
- OSM Wiki. (2024). *Key:layer*. OpenStreetMap Foundation. https://wiki.openstreetmap.org/wiki/Key:layer
