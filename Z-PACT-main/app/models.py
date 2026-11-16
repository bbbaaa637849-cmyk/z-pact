# ================================================================
# app/models.py — نماذج قاعدة البيانات (SQLAlchemy ORM)
# مشروع: WebPDF Elite — منصة بيع وحماية ملفات PDF
#
# الجداول الموجودة هنا:
#   1. User          — المستخدمون (Admin / Customer)
#   2. Material      — الملفات (PDF) المرفوعة للبيع
#   3. StampLayout   — تنسيق مواضع الأختام (افتراضي أو مخصص)
#   4. Purchase      — سجل عمليات الشراء والختم
#   5. HiddenMark    — العلامات المخفية (Steganography) لكل عملية شراء
#   6. PreviewToken  — توكن مؤقت للمعاينة الآمنة بدون تحميل
# ================================================================

import enum
import json
from typing import Optional
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float,
    ForeignKey, Integer, JSON, Numeric, String, Text, CheckConstraint
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ---------------------------------------------------------------
# القاعدة الأساسية — كل الـ Models بترث منها
# ---------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ================================================================
# 1. ENUMS — القيم المحددة مسبقاً
# ================================================================

class UserRole(str, enum.Enum):
    """دور المستخدم في المنصة"""
    ADMIN    = "admin"     # يرفع الملفات ويدير المتجر
    CUSTOMER = "customer"  # يشتري ويحمل الملفات المختومة


class LayoutType(str, enum.Enum):
    """نوع تنسيق الختم"""
    DEFAULT = "default"  # التنسيق الذي صممه الـ Admin للمادة
    DEFAULT_TEXT = "default_text"  # قالب العلامة المائية النصية
    DEFAULT_LOGO = "default_logo"  # قالب العلامة المائية اللوجو
    CUSTOM  = "custom"   # التنسيق الذي عدّله العميل قبل الشراء


class PurchaseStatus(str, enum.Enum):
    """حالة عملية الشراء"""
    PENDING    = "pending"     # بانتظار معالجة الدفع
    PROCESSING = "processing"  # جاري ختم الملف في الـ Backend
    COMPLETED  = "completed"   # الملف جاهز للتحميل
    FAILED     = "failed"      # فشل في المعالجة


class PaymentMethod(str, enum.Enum):
    """طريقة الدفع"""
    MONEY = "money"
    COINS = "coins"


class TransactionType(str, enum.Enum):
    """نوع حركة الكوينز"""
    RECHARGE = "recharge"  # شحن رصيد
    PURCHASE = "purchase"  # شراء ملف
    REFUND   = "refund"    # استرجاع كوينز


class MarkType(str, enum.Enum):
    """نوع العلامة المخفية داخل الـ PDF"""
    INVISIBLE_TEXT = "invisible_text"  # نص أبيض حجم 1px
    METADATA       = "metadata"        # مدفون في metadata الـ PDF
    MICRO_QR       = "micro_qr"        # QR code مصغر جداً


# ================================================================
# 7. BLACKLIST — قائمة البيانات المحظورة
# ================================================================

class Blacklist(Base):
    """
    قائمة البيانات المحظورة من الختم (أسماء، أرقام تليفونات، أو نصوص مخصصة)
    لمنع المسرّبين المعروفين من استخدام المنصة ببياناتهم السابقة.
    """
    __tablename__ = "blacklist"

    id         = Column(Integer, primary_key=True, index=True)
    type       = Column(String(50), nullable=False) # 'phone', 'name', 'text'
    value      = Column(String(255), nullable=False, unique=True, index=True)
    reason     = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ================================================================
# 2. USERS — جدول المستخدمين
# ================================================================

