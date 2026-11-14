# ================================================================
# bot_service/keyboards/__init__.py
# ================================================================

from bot_service.keyboards.reply import get_main_keyboard, get_cancel_keyboard, get_skip_keyboard
from bot_service.keyboards.inline import (
    get_materials_keyboard,
    get_material_actions_keyboard,
    get_profile_decision_keyboard,
    get_order_confirmation_keyboard,
    get_payment_methods_keyboard,
    get_admin_order_actions_keyboard,
    get_admin_main_keyboard,
    get_support_keyboard,
    get_support_cancel_keyboard,
    get_admin_user_reply_keyboard
)

__all__ = [
    "get_main_keyboard",
    "get_cancel_keyboard",
    "get_skip_keyboard",
    "get_materials_keyboard",
    "get_material_actions_keyboard",
    "get_profile_decision_keyboard",
    "get_order_confirmation_keyboard",
    "get_payment_methods_keyboard",
    "get_admin_order_actions_keyboard",
    "get_admin_main_keyboard",
    "get_support_keyboard",
    "get_support_cancel_keyboard",
    "get_admin_user_reply_keyboard"
]

