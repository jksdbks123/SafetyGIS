from fastapi import APIRouter

from app.debug.sacramento import router as sacramento_router
from app.debug.sandbox import router as sandbox_router
from app.debug.facility import router as facility_router

router = APIRouter()
router.include_router(sacramento_router)
router.include_router(sandbox_router)
router.include_router(facility_router)