class User(Base):
    """
    المستخدمون — Admin أو Customer.

    العلاقات:
      - User  →  Material       (one-to-many): Admin يرفع ملفات
      - User  →  StampLayout    (one-to-many): يصمم تنسيقات
      - User  →  Purchase       (one-to-many): Customer يشتري
      - User  →  PreviewToken   (one-to-many): يطلب معاينات مؤقتة
    """
    __tablename__ = "users"
    __table_args__ = (CheckConstraint('coins >= 0', name='check_coins_positive'),)

    id            = Column(Integer, primary_key=True, index=True)
    full_name     = Column(String(100), nullable=False)
    email         = Column(String(150), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)  # bcrypt hash
    role          = Column(Enum(UserRole), default=UserRole.CUSTOMER, nullable=False)
    is_active     = Column(Boolean, default=True)        # لإمكانية إيقاف حساب
    coins         = Column(Numeric(10, 2), default=0.0)  # رصيد الكوينز
    phone         = Column(String(20), nullable=True)
    profile_picture_path = Column(String(500), nullable=True)
    watermark_logo_path = Column(String(500), nullable=True)
    total_spent   = Column(Numeric(10, 2), default=0.0)
    preferences   = Column(JSON, nullable=True, default={"theme": "dark", "chat_visible": True, "notifications": True})
    referral_code = Column(String(50), unique=True, index=True, nullable=True)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_reward_claimed = Column(Boolean, default=False)
    serial_code   = Column(String(64), unique=True, index=True, nullable=True)
    approval_status = Column(String(20), default="approved", index=True)  # approved, pending, rejected
    pin_code_hash = Column(String(255), nullable=True)
    phone_verified = Column(Boolean, default=False)
    has_custom_password = Column(Boolean, default=False)
    is_founding_admin = Column(Boolean, default=False, index=True)        # أول أدمن (المؤسس السيادي)
    max_devices       = Column(Integer, nullable=True)                    # الحد الأقصى للأجهزة المسموح بها (null = الافتراضي حسب الدور)
    token_version     = Column(Integer, default=1, nullable=False)        # رقم إصدار التوكنات لإبطال جميع الجلسات لحظياً
    recovery_password_hash = Column(String(255), nullable=True)           # كلمة سر الاسترداد الطارئة (منفصلة عن كلمة المرور العادية)

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    @property
    def has_recovery_password(self) -> bool:
        return bool(self.recovery_password_hash)
    admin_label   = Column(String(120), nullable=True)                    # اسم/وصف حساب الأدمن
    
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- العلاقات ---
    uploaded_materials = relationship("Material",      back_populates="uploader",       foreign_keys="Material.uploaded_by")
    stamp_layouts      = relationship("StampLayout",   back_populates="creator",         foreign_keys="StampLayout.created_by")
    purchases          = relationship("Purchase",      back_populates="buyer",           foreign_keys="Purchase.buyer_id")
    preview_tokens     = relationship("PreviewToken",  back_populates="user",            foreign_keys="PreviewToken.user_id")
    reviews            = relationship("Review",        back_populates="user",            cascade="all, delete-orphan")
    
    # حماية الأجهزة
    devices            = relationship("UserDevice",    back_populates="user",           cascade="all, delete-orphan")
    
    # التسويق
    referred_by        = relationship("User", remote_side=[id])

    # خدمات البوت والملف التعريفي
    teacher_profile    = relationship("TeacherProfile", back_populates="user", uselist=False)
    bot_orders         = relationship("BotOrder", back_populates="user")

    def __repr__(self):
        return f"<User id={self.id} email={self.email} role={self.role}>"


# ================================================================
# 3. MATERIALS — جدول الملفات (PDF) المرفوعة
# ================================================================

class Material(Base):
    """
    الملف (المادة) المعروضة للبيع.
    Admin يرفعها، وهي الـ PDF الأصلي قبل الختم.

    العلاقات:
      - Material  →  StampLayout   (one-to-many): لها تنسيقات أختام متعددة
      - Material  →  Purchase      (one-to-many): تُشترى مرات كثيرة
      - Material  →  PreviewToken  (one-to-many): لها توكنات معاينة
    """
    __tablename__ = "materials"

    id                = Column(Integer, primary_key=True, index=True)
    uploaded_by       = Column(Integer, ForeignKey("users.id"), nullable=False)
    title             = Column(String(200), nullable=False)
    tag               = Column(String(100), nullable=True)
    description       = Column(Text, nullable=True)
    price             = Column(Numeric(10, 2), nullable=False)
    original_pdf_path = Column(String(500), nullable=False)  # المسار على السيرفر
    price             = Column(Numeric(10, 2), nullable=False)
    price_coins       = Column(Numeric(10, 2), default=0.0)  # السعر بالكوينز
    is_published      = Column(Boolean, default=False)       # لا يظهر في المتجر حتى ينشر
    compression_enabled = Column(Boolean, default=True)      # تفعيل ضغط الملف لتقليل الحجم
    total_pages       = Column(Integer, nullable=True)       # عدد صفحات الـ PDF
    cover_image_path  = Column(String(500), nullable=True)       # مسار صورة غلاف المذكرة
    page_frame_path   = Column(String(500), nullable=True)       # مسار إطار المذكرة
    sender_commission = Column(Float, nullable=True)             # عمولة المرسل المخصصة لهذه المذكرة (إن وجدت)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # --- العلاقات ---
    uploader       = relationship("User",         back_populates="uploaded_materials", foreign_keys=[uploaded_by])
    stamp_layouts  = relationship("StampLayout",  back_populates="material",           cascade="all, delete-orphan")
    purchases      = relationship("Purchase",     back_populates="material")
    preview_tokens = relationship("PreviewToken", back_populates="material",           cascade="all, delete-orphan")
    reviews        = relationship("Review",       back_populates="material",           cascade="all, delete-orphan")
    bot_orders     = relationship("BotOrder",     back_populates="material")

    def __repr__(self):
        return f"<Material id={self.id} title='{self.title}' price={self.price}>"


# ================================================================
# 4. STAMP LAYOUTS — تنسيق مواضع الأختام
# ================================================================

