# ================================================================
# payment_webhook/parsers/instapay.py — محلل رسائل إنستاباي والتحويلات اللحظية
# ================================================================

import re
from typing import Optional
from payment_webhook.parsers.base import BaseSMSParser, ParsedPayment

class InstaPayParser(BaseSMSParser):
    """
    محلل رسائل شبكة المدفوعات اللحظية (InstaPay / IPN) والتحويلات البنكية الفورية
    يدعم استخراج: المبلغ، اسم أو هاتف المرسل، والرقم المرجعي للبنك المركزي
    """

    KEYWORDS = ["instapay", "إنستاباي", "انستاباي", "تحويل لحظي", "تحويل فوري", "ipn", "instant transfer"]

    def can_parse(self, message: str) -> bool:
        msg_lower = message.lower()
        if any(kw in msg_lower for kw in self.KEYWORDS):
            return True
        if "إلى حسابكم" in message or "to your account" in msg_lower:
            return True
        return False

    def parse(self, message: str) -> Optional[ParsedPayment]:
        text = message.strip()
        amount: Optional[float] = None
        sender: Optional[str] = None
        ref_num: Optional[str] = None
        pattern_name: Optional[str] = None

        # -------------------------------------------------------------
        # 1. إنستاباي — عربي نمط 1 (الرسمي المعتاد)
        # مثال: "تم استلام مبلغ 250.00 ج.م من أحمد محمد إلى حسابكم"
        # -------------------------------------------------------------
        m1 = re.search(
            r"تم استلام مبلغ ([0-9]+(?:\.[0-9]+)?)\s*(?:ج\.م|جنيه|جم).*?من (.+?)(?:\s+إلى حسابكم|\s+عبر|\s+رقم مرجعي|$)",
            text
        )
        if m1:
            amount = float(m1.group(1))
            sender = m1.group(2).strip()
            pattern_name = "ar_instapay_standard"

        # -------------------------------------------------------------
        # 2. تحويل لحظي بنكي (CIB / الأهلي / بنك مصر / QNB)
        # مثال: "تم إضافة تحويل لحظي الي بطاقة رقم 507803******7783 بمبلغ 80 من شريف فرج رقم مرجعي 357018762882"
        # -------------------------------------------------------------
        if not pattern_name:
            m2 = re.search(
                r"تم إضافة تحويل (?:لحظي|فوري).*?بمبلغ ([0-9]+(?:\.[0-9]+)?).*?من (.+?)(?:\s+على|\s+رقم مرجعي|\s+بتاريخ|$)",
                text
            )
            if m2:
                amount = float(m2.group(1))
                sender = m2.group(2).strip()
                pattern_name = "ar_bank_instant"

        # -------------------------------------------------------------
        # 3. إنستاباي — إنجليزي نمط 1
        # مثال: "You have received EGP 200 from John to your account"
        # -------------------------------------------------------------
        if not pattern_name:
            m3 = re.search(
                r"You (?:have )?received (?:EGP|LE)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:EGP|LE)?\s*from (.+?)(?:\s+to your account|\s+via|\s+Ref|$)",
                text,
                re.IGNORECASE
            )
            if m3:
                amount = float(m3.group(1))
                sender = m3.group(2).strip()
                pattern_name = "en_instapay_standard"

        # -------------------------------------------------------------
        # 4. تحويل فوري إنجليزي للبنك
        # مثال: "Instant transfer received for EGP 80 from Sherif Farag. Ref: 357018762882"
        # -------------------------------------------------------------
        if not pattern_name:
            m4 = re.search(
                r"Instant transfer (?:received )?(?:for )?(?:EGP|LE)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:EGP|LE)?\s*from (.+?)(?:\.|\s+Ref|\s+Reference|$)",
                text,
                re.IGNORECASE
            )
            if m4:
                amount = float(m4.group(1))
                sender = m4.group(2).strip()
                pattern_name = "en_bank_instant"

        if not pattern_name:
            return None

        # -------------------------------------------------------------
        # تنظيف المرسل إذا احتوى على رقم هاتف 11 رقم
        # -------------------------------------------------------------
        if sender:
            phone_match = re.search(r"\b(01[0-9]{9})\b", sender)
            if phone_match:
                sender = phone_match.group(1)

        # -------------------------------------------------------------
        # استخراج الرقم المرجعي
        # -------------------------------------------------------------
        ref_patterns = [
            r"(?:رقم مرجعي|مرجع|الرقم المرجعي)[\s:]*([0-9A-Za-z]+)",
            r"(?:Reference number|Ref number|Ref no|Ref)[\s:#]*([0-9A-Za-z]+)",
        ]
        for rp in ref_patterns:
            m_ref = re.search(rp, text, re.IGNORECASE)
            if m_ref:
                ref_num = m_ref.group(1).strip()
                break

        return ParsedPayment(
            raw_message=text,
            source_provider="InstaPay",
            amount=amount,
            sender=sender,
            reference_number=ref_num,
            matched_pattern=pattern_name,
            confidence=0.97 if ref_num else 0.88
        )

