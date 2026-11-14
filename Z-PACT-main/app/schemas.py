# ================================================================
# app/schemas.py — Pydantic Schemas (التحقق من البيانات)
#
# كل Schema بيحدد:
#   - إيه البيانات اللي بتيجي من الـ Client (Request)
#   - إيه البيانات اللي بترجع للـ Client (Response)
#
# القاعدة:
#   XxxCreate  → بيانات الإنشاء (من الـ Client)
#   XxxUpdate  → بيانات التعديل (من الـ Client، كلها Optional)
#   XxxOut     → بيانات الاستجابة (للـ Client، بدون بيانات حساسة)
# ================================================================

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models import (
    LayoutType, MarkType, PurchaseStatus, UserRole, PaymentMethod, TransactionType,
    OrderFulfillmentStatus
)
from app.utils import sanitize_string


# ================================================================
# BASE CONFIG — إعداد مشترك لكل الـ Schemas
# ================================================================

class AppBaseModel(BaseModel):
    """Base مشترك — بيفعّل قراءة البيانات من ORM objects مباشرة"""
    model_config = {"from_attributes": True}

    @field_validator("*", mode="before")
    @classmethod
    def sanitize_all_strings(cls, v: Any) -> Any:
        """تطهير تلقائي لأي حقل نصي قبل معالجته"""
        if isinstance(v, str):
            return sanitize_string(v)
        return v


# ================================================================
# USER SCHEMAS
# ================================================================

class UserCreate(AppBaseModel):
    """البيانات المطلوبة لإنشاء مستخدم جديد"""
    full_name: str = Field(..., min_length=2, max_length=100)
    email:     EmailStr
    password:  str = Field(..., min_length=8, description="8 أحرف على الأقل")
    referral_code: Optional[str] = None # كود الشخص اللي دعاه
    # role مش بيتبعت من الـ Client — بيتحدد من الـ Backend لأمان أكتر


class UserLogin(AppBaseModel):
    """بيانات تسجيل الدخول"""
    email:     EmailStr
    password:  str
    device_id: str = Field(..., description="معرف الجهاز الفريد (Fingerprint)")


class UserOut(AppBaseModel):
    """بيانات المستخدم اللي بترجع — بدون password_hash أبداً"""
    id:         int
    full_name:  str
    email:      str
    phone:      Optional[str] = None
    profile_picture_path: Optional[str] = None
    watermark_logo_path: Optional[str] = None
    role:       UserRole
    is_active:  Optional[bool] = True
    coins:      Optional[Decimal] = Decimal("0.0")
    total_spent: Optional[Decimal] = Decimal("0.0")
    preferences: Optional[dict] = None
    
    referral_code:   Optional[str] = None
    referred_by_id:  Optional[int] = None
    serial_code:     Optional[str] = None
    approval_status: Optional[str] = "approved"
    phone_verified:  Optional[bool] = False
    is_founding_admin: Optional[bool] = False
    admin_label:     Optional[str] = None
    
    created_at: datetime

class UserUpdate(AppBaseModel):
    full_name: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r"^01[0-2,5]{1}[0-9]{8}$")
    password: Optional[str] = Field(None, min_length=8)
    preferences: Optional[dict] = None
    watermark_logo_path: Optional[str] = None

class UserPreferencesUpdate(AppBaseModel):
    preferences: dict


class TokenOut(AppBaseModel):
    """الـ JWT Token اللي بيرجع بعد تسجيل الدخول"""
    access_token: str
    token_type:   str = "bearer"
    user:         UserOut


# ================================================================
# MATERIAL SCHEMAS
# ================================================================

class MaterialCreate(AppBaseModel):
    """بيانات رفع ملف جديد — الـ PDF بيتبعت كـ multipart/form-data منفصل"""
    title:       str = Field(..., min_length=3, max_length=200)
    tag:         Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    price:       Decimal = Field(Decimal("0.0"), ge=0, description="السعر النقدي (مهمل)")
    price_coins: Decimal = Field(0.0, ge=0, description="السعر بالكوينز")
    compression_enabled: bool = True
    cover_image_path: Optional[str] = None
    page_frame_path: Optional[str] = None