class StampLayout(Base):
    """
    تنسيق الأختام — يحدد مكان وشكل كل ختم على الصفحات.

    الـ stamp_elements عبارة عن JSON فيه قائمة من الأختام، كل ختم عنده:
      {
        "type": "name" | "phone" | "watermark",
        "page": 1,          // رقم الصفحة (0 = كل الصفحات)
        "x_pct": 10.5,      // النسبة المئوية من عرض الصفحة
        "y_pct": 90.2,      // النسبة المئوية من ارتفاع الصفحة
        "font_size": 12,
        "color": "#FF0000",
        "opacity": 0.4,
        "rotation": -45
      }

    استخدام النسب المئوية (%) بدل البكسل يضمن دقة 100% على أي حجم PDF.

    العلاقات:
      - StampLayout →  Material  (many-to-one)
      - StampLayout →  User      (many-to-one): من صممه
      - StampLayout →  Purchase  (one-to-many): يُستخدم في مشتريات
    """
    __tablename__ = "stamp_layouts"

    id             = Column(Integer, primary_key=True, index=True)
    material_id    = Column(Integer, ForeignKey("materials.id"), nullable=False)
    created_by     = Column(Integer, ForeignKey("users.id"), nullable=False)
    layout_type    = Column(Enum(LayoutType), default=LayoutType.DEFAULT)
    # قائمة JSON بمواضع الأختام — انظر تعليق الكلاس أعلاه
    stamp_elements = Column(JSON, nullable=False, default=list)
    created_at     = Column(DateTime, default=datetime.utcnow)

    # --- العلاقات ---
    material  = relationship("Material", back_populates="stamp_layouts", foreign_keys=[material_id])
    creator   = relationship("User",     back_populates="stamp_layouts",  foreign_keys=[created_by])
    purchases = relationship("Purchase", back_populates="stamp_layout")

    def get_elements(self) -> list:
        """إرجاع عناصر الأختام كـ Python list"""
        if isinstance(self.stamp_elements, str):
            return json.loads(self.stamp_elements)
        return self.stamp_elements or []

    def __repr__(self):
        return f"<StampLayout id={self.id} material_id={self.material_id} type={self.layout_type}>"


# ================================================================
# 5. PURCHASES — جدول عمليات الشراء
# ================================================================

class Purchase(Base):
    """
    سجل كل عملية شراء.
    هنا بيتخزن:
      - بيانات الختم الخاصة بالمشتري (الاسم والهاتف)
      - مسار ملف الـ PDF النهائي المختوم
      - حالة العملية (pending → processing → completed)

    العلاقات:
      - Purchase  →  User          (many-to-one): المشتري
      - Purchase  →  Material      (many-to-one): الملف المشترى
      - Purchase  →  StampLayout   (many-to-one): التنسيق المستخدم
      - Purchase  →  HiddenMark    (one-to-many): العلامات المخفية المضافة
    """
    __tablename__ = "purchases"

    id                = Column(Integer, primary_key=True, index=True)
    buyer_id          = Column(Integer, ForeignKey("users.id"), nullable=False)
    material_id       = Column(Integer, ForeignKey("materials.id"), nullable=False)
    stamp_layout_id   = Column(Integer, ForeignKey("stamp_layouts.id"), nullable=True)

    # البيانات الشخصية التي ستُختم على الملف
    buyer_name_stamp  = Column(String(100), nullable=False)  # الاسم المكتوب على الختم
    buyer_phone_stamp = Column(String(100), nullable=False)  # رقم الهاتف (= كلمة السر أيضاً)
    buyer_phone_stamp_2 = Column(String(100), nullable=True)  # رقم الهاتف الثاني
    doc_name_stamp    = Column(String(200), nullable=True)   # اسم المذكرة المخصص على الختم
    series_name       = Column(String(150), nullable=True)   # اسم السلسلة التعليمية
    watermark_text    = Column(String(200), nullable=True)   # نص العلامة المائية
    watermark_type    = Column(String(50), default="none")   # none, text, logo
    watermark_logo_path = Column(String(500), nullable=True) # مسار لوجو العلامة المائية المخصصة

    # نتيجة العملية
    output_pdf_path   = Column(String(500), nullable=True)   # مسار الملف النهائي بعد الختم
    status            = Column(Enum(PurchaseStatus), default=PurchaseStatus.PENDING)

    # بيانات الدفع
    transaction_id    = Column(String(100), nullable=True, unique=True)  # من بوابة الدفع
    amount_paid       = Column(Numeric(10, 2), nullable=False)
    payment_method    = Column(Enum(PaymentMethod), default=PaymentMethod.MONEY)

    # التوقيتات
    purchased_at      = Column(DateTime, default=datetime.utcnow)
    completed_at      = Column(DateTime, nullable=True)  # وقت جهوز الملف
    is_hidden         = Column(Boolean,  default=False)  # للحذف الصوري من طرف المستخدم

    # --- العلاقات ---
    buyer        = relationship("User",        back_populates="purchases",    foreign_keys=[buyer_id])
    material     = relationship("Material",    back_populates="purchases",    foreign_keys=[material_id])
    stamp_layout = relationship("StampLayout", back_populates="purchases",    foreign_keys=[stamp_layout_id])
    hidden_marks = relationship("HiddenMark",  back_populates="purchase",     cascade="all, delete-orphan")
    bot_orders   = relationship("BotOrder",    back_populates="purchase")

    def __repr__(self):
        return f"<Purchase id={self.id} buyer_id={self.buyer_id} status={self.status}>"


# ================================================================
# 6. HIDDEN MARKS — العلامات المخفية (Steganography)
# ================================================================

