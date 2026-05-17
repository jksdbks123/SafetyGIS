"""Download California OSM PBF from Geofabrik and generate tile caches.

Usage:
    python scripts/fetch_osm_bulk.py --counties sacramento
    python scripts/fetch_osm_bulk.py --counties sacramento,humboldt
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.osm.pbf import process_pbf_to_tiles


def main():
    parser = argparse.ArgumentParser(description="Fetch OSM data from Geofabrik PBF")
    parser.add_argument(
        "--counties", default="sacramento",
        help="Comma-separated county names (default: sacramento)",
    )
    args = parser.parse_args()
    county_names = [n.strip().lower() for n in args.counties.split(",") if n.strip()]

    last_phase = [""]

    def progress_cb(phase: str, done: int, total: int) -> None:
        if phase != last_phase[0]:
            print(f"\n[{phase}]", end=" ", flush=True)
            last_phase[0] = phase
        if total > 0:
            pct = done / total * 100
            print(f"\r[{phase}] {done}/{total} ({pct:.1f}%)", end="", flush=True)
        else:
            print(f"\r[{phase}] processing…", end="", flush=True)

    result = process_pbf_to_tiles(county_names, progress_cb)
    print(f"\nDone: {result['tiles_cached']}/{result['tiles_total']} tiles cached.")


if __name__ == "__main__":
    main()
