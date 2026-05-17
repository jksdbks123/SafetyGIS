import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR, GOOGLE_MAPS_KEY, MAPILLARY_TOKEN
from app.osm.router      import router as osm_router
from app.crash.router    import router as crash_router
from app.rankings.router import router as rankings_router
from app.mapillary.router import router as mapillary_router
from app.debug.router    import router as debug_router

app = FastAPI(title="GIS-Track")

app.include_router(osm_router)
app.include_router(crash_router)
app.include_router(rankings_router)
app.include_router(mapillary_router)
app.include_router(debug_router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/config")
def get_config():
    return {"has_mapillary": bool(MAPILLARY_TOKEN)}


@app.get("/api/googlemaps/config")
def googlemaps_config():
    return {"has_google_maps": bool(GOOGLE_MAPS_KEY), "key": GOOGLE_MAPS_KEY}
