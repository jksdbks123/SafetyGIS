FROM python:3.12-slim

WORKDIR /app

# Install dependencies first — separate layer so Docker cache skips pip on code-only changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .
COPY static/ ./static/
COPY scripts/ ./scripts/

# Pre-create all cache directories so volume mounts work on first run
RUN mkdir -p data/crash_cache data/osm_cache data/mapillary_cache data/rankings data/enrichment

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/mapillary/token')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
