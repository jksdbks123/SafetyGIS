# Runtime Data

This directory is for local runtime caches and large reference datasets.

The repository should not commit generated cache files from:

- `crash_cache/`
- `osm_cache/`
- `osm_relation_cache/`
- `osm_consolidated/`
- `party_cache/`
- `mapillary_cache/`
- `rankings/`
- `debug/`
- `enrichment/`
- local `.osm.pbf` downloads
- `CaltransAADT/`

The app and scripts recreate the cache directories as needed.
