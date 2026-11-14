# ================================================================
# payment_webhook/parsers/base.py — الواجهة الأساسية ونموذج البيانات
# ================================================================

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

class ParsedPayment(BaseModel):
    """
    النموذج الموحد لبيانات الدفع المستخرجة من رسائل الـ SMS
    """
    raw_message: str
    source_provider: str # VodafoneCash, InstaPay, OrangeCash, EtisalatCash, WePay, Bank
    amount: Optional[float] = None
    sender: Optional[str] = None # رقم الهاتف أو اسم المحول
    reference_number: Optional[str] = None # رقم العملية أو كود المرجع
    matched_pattern: Optional[str] = None
    confidence: float = 0.0 # نسبة الثقة في التحليل (0.0 إلى 1.0)
    parsed_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_valid(self) -> bool:
        """التحقق من أن المعاملة مكتملة الأركان (يوجد مبلغ مالي صحيح)"""
        return self.amount is not None and self.amount > 0


class BaseSMSParser(ABC):
    """
    الواجهة القياسية لمحللات الرسائل القصيرة
    """
    @abstractmethod
    def can_parse(self, message: str) -> bool:
        """فحص أولي هل الرسالة تابعة لهذه الخدمة"""
        pass

    @abstractmethod
    def parse(self, message: str) -> Optional[ParsedPayment]:
        """استخراج بيانات التحويل وإرجاع كائن ParsedPayment"""
        pass

