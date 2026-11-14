# ================================================================
# bot_service/services/__init__.py
# ================================================================

from bot_service.services.bridge_client import bridge_client, BridgeClient
from bot_service.services.notifier import RealTimeNotifier

__all__ = ["bridge_client", "BridgeClient", "RealTimeNotifier"]

