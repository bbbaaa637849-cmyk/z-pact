# ================================================================
# payment_webhook/matcher.py — محرك المطابقة والتنسيق والتوجيه
# ================================================================

import logging
import re
from typing import Optional, Dict, Any, List
import httpx

from payment_webhook.config import settings
from payment_webhook.parsers.base import BaseSMSParser, ParsedPayment
from payment_webhook.parsers.vodafone_cash import VodafoneCashParser
from payment_webhook.parsers.instapay import InstaPayParser
from payment_webhook.parsers.orange_etisalat import OrangeCashParser, EtisalatCashParser

logger = logging.getLogger("payment_webhook")


_ARABIC_DIGITS_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789.")


def normalize_sms_digits(text: str) -> str:
    """تحويل الأرقام العربية الشرقية إلى أرقام قياسية لضمان عدم ضياع أي رسالة تحويل"""
    return (text or "").translate(_ARABIC_DIGITS_MAP)


SPAM_AND_PROMO_KEYWORDS = [
    "كسبت", "مبروك", "اربح", "ربحت", "سحب على", "فرصة لربح", "مليونير",
    "هدية", "هديتك", "هدايا", "عجلة الحظ", "صندوق الحظ", "عرض خاص", "عروض",
    "خصم", "خصومات", "كاش باك", "cashback", "قسيمة", "كوبون", "تسوق",
    "اشحن", "عند الشحن", "شحن رصيد", "تم شحن", "كارت شحن", "كروت شحن", "رصيد شحن",
    "باقة", "باقات", "تجديد باقة", "تم تجديد", "فليكس", "فليكسات", "فلكسات",
    "ميجا", "جيجا", "دقائق", "وحدات", "إنترنت", "انترنت", "سوشيال",
    "سلفني", "شكراً", "خدمة سلفني", "استهلاكك", "استهلكت",
    "فاتورة", "فاتورتك", "سداد فاتورة", "دفع فاتورة",
    "تم تحويل رصيد", "تحويل رصيد", "تم خصم", "تم سحب مبلغ", "سحب نقدي من",
    "مصاريف إدارية", "كود التحقق", "رمز التحقق", "الرقم السري المتغير", "otp"
]


def is_spam_or_promo_sms(text: str) -> bool:
    """التحقق الصارم مما إذا كانت الرسالة إعلانية أو ترويجية أو شحن رصيد أو مسابقات"""
    t_lower = (text or "").lower()
    return any(kw in t_lower for kw in SPAM_AND_PROMO_KEYWORDS)


class GenericFallbackParser(BaseSMSParser):
    """
    محلل احتياطي شامل لأي رسالة تحويل مالي أو محفظة إلكترونية أو إيداع بنكي/إنستاباي
    """
    TRANSFER_HINTS = [
        "تم استلام", "استلام مبلغ", "استلمت", "وصلك مبلغ", "تم إضافة مبلغ", "تم اضافه مبلغ",
        "تم إيداع", "تم ايداع", "تحويل لحظي بمبلغ", "تحويل فوري بمبلغ",
        "received", "credited to", "transferred to your"
    ]

    def can_parse(self, message: str) -> bool:
        if is_spam_or_promo_sms(message):
            return False
        return True

    def parse(self, message: str) -> Optional[ParsedPayment]:
        if is_spam_or_promo_sms(message):
            return None
        text = normalize_sms_digits(message).strip()
        msg_lower = text.lower()

        m_amount = re.search(
            r"(?:بمبلغ|مبلغ|بقيمة|استلام|إضافة|اضافة|إيداع|EGP|LE)[\s:]*([0-9]+(?:\.[0-9]+)?)"
            r"|([0-9]+(?:\.[0-9]+)?)\s*(?:جنيه|جنية|ج\.م|EGP|LE)",
            text,
            re.IGNORECASE,
        )
        m_phone = re.search(r"\b(01[0125][0-9]{8})\b", text)
        has_hint = any(h in msg_lower for h in self.TRANSFER_HINTS)

        # يجب توفر صيغة تحويل صريحة + رقم هاتف أو كود عملية مرجعي حقيقي
        if m_amount and has_hint and (m_phone or "من " in text or "from " in msg_lower):
            raw_amt = m_amount.group(1) or m_amount.group(2)
            amount = float(raw_amt)
            if amount <= 0:
                return None

            sender = m_phone.group(1) if m_phone else None
            if not sender:
                m_sender_name = re.search(r"(?:from|من)\s+([A-Za-z\u0600-\u06FF0-9_@.\s]{3,30})", text, re.IGNORECASE)
                if m_sender_name:
                    sender = m_sender_name.group(1).strip()[:30]

            m_ref = re.search(
                r"(?:رقم العملية|كود العملية|رقم المعاملة|مرجع|Ref|Reference|Txn|ID)[\s:#]*([0-9A-Za-z_-]{3,})",
                text,
                re.IGNORECASE,
            )
            ref_num = m_ref.group(1).strip() if m_ref else None

            return ParsedPayment(
                raw_message=text,
                source_provider="InstaPay" if ("insta" in msg_lower or "انستا" in msg_lower or not m_phone) else "VodafoneCash",
                amount=amount,
                sender=sender or "BankTransfer",
                reference_number=ref_num,
                matched_pattern="generic_fallback",
                confidence=0.80 if m_phone else 0.70,
            )
        return None


