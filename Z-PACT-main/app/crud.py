# ================================================================
# app/crud.py — عمليات قاعدة البيانات (Create, Read, Update, Delete)
#
# كل دالة هنا مسؤولة عن عملية واحدة فقط على قاعدة البيانات.
# الـ endpoints في routers/ بتستدعي الدوال دي — مش بتتكلم
# مع قاعدة البيانات مباشرة. ده بيخلي الكود نظيف وسهل الاختبار.
#
# التنظيم:
#   crud_user      → عمليات المستخدمين
#   crud_material  → عمليات الملفات
#   crud_layout    → عمليات تنسيقات الأختام
#   crud_purchase  → عمليات المشتريات
#   crud_token     → عمليات توكنات المعاينة
# ================================================================

import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from sqlalchemy.orm import Session, joinedload

from app import models, schemas

# -----------------------------------------------------------
# تشفير كلمات المرور باستخدام مكتبة bcrypt مباشرة لحل مشاكل توافق passlib
# -----------------------------------------------------------

def hash_password(password: str) -> str:
    """تشفير كلمة المرور — بنخزن الـ hash مش الكلمة الأصلية أبداً"""
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(pwd_bytes, salt)
    return hashed_bytes.decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    """التحقق من كلمة المرور مقارنةً بالـ hash المخزن"""
    try:
        return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False



# ================================================================
# CRUD — USERS
# ================================================================

def get_user_by_id(db: Session, user_id: int) -> Optional[models.User]:
    """جلب مستخدم بالـ ID"""
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_user_by_email(db: Session, email: str) -> Optional[models.User]:
    """جلب مستخدم بالإيميل — بيُستخدم في تسجيل الدخول"""
    return db.query(models.User).filter(models.User.email == email).first()


def create_user(
    db:   Session,
    data: schemas.UserCreate,
    role: models.UserRole = models.UserRole.CUSTOMER,
) -> models.User:
    """
    إنشاء مستخدم جديد.
    الـ role بيتحدد من الـ Backend — مش من الـ Client.
    """
    # توليد كود دعوة فريد
    import secrets
    ref_code = secrets.token_hex(4).upper() # 8 characters
    
    user = models.User(
        full_name     = data.full_name,
        email         = data.email,
        password_hash = hash_password(data.password),
        role          = role,
        referral_code = ref_code,
        referred_by_id = None
    )
    
    # لو فيه كود دعوة، نربطه
    if data.referral_code:
        inviter = db.query(models.User).filter(models.User.referral_code == data.referral_code).first()
        if inviter:
            user.referred_by_id = inviter.id

    db.add(user)
    db.commit()
    db.refresh(user)  # بنجيب الـ id اللي اتعمله من قاعدة البيانات
    return user


def authenticate_user(db: Session, email: str, password: str) -> Optional[models.User]:
    """
    التحقق من بيانات الدخول.
    بيرجع الـ User لو صح، أو None لو غلط.
    """
    user = get_user_by_email(db, email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user


def deactivate_user(db: Session, user_id: int) -> Optional[models.User]:
    """إيقاف حساب مستخدم (بدل الحذف الكامل للحفاظ على سجل المشتريات)"""
    user = get_user_by_id(db, user_id)
    if not user:
        return None
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


def get_all_users(
    db:    Session,
    skip:  int = 0,
    limit: int = 100,
    search: str = None,
) -> tuple[list[models.User], int]:
    """جلب كل المستخدمين (للـ Admin) مع إمكانية البحث"""
    query = db.query(models.User)
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (models.User.full_name.ilike(search_filter)) |
            (models.User.email.ilike(search_filter)) |
            (models.User.id.cast(models.String).ilike(search_filter))
        )
    query = query.order_by(models.User.created_at.desc())
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return items, total


# ================================================================
# DEVICE MANAGEMENT
# ================================================================

def get_user_devices(db: Session, user_id: int):
    return db.query(models.UserDevice).filter(models.UserDevice.user_id == user_id).all()