class HiddenMark(Base):
    """
    سجل العلامات المخفية المضافة لكل ملف مشترى.
    بنخزنها عشان لو حصل تسريب نقدر نرجع ونتحقق مين مصدره.

    كل عملية شراء بتضيف على الأقل علامة واحدة مخفية
    في زاوية من كل صفحة — نص أبيض حجم 1px يحمل كود المشتري.

    العلاقات:
      - HiddenMark →  Purchase  (many-to-one)
    """
    __tablename__ = "hidden_marks"

    id          = Column(Integer, primary_key=True, index=True)
    purchase_id = Column(Integer, ForeignKey("purchases.id"), nullable=False)
    mark_code   = Column(String(64),  nullable=False)   # كود فريد (UUID مثلاً) للمشتري
    page_number = Column(Integer,     nullable=False)    # رقم الصفحة اللي فيها العلامة
    x_position  = Column(Float,       nullable=False)    # نسبة مئوية من عرض الصفحة
    y_position  = Column(Float,       nullable=False)    # نسبة مئوية من ارتفاع الصفحة
    mark_type   = Column(Enum(MarkType), default=MarkType.INVISIBLE_TEXT)
    created_at  = Column(DateTime, default=datetime.utcnow)

    # --- العلاقات ---
    purchase = relationship("Purchase", back_populates="hidden_marks")

    def __repr__(self):
        return f"<HiddenMark id={self.id} purchase_id={self.purchase_id} page={self.page_number}>"


# ================================================================
# 7. PREVIEW TOKENS — توكنات المعاينة الآمنة
# ================================================================

class PreviewToken(Base):
    """
    توكن مؤقت يسمح للمستخدم بمعاينة الملف داخل المتصفح
    بدون السماح بتحميله.

    آلية الأمان:
      - التوكن صالح لفترة قصيرة (expires_at)
      - يُلغى بعد الاستخدام أو انتهاء مدته
      - الـ Backend يُرجع binary blob مشفر عبر PDF.js
        ولا يُعطي رابط مباشر للملف الأصلي أبداً
      - pages_allowed: يحدد كم صفحة يقدر يشوفها (معاينة جزئية)

    العلاقات:
      - PreviewToken →  Material  (many-to-one)
      - PreviewToken →  User      (many-to-one)
    """
    __tablename__ = "preview_tokens"

    id            = Column(Integer, primary_key=True, index=True)
    material_id   = Column(Integer, ForeignKey("materials.id"), nullable=False)
    user_id       = Column(Integer, ForeignKey("users.id"),     nullable=False)
    token         = Column(String(64), unique=True, nullable=False, index=True)  # UUID أو JWT
    allowed_pages = Column(JSON, default=list)    # مصفوفة أرقام الصفحات المسموح بمعاينتها
    expires_at    = Column(DateTime, nullable=False)   # وقت انتهاء صلاحية التوكن
    created_at    = Column(DateTime, default=datetime.utcnow)

    # --- العلاقات ---
    material = relationship("Material", back_populates="preview_tokens")
    user     = relationship("User",     back_populates="preview_tokens")

    @property
    def is_expired(self) -> bool:
        """هل انتهت صلاحية التوكن؟"""
        return datetime.utcnow() > self.expires_at

    def __repr__(self):
        return f"<PreviewToken id={self.id} material_id={self.material_id} expires_at={self.expires_at}>"

# ================================================================
# 8. LAYOUT PRESETS — قوالب الأختام الجاهزة
# ================================================================

class LayoutPreset(Base):
    """
    قوالب أختام جاهزة يمكن تطبيقها على أي ملف.
    تساعد المسؤول في توفير الوقت بدل إعادة التصميم لكل ملف.
    """
    __tablename__ = "layout_presets"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(100), nullable=False)
    created_by     = Column(Integer, ForeignKey("users.id"), nullable=False)
    stamp_elements = Column(JSON, nullable=False, default=list)
    created_at     = Column(DateTime, default=datetime.utcnow)

    # --- العلاقات ---
    creator = relationship("User", foreign_keys=[created_by])

    def __repr__(self):
        return f"<LayoutPreset id={self.id} name='{self.name}'>"
# ================================================================
# 9. BACKGROUND JOBS — سجل المهام الخلفية
# ================================================================

