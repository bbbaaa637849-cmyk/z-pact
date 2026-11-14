# ================================================================
# payment_webhook/router.py — موجه الويب هوك المستقل والمشترك
# ================================================================

import logging
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException, status
from fastapi.responses import JSONResponse

from payment_webhook.config import settings
from payment_webhook.matcher import payment_matcher
from payment_webhook.parsers.base import ParsedPayment

logger = logging.getLogger("payment_webhook.router")
router = APIRouter(tags=["SMS Payment Webhook"])


def verify_webhook_token(request: Request, body_data: Optional[Dict[str, Any]] = None):
    """
    التحقق من توكن الأمان القادم من تطبيق الهاتف (SMS Forwarder / MacroDroid)
    """
    token = request.headers.get("X-Webhook-Secret") or request.headers.get("Authorization")
    if not token and body_data:
        token = body_data.get("token") or body_data.get("secret")
    if not token:
        token = request.query_params.get("token") or request.query_params.get("secret")

    if token and token.startswith("Bearer "):
        token = token[7:]

    expected = settings.WEBHOOK_SECRET_TOKEN
    if expected and token != expected:
        client_host = request.client.host if request.client else 'unknown'
        logger.warning(f"⛔ رفض طلب ويبهوك لعدم تطابق التوكن من: {client_host}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid webhook secret token"
        )


@router.get("/health", summary="فحص صحة خدمة الـ SMS Webhook")
def health_check():
    return {
        "status": "healthy",
        "service": "payment_webhook",
        "port": settings.WEBHOOK_PORT,
        "environment": settings.ENVIRONMENT
    }


@router.post("/webhook/sms", summary="استقبال رسائل SMS في الوقت الحقيقي")
@router.post("/api/webhook/sms", summary="استقبال رسائل SMS عبر مسار الـ API")
async def receive_sms_webhook(request: Request):
    """
    استقبال وتحليل رسائل SMS للتحويلات المالية من الهاتف مباشرة وتوجيهها للمطابقة اللحظية
    """
    raw_text = ""
    json_data = None

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            json_data = await request.json()
            if isinstance(json_data, dict):
                raw_text = (
                    json_data.get("message")
                    or json_data.get("text")
                    or json_data.get("body")
                    or json_data.get("msg")
                    or json_data.get("content")
                    or ""
                )
        except Exception as e:
            logger.error(f"⚠️ خطأ أثناء قراءة JSON: {e}")

    if not raw_text:
        body_bytes = await request.body()
        raw_text = body_bytes.decode("utf-8", errors="replace").strip()

    # التحقق من رمز الأمان
    verify_webhook_token(request, json_data)

    if not raw_text or not raw_text.strip():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"status": "ignored", "reason": "empty_message"}
        )

    logger.info(f"📥 [SMS Raw] رسالة واردة ({len(raw_text)} حرف): {raw_text[:120]}...")

    # تحليل الرسالة واستخراج البيانات المالية
    parsed_payment: Optional[ParsedPayment] = payment_matcher.parse_message(raw_text)

    if not parsed_payment or not parsed_payment.is_valid:
        logger.warning(f"⚠️ لم يتم التعرف على أي نمط مالي صحيح في الرسالة: {raw_text[:80]}")
        return {
            "status": "ignored",
            "reason": "no_payment_pattern_matched",
            "preview": raw_text[:80]
        }

    logger.info(f"✅ تم التعرف على التحويل: {parsed_payment.amount} ج.م من {parsed_payment.sender} عبر {parsed_payment.source_provider} (المرجع: {parsed_payment.reference_number})")

    # توجيه التحويل فوراً إلى الجسر الداخلي للمنصة لمطابقة الطلبات والختم
    reconcile_result = await payment_matcher.forward_to_core(parsed_payment)

    return {
        "status": "processed",
        "parsed": parsed_payment.model_dump(),
        "core_reconcile": reconcile_result
    }


@router.post("/webhook/test", summary="اختبار فوري لأي نص رسالة لمعاينة نتيجة التحليل")
def test_sms_parser(request: Request, body: Dict[str, str]):
    message = body.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="حقل 'message' مطلوب للاختبار")

    parsed = payment_matcher.parse_message(message)
    if not parsed:
        return {"matched": False, "reason": "No parser matched this text"}

    return {
        "matched": True,
        "details": parsed.model_dump()
    }

