# ================================================================
# bot_service/states/order_flow.py — آلة الحالات المنتهية (FSM)
# ================================================================

from aiogram.fsm.state import State, StatesGroup

class TeacherProfileStates(StatesGroup):
    """حالات تسجيل وتعديل الملف التعريفي للمدرس"""
    waiting_name = State()
    waiting_phone_1 = State()
    waiting_phone_2 = State()
    waiting_subject = State()
    waiting_series = State()
    waiting_watermark = State()

class OrderWizardStates(StatesGroup):
    """حالات معالج طلب المذكرة وتخصيص الأختام"""
    confirming_profile = State()
    custom_name = State()
    custom_phone_1 = State()
    custom_phone_2 = State()
    custom_series = State()
    custom_watermark = State()
    confirming_order = State()

class PaymentStates(StatesGroup):
    """حالات تسجيل التحويل ورفع الإيصال"""
    waiting_claimed_phone = State()
    waiting_receipt_photo = State()

class SupportStates(StatesGroup):
    """حالات خدمة العملاء والدعم الفني"""
    waiting_inquiry = State()
    admin_replying = State()

class AdminVIPStates(StatesGroup):
    """حالات توليد الأختام الفورية للمشرفين (VIP Direct Generator)"""
    waiting_material_id = State()
    waiting_teacher_name = State()
    waiting_phone = State()
    waiting_phone_2 = State()
    waiting_series = State()
    waiting_watermark = State()

class AdminSettingsStates(StatesGroup):
    """حالات تحديث إعدادات الدفع والمحافظ والتطبيق المصغر"""
    waiting_new_wallet = State()
    waiting_new_instapay = State()
    waiting_new_miniapp = State()

class AdminMaterialManageStates(StatesGroup):
    """حالات إدارة المذكرات ورفع ملفات جديدة"""
    waiting_new_price = State()
    uploading_pdf = State()
    waiting_material_title = State()
    waiting_material_tag = State()
    waiting_material_price = State()
    waiting_material_description = State()
    waiting_material_cover = State()
    waiting_material_frame = State()
    waiting_frame_image = State()
    waiting_replace_pdf = State()

class AdminFontSettingsStates(StatesGroup):
    """حالات ضبط الخطوط والأحجام والإحداثيات للمذكرة من البوت"""
    waiting_custom_font_size = State()

class AdminBatchStampStates(StatesGroup):
    """حالات معالجة دفعات الإكسيل والوورد من البوت بدون استهلاك معالج"""
    reviewing_batch = State()
    waiting_batch_frame = State()
    waiting_batch_text = State()
    waiting_batch_logo = State()


class AdminMaterialRosterStates(StatesGroup):
    """حالات استعراض واختيار مدرسين محددين لإعادة الختم من سجل المذكرة"""
    selecting_teachers = State()

class AdminBroadcastStates(StatesGroup):
    """حالات الإذاعة العامة لجميع المدرسين"""
    waiting_broadcast_message = State()


class AdminManageStates(StatesGroup):
    """حالات إضافة مشرف جديد وتحديد صلاحياته"""
    waiting_new_admin_target = State()
    customizing_new_admin_perms = State()


class BundleCartStates(StatesGroup):
    """حالات سلة شراء عدة مذكرات معاً عبر البوت"""
    selecting_items = State()
    waiting_stamp_name = State()
    waiting_stamp_phone = State()
    waiting_from_wallet = State()
    waiting_receipt_photo = State()


class OtpVerificationStates(StatesGroup):
    """حالات التحقق من رقم الهاتف لاستلام رمز OTP الموحد"""
    waiting_contact = State()