class MaterialUpdate(AppBaseModel):
    """تعديل بيانات ملف — كل الحقول Optional"""
    title:        Optional[str]     = Field(None, min_length=3, max_length=200)
    tag:          Optional[str]     = Field(None, max_length=100)
    description:  Optional[str]     = None
    price:        Optional[Decimal] = Field(None, ge=0)
    price_coins:  Optional[Decimal] = Field(None, ge=0)
    is_published: Optional[bool]    = None
    compression_enabled: Optional[bool] = None
    cover_image_path: Optional[str] = None
    page_frame_path: Optional[str] = None


class MaterialOut(AppBaseModel):
    """بيانات الملف في الاستجابة"""
    id:           int
    title:        str
    tag:          Optional[str]
    description:  Optional[str]
    price:        Decimal
    price_coins:  Decimal
    is_published: bool
    compression_enabled: bool
    total_pages:  Optional[int]
    uploaded_by:  int
    cover_image_path: Optional[str] = None
    page_frame_path: Optional[str] = None
    sender_commission: Optional[float] = None
    created_at:   datetime

    # بيانات الرافع (nested)
    uploader: Optional[UserOut] = None


# ================================================================
# STAMP LAYOUT SCHEMAS
# ================================================================

class StampElement(AppBaseModel):
    """
    عنصر ختم واحد داخل التنسيق.
    كل الإحداثيات بنسب مئوية (0-100) لضمان دقة 100% على أي PDF.
    """
    type:           str       = Field(..., pattern="^(name|phone|watermark|doc_name|logo_watermark)$")
    page:           int       = Field(..., ge=0, description="0 = على كل الصفحات")
    x_pct:          float     = Field(..., ge=-1000, le=1000)
    y_pct:          float     = Field(..., ge=-1000, le=1000)
    font_size:      float     = Field(14.0, ge=0.1, le=200.0)
    font_family:    Optional[str] = "cairo"
    color:          str       = Field("#FF0000", pattern="^#[0-9A-Fa-f]{6}$")
    opacity:        float     = Field(0.4, ge=0.0, le=1.0)
    rotation:       float     = Field(0.0, ge=-3600, le=3600)
    custom_text:    Optional[str] = None
    excluded_pages: list[int] = Field(default_factory=list)
    pattern:        str       = "single" # single, diagonal, tiled
    z_index_mode:   Optional[str] = Field("background", pattern="^(background|foreground)$")


class StampLayoutCreate(AppBaseModel):
    """إنشاء تنسيق ختم جديد"""
    material_id:    int
    layout_type:    LayoutType = LayoutType.DEFAULT
    stamp_elements: list[StampElement] = Field(..., min_length=1)


class StampLayoutUpdate(AppBaseModel):
    """تعديل تنسيق — بنستبدل العناصر كلها"""
    stamp_elements: list[StampElement] = Field(..., min_length=1)


class StampLayoutOut(AppBaseModel):
    """بيانات التنسيق في الاستجابة"""
    id:             int
    material_id:    int
    created_by:     int
    layout_type:    LayoutType
    stamp_elements: list[dict[str, Any]]
    created_at:     datetime


# ================================================================
# LAYOUT PRESET SCHEMAS
# ================================================================

class LayoutPresetCreate(AppBaseModel):
    """حفظ قالب أختام جديد"""
    name:           str = Field(..., min_length=1, max_length=100)
    stamp_elements: list[StampElement] = Field(..., min_length=1)


class LayoutPresetOut(AppBaseModel):
    """بيانات القالب في الاستجابة"""
    id:             int
    name:           str
    created_by:     int
    stamp_elements: list[dict[str, Any]]
    created_at:     datetime


# ================================================================
# PURCHASE SCHEMAS
# ================================================================