class PaymentEngineMatcher:
    """
    المنسق العام: يمرر الرسالة على جميع المحللات ويرسل النتيجة للجسر الداخلي
    """
    def __init__(self):
        self.parsers: List[BaseSMSParser] = [
            OrangeCashParser(),
            EtisalatCashParser(),
            VodafoneCashParser(),
            InstaPayParser(),
            GenericFallbackParser()
        ]

    def parse_message(self, message: str) -> Optional[ParsedPayment]:
        """تحليل الرسالة وتحديد المحفظة والمبلغ والراسل"""
        if not message or not message.strip():
            return None

        normalized = normalize_sms_digits(message)

        # تجاهل نصوص الاختبار التلقائي أو النصوص الفارغة
        if normalized.strip().lower() in ["[clipboard_text]", "clipboard_text", "test"]:
            return None

        # تجاهل الرسائل الترويجية والإعلانية والشحن والمسابقات فوراً
        if is_spam_or_promo_sms(normalized):
            logger.info(f"🚫 [SMS Filter] تم تجاهل رسالة إعلانية / شحن / مسابقات: {normalized[:60]}...")
            return None

        for parser in self.parsers:
            if parser.can_parse(normalized):
                result = parser.parse(normalized)
                if result and result.is_valid:
                    return result
        return None

    async def forward_to_core(self, payment: ParsedPayment) -> Dict[str, Any]:
        """
        إرسال البيانات المستخرجة إلى المنصة الأساسية (Internal Bridge Reconcile API)
        لمطابقتها وتفعيل الختم والتسليم فوراً
        """
        url = f"{settings.CORE_API_URL.rstrip('/')}/internal/webhook/reconcile"
        headers = {
            "X-Internal-Service-Key": settings.INTERNAL_SERVICE_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "raw_message": payment.raw_message,
            "source_provider": payment.source_provider,
            "extracted_amount": payment.amount,
            "extracted_sender": payment.sender,
            "reference_number": payment.reference_number
        }

        logger.info(f"📤 [Payment Webhook] توجيه التحويل إلى المنصة الأساسية ({url}): {payment.amount} ج.م من {payment.sender}")

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"✅ [Payment Webhook] نتيجة المطابقة من المنصة الأساسية: {data}")
                    return data
                else:
                    logger.error(f"❌ [Payment Webhook] خطأ استجابة من المنصة الأساسية ({response.status_code}): {response.text}")
                    return {"status": "error", "code": response.status_code, "detail": response.text}
            except Exception as e:
                logger.error(f"❌ [Payment Webhook] فشل الاتصال بالمنصة الأساسية: {e}")
                return {"status": "connection_failed", "error": str(e)}

payment_matcher = PaymentEngineMatcher()
