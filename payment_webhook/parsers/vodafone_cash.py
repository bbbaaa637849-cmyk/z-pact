# ================================================================
# payment_webhook/parsers/vodafone_cash.py — محلل رسائل فودافون كاش
# ================================================================

import re
from typing import Optional
from payment_webhook.parsers.base import BaseSMSParser, ParsedPayment

class VodafoneCashParser(BaseSMSParser):
    """
    محلل رسائل خدمة فودافون كاش (Vodafone Cash) بالعربية والإنجليزية
    يدعم استخراج: المبلغ، رقم هاتف المحول، ورقم العملية المرجعي
    """
    
    # الكلمات المفتاحية الدالة على فودافون كاش
    KEYWORDS = ["فودافون كاش", "vodafone cash", "vf-cash", "vfcash", "فودافون"]

    def can_parse(self, message: str) -> bool:
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in self.KEYWORDS):
            return True
        # إذا كانت الرسالة بالنمط الشهير: تم استلام ... من رقم فودافون 010...
        if re.search(r"(?:تم استلام|استلمت|received).*?010[0-9]{8}", message, re.IGNORECASE):
            return True
        return False

    def parse(self, message: str) -> Optional[ParsedPayment]:
        text = message.strip()
        amount: Optional[float] = None
        sender: Optional[str] = None
        ref_num: Optional[str] = None
        pattern_name: Optional[str] = None

        # -------------------------------------------------------------
        # 1. فودافون كاش — عربي (النمط القياسي الرسمي)
        # مثال: "تم استلام مبلغ 200 جنيه من رقم 01027585126 ورقم العملية 58921478"
        # -------------------------------------------------------------
        m1 = re.search(
            r"تم استلام (?:مبلغ )?([0-9]+(?:\.[0-9]+)?)\s*(?:جنيه|ج\.م|جنية).*?(?:من (?:رقم )?|من )([0-9]{11})",
            text
        )
        if m1 and (m1.group(2).startswith("010") or any(kw in text.lower() for kw in self.KEYWORDS)):
            amount = float(m1.group(1))
            sender = m1.group(2)
            pattern_name = "ar_vf_standard"

        # -------------------------------------------------------------
        # 2. فودافون كاش — إنجليزي (النمط القياسي الرسمي)
        # مثال: "You have received 200 EGP from 01027585126. Transaction ID: 58921478"
        # -------------------------------------------------------------
        if not pattern_name:
            m2 = re.search(
                r"You (?:have )?received (?:EGP )?([0-9]+(?:\.[0-9]+)?)\s*(?:EGP|LE)?.*?from ([0-9]{11})",
                text,
                re.IGNORECASE
            )
            if m2 and (m2.group(2).startswith("010") or any(kw in text.lower() for kw in self.KEYWORDS)):
                amount = float(m2.group(1))
                sender = m2.group(2)
                pattern_name = "en_vf_standard"

        # -------------------------------------------------------------
        # 3. نمط مختصر / تحويلات محفظة فودافون
        # مثال: "استلمت 150 ج.م من 01011122233"
        # -------------------------------------------------------------
        if not pattern_name:
            m3 = re.search(
                r"(?:استلمت|وصلك|تحويل بمبلغ)\s*([0-9]+(?:\.[0-9]+)?)\s*(?:جنيه|ج\.م|LE|EGP).*?(010[0-9]{8})",
                text,
                re.IGNORECASE
            )
            if m3:
                amount = float(m3.group(1))
                sender = m3.group(2)
                pattern_name = "ar_vf_short"

        if not pattern_name:
            return None

        # -------------------------------------------------------------
        # استخراج رقم العملية (Reference / Transaction ID)
        # -------------------------------------------------------------
        ref_patterns = [
            r"(?:رقم العملية|العملية رقم|كود العملية|مرجع)[\s:]*([0-9A-Za-z_-]+)",
            r"(?:Transaction ID|Txn ID|Trans ID|Ref|Reference)[\s:#]*([0-9A-Za-z_-]+)",
        ]
        for rp in ref_patterns:
            m_ref = re.search(rp, text, re.IGNORECASE)
            if m_ref:
                ref_num = m_ref.group(1).strip()
                break

        return ParsedPayment(
            raw_message=text,
            source_provider="VodafoneCash",
            amount=amount,
            sender=sender,
            reference_number=ref_num,
            matched_pattern=pattern_name,
            confidence=0.98 if ref_num else 0.90
        )