class PurchaseCreate(AppBaseModel):
    """
    بدء عملية شراء جديدة.
    المشتري بيبعت بياناته الشخصية + التنسيق المختار.
    """
    material_id:      int
    stamp_layout_id:  Optional[int] = None  # None = يستخدم التنسيق الافتراضي

    # البيانات الشخصية للختم (يُكتفى بأي بيان متاح للمستخدم، والكل اختياري للأدمن طبقاً لـ stamp_settings.py)
    buyer_name_stamp:  Optional[str] = Field("", max_length=100)
    buyer_phone_stamp: Optional[str] = Field("", max_length=100, description="رقم الهاتف الأول")
    buyer_phone_stamp_2: Optional[str] = Field(None, max_length=100, description="رقم الهاتف الثاني")
    series_name:       Optional[str] = Field(None, max_length=150, description="اسم السلسلة")
    watermark_text:    Optional[str] = Field(None, max_length=200)
    doc_name_stamp:    Optional[str] = Field(None, max_length=200, description="اسم المذكرة المخصص")
    watermark_type:    Optional[str] = "none"
    watermark_logo_path: Optional[str] = None

    @field_validator("buyer_name_stamp", "buyer_phone_stamp", mode="after")
    @classmethod
    def _coerce_purchase_stamp_empty(cls, v: Any) -> str:
        return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())

    # بيانات الدفع
    transaction_id: Optional[str] = Field(None, description="رقم عملية الدفع من بوابة الدفع")
    payment_method: PaymentMethod = PaymentMethod.MONEY


class PurchaseOut(AppBaseModel):
    """بيانات عملية الشراء في الاستجابة"""
    id:                int
    buyer_id:          int
    material_id:       int
    buyer_name_stamp:  Optional[str] = ""
    buyer_phone_stamp: Optional[str] = ""  # بيظهر مشفر في Response الحقيقي
    buyer_phone_stamp_2: Optional[str] = None
    status:            PurchaseStatus
    amount_paid:       Decimal
    payment_method:    PaymentMethod
    purchased_at:      datetime
    completed_at:      Optional[datetime]
    watermark_type:    Optional[str] = "text"

    # بيانات الملف (nested) — بدون مسار الملف الأصلي لأمان أكتر
    material: Optional[MaterialOut] = None


class PurchaseDownloadOut(AppBaseModel):
    """بيانات تحميل الملف المختوم — بتُرجع بعد اكتمال الختم"""
    purchase_id:     int
    download_token:  str  # توكن مؤقت للتحميل (صالح 10 دقائق)
    expires_at:      datetime


# ================================================================
# HIDDEN MARK SCHEMAS
# ================================================================

class HiddenMarkOut(AppBaseModel):
    """بيانات العلامة المخفية — للـ Admin فقط عند التحقيق في تسريب"""
    id:          int
    purchase_id: int
    mark_code:   str
    page_number: int
    x_position:  float
    y_position:  float
    mark_type:   MarkType
    created_at:  datetime


# ================================================================
# PREVIEW TOKEN SCHEMAS
# ================================================================

class PreviewTokenCreate(AppBaseModel):
    """طلب توكن معاينة لملف معين"""
    material_id:   int


class PreviewTokenOut(AppBaseModel):
    """التوكن الراجع للـ Client — يُستخدم مع PDF.js"""
    token:         str
    material_id:   int
    allowed_pages: list[int]
    expires_at:    datetime


# ================================================================
# GENERIC RESPONSE SCHEMAS
# ================================================================

class MessageOut(AppBaseModel):
    """استجابة نصية بسيطة للعمليات اللي مش بترجع data"""
    message: str
    success: bool = True


class PaginatedOut(AppBaseModel):
    """استجابة مقسمة صفحات لقوائم البيانات"""
    items:       list[Any]
    total:       int
    page:        int
    per_page:    int
    total_pages: int


# ================================================================
# COIN TRANSACTION SCHEMAS
# ================================================================

class CoinTransactionOut(AppBaseModel):
    """بيانات حركة الكوينز في الاستجابة"""
    id:          int
    user_id:     int
    amount:      Decimal
    type:        TransactionType
    description: Optional[str]
    material_id: Optional[int]
    created_at:  datetime

    # بيانات المادة (لو موجودة)
    material: Optional[MaterialOut] = None


class CoinRecharge(AppBaseModel):
    """طلب شحن رصيد (للـ Admin)"""
    amount:      Decimal = Field(..., gt=0)
    description: Optional[str] = "شحن رصيد من قبل المشرف"

