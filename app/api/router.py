from fastapi import APIRouter

from app.api.adventures import router as adventures_router
from app.api.places import router as places_router

api_router = APIRouter()
api_router.include_router(places_router, prefix="/places", tags=["places"])
api_router.include_router(adventures_router, prefix="/adventures", tags=["adventures"])
