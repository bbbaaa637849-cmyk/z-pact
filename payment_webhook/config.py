# ================================================================
# payment_webhook/config.py — إعدادات محرك الـ SMS Webhook
# ================================================================

import os
from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(BASE_DIR, ".env"))

class WebhookSettings:
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    WEBHOOK_HOST: str = os.getenv("PAYMENT_WEBHOOK_HOST", "0.0.0.0")
    WEBHOOK_PORT: int = int(os.getenv("PAYMENT_WEBHOOK_PORT", "8001"))
    
    # الرمز السري لمصادقة تطبيق تحويل الرسائل من هاتف الأندرويد
    WEBHOOK_SECRET_TOKEN: str = os.getenv("PAYMENT_WEBHOOK_SECRET", "zpact_sms_webhook_secret_2026")
    
    # رابط ومنفذ المنصة الرئيسية (Core Platform API)
    CORE_API_URL: str = os.getenv("CORE_API_URL") or f"http://127.0.0.1:{os.getenv('PORT', '8000')}/api"
    
    # المفتاح الداخلي لمخاطبة الـ Internal Bridge
    INTERNAL_SERVICE_KEY: str = os.getenv("INTERNAL_SERVICE_KEY", "zpact_internal_bridge_secret_key_2026")
    
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

settings = WebhookSettings()

