import unittest

from app.facility_model import ranking


def junction(fid, lon, lat, control="signalized", highways="primary,residential", subtype="at_grade_intersection"):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "facility_id": fid,
            "facility_type": "junction",
            "subtype": subtype,
            "geometric_configuration": "four_leg",
            "traffic_control_type": control,
            "structural_grade": "at_grade",
            "approach_highways": highways,
            "approach_max_speed_mph": 45,
            "approach_max_lanes": 2,
        },
    }


def segment(fid, coords, highway="primary", facility_type="road_segment", speed_mph=45, lanes=2):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "facility_id": fid,
            "facility_type": facility_type,
            "subtype": "ramp_or_slip_lane" if facility_type == "ramp" else "roadway",
            "highway": highway,
            "length_m": 150.0,
            "speed_mph": speed_mph,
            "lanes": lanes,
        },
    }


def facility_model(features):
    junctions = [f for f in features if f["properties"]["facility_type"] == "junction"]
    roads = [f for f in features if f["properties"]["facility_type"] == "road_segment"]
    ramps = [f for f in features if f["properties"]["facility_type"] == "ramp"]
    return {
        "metadata": {"version": 99, "bbox": [-121.01, 37.99, -120.99, 38.01]},
        "junction_candidates": {"type": "FeatureCollection", "features": junctions},
        "road_segments": {"type": "FeatureCollection", "features": roads},
        "ramp_segments": {"type": "FeatureCollection", "features": ramps},
    }


class FacilityRankingTests(unittest.TestCase):
    def test_crash_matches_nearest_junction_and_line(self):
        fm = facility_model([
            junction("j1", -121.0, 38.0),
            segment("s1", [[-121.001, 38.0], [-120.999, 38.0]]),
        ])
        crashes = [{"lon": -121.0, "lat": 38.0, "severity": "fatal"}]
        registry, stats = ranking.match_crashes_to_facilities(fm, crashes)
        self.assertEqual(stats["matched_junction_assignments"], 1)
        self.assertEqual(stats["matched_line_assignments"], 1)
        self.assertEqual(len(registry["j1"]["crashes"]), 1)
        self.assertEqual(len(registry["s1"]["crashes"]), 1)

    def test_peer_percentile_includes_zero_crash_facilities(self):
        fm = facility_model([
            junction("j1", -121.0, 38.0),
            junction("j2", -121.0005, 38.0),
            junction("j3", -121.0010, 38.0),
        ])
        crashes = [{"lon": -121.0, "lat": 38.0, "severity": "fatal"}]
        registry, _stats = ranking.match_crashes_to_facilities(fm, crashes)
        ranked, groups, grouped = ranking.rank_facilities(registry)
        self.assertEqual(len(ranked), 1)
        props = ranked[0]["properties"]
        self.assertEqual(props["facility_id"], "j1")
        self.assertEqual(props["peer_size"], 3)
        self.assertEqual(props["epdo_percentile"], 83.33)
        self.assertEqual(props["epdo_band"], "elevated")
        self.assertEqual(groups[props["peer_group"]]["facility_count"], 3)
        self.assertEqual(grouped[props["peer_group"]][0]["properties"]["facility_id"], "j1")

    def test_freeway_crash_prefers_ramp_or_highway_facility(self):
        fm = facility_model([
            junction("j_local", -121.0, 38.0, highways="residential"),
            junction("j_ramp", -121.0002, 38.0, highways="motorway,motorway_link", subtype="ramp_terminal"),
            segment("s_local", [[-121.001, 38.0], [-120.999, 38.0]], highway="residential"),
            segment("r1", [[-121.001, 38.0002], [-120.999, 38.0002]], highway="motorway_link", facility_type="ramp"),
        ])
        crashes = [{"lon": -121.0001, "lat": 38.0001, "severity": "pdo", "isfreeway": True}]
        registry, stats = ranking.match_crashes_to_facilities(fm, crashes)
        self.assertEqual(stats["matched_junction_assignments"], 1)
        self.assertEqual(stats["matched_line_assignments"], 1)
        self.assertEqual(len(registry["j_local"]["crashes"]), 0)
        self.assertEqual(len(registry["j_ramp"]["crashes"]), 1)
        self.assertEqual(len(registry["s_local"]["crashes"]), 0)
        self.assertEqual(len(registry["r1"]["crashes"]), 1)

    def test_peer_group_preserves_classification_tree_dimensions(self):
        fm = facility_model([
            junction("j1", -121.0, 38.0),
            segment("s1", [[-121.001, 38.0], [-120.999, 38.0]], speed_mph=35, lanes=4),
        ])
        registry, _stats = ranking.match_crashes_to_facilities(fm, [])
        ranked, groups, _grouped = ranking.rank_facilities(registry)
        self.assertEqual(ranked, [])
        group_tree = ranking._build_group_tree(groups)
        self.assertEqual(group_tree["facility_count"], 2)
        group_labels = [info["label"] for info in groups.values()]
        self.assertTrue(any("41-55mph" in label and "Four Leg" in label and "Signalized" in label for label in group_labels))
        self.assertTrue(any("26-40mph" in label and "3-4 lanes" in label and "Roadway" in label for label in group_labels))


if __name__ == "__main__":
    unittest.main()
