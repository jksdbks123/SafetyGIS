import unittest

from app.facility_model import model


def way(osm_id, coords, highway="residential", **tags):
    props = {"id": osm_id, "type": highway, "highway": highway, **tags}
    return {
        "osm_id": str(osm_id),
        "coords": coords,
        "props": props,
        "highway": highway,
        "length_m": model._line_length_m(coords),
    }


class FacilityModelTests(unittest.TestCase):
    def test_deterministic_ids(self):
        first = model._hash_id("j", ["a", "b", 3])
        second = model._hash_id("j", ["a", "b", 3])
        other = model._hash_id("j", ["a", "b", 4])
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_grade_separation_is_diagnostic_not_candidate(self):
        ways = [
            way(1, [[-121.0, 38.0], [-121.001, 38.0]], layer="0"),
            way(2, [[-121.0, 38.0], [-121.0, 38.001]], layer="1", bridge="yes"),
        ]
        graph = model.build_graph_from_ways(ways)
        candidates, diagnostics = model._candidate_rows(graph, set())
        self.assertEqual(candidates, [])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["kind"], "excluded_grade_separation")

    def test_roundabout_becomes_compound_junction(self):
        ways = [
            way(
                10,
                [[-121.0, 38.0], [-121.0002, 38.0], [-121.0002, 38.0002], [-121.0, 38.0]],
                highway="roundabout",
                junction="roundabout",
            ),
            way(11, [[-121.0002, 38.0], [-121.001, 38.0]], highway="residential"),
        ]
        graph = model.build_graph_from_ways(ways)
        features, consumed = model._roundabout_junctions(ways, graph)
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["subtype"], "roundabout")
        self.assertEqual(features[0]["properties"]["geometric_configuration"], "roundabout")
        self.assertEqual(features[0]["properties"]["traffic_control_type"], "roundabout_control")
        self.assertEqual(features[0]["properties"]["structural_grade"], "at_grade")
        self.assertIn("compound_roundabout", features[0]["properties"]["diagnostic_flags"])
        self.assertTrue(consumed)

    def test_link_way_becomes_ramp_segment(self):
        ways = [
            way(20, [[-121.0, 38.0], [-121.001, 38.0]], highway="motorway_link"),
        ]
        roads, ramps, diagnostics = model._segment_features(ways, [])
        self.assertEqual(roads, [])
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(ramps), 1)
        self.assertEqual(ramps[0]["properties"]["facility_type"], "ramp")
        self.assertEqual(ramps[0]["properties"]["subtype"], "ramp_or_slip_lane")

    def test_road_segment_splits_at_junction_member_node(self):
        coords = [[-121.0, 38.0], [-121.001, 38.0], [-121.002, 38.0]]
        middle_id = model._node_id_for_key(model._coord_key(coords[1]))
        junction = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords[1]},
            "properties": {
                "facility_id": "j_test",
                "member_node_ids": middle_id,
            },
        }
        roads, ramps, diagnostics = model._segment_features([way(30, coords)], [junction])
        self.assertEqual(ramps, [])
        self.assertEqual(diagnostics, [])
        self.assertEqual(len(roads), 2)
        self.assertTrue(all(r["properties"]["facility_type"] == "road_segment" for r in roads))

    def test_control_points_classify_signalized_junction(self):
        junction = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-121.0, 38.0]},
            "properties": {
                "facility_id": "j_signal",
                "facility_type": "junction",
                "subtype": "at_grade_intersection",
                "traffic_control_type": "unknown_control",
                "leg_count": 4,
                "diagnostic_flags": "",
            },
        }
        controls = [{"osm_id": "100", "kind": "traffic_signals", "lon": -121.0, "lat": 38.0}]
        classified = model._classify_junctions([junction], controls, [-121.01, 37.99, -120.99, 38.01])
        self.assertEqual(classified[0]["properties"]["traffic_control_type"], "signalized")
        self.assertEqual(classified[0]["properties"]["traffic_control_subtype"], "signal")

    def test_stop_count_classifies_all_way_stop(self):
        control = [
            {"osm_id": str(i), "kind": "stop", "lon": -121.0 + i * 0.00001, "lat": 38.0}
            for i in range(4)
        ]
        traffic_type, subtype, flags = model._traffic_control_from_nearby("at_grade_intersection", control, 4)
        self.assertEqual(traffic_type, "stop_controlled")
        self.assertEqual(subtype, "all_way_stop")
        self.assertEqual(flags, [])


if __name__ == "__main__":
    unittest.main()
