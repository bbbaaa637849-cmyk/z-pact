# ================================================================
# bot_service/handlers/__init__.py
# ================================================================

from bot_service.handlers.start import router as start_router
from bot_service.handlers.catalog import router as catalog_router
from bot_service.handlers.order import router as order_router
from bot_service.handlers.payment import router as payment_router
from bot_service.handlers.admin import router as admin_router
from bot_service.handlers.admin_admins import router as admin_admins_router
from bot_service.handlers.admin_studio import router as admin_studio_router
from bot_service.handlers.support import router as support_router

__all__ = [
    "start_router",
    "catalog_router",
    "order_router",
    "payment_router",
    "admin_router",
    "admin_admins_router",
    "admin_studio_router",
    "support_router"
]