def register_user_device(db: Session, user_id: int, device_id: str, user_agent: str = None, ip: str = None):
    # Check if already exists
    device = db.query(models.UserDevice).filter(
        models.UserDevice.user_id == user_id,
        models.UserDevice.device_id == device_id
    ).first()
    
    if device:
        device.last_login = datetime.utcnow()
        device.last_ip = ip
        device.user_agent = user_agent
        db.commit()
        return device
    
    # Create new
    device = models.UserDevice(
        user_id=user_id,
        device_id=device_id,
        user_agent=user_agent,
        last_ip=ip
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device

def delete_all_user_devices(db: Session, user_id: int):
    """حذف كل الأجهزة المسجلة للمستخدم (Reset)"""
    db.query(models.UserDevice).filter(models.UserDevice.user_id == user_id).delete()
    db.commit()

# ================================================================
# BLACKLIST OPERATIONS
# ================================================================

def add_to_blacklist(db: Session, val_type: str, value: str, reason: str = None):
    # Check if exists
    existing = db.query(models.Blacklist).filter(models.Blacklist.value == value).first()
    if existing: return existing
    
    entry = models.Blacklist(type=val_type, value=value, reason=reason)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry

def get_blacklist(db: Session):
    return db.query(models.Blacklist).order_by(models.Blacklist.created_at.desc()).all()

def remove_from_blacklist(db: Session, entry_id: int):
    db.query(models.Blacklist).filter(models.Blacklist.id == entry_id).delete()
    db.commit()

def is_blacklisted(db: Session, value: str) -> bool:
    clean_val = (value or "").strip()
    if not clean_val:
        return False
    return db.query(models.Blacklist).filter(models.Blacklist.value == clean_val).first() is not None

def check_stamp_data_against_blacklist(db: Session, name: Optional[str], phone: Optional[str], text: Optional[str]):
    """التحقق من البيانات المدخلة للختم ضد القائمة السوداء"""
    clean_name = (name or "").strip()
    clean_phone = (phone or "").strip()
    clean_text = (text or "").strip()
    if is_blacklisted(db, clean_phone): return f"⚠️ رقم الهاتف '{clean_phone}' محظور لسوابق تسريب. يرجى مراجعة الإدارة."
    if is_blacklisted(db, clean_name):  return f"⚠️ الاسم '{clean_name}' محظور لسوابق تسريب. يرجى مراجعة الإدارة."
    if is_blacklisted(db, clean_text):  return f"⚠️ النص '{clean_text}' محظور لاحتوائه على بيانات مسربة."
    return None


def update_user_coins(
    db:          Session,
    user_id:     int,
    amount:      float,
    tx_type:     models.TransactionType,
    description: Optional[str] = None,
    material_id: Optional[int] = None,
) -> Optional[models.User]:
    """
    تحديث رصيد المستخدم وتسجيل الحركة في الـ Ledger.
    الـ amount ممكن يكون موجب (إضافة) أو سالب (سحب).
    """
    user = get_user_by_id(db, user_id)
    if not user:
        return None

    # تحديث الرصيد
    new_balance = float(user.coins) + float(amount)
    if new_balance < 0:
        raise ValueError("الرصيد غير كافٍ. لا يمكن أن يكون الرصيد بالسالب.")
    user.coins = new_balance

    # تسجيل الحركة
    tx = models.CoinTransaction(
        user_id     = user_id,
        amount      = amount,
        type        = tx_type,
        description = description,
        material_id = material_id,
    )
    db.add(tx)
    
    db.commit()
    db.refresh(user)
    return user


def get_user_coin_history(
    db:      Session,
    user_id: int,
    limit:   int = 50,
) -> list[models.CoinTransaction]:
    """جلب سجل حركات الكوينز لمستخدم"""
    return (
        db.query(models.CoinTransaction)
        .options(joinedload(models.CoinTransaction.material))
        .filter(models.CoinTransaction.user_id == user_id)
        .order_by(models.CoinTransaction.created_at.desc())
        .limit(limit)
        .all()
    )


# ================================================================
# CRUD — MATERIALS
# ================================================================

def get_material(db: Session, material_id: int) -> Optional[models.Material]:
    """جلب ملف بالـ ID مع بيانات الرافع (eager loading)"""
    return (
        db.query(models.Material)
        .options(joinedload(models.Material.uploader))
        .filter(models.Material.id == material_id)
        .first()
    )


def get_published_materials(
    db:       Session,
    skip:     int = 0,
    limit:    int = 20,
) -> tuple[list[models.Material], int]:
    """
    جلب الملفات المنشورة للمتجر مع pagination.
    بيرجع (قائمة الملفات، إجمالي العدد).
    """
    query = (
        db.query(models.Material)
        .options(joinedload(models.Material.uploader))
        .filter(models.Material.is_published == True)
        .order_by(models.Material.created_at.desc())
    )
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return items, total


def get_all_materials(
    db:    Session,
    skip:  int = 0,
    limit: int = 50,
) -> tuple[list[models.Material], int]:
    """جلب كل الملفات (للـ Admin) بما فيها غير المنشورة"""
    query = (
        db.query(models.Material)
        .options(joinedload(models.Material.uploader))
        .order_by(models.Material.created_at.desc())
    )
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return items, total


def create_material(
    db:          Session,
    data:        schemas.MaterialCreate,
    uploader_id: int,
    pdf_path:    str,
    total_pages: int,
    cover_image_path: Optional[str] = None,
    is_coming_soon: bool = False,
    is_published: bool = False,
    layout_confirmed: bool = False,
) -> models.Material:
    """
    إنشاء سجل ملف جديد في قاعدة البيانات.
    الـ pdf_path هو المسار على الـ server بعد رفع الملف (أو فارغ لمذكرات قريباً/تشويق).
    """
    material = models.Material(
        uploaded_by       = uploader_id,
        title             = data.title,
        tag               = data.tag,
        description       = data.description,
        price             = data.price,
        price_coins       = data.price_coins,
        original_pdf_path = pdf_path,
        total_pages       = total_pages,
        is_published      = is_published,
        is_coming_soon    = is_coming_soon,
        layout_confirmed  = layout_confirmed,
        compression_enabled = data.compression_enabled,
        cover_image_path  = cover_image_path,
        page_frame_path   = data.page_frame_path,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


def update_material(
    db:          Session,
    material_id: int,
    data:        schemas.MaterialUpdate,
) -> Optional[models.Material]:
    """تعديل بيانات ملف — بس الحقول اللي اتبعتت"""
    material = get_material(db, material_id)
    if not material:
        return None

    # استخدام exclude_unset لتمكين مسح حقول مثل التاج (بإرسال null)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(material, field, value)

    db.commit()
    db.refresh(material)
    return material


def delete_material(db: Session, material_id: int) -> bool:
    """
    حذف ملف من قاعدة البيانات والـ cascade هيحذف الـ layouts والـ tokens.
    بيرجع True لو اتحذف، False لو مش موجود.
    """
    material = get_material(db, material_id)
    if not material:
        return False
    db.delete(material)
    db.commit()
    return True


# ================================================================
# CRUD — STAMP LAYOUTS
# ================================================================

def get_layout(db: Session, layout_id: int) -> Optional[models.StampLayout]:
    """جلب تنسيق ختم بالـ ID"""
    return db.query(models.StampLayout).filter(models.StampLayout.id == layout_id).first()


def get_default_layout(db: Session, material_id: int, layout_type: models.LayoutType = models.LayoutType.DEFAULT) -> Optional[models.StampLayout]:
    """جلب التنسيق الافتراضي لمادة معينة حسب النوع"""
    ly = (
        db.query(models.StampLayout)
        .filter(
            models.StampLayout.material_id == material_id,
            models.StampLayout.layout_type == layout_type,
        )
        .first()
    )
    if ly:
        return ly
        
    # fallback to DEFAULT if the requested type is not found
    if layout_type != models.LayoutType.DEFAULT:
        return (
            db.query(models.StampLayout)
            .filter(
                models.StampLayout.material_id == material_id,
                models.StampLayout.layout_type == models.LayoutType.DEFAULT,
            )
            .first()
        )
    return None


def create_layout(
    db:         Session,
    data:       schemas.StampLayoutCreate,
    creator_id: int,
) -> models.StampLayout:
    """
    إنشاء تنسيق أختام جديد لمادة معينة.
    لو فيه تنسيق من نفس النوع موجود للمادة، بنحدثه بدل ما نكرره.
    """
    existing = (
        db.query(models.StampLayout)
        .filter(models.StampLayout.material_id == data.material_id)
        .filter(models.StampLayout.layout_type == data.layout_type)
        .first()
    )

    if existing:
        existing.stamp_elements = [el.model_dump() for el in data.stamp_elements]
        existing.created_by = creator_id
        db.commit()
        db.refresh(existing)
        return existing

    layout = models.StampLayout(
        material_id    = data.material_id,
        created_by     = creator_id,
        layout_type    = data.layout_type,
        stamp_elements = [el.model_dump() for el in data.stamp_elements],
    )
    db.add(layout)
    db.commit()
    db.refresh(layout)
    return layout


def update_layout(
    db:        Session,
    layout_id: int,
    data:      schemas.StampLayoutUpdate,
) -> Optional[models.StampLayout]:
    """تحديث عناصر التنسيق — بنستبدل القائمة كلها"""
    layout = get_layout(db, layout_id)
    if not layout:
        return None
    layout.stamp_elements = [el.model_dump() for el in data.stamp_elements]
    db.commit()
    db.refresh(layout)
    return layout


def clone_layout_as_custom(
    db:         Session,
    layout_id:  int,
    buyer_id:   int,
    new_elements: list[schemas.StampElement],
) -> models.StampLayout:
    """
    نسخ تنسيق الـ Admin وحفظه كـ custom للمشتري.
    بيُستخدم لما العميل يعدّل مواضع الأختام قبل الشراء.
    """
    original = get_layout(db, layout_id)
    custom = models.StampLayout(
        material_id    = original.material_id,
        created_by     = buyer_id,
        layout_type    = models.LayoutType.CUSTOM,
        stamp_elements = [el.model_dump() for el in new_elements],
    )
    db.add(custom)
    db.commit()
    db.refresh(custom)
    return custom


# ================================================================
# CRUD — PURCHASES
# ================================================================

def get_purchase(db: Session, purchase_id: int) -> Optional[models.Purchase]:
    """جلب عملية شراء بالـ ID مع البيانات المرتبطة"""
    return (
        db.query(models.Purchase)
        .options(
            joinedload(models.Purchase.material),
            joinedload(models.Purchase.buyer),
        )
        .filter(models.Purchase.id == purchase_id)
        .first()
    )


def get_user_purchases(
    db:      Session,
    user_id: int,
    skip:    int = 0,
    limit:   int = 20,
) -> tuple[list[models.Purchase], int]:
    """جلب كل مشتريات مستخدم معين"""
    query = (
        db.query(models.Purchase)
        .options(joinedload(models.Purchase.material))
        .filter(models.Purchase.buyer_id == user_id)
        .filter(models.Purchase.is_hidden == False)
        .order_by(models.Purchase.purchased_at.desc())
    )
    total = query.count()
    items = query.offset(skip).limit(limit).all()
    return items, total


def create_purchase(
    db:      Session,
    data:    schemas.PurchaseCreate,
    buyer_id: int,
    amount:  float,
) -> models.Purchase:
    """
    إنشاء عملية شراء جديدة.
    الحالة الأولية: PENDING — تتغير لـ PROCESSING بعد تأكيد الدفع.
    """
    # تنظيف رقم العملية لتجنب تعارض الـ UNIQUE constraint مع النصوص الفارغة في SQLite
    tx_id = (data.transaction_id.strip() or None) if (data.transaction_id and isinstance(data.transaction_id, str)) else None

    series_val = (getattr(data, "series_name", None) or "").strip() or None
    doc_name_val = (series_val or getattr(data, "doc_name_stamp", None) or "").strip() or None

    purchase = models.Purchase(
        buyer_id          = buyer_id,
        material_id       = data.material_id,
        stamp_layout_id   = data.stamp_layout_id,
        buyer_name_stamp  = (data.buyer_name_stamp or "").strip(),
        buyer_phone_stamp = (data.buyer_phone_stamp or "").strip(),
        buyer_phone_stamp_2 = (data.buyer_phone_stamp_2 or "").strip() or None,
        series_name       = series_val,
        watermark_text    = (data.watermark_text or "").strip() or None,
        doc_name_stamp    = doc_name_val,
        watermark_type    = data.watermark_type or "none",
        watermark_logo_path = getattr(data, "watermark_logo_path", None),
        transaction_id    = tx_id,
        payment_method    = data.payment_method,
        amount_paid       = amount,
        status            = models.PurchaseStatus.COMPLETED, # أون-ذا-فلاي: تكتمل العملية فوراً بدون ختم
    )
    db.add(purchase)

    # لو الدفع بالكوينز، نسحبهم فوراً
    if data.payment_method == models.PaymentMethod.COINS:
        update_user_coins(
            db          = db,
            user_id     = buyer_id,
            amount      = -float(amount),
            tx_type     = models.TransactionType.PURCHASE,
            description = f"شراء مادة: {purchase.material_id}",
            material_id = purchase.material_id,
        )

    db.commit()
    db.refresh(purchase)
    return purchase


def update_purchase_status(
    db:          Session,
    purchase_id: int,
    status:      models.PurchaseStatus,
    output_path: Optional[str] = None,
) -> Optional[models.Purchase]:
    """
    تحديث حالة عملية الشراء.
    بيُستدعى من الـ stamping engine بعد الانتهاء من الختم.
    """
    purchase = get_purchase(db, purchase_id)
    if not purchase:
        return None

    purchase.status = status

    # لو اكتمل الختم، نسجل المسار ووقت الاكتمال
    if status == models.PurchaseStatus.COMPLETED:
        purchase.output_pdf_path = output_path
        purchase.completed_at    = datetime.utcnow()

    db.commit()
    db.refresh(purchase)
    return purchase


def hide_purchase(db: Session, purchase_id: int) -> bool:
    """إخفاء عملية الشراء (حذف صوري)"""
    purchase = get_purchase(db, purchase_id)
    if not purchase:
        return False
    purchase.is_hidden = True
    db.commit()
    return True


def record_hidden_mark(
    db:          Session,
    purchase_id: int,
    page_number: int,
    x_pct:       float,
    y_pct:       float,
    mark_code:   Optional[str] = None,
    mark_type:   models.MarkType = models.MarkType.INVISIBLE_TEXT,
) -> models.HiddenMark:
    """
    تسجيل علامة مخفية بعد إضافتها للملف.
    """
    code = mark_code if mark_code else str(uuid.uuid4())
    mark = models.HiddenMark(
        purchase_id = purchase_id,
        mark_code   = code,
        page_number = page_number,
        x_position  = x_pct,
        y_position  = y_pct,
        mark_type   = mark_type,
    )
    db.add(mark)
    db.commit()
    db.refresh(mark)
    return mark


def record_hidden_marks_bulk(db: Session, entries: list[dict]) -> int:
    """تسجيل مجموعة علامات مخفية دفعة واحدة لتفادي تكرار عمليات الحفظ البطيئة."""
    if not entries:
        return 0
    marks = [
        models.HiddenMark(
            purchase_id=e["purchase_id"],
            mark_code=e["mark_code"],
            page_number=e["page_number"],
            x_position=e["x_pct"],
            y_position=e["y_pct"],
            mark_type=e.get("mark_type", models.MarkType.INVISIBLE_TEXT),
        )
        for e in entries
    ]
    db.add_all(marks)
    db.commit()
    return len(marks)


def find_purchase_by_mark_code(db: Session, mark_code: str) -> Optional[models.Purchase]:
    """
    تتبع التسريب — البحث عن عملية الشراء بواسطة كود العلامة المخفية.
    لو لقينا الكود في ملف مسرّب، نعرف مين اشتراه.
    """
    mark = (
        db.query(models.HiddenMark)
        .filter(models.HiddenMark.mark_code == mark_code)
        .first()
    )
    if not mark:
        return None
    return get_purchase(db, mark.purchase_id)


# ================================================================
# CRUD — USER MANAGEMENT (Admin Only Actions)
# ================================================================

def set_user_active_status(db: Session, user_id: int, is_active: bool) -> Optional[models.User]:
    """تفعيل أو تجميد حساب مستخدم"""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user: return None
    user.is_active = is_active
    db.commit()
    db.refresh(user)
    return user

def set_user_role(db: Session, user_id: int, role: models.UserRole) -> Optional[models.User]:
    """تغيير دور المستخدم (مثلاً ترقية لـ Admin)"""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user: return None
    user.role = role
    db.commit()
    db.refresh(user)
    return user


# ================================================================
# CRUD — PREVIEW TOKENS
# ================================================================

def create_preview_token(
    db:          Session,
    material_id: int,
    user_id:     int,
    valid_mins:  int = 30,
) -> models.PreviewToken:
    """
    إنشاء توكن معاينة مؤقت وتطبيق خوارزمية المعاينة الذكية (Smart Preview).
    الـ token عشوائي بالكامل — مش JWT عشان نتحكم في إلغائه.
    """
    # 1. استخراج عدد صفحات المذكرة الكلي (N)
    material = get_material(db, material_id)
    n_pages = material.total_pages if material and material.total_pages else 10

    # 2. حساب عدد صفحات العينة بشكل متناسب:
    #    - 15% من الصفحات الكلية
    #    - حد أدنى: 10 صفحات (أو كل الصفحات لو الملف صغير)
    #    - حد أقصى: 50 صفحة
    raw_count = max(10, int(n_pages * 0.15))
    c_pages   = min(raw_count, 50, n_pages)

    # 3. توليد قائمة الصفحات العشوائية
    import random as _rnd

    # الصفحة الأولى دايماً مسموح بيها (الغلاف)
    allowed_set = {1}

    if n_pages > 1:
        # باقي الصفحات تُختار عشوائياً من كل الملف (من صفحة 2 للأخيرة)
        remaining_pool = list(range(2, n_pages + 1))
        extra_count    = min(c_pages - 1, len(remaining_pool))
        extra_pages    = _rnd.sample(remaining_pool, extra_count)
        allowed_set.update(extra_pages)

    allowed = sorted(allowed_set)

    token = models.PreviewToken(
        material_id   = material_id,
        user_id       = user_id,
        token         = secrets.token_urlsafe(32),  # 32 بايت عشوائي
        allowed_pages = allowed,
        expires_at    = datetime.utcnow() + timedelta(minutes=valid_mins),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def get_valid_preview_token(db: Session, token: str) -> Optional[models.PreviewToken]:
    """
    جلب توكن المعاينة لو كان صالح وغير منتهي.
    بيُستدعى قبل كل عملية معاينة.
    """
    preview = (
        db.query(models.PreviewToken)
        .options(joinedload(models.PreviewToken.material))
        .filter(
            models.PreviewToken.token      == token,
            models.PreviewToken.expires_at >  datetime.utcnow(),  # غير منتهي
        )
        .first()
    )
    return preview


def revoke_preview_token(db: Session, token: str) -> bool:
    """
    إلغاء توكن المعاينة — بنعمله منتهي على طول.
    بيُستدعى بعد انتهاء الجلسة أو تسجيل الخروج.
    """
    preview = db.query(models.PreviewToken).filter(
        models.PreviewToken.token == token
    ).first()
    if not preview:
        return False

    # بنجعل الـ expires_at في الماضي بدل الحذف الكامل
    # عشان نحتفظ بسجل المعاينات للـ analytics
    preview.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    return True


# ================================================================
# CRUD — LAYOUT PRESETS
# ================================================================

def get_presets(db: Session) -> list[models.LayoutPreset]:
    """جلب كل القوالب الجاهزة"""
    return db.query(models.LayoutPreset).order_by(models.LayoutPreset.created_at.desc()).all()


def create_preset(
    db:         Session,
    data:       schemas.LayoutPresetCreate,
    creator_id: int,
) -> models.LayoutPreset:
    """إنشاء قالب أختام جديد"""
    preset = models.LayoutPreset(
        name           = data.name,
        created_by     = creator_id,
        stamp_elements = [el.model_dump() for el in data.stamp_elements],
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return preset


def delete_preset(db: Session, preset_id: int) -> bool:
    """حذف قالب جاهز"""
    preset = db.query(models.LayoutPreset).filter(models.LayoutPreset.id == preset_id).first()
    if not preset:
        return False
    db.delete(preset)
    db.commit()
    return True
# ================================================================
# CRUD — BACKGROUND JOBS
# ================================================================

def create_background_job(
    db:      Session,
    user_id: int,
    job_id:  str,
    label:   str,
) -> models.BackgroundJob:
    """إنشاء سجل وظيفة خلفية جديد"""
    job = models.BackgroundJob(
        user_id = user_id,
        job_id  = job_id,
        label   = label,
        status  = "processing",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def update_background_job(
    db:          Session,
    job_id:      str,
    status:      str,
    output_path: Optional[str] = None,
    error:       Optional[str] = None,
) -> Optional[models.BackgroundJob]:
    """تحديث حالة الوظيفة ونتائجها"""
    job = db.query(models.BackgroundJob).filter(models.BackgroundJob.job_id == job_id).first()
    if not job:
        return None
    
    job.status        = status
    job.output_path   = output_path
    job.error_message = error
    db.commit()
    db.refresh(job)
    return job


def get_user_background_jobs(
    db:      Session,
    user_id: int,
    limit:   int = 10,
) -> list[models.BackgroundJob]:
    """جلب آخر الوظائف لمستخدم معين"""
    return (
        db.query(models.BackgroundJob)
        .filter(models.BackgroundJob.user_id == user_id)
        .order_by(models.BackgroundJob.created_at.desc())
        .limit(limit)
        .all()
    )


def get_background_job_by_id(db: Session, job_id: str) -> Optional[models.BackgroundJob]:
    """جلب تفاصيل وظيفة معينة بالـ UUID"""
    return db.query(models.BackgroundJob).filter(models.BackgroundJob.job_id == job_id).first()


# ================================================================
# CRUD — SUPREME ADMIN SECURITY, DEVICE LIMITS & USER SESSIONS
# ================================================================

def get_supreme_admin(db: Session) -> Optional[models.User]:
    """إرجاع حساب الأدمن الأعلى (المؤسس السيادي أو أقدم أدمن نشط عند عدم تفعيل المؤسس)"""
    founding = (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.ADMIN, models.User.is_founding_admin == True)
        .order_by(models.User.id.asc())
        .first()
    )
    if founding:
        return founding
    return (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.ADMIN, models.User.is_active == True)
        .order_by(models.User.id.asc())
        .first()
    )


def is_supreme_admin(db: Session, user: Optional[models.User]) -> bool:
    """التحقق مما إذا كان المستخدم هو حساب الأدمن الأعلى"""
    if not user or user.role != models.UserRole.ADMIN:
        return False
    if bool(getattr(user, "is_founding_admin", False)):
        return True
    supreme = get_supreme_admin(db)
    return bool(supreme and supreme.id == user.id)


def get_effective_device_limit(user: models.User) -> int:
    """الحد الأقصى للأجهزة المسموح بها للحساب (افتراضياً 5 للأدمن و 2 للعميل)"""
    custom_limit = getattr(user, "max_devices", None)
    if custom_limit is not None and int(custom_limit) >= 1:
        return int(custom_limit)
    return 5 if user.role == models.UserRole.ADMIN else 2


def check_and_register_device_for_login(
    db: Session,
    user: models.User,
    device_id: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[models.UserDevice]]:
    """
    التحقق الموحد من قفل وحظر الأجهزة والحد الأقصى المسموح به قبل إصدار توكن الدخول.
    يُرجع (allowed, error_message, device_obj).
    """
    clean_dev = (device_id or "web-unknown").strip()[:255] or "web-unknown"
    devices = get_user_devices(db, user.id)
    limit = get_effective_device_limit(user)
    existing = next((d for d in devices if d.device_id == clean_dev), None)

    if existing:
        if existing.is_blocked:
            return False, "عذراً، هذا الجهاز محظور من دخول المنصة", existing
    else:
        unblocked_count = sum(1 for d in devices if not d.is_blocked)
        if unblocked_count >= limit:
            unit_label = "جهاز واحد" if limit == 1 else f"{limit} أجهزة"
            return (
                False,
                f"تم الوصول للحد الأقصى للأجهزة المسموح بها لهذا الحساب ({unit_label}). يرجى إنهاء جلسة أو إزالة جهاز من الإعدادات أولاً.",
                None,
            )

    dev_obj = register_user_device(db, user.id, clean_dev, user_agent, ip_address)
    return True, None, dev_obj


def create_user_session(
    db: Session,
    user: models.User,
    device_id: str,
    user_agent: Optional[str] = None,
    ip_address: Optional[str] = None,
    login_surface: str = "web",
) -> models.UserSession:
    """إنشاء جلسة نشطة مرتبطة بالتوكن لإدارة الجلسات وإبطالها لحظياً"""
    now = datetime.utcnow()
    sess = models.UserSession(
        session_id=uuid.uuid4().hex,
        user_id=user.id,
        device_id=(device_id or "web-unknown").strip()[:255] or "web-unknown",
        user_agent=(user_agent or "")[:500] or None,
        ip_address=(ip_address or "")[:100] or None,
        login_surface=(login_surface or "web")[:50],
        is_active=True,
        created_at=now,
        last_seen_at=now,
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def list_active_sessions(db: Session, user_id: int) -> list[models.UserSession]:
    """جلب كافة الجلسات النشطة للمستخدم مرتبة حسب أحدث نشاط"""
    return (
        db.query(models.UserSession)
        .filter(models.UserSession.user_id == user_id, models.UserSession.is_active == True)
        .order_by(models.UserSession.last_seen_at.desc(), models.UserSession.id.desc())
        .all()
    )


def revoke_session(
    db: Session,
    user_id: int,
    session_id: str,
    release_device: bool = True,
    block_device: bool = False,
) -> Optional[models.UserSession]:
    """إنهاء جلسة محددة وإبطال التوكن المرتبط بها فوراً"""
    sess = (
        db.query(models.UserSession)
        .filter(models.UserSession.user_id == user_id, models.UserSession.session_id == session_id)
        .first()
    )
    if not sess:
        return None

    sess.is_active = False
    sess.revoked_at = datetime.utcnow()
    db.flush()

    if block_device:
        dev = (
            db.query(models.UserDevice)
            .filter(models.UserDevice.user_id == user_id, models.UserDevice.device_id == sess.device_id)
            .first()
        )
        if dev:
            dev.is_blocked = True
    elif release_device:
        remaining = (
            db.query(models.UserSession)
            .filter(
                models.UserSession.user_id == user_id,
                models.UserSession.device_id == sess.device_id,
                models.UserSession.is_active == True,
            )
            .count()
        )
        if remaining == 0:
            db.query(models.UserDevice).filter(
                models.UserDevice.user_id == user_id,
                models.UserDevice.device_id == sess.device_id,
                models.UserDevice.is_blocked == False,
            ).delete(synchronize_session=False)

    db.commit()
    return sess


def revoke_all_user_sessions(
    db: Session,
    user_id: int,
    except_session_id: Optional[str] = None,
    release_devices: bool = True,
    except_device_id: Optional[str] = None,
) -> int:
    """إنهاء جميع الجلسات النشطة للمستخدم (ما عدا الجلسة الحالية إن طُلب)"""
    now = datetime.utcnow()
    q = db.query(models.UserSession).filter(
        models.UserSession.user_id == user_id,
        models.UserSession.is_active == True,
    )
    if except_session_id:
        q = q.filter(models.UserSession.session_id != except_session_id)

    sessions_to_revoke = q.all()
    for sess in sessions_to_revoke:
        sess.is_active = False
        sess.revoked_at = now
    db.flush()

    if release_devices:
        active_dev_ids = {
            row[0]
            for row in db.query(models.UserSession.device_id)
            .filter(models.UserSession.user_id == user_id, models.UserSession.is_active == True)
            .all()
        }
        if except_device_id:
            active_dev_ids.add(except_device_id)

        dev_q = db.query(models.UserDevice).filter(
            models.UserDevice.user_id == user_id,
            models.UserDevice.is_blocked == False,
        )
        if active_dev_ids:
            dev_q = dev_q.filter(~models.UserDevice.device_id.in_(active_dev_ids))
        dev_q.delete(synchronize_session=False)

    db.commit()
    return len(sessions_to_revoke)


def enforce_device_limit_on_update(
    db: Session,
    user: models.User,
    new_limit: int,
    current_device_id: Optional[str] = None,
) -> int:
    """تطبيق الحد الجديد للأجهزة فوراً وطرد الأجهزة/الجلسات الأقدم الزائدة"""
    devices = (
        db.query(models.UserDevice)
        .filter(models.UserDevice.user_id == user.id, models.UserDevice.is_blocked == False)
        .order_by(models.UserDevice.last_login.desc(), models.UserDevice.id.desc())
        .all()
    )
    if len(devices) <= new_limit:
        return 0

    kept_ids: list[str] = []
    if current_device_id and any(d.device_id == current_device_id for d in devices):
        kept_ids.append(current_device_id)

    for d in devices:
        if len(kept_ids) >= new_limit:
            break
        if d.device_id not in kept_ids:
            kept_ids.append(d.device_id)

    excess_dev_ids = [d.device_id for d in devices if d.device_id not in kept_ids]
    if not excess_dev_ids:
        return 0

    now = datetime.utcnow()
    db.query(models.UserSession).filter(
        models.UserSession.user_id == user.id,
        models.UserSession.device_id.in_(excess_dev_ids),
        models.UserSession.is_active == True,
    ).update({"is_active": False, "revoked_at": now}, synchronize_session=False)

    db.query(models.UserDevice).filter(
        models.UserDevice.user_id == user.id,
        models.UserDevice.device_id.in_(excess_dev_ids),
    ).delete(synchronize_session=False)

    db.commit()
    return len(excess_dev_ids)


# ================================================================
# CRUD — UPCOMING MATERIALS, TEACHER QUEUE/ROSTER & BATCH COINS
# ================================================================

def _normalize_match_text(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")).strip().lower()
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)
    s = re.sub(r"[أإآ]", "ا", s).replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"[^\w\s\u0600-\u06FF]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_match_phone(val: Optional[str]) -> str:
    if not val:
        return ""
    s = str(val).translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")).strip()
    digits = re.sub(r"\D+", "", s)
    if digits.startswith("0020") and len(digits) >= 14:
        digits = "0" + digits[4:]
    elif digits.startswith("20") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif len(digits) == 10 and digits.startswith("1"):
        digits = "0" + digits
    return digits


def normalize_teacher_fingerprint(
    teacher_name: Optional[str],
    phone: Optional[str],
    series_name: Optional[str],
) -> str:
    p = _normalize_match_phone(phone)
    n = _normalize_match_text(teacher_name)
    s = _normalize_match_text(series_name)
    if p or n or s:
        return f"{p}|{n}|{s}"[:250]
    return f"anon-{uuid.uuid4().hex[:12]}"


def upsert_material_teacher_entry(
    db: Session,
    material_id: int,
    teacher_name: Optional[str] = None,
    phone: Optional[str] = None,
    phone_2: Optional[str] = None,
    series_name: Optional[str] = None,
    watermark_text: Optional[str] = None,
    watermark_type: str = "none",
    watermark_logo_path: Optional[str] = None,
    font_family: Optional[str] = None,
    font_size: Optional[float] = None,
    override_frame_path: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    telegram_user_id: Optional[str] = None,
    status: str = "waiting_release",
    last_order_code: Optional[str] = None,
    error_message: Optional[str] = None,
    entry_id: Optional[int] = None,
) -> models.MaterialTeacherEntry:
    """حفظ أو تحديث بيانات مدرس في سجل المذكرة (سواء في قائمة الانتظار أو السجل الدائم لإعادة الختم)"""
    fp = normalize_teacher_fingerprint(teacher_name, phone, series_name)
    existing = None
    if entry_id:
        existing = (
            db.query(models.MaterialTeacherEntry)
            .filter(
                models.MaterialTeacherEntry.id == int(entry_id),
                models.MaterialTeacherEntry.material_id == material_id,
            )
            .first()
        )
    if not existing and fp:
        existing = (
            db.query(models.MaterialTeacherEntry)
            .filter(
                models.MaterialTeacherEntry.material_id == material_id,
                models.MaterialTeacherEntry.stamp_fingerprint == fp,
            )
            .first()
        )

    now = datetime.utcnow()
    clean_wm_type = (watermark_type or ("logo" if watermark_logo_path else ("text" if watermark_text else "none"))).strip().lower()
    if existing:
        if teacher_name is not None:
            existing.teacher_name = teacher_name.strip() or existing.teacher_name
        if phone is not None:
            existing.phone = phone.strip() or existing.phone
        if phone_2 is not None:
            existing.phone_2 = phone_2.strip() or None
        if series_name is not None:
            existing.series_name = series_name.strip() or None
        existing.watermark_type = clean_wm_type
        if clean_wm_type == "logo" and watermark_logo_path:
            existing.watermark_logo_path = watermark_logo_path
            existing.watermark_text = None
        elif clean_wm_type == "text":
            existing.watermark_text = watermark_text
            existing.watermark_logo_path = None
        else:
            existing.watermark_text = None
            existing.watermark_logo_path = None
        if font_family is not None:
            existing.font_family = font_family
        if font_size is not None:
            existing.font_size = float(font_size)
        if override_frame_path is not None:
            existing.override_frame_path = override_frame_path
        if telegram_chat_id:
            existing.telegram_chat_id = str(telegram_chat_id)
        if telegram_user_id:
            existing.telegram_user_id = str(telegram_user_id)
        existing.status = status
        if last_order_code:
            existing.last_order_code = last_order_code
        existing.error_message = error_message
        existing.stamp_fingerprint = normalize_teacher_fingerprint(
            existing.teacher_name, existing.phone, existing.series_name
        )
        existing.updated_at = now
        if status == "completed":
            existing.last_stamped_at = now
        db.commit()
        db.refresh(existing)
        return existing

    entry = models.MaterialTeacherEntry(
        material_id=material_id,
        teacher_name=(teacher_name or "").strip() or None,
        phone=(phone or "").strip() or None,
        phone_2=(phone_2 or "").strip() or None,
        series_name=(series_name or "").strip() or None,
        watermark_text=None if clean_wm_type == "logo" else ((watermark_text or "").strip() or None),
        watermark_type=clean_wm_type,
        watermark_logo_path=watermark_logo_path if clean_wm_type == "logo" else None,
        font_family=font_family,
        font_size=float(font_size) if font_size is not None else None,
        override_frame_path=override_frame_path,
        telegram_chat_id=str(telegram_chat_id) if telegram_chat_id else None,
        telegram_user_id=str(telegram_user_id) if telegram_user_id else None,
        status=status,
        last_order_code=last_order_code,
        error_message=error_message,
        stamp_fingerprint=fp,
        created_at=now,
        updated_at=now,
        last_stamped_at=now if status == "completed" else None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_material_teacher_entries(
    db: Session,
    material_id: int,
    status: Optional[str] = None,
) -> list[models.MaterialTeacherEntry]:
    q = db.query(models.MaterialTeacherEntry).filter(models.MaterialTeacherEntry.material_id == material_id)
    if status:
        q = q.filter(models.MaterialTeacherEntry.status == status)
    return q.order_by(models.MaterialTeacherEntry.id.asc()).all()


def count_material_teacher_entries(db: Session, material_id: int) -> tuple[int, int]:
    """يُرجع (عدد المنتظرين للصدور، عدد المختومين المحفوظين)"""
    rows = (
        db.query(models.MaterialTeacherEntry.status)
        .filter(models.MaterialTeacherEntry.material_id == material_id)
        .all()
    )
    waiting = sum(1 for (st,) in rows if st == "waiting_release")
    completed = sum(1 for (st,) in rows if st == "completed")
    return waiting, completed


def upsert_upcoming_material(
    db: Session,
    title: str,
    uploader_id: int,
    description: Optional[str] = None,
    tag: Optional[str] = None,
    price: Optional[float] = None,
    price_coins: Optional[float] = None,
    is_published: bool = True,
) -> tuple[models.Material, bool]:
    """
    إنشاء أو تحديث مذكرة قادمة (تشويق/حجز بدون ملف PDF بعد) أو مطابقة مذكرة قائمة مع الحفاظ على سعر المنصة إذا تُرك السعر فارغاً.
    يُرجع (material, created_bool).
    """
    clean_title = (title or "").strip()[:200]
    norm_target = _normalize_match_text(clean_title)
    existing = None
    if norm_target:
        all_mats = db.query(models.Material).order_by(models.Material.id.asc()).all()
        for m in all_mats:
            if _normalize_match_text(m.title) == norm_target:
                existing = m
                break

    if existing:
        changed = False
        clean_desc = (description or "").strip() if description is not None else ""
        if clean_desc and (not existing.description or existing.is_coming_soon):
            existing.description = clean_desc
            changed = True
        clean_tag = (tag or "").strip() if tag is not None else ""
        if clean_tag and (not existing.tag or existing.tag == "مذكرات عامة" or existing.is_coming_soon):
            existing.tag = clean_tag[:100]
            changed = True
        # لو تم تمرير سعر صريح في الإكسيل للمذكرة القادمة/الجديدة، نطبّقه؛ وإن تُرك فارغاً نُبقي سعر المنصة كما هو
        if price is not None and (existing.is_coming_soon or float(existing.price or 0) == 0):
            existing.price = float(price)
            if price_coins is None and (existing.is_coming_soon or float(existing.price_coins or 0) == 0):
                existing.price_coins = float(price)
            changed = True
        if price_coins is not None and (existing.is_coming_soon or float(existing.price_coins or 0) == 0):
            existing.price_coins = float(price_coins)
            changed = True
        if existing.is_coming_soon and is_published and not existing.is_published:
            existing.is_published = True
            changed = True
        if changed:
            db.commit()
            db.refresh(existing)
        return existing, False

    eff_price = float(price) if price is not None else (float(price_coins) if price_coins is not None else 0.0)
    eff_coins = float(price_coins) if price_coins is not None else eff_price
    eff_desc = (description or "").strip() or None
    eff_tag = (tag or "").strip()[:100] if tag else "مذكرات عامة"

    mat = models.Material(
        uploaded_by=uploader_id,
        title=clean_title,
        tag=eff_tag,
        description=eff_desc,
        price=eff_price,
        price_coins=eff_coins,
        original_pdf_path="",
        total_pages=0,
        is_published=is_published,
        is_coming_soon=True,
        layout_confirmed=False,
        compression_enabled=True,
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)
    return mat, True


def match_user_for_coin_recharge(
    db: Session,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    user_id: Optional[int] = None,
    name: Optional[str] = None,
) -> tuple[Optional[models.User], str]:
    """مطابقة دقيقة لحساب المستخدم عند شحن الكوينز عبر ملف Excel/Word."""
    if user_id is not None:
        u = get_user_by_id(db, int(user_id))
        if u:
            return u, "exact_id"

    clean_email = (email or "").strip().lower()
    if clean_email:
        all_users = db.query(models.User).all()
        u = next((x for x in all_users if (x.email or "").strip().lower() == clean_email), None)
        if u:
            return u, "email"

    clean_phone = _normalize_match_phone(phone)
    if clean_phone:
        tail = clean_phone[-10:] if len(clean_phone) >= 10 else clean_phone
        users = db.query(models.User).all()
        for u in users:
            up = _normalize_match_phone(u.phone)
            if up and (up == clean_phone or (tail and up.endswith(tail))):
                return u, "phone"
            em_local = (u.email or "").split("@")[0]
            em_digits = _normalize_match_phone(em_local)
            if em_digits and (em_digits == clean_phone or (tail and em_digits.endswith(tail))):
                return u, "phone_email"

    norm_name = _normalize_match_text(name)
    if norm_name:
        users = db.query(models.User).all()
        matches = [u for u in users if _normalize_match_text(u.full_name) == norm_name]
        if len(matches) == 1:
            return matches[0], "exact_name"
        if len(matches) > 1:
            return None, "ambiguous_name"

    return None, "not_found"