class BackgroundJob(Base):
    """
    سجل المهام الخلفية (مثل توليد المعاينة أو التحميلات التجريبية).
    يضمن بقاء الملف متاحاً للمستخدم حتى بعد إغلاق المتصفح.
    """
    __tablename__ = "background_jobs"

    id            = Column(Integer, primary_key=True, index=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_id        = Column(String(100), unique=True, index=True, nullable=False)
    label         = Column(String(200), nullable=False)
    status        = Column(String(20), default="processing") # processing, done, failed
    output_path   = Column(String(500), nullable=True)
    error_message = Column(String(500), nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    # --- العلاقات ---
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<BackgroundJob id={self.id} job_id={self.job_id} status={self.status}>"


# ================================================================
# 10. COIN TRANSACTIONS — سجل حركات الكوينز
# ================================================================

class CoinTransaction(Base):
    """
    سجل حركات الرصيد (الكوينز) للمستخدم.
    يعمل كـ Ledger (دفتر أستاذ) لضمان دقة العمليات.
    """
    __tablename__ = "coin_transactions"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount      = Column(Numeric(10, 2), nullable=False) # ممكن يكون موجب (شحن) أو سالب (دفع)
    type        = Column(Enum(TransactionType), nullable=False)
    description = Column(String(255), nullable=True) # وصف العملية
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=True) # لو دفع لملف
    created_at  = Column(DateTime, default=datetime.utcnow)

    # --- العلاقات ---
    user = relationship("User", foreign_keys=[user_id])
    material = relationship("Material", foreign_keys=[material_id])

    def __repr__(self):
        return f"<CoinTransaction id={self.id} user_id={self.user_id} amount={self.amount} type={self.type}>"

# ================================================================
# 11. أجهزة المستخدم (User Devices) — للحماية من مشاركة الحسابات
# ================================================================

class UserSession(Base):
    """سجل الجلسات النشطة لكل مستخدم (لإدارة الجلسات وإبطال التوكنات لحظياً)"""
    __tablename__ = "user_sessions"

    id            = Column(Integer, primary_key=True, index=True)
    session_id    = Column(String(64), unique=True, index=True, nullable=False)
    user_id       = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_id     = Column(String(255), nullable=False, index=True)
    user_agent    = Column(String(500), nullable=True)
    ip_address    = Column(String(100), nullable=True)
    login_surface = Column(String(50), default="web", nullable=False)
    is_active     = Column(Boolean, default=True, nullable=False, index=True)
    revoked_at    = Column(DateTime, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="sessions")


class UserDevice(Base):
    __tablename__ = "user_devices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # بصمة الجهاز (Fingerprint) - سلسلة نصية فريدة من الـ Frontend
    device_id = Column(String(255), nullable=False, index=True)
    user_agent = Column(String(500), nullable=True)
    last_ip = Column(String(100), nullable=True)
    
    is_blocked = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="devices")

    def __repr__(self):
        return f"<UserDevice id={self.id} user_id={self.user_id} device_id={self.device_id}>"

# ================================================================
# 12. تحويل من محفظة فودافون كاش إلى كوينز
# ================================================================

class WalletTransaction(Base):
    __tablename__ = "wallet_transactions"

    id = Column(Integer, primary_key=True, index=True)
    sender_phone = Column(String(100), nullable=False)
    amount = Column(Float, nullable=False)
    transaction_date = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="pending") # pending, completed
    source = Column(String(50)) # VodafoneCash, InstaPay


# ================================================================
# 12. تتبع سجل تعديلات المستخدم (User Audit Log)
# ================================================================
class UserAuditLog(Base):
    __tablename__ = "user_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(50), nullable=False) # e.g. "UPDATE_PROFILE"
    old_data = Column(JSON, nullable=True)
    new_data = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


# ================================================================
# 13. الشات العالمي (Global Chat)
# ================================================================
class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    reply_to_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])
    reply_to = relationship("ChatMessage", remote_side=[id])

    @property
    def reply_preview(self) -> Optional[str]:
        if self.reply_to:
            return self.reply_to.message
        return None

    @property
    def reply_user_name(self) -> Optional[str]:
        if self.reply_to and self.reply_to.user:
            return self.reply_to.user.full_name
        return None

    @property
    def user_name(self) -> Optional[str]:
        if self.user:
            return self.user.full_name
        return None

    @property
    def avatar(self) -> Optional[str]:
        if self.user:
            return self.user.profile_picture_path
        return None


# ================================================================
# 14. مركز الإشعارات (Notifications)
# ================================================================
class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True) # None means broadcast to all
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


# ================================================================
# 15. نظام الكوبونات التلقائي (Auto-Coupons) ومستويات الإنفاق
# ================================================================
class SpendingLevel(Base):
    __tablename__ = "spending_levels"

    id = Column(Integer, primary_key=True, index=True)
    threshold = Column(Numeric(10, 2), nullable=False, unique=True) # الحد الأدنى للإنفاق (مثال: 500)
    reward_coins = Column(Numeric(10, 2), nullable=False) # المكافأة (مثال: 50 كوينز)
    created_at = Column(DateTime, default=datetime.utcnow)

class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False) # الكوبون مخصص لهذا المستخدم فقط
    reward_coins = Column(Numeric(10, 2), nullable=False)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    used_at = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id])


# ================================================================
# 16. نظام المراجعات والتقييمات للمواد (B2B Reviews & Ratings)
# ================================================================
class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewer_title = Column(String(200), nullable=False)  # Physics Teacher etc.
    content_quality_rating = Column(Integer, nullable=False)  # 1-5
    print_formatting_rating = Column(Integer, nullable=False)  # 1-5
    stamp_appearance_rating = Column(Integer, nullable=False)  # 1-5
    overall_rating = Column(Numeric(3, 2), nullable=False)  # Average of 3
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    material = relationship("Material", back_populates="reviews")
    user = relationship("User", back_populates="reviews")


# ================================================================
# 17. BOT ENUMS & MODELS (Enterprise Telegram Ecosystem)
# ================================================================

