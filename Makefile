.PHONY: dev restart fetch-osm fetch-crashes fetch-all

PORT ?= 8000

# Kill any process on PORT, then start uvicorn with --reload
dev restart:
	@-lsof -ti:$(PORT) | xargs kill -9 2>/dev/null; true
	@sleep 0.3
	.venv/bin/uvicorn main:app --port $(PORT) --reload

# Data fetch shortcuts
fetch-osm:
	.venv/bin/python scripts/fetch_osm.py

fetch-crashes:
	.venv/bin/python scripts/fetch_crash_data.py

fetch-all: fetch-osm fetch-crashes