class WalletAutoPurchase(AppBaseModel):
    """البيانات المطلوبة لعملية الدفع التلقائي عبر المحفظة (فودافون كاش / إنستا باي)"""
    material_id:       int
    transfer_phone:    str = Field(..., description="رقم الهاتف أو عنوان إنستا باي الذي تم التحويل منه")
    buyer_name_stamp:  Optional[str] = Field("", max_length=100)
    buyer_phone_stamp: Optional[str] = Field("", max_length=100)
    buyer_phone_stamp_2: Optional[str] = Field(None, max_length=100)
    series_name:       Optional[str] = Field(None, max_length=150)
    watermark_text:    Optional[str] = Field(None, max_length=200)
    doc_name_stamp:    Optional[str] = Field(None, max_length=200)
    watermark_type:    Optional[str] = "text"

    @field_validator("buyer_name_stamp", "buyer_phone_stamp", mode="after")
    @classmethod
    def _coerce_wallet_stamp_empty(cls, v: Any) -> str:
        return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())

# ================================================================
# NEW MODELS: Chat, Notifications, Loyalty
# ================================================================

class UserAuditLogOut(AppBaseModel):
    id: int
    user_id: int
    action: str
    old_data: Optional[dict]
    new_data: Optional[dict]
    created_at: datetime

class UserDeviceOut(AppBaseModel):
    id: int
    device_id: str
    user_agent: Optional[str]
    last_ip: Optional[str]
    is_blocked: bool
    last_login: datetime
    created_at: datetime

class ChatMessageOut(AppBaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    avatar: Optional[str] = None
    message: str
    reply_to_id: Optional[int] = None
    reply_preview: Optional[str] = None
    reply_user_name: Optional[str] = None
    created_at: datetime
    user: Optional[UserOut] = None

class NotificationOut(AppBaseModel):
    id: int
    user_id: Optional[int]
    title: str
    message: str
    is_read: bool
    created_at: datetime

class SpendingLevelCreate(AppBaseModel):
    threshold: Decimal = Field(..., gt=0)
    reward_coins: Decimal = Field(..., gt=0)

class SpendingLevelOut(AppBaseModel):
    id: int
    threshold: Decimal
    reward_coins: Decimal
    created_at: datetime

class CouponOut(AppBaseModel):
    id: int
    code: str
    user_id: int
    reward_coins: Decimal
    is_used: bool
    created_at: datetime
    used_at: Optional[datetime]

class AdminBroadcast(AppBaseModel):
    user_id: Optional[int] = None # None = للجميع
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1)

# ================================================================
# B2B Review & Rating Schemas
# ================================================================
class ReviewCreate(AppBaseModel):
    reviewer_title: str = Field(..., min_length=1, max_length=200)
    content_quality_rating: int = Field(..., ge=1, le=5)
    print_formatting_rating: int = Field(..., ge=1, le=5)
    stamp_appearance_rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class ReviewOut(AppBaseModel):
    id: int
    material_id: int
    user_id: int
    reviewer_title: str
    content_quality_rating: int
    print_formatting_rating: int
    stamp_appearance_rating: int
    overall_rating: float
    comment: Optional[str] = None
    created_at: datetime
    reviewer_name: str

class MaterialReviewsOut(AppBaseModel):
    reviews: list[ReviewOut]
    avg_content_quality: float
    avg_print_formatting: float
    avg_stamp_appearance: float
    avg_overall: float
    total_count: int


# ================================================================
# TELEGRAM BOT & INTERNAL BRIDGE SCHEMAS
# ================================================================

class TeacherProfileCreate(AppBaseModel):
    telegram_user_id: str = Field(..., max_length=50)
    telegram_username: Optional[str] = None
    teacher_name: Optional[str] = Field("", max_length=150)
    full_name: Optional[str] = Field(None, max_length=150)
    subject_specialty: Optional[str] = Field(None, max_length=100)
    primary_phone: Optional[str] = Field("", max_length=30)
    secondary_phone: Optional[str] = Field(None, max_length=30)
    series_title: Optional[str] = Field(None, max_length=150)
    watermark_type: Optional[str] = "text"
    watermark_text: Optional[str] = None
    logo_image_path: Optional[str] = None
    is_admin: Optional[bool] = None

    @field_validator("teacher_name", "primary_phone", mode="after")
    @classmethod
    def _coerce_teacher_empty(cls, v: Any) -> str:
        return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


class TeacherProfileUpdate(AppBaseModel):
    teacher_name: Optional[str] = None
    subject_specialty: Optional[str] = None
    primary_phone: Optional[str] = None
    secondary_phone: Optional[str] = None
    series_title: Optional[str] = None
    watermark_type: Optional[str] = None
    watermark_text: Optional[str] = None
    logo_image_path: Optional[str] = None
    is_verified: Optional[bool] = None