class OrderFulfillmentStatus(str, enum.Enum):
    PENDING_PAYMENT      = "pending_payment"
    PAYMENT_CONFIRMED    = "payment_confirmed"
    PROCESSING_STAMP     = "processing_stamp"
    READY_FOR_DELIVERY   = "ready_for_delivery"
    DELIVERED            = "delivered"
    FAILED               = "failed"
    CANCELLED            = "cancelled"


class TeacherProfile(Base):
    """
    الملف التعريفي للمدرس لتسهيل وإعادة استخدام بيانات الختم تلقائياً
    """
    __tablename__ = "teacher_profiles"

    id                   = Column(Integer, primary_key=True, index=True)
    user_id              = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    telegram_user_id     = Column(String(50), unique=True, nullable=True, index=True)
    telegram_username    = Column(String(100), nullable=True)
    
    # بيانات الختم الخاصة بالمدرس
    teacher_name         = Column(String(150), nullable=False)   # مثال: أ/ أحمد عصام
    subject_specialty    = Column(String(100), nullable=True)    # مادة الفيزياء
    primary_phone        = Column(String(30), nullable=False)    # رقم الموبايل 1
    secondary_phone      = Column(String(30), nullable=True)     # رقم الموبايل 2
    series_title         = Column(String(150), nullable=True)    # اسم السلسلة
    watermark_type       = Column(String(50), default="text")    # text | logo | dual
    watermark_text       = Column(String(200), nullable=True)
    logo_image_path      = Column(String(500), nullable=True)
    
    is_verified          = Column(Boolean, default=False)
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # العلاقات
    user                 = relationship("User", back_populates="teacher_profile")
    orders               = relationship("BotOrder", back_populates="teacher_profile")

    def __repr__(self):
        return f"<TeacherProfile id={self.id} name='{self.teacher_name}' tg={self.telegram_user_id}>"


class BotSession(Base):
    """
    حفظ جلسات المحادثة وحالة الـ FSM الخاصة بالمستخدم في التيليجرام
    """
    __tablename__ = "bot_sessions"

    telegram_chat_id     = Column(String(50), primary_key=True, index=True)
    telegram_user_id     = Column(String(50), nullable=False, index=True)
    username             = Column(String(100), nullable=True)
    current_state        = Column(String(100), nullable=True)
    state_data           = Column(JSON, default=dict)
    last_interaction     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<BotSession chat_id={self.telegram_chat_id} state={self.current_state}>"


class BotOrder(Base):
    """
    سجل طلبات التيليجرام والـ Mini App مع تتبع الإيصالات والمطابقة اللحظية
    """
    __tablename__ = "bot_orders"

    id                   = Column(Integer, primary_key=True, index=True)
    order_code           = Column(String(32), unique=True, index=True, nullable=False) # e.g. ORD-98421
    telegram_chat_id     = Column(String(50), nullable=False, index=True)
    telegram_user_id     = Column(String(50), nullable=True, index=True)
    user_id              = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    teacher_profile_id   = Column(Integer, ForeignKey("teacher_profiles.id"), nullable=True, index=True)
    material_id          = Column(Integer, ForeignKey("materials.id"), nullable=False, index=True)
    
    # بيانات الختم المحددة للطلب
    buyer_name_stamp     = Column(String(150), nullable=False)
    buyer_phone_stamp    = Column(String(50), nullable=False)
    buyer_phone_stamp_2  = Column(String(50), nullable=True)
    doc_name_stamp       = Column(String(150), nullable=True)
    series_name          = Column(String(150), nullable=True)
    watermark_text       = Column(String(200), nullable=True)
    watermark_type       = Column(String(50), default="none")
    watermark_logo_path  = Column(String(500), nullable=True)
    
    # البيانات المالية وتتبع الإيصالات والمطابقة
    total_amount         = Column(Float, nullable=False)
    payment_method       = Column(String(50), default="vodafone_cash") # vodafone_cash, instapay, coins, orange_cash, etisalat_cash
    claimed_sender_phone = Column(String(50), nullable=True, index=True) # رقم الهاتف الذي يدعي المشتري التحويل منه
    receiver_wallet_phone = Column(String(50), nullable=True) # الرقم المحول إليه (محفظة المنصة أو المرسل)
    bundle_code          = Column(String(40), nullable=True, index=True) # كود السلة المجمعة لشراء عدة مذكرات معاً
    processed_by_sender_id = Column(Integer, ForeignKey("sender_agents.id"), nullable=True, index=True)
    sender_commission_earned = Column(Float, default=0.0)
    receipt_image_path   = Column(String(500), nullable=True) # مسار سكرين شوت التحويل المحفوظ في storage/receipts/
    receipt_file_id      = Column(String(255), nullable=True) # Telegram file_id
    payment_reference    = Column(String(100), nullable=True, index=True)
    
    # حالة معالجة الطلب والتنفيذ
    status               = Column(Enum(OrderFulfillmentStatus), default=OrderFulfillmentStatus.PENDING_PAYMENT, index=True)
    purchase_id          = Column(Integer, ForeignKey("purchases.id"), nullable=True)
    stamped_pdf_path     = Column(String(500), nullable=True)
    telegram_message_id  = Column(Integer, nullable=True)
    error_message        = Column(String(500), nullable=True)
    
    created_at           = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at         = Column(DateTime, nullable=True)

    # العلاقات
    teacher_profile      = relationship("TeacherProfile", back_populates="orders")
    material             = relationship("Material", back_populates="bot_orders")
    user                 = relationship("User", back_populates="bot_orders")
    purchase             = relationship("Purchase", back_populates="bot_orders")
    payment_sms_logs     = relationship("PaymentSMSLog", back_populates="matched_order")

    def __repr__(self):
        return f"<BotOrder id={self.id} code={self.order_code} status={self.status} amount={self.total_amount}>"


