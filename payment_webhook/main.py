# ================================================================
# payment_webhook/main.py — سيرفر الـ Webhook المستقل لاستقبال رسائل الدفع
# ================================================================

import logging
import sys
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# إضافة المسار الحالي للمكتبات
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from payment_webhook.config import settings
from payment_webhook.router import router as webhook_router

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [SMS-Webhook] %(message)s"
)
logger = logging.getLogger("payment_webhook")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("==============================================================")
    logger.info(">> Real-Time SMS Payment Webhook Engine Started")
    logger.info(f">> Listening Port: {settings.WEBHOOK_PORT}")
    logger.info(f">> Core Bridge API: {settings.CORE_API_URL}")
    logger.info("==============================================================")
    yield
    logger.info(">> SMS Webhook Engine Stopped")

app = FastAPI(
    title="Z-PACT SMS Payment Webhook Engine",
    description="محرك استقبال ومطابقة رسائل SMS للتحويلات البنكية ومحافظ الهاتف في مصر",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# تضمين موجه الويب هوك
app.include_router(webhook_router)

if __name__ == "__main__":
    uvicorn.run(
        "payment_webhook.main:app",
        host=settings.WEBHOOK_HOST,
        port=settings.WEBHOOK_PORT,
        reload=False
    )
