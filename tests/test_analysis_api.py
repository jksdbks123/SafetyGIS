import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app


class AnalysisFacilityRankingApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_facility_model_compute_requires_sacramento_osm_cache(self):
        with patch(
            "app.rankings.router.require_sacramento_osm_cache",
            side_effect=HTTPException(400, "No Sacramento OSM tiles are cached. Download or build OSM data first."),
        ):
            resp = self.client.post(
                "/api/rankings/facility_model/compute",
                json={"county": "sacramento", "force_refresh": True},
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("No Sacramento OSM tiles", resp.json()["detail"])

    def test_facility_model_compute_returns_metadata_when_inputs_exist(self):
        fake_result = {
            "metadata": {
                "model": "SafetyGIS Facility Ranking Demo",
                "county": "sacramento",
                "facility_count": 3,
            }
        }
        with patch("app.rankings.router.require_sacramento_osm_cache", return_value=1), patch(
            "app.facility_model.ranking.compute_facility_ranking",
            return_value=fake_result,
        ):
            resp = self.client.post(
                "/api/rankings/facility_model/compute",
                json={"county": "sacramento", "force_refresh": True},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["metadata"], fake_result["metadata"])

    def test_facility_model_result_returns_cached_ranking(self):
        fake_result = {
            "metadata": {"model": "SafetyGIS Facility Ranking Demo"},
            "top_ranked": {"type": "FeatureCollection", "features": []},
            "group_rankings": {},
        }
        with patch("app.facility_model.ranking.load_cached_ranking", return_value=fake_result):
            resp = self.client.get("/api/rankings/facility_model/result")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), fake_result)


if __name__ == "__main__":
    unittest.main()