class PaymentSMSLog(Base):
    """
    سجل رسائل SMS المستلمة من الـ Webhook Engine مع المطابقة ومنع إعادة الاستخدام
    """
    __tablename__ = "payment_sms_logs"

    id                   = Column(Integer, primary_key=True, index=True)
    raw_message          = Column(Text, nullable=False)
    source_provider      = Column(String(50), nullable=False) # VodafoneCash, InstaPay, OrangeCash, EtisalatCash, CIB, BankTransfer
    extracted_amount     = Column(Float, nullable=True, index=True)
    extracted_sender     = Column(String(50), nullable=True, index=True)
    reference_number     = Column(String(100), nullable=True, index=True)
    
    # المطابقة مع الطلبات
    matched_order_id     = Column(Integer, ForeignKey("bot_orders.id"), nullable=True, index=True)
    is_reconciled        = Column(Boolean, default=False, index=True)
    match_confidence     = Column(Float, default=0.0) # 0.0 to 1.0
    reconciled_at        = Column(DateTime, nullable=True)
    
    received_at          = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at         = Column(DateTime, nullable=True)

    # العلاقات
    matched_order        = relationship("BotOrder", back_populates="payment_sms_logs")

    def __repr__(self):
        return f"<PaymentSMSLog id={self.id} src={self.source_provider} amount={self.extracted_amount} sender={self.extracted_sender} reconciled={self.is_reconciled}>"


# ================================================================
# 18. ACTIVATION SERIALS — سيريالات التفعيل الفورية للمدرسين والمرسلين
# ================================================================

