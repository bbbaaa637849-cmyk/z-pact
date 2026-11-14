# ================================================================
# payment_webhook/parsers/orange_etisalat.py — محلل رسائل أورنج واتصالات ووي كاش
# ================================================================

import re
from typing import Optional
from payment_webhook.parsers.base import BaseSMSParser, ParsedPayment

class OrangeCashParser(BaseSMSParser):
    """
    محلل رسائل محفظة أورنج كاش (Orange Cash) بالعربية والإنجليزية
    """
    KEYWORDS = ["أورنچ كاش", "اورنج كاش", "orange cash", "أورنچ", "اورنج", "orange"]

    SPAM_TERMS = ["كسبت", "مبروك", "اشحن", "شحن", "باقة", "تجديد", "كاش باك", "cashback", "عرض", "عروض", "اربح", "هدية", "فاتورة", "خصم"]

    def can_parse(self, message: str) -> bool:
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in self.SPAM_TERMS):
            return False
        has_kw = any(kw in msg_lower for kw in self.KEYWORDS)
        has_incoming = any(h in message for h in ["استلام", "استلمت", "إضافة", "اضافة", "إيداع", "ايداع", "received"])
        if has_kw and has_incoming:
            return True
        if re.search(r"012[0-9]{8}", message) and has_incoming:
            return True
        return False

    def parse(self, message: str) -> Optional[ParsedPayment]:
        text = message.strip()
        amount: Optional[float] = None
        sender: Optional[str] = None
        ref_num: Optional[str] = None

        m_ar = re.search(
            r"(?:تم استلام|تم إضافة|تم ايداع|تم تحويل|استلمت)\s*(?:مبلغ )?([0-9]+(?:\.[0-9]+)?)\s*(?:جنيه|ج\.م).*?(?:من (?:رقم )?|من )([0-9]{11})",
            text
        )
        if m_ar:
            amount = float(m_ar.group(1))
            sender = m_ar.group(2)
        else:
            m_en = re.search(
                r"You (?:have )?received ([0-9]+(?:\.[0-9]+)?)\s*(?:EGP|LE).*?from ([0-9]{11})",
                text,
                re.IGNORECASE
            )
            if m_en:
                amount = float(m_en.group(1))
                sender = m_en.group(2)

        if not amount or not sender:
            return None

        # رقم العملية
        m_ref = re.search(r"(?:رقم العملية|Txn ID|Transaction ID|Ref)[\s:#]*([0-9A-Za-z]+)", text, re.IGNORECASE)
        if m_ref:
            ref_num = m_ref.group(1).strip()

        return ParsedPayment(
            raw_message=text,
            source_provider="OrangeCash",
            amount=amount,
            sender=sender,
            reference_number=ref_num,
            matched_pattern="orange_standard",
            confidence=0.95 if ref_num else 0.88
        )


class EtisalatCashParser(BaseSMSParser):
    """
    محلل رسائل محفظة اتصالات كاش (Etisalat Cash / e&) بالعربية والإنجليزية
    """
    KEYWORDS = ["اتصالات كاش", "etisalat cash", "e&", "etisalat"]

    def can_parse(self, message: str) -> bool:
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in OrangeCashParser.SPAM_TERMS):
            return False
        has_kw = any(kw in msg_lower for kw in self.KEYWORDS)
        has_incoming = any(h in message for h in ["استلام", "استلمت", "إضافة", "اضافة", "إيداع", "ايداع", "received"])
        if has_kw and has_incoming:
            return True
        if re.search(r"011[0-9]{8}", message) and has_incoming:
            return True
        return False

    def parse(self, message: str) -> Optional[ParsedPayment]:
        text = message.strip()
        amount: Optional[float] = None
        sender: Optional[str] = None
        ref_num: Optional[str] = None

        m_ar = re.search(
            r"(?:تم استلام|تم إضافة|تم ايداع|تم تحويل|استلمت)\s*(?:مبلغ )?([0-9]+(?:\.[0-9]+)?)\s*(?:جنيه|ج\.م).*?(?:من (?:رقم )?|من )([0-9]{11})",
            text
        )
        if m_ar:
            amount = float(m_ar.group(1))
            sender = m_ar.group(2)
        else:
            m_en = re.search(
                r"You (?:have )?received ([0-9]+(?:\.[0-9]+)?)\s*(?:EGP|LE).*?from ([0-9]{11})",
                text,
                re.IGNORECASE
            )
            if m_en:
                amount = float(m_en.group(1))
                sender = m_en.group(2)

        if not amount or not sender:
            return None

        m_ref = re.search(r"(?:كود المعاملة|رقم العملية|Transaction ID|Ref)[\s:#]*([0-9A-Za-z]+)", text, re.IGNORECASE)
        if m_ref:
            ref_num = m_ref.group(1).strip()

        return ParsedPayment(
            raw_message=text,
            source_provider="EtisalatCash",
            amount=amount,
            sender=sender,
            reference_number=ref_num,
            matched_pattern="etisalat_standard",
            confidence=0.95 if ref_num else 0.88
        )