class TeacherProfileOut(AppBaseModel):
    id: int
    user_id: Optional[int] = None
    telegram_user_id: Optional[str] = None
    telegram_username: Optional[str] = None
    teacher_name: Optional[str] = ""
    subject_specialty: Optional[str] = None
    primary_phone: Optional[str] = ""
    secondary_phone: Optional[str] = None
    series_title: Optional[str] = None
    watermark_type: Optional[str] = "text"
    watermark_text: Optional[str] = None
    logo_image_path: Optional[str] = None
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class BotSessionUpdate(AppBaseModel):
    telegram_chat_id: str
    telegram_user_id: str
    username: Optional[str] = None
    current_state: Optional[str] = None
    state_data: Optional[dict] = None


class BotSessionOut(AppBaseModel):
    telegram_chat_id: str
    telegram_user_id: str
    username: Optional[str] = None
    current_state: Optional[str] = None
    state_data: Optional[dict] = None
    last_interaction: datetime


class BotOrderCreate(AppBaseModel):
    material_id: int
    telegram_chat_id: str
    telegram_user_id: Optional[str] = None
    teacher_profile_id: Optional[int] = None
    buyer_name_stamp: Optional[str] = Field("", max_length=150)
    buyer_phone_stamp: Optional[str] = Field("", max_length=50)
    buyer_phone_stamp_2: Optional[str] = None
    doc_name_stamp: Optional[str] = None
    series_name: Optional[str] = None
    watermark_text: Optional[str] = None
    watermark_type: Optional[str] = "none"
    watermark_logo_path: Optional[str] = None
    total_amount: float = Field(..., ge=0)
    payment_method: Optional[str] = "vodafone_cash"
    claimed_sender_phone: Optional[str] = None
    receipt_image_path: Optional[str] = None
    receipt_file_id: Optional[str] = None
    payment_reference: Optional[str] = None
    is_admin: Optional[bool] = None

    @field_validator("buyer_name_stamp", "buyer_phone_stamp", mode="after")
    @classmethod
    def _coerce_bot_order_empty(cls, v: Any) -> str:
        return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


class BotOrderAdminAction(AppBaseModel):
    action: str = Field(..., description="'force_approve', 'reject', 'retrigger_stamp'")
    admin_notes: Optional[str] = None


class BotOrderOut(AppBaseModel):
    id: int
    order_code: str
    telegram_chat_id: str
    telegram_user_id: Optional[str] = None
    material_id: int
    buyer_name_stamp: Optional[str] = ""
    buyer_phone_stamp: Optional[str] = ""
    buyer_phone_stamp_2: Optional[str] = None
    doc_name_stamp: Optional[str] = None
    series_name: Optional[str] = None
    watermark_text: Optional[str] = None
    watermark_type: Optional[str] = "none"
    watermark_logo_path: Optional[str] = None
    total_amount: float
    payment_method: str
    claimed_sender_phone: Optional[str] = None
    receipt_image_path: Optional[str] = None
    receipt_file_id: Optional[str] = None
    payment_reference: Optional[str] = None
    status: OrderFulfillmentStatus
    stamped_pdf_path: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class PaymentSMSReconcileRequest(AppBaseModel):
    raw_message: str
    source_provider: str
    extracted_amount: Optional[float] = None
    extracted_sender: Optional[str] = None
    reference_number: Optional[str] = None


class PaymentSMSLogOut(AppBaseModel):
    id: int
    raw_message: str
    source_provider: str
    extracted_amount: Optional[float] = None
    extracted_sender: Optional[str] = None
    reference_number: Optional[str] = None
    matched_order_id: Optional[int] = None
    is_reconciled: bool
    match_confidence: float
    received_at: datetime
    reconciled_at: Optional[datetime] = None


class InternalAdminStats(AppBaseModel):
    total_orders: int
    pending_orders: int
    completed_orders: int
    total_materials: int
    total_teachers: int
    total_sms_logs: int
    unreconciled_sms_logs: int
    recent_orders: list[BotOrderOut]
    recent_unmatched_sms: list[PaymentSMSLogOut]