class ActivationSerial(Base):
    """
    أكواد وسيريالات التفعيل الفوري المعتمدة من الأدمن لمنع التسجيل العشوائي
    """
    __tablename__ = "activation_serials"

    id          = Column(Integer, primary_key=True, index=True)
    serial_code = Column(String(64), unique=True, index=True, nullable=False)  # e.g. ZP-VIP-4A9F
    target_role = Column(String(20), default="teacher", index=True)            # teacher | sender
    max_uses    = Column(Integer, default=1)
    used_count  = Column(Integer, default=0)
    bonus_coins = Column(Float, default=0.0)
    note        = Column(String(255), nullable=True)
    is_active   = Column(Boolean, default=True, index=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


# ================================================================
# 19. SENDER AGENTS & COMMISSION LEDGER — فريق المرسلين والعمولات
# ================================================================

class SenderAgent(Base):
    """
    حسابات فريق المرسلين (التسجيل برقم الهاتف + كلمة السر + OTP + ربط السيريال + موافقة الأدمن)
    """
    __tablename__ = "sender_agents"

    id                          = Column(Integer, primary_key=True, index=True)
    phone                       = Column(String(30), unique=True, index=True, nullable=False)
    display_name                = Column(String(120), nullable=True)
    password_hash               = Column(String(255), nullable=True)
    phone_verified              = Column(Boolean, default=False)
    telegram_user_id            = Column(String(50), nullable=True, index=True)
    serial_code                 = Column(String(64), unique=True, index=True, nullable=False)  # ZP-SND-XXXX-XXXX
    device_id                   = Column(String(255), nullable=True, index=True)
    auth_token                  = Column(String(128), unique=True, index=True, nullable=True)
    status                      = Column(String(20), default="pending", index=True)            # pending | approved | rejected | suspended
    wallet_number               = Column(String(50), nullable=True)

    # نظام حساب عمولة المرسل
    commission_per_pdf          = Column(Float, default=15.0)                                  # عمولة ثابتة لكل مذكرة (ج.م)
    commission_percent          = Column(Float, default=0.0)                                   # نسبة مئوية اختيارية من السعر
    custom_material_commissions = Column(JSON, default=dict)                                   # {"material_id": custom_amount}

    # الإحصائيات والعدادات المالية التراكمية
    total_pdfs_count            = Column(Integer, default=0)
    total_orders_count          = Column(Integer, default=0)
    total_collected_by_sender   = Column(Float, default=0.0)   # إجمالي التحويلات التي استلمها المرسل في محفظته
    total_collected_by_admin    = Column(Float, default=0.0)   # إجمالي التحويلات التي دخلت محفظة المنصة مباشرة
    total_commission_earned     = Column(Float, default=0.0)   # إجمالي أرباح وعمولات المرسل
    net_due_to_admin            = Column(Float, default=0.0)   # الصافي المستحق للإدارة طرف المرسل (سالب = مستحق للمرسل عند الإدارة)
    settled_amount              = Column(Float, default=0.0)   # إجمالي المبالغ التي تمت تسويتها وتصفيتها
    admin_notes                 = Column(String(500), nullable=True)

    created_at                  = Column(DateTime, default=datetime.utcnow, index=True)
    approved_at                 = Column(DateTime, nullable=True)
    last_seen_at                = Column(DateTime, default=datetime.utcnow)

    ledger_entries              = relationship("SenderLedgerEntry", back_populates="sender", cascade="all, delete-orphan")


class SenderLedgerEntry(Base):
    """
    دفتر أستاذ تفصيلي لكل عملية أو تسوية مالية خاصة بالمرسل
    """
    __tablename__ = "sender_ledger_entries"

    id                        = Column(Integer, primary_key=True, index=True)
    sender_id                 = Column(Integer, ForeignKey("sender_agents.id"), nullable=False, index=True)
    entry_type                = Column(String(30), nullable=False, index=True)  # direct_order | pending_fulfilled | settlement | adjustment
    bundle_code               = Column(String(40), nullable=True, index=True)
    order_code                = Column(String(40), nullable=True, index=True)

    teacher_name              = Column(String(150), nullable=True)
    teacher_phone             = Column(String(50), nullable=True)
    from_wallet_phone         = Column(String(50), nullable=True)   # الرقم المحول منه
    to_wallet_phone           = Column(String(50), nullable=True)   # الرقم المحول إليه
    collected_to_admin_wallet = Column(Boolean, default=False)

    pdfs_count                = Column(Integer, default=0)
    materials_summary         = Column(Text, nullable=True)
    gross_amount              = Column(Float, default=0.0)          # إجمالي قيمة المذكرات / التحويل
    sender_commission         = Column(Float, default=0.0)          # نصيب المرسل الصافي من العملية
    admin_net_delta           = Column(Float, default=0.0)          # الأثر الصافي على عهدة المرسل للإدارة

    receipt_image_path        = Column(String(500), nullable=True)
    download_items_json       = Column(JSON, default=list)
    notes                     = Column(String(500), nullable=True)
    created_at                = Column(DateTime, default=datetime.utcnow, index=True)

    sender                    = relationship("SenderAgent", back_populates="ledger_entries")


# ================================================================
# 20. APP RELEASE CONFIG — نظام التحديث التلقائي المباشر للتطبيقات (OTA)
# ================================================================

class AppReleaseConfig(Base):
    """
    إدارة التحديثات الهوائية الفورية للتطبيقات الثلاثة (الأدمن، المدرسين، المرسلين)
    """
    __tablename__ = "app_release_configs"

    id            = Column(Integer, primary_key=True, index=True)
    flavor        = Column(String(20), unique=True, index=True, nullable=False)  # admin | teacher | sender
    version_code  = Column(Integer, default=4, nullable=False)
    version_name  = Column(String(30), default="3.1.0", nullable=False)
    title         = Column(String(200), default="🚀 تحديث جديد لمنظومة Z-PACT v3.1")
    release_notes = Column(Text, default="بوابة الأدمن السيادية بكلمة السر فقط + حماية الهاتف وكلمة السر والـ OTP للمدرسين والمرسلين.")
    force_update  = Column(Boolean, default=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ================================================================
# 21. OTP CHALLENGES & ADMIN SECURITY AUDIT (Z-PACT v3.1)
# ================================================================

class OtpChallenge(Base):
    """
    رموز التحقق الفورية (OTP 6-Digits) لتأمين تسجيل ودخول المدرسين والمرسلين
    عبر رسائل شريحة هاتف الأدمن (SIM SMS Relay) وبوت التليجرام اللحظي.
    """
    __tablename__ = "otp_challenges"

    id                 = Column(Integer, primary_key=True, index=True)
    challenge_token    = Column(String(64), unique=True, index=True, nullable=False)
    phone              = Column(String(30), index=True, nullable=False)
    target_role        = Column(String(20), default="teacher", index=True)   # teacher | sender
    purpose            = Column(String(30), default="register", index=True)  # register | login_device | reset_password
    otp_code_hash      = Column(String(255), nullable=False)
    otp_plain_outbox   = Column(String(16), nullable=True)                   # يُمسح فور التحقق أو انتهاء الصلاحية
    sms_dispatch_status = Column(String(20), default="queued", index=True)   # queued | sent | skipped
    telegram_delivered = Column(Boolean, default=False)
    is_verified        = Column(Boolean, default=False, index=True)
    attempts           = Column(Integer, default=0)
    expires_at         = Column(DateTime, nullable=False, index=True)
    created_at         = Column(DateTime, default=datetime.utcnow, index=True)


class AdminSecurityEvent(Base):
    """
    سجل أمني سيادي يوثق كل عملية دخول للأدمن وطلبات إنشاء حسابات أدمن إضافية (لإعلام أول أدمن)
    """
    __tablename__ = "admin_security_events"

    id          = Column(Integer, primary_key=True, index=True)
    admin_id    = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event_type  = Column(String(50), nullable=False, index=True)  # FOUNDING_SETUP | ADMIN_LOGIN | CO_ADMIN_REQUEST | CO_ADMIN_APPROVED | CO_ADMIN_REJECTED
    admin_label = Column(String(120), nullable=True)
    device_id   = Column(String(255), nullable=True)
    ip_address  = Column(String(100), nullable=True)
    details     = Column(String(500), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow, index=True)
