from routers.auth import router as auth_router
from routers.admin import router as admin_router
from routers.candidates import router as candidates_router
from routers.institutions import router as institutions_router
from routers.config import router as config_router

__all__ = [
    "auth_router",
    "admin_router",
    "candidates_router",
    "institutions_router",
    "config_router",
]
