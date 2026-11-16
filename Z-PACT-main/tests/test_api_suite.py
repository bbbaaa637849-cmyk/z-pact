# ================================================================
# tests/test_api_suite.py — حزمة الاختبارات الشاملة لنظام Z-PACT
#
# تغطي هذه الحزمة:
#   1. دورة التوثيق وتسجيل الدخول والتسجيل
#   2. اختبارات الأمان وحماية الصلاحيات (منع ثغرة IDOR في المشتريات)
#   3. إدارة وتتبع الأجهزة وبصمة المتصفح والحد الأقصى
#   4. أمان العملات وصلاحيات لوحة التحكم
#   5. استخراج وتضمين العلامة المائية غير المرئية (DCT Steganography)
#   6. دوال التنسيق للمدرسين وأرقام الهواتف
# ================================================================

import os
import sys
import tempfile
import unittest
import numpy as np
import cv2

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, User, UserRole, Material, Purchase, LayoutType, StampLayout, PurchaseStatus
from app.database import get_db
import app.database as app_db
from app.crud import hash_password
from app.auth import create_access_token
from app.formatting import format_instructor_name, format_contact_phone, clean_phone_number
from app.stamper import _get_smart_text
from app.watermark_dct import embed_dct_watermark, extract_dct_watermark
from main import app


class TestZPACTComprehensiveSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create a temporary directory for isolated test database
        try:
            cls.test_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:
            cls.test_dir = tempfile.TemporaryDirectory()

        cls.test_db_path = os.path.join(cls.test_dir.name, "test_z_pact.db")
        cls.test_engine = create_engine(
            f"sqlite:///{cls.test_db_path}",
            connect_args={"check_same_thread": False}
        )
        cls.TestingSessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=cls.test_engine
        )

        # Build schema
        Base.metadata.create_all(bind=cls.test_engine)

        # Patch app_db.SessionLocal
        cls.original_session_local = app_db.SessionLocal
        app_db.SessionLocal = cls.TestingSessionLocal

        # Override get_db dependency
        def override_get_db():
            db = cls.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

        # Seed initial admin user
        with cls.TestingSessionLocal() as db:
            admin_user = User(
                email="admin_primary@example.com",
                password_hash=hash_password("adminSecret123!"),
                full_name="المدير العام للمنصة",
                role=UserRole.ADMIN,
                is_active=True,
                coins=9999
            )
            db.add(admin_user)
            db.commit()

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        app_db.SessionLocal = cls.original_session_local
        cls.test_engine.dispose()
        try:
            cls.test_dir.cleanup()
        except Exception:
            pass

    def setUp(self):
        self.db = self.TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    # -------------------------------------------------------------
    # 1. اختبارات التوثيق وتسجيل الدخول
    # -------------------------------------------------------------
    def test_01_authentication_flow(self):
        """تسجيل حساب جديد ثم تسجيل الدخول والتحقق من التوكن"""
        # 1. تسجيل طالب جديد
        reg_payload = {
            "email": "student_alpha@example.com",
            "password": "SecurePassword123!",
            "full_name": "أحمد الطالب الأول"
        }
        res_reg = self.client.post("/api/auth/register", json=reg_payload)
        self.assertEqual(res_reg.status_code, 201, f"Registration failed: {res_reg.text}")
        data = res_reg.json()
        self.assertEqual(data["email"], "student_alpha@example.com")
        self.assertEqual(data["role"], "customer")

        # 2. تكرار التسجيل بنفس الإيميل يجب أن يعيد 400
        res_dup = self.client.post("/api/auth/register", json=reg_payload)
        self.assertEqual(res_dup.status_code, 400)

        # 3. تسجيل الدخول بكلمة مرور خاطئة -> 401
        res_bad_pw = self.client.post(
            "/api/auth/login",
            json={
                "email": "student_alpha@example.com",
                "password": "WrongPassword!",
                "device_id": "device_101"
            }
        )
        self.assertEqual(res_bad_pw.status_code, 401)

        # 4. قبل موافقة الإدارة: الحساب الجديد يكون pending ويُمنع الدخول العشوائي (403)
        res_pending = self.client.post(
            "/api/auth/login",
            json={
                "email": "student_alpha@example.com",
                "password": "SecurePassword123!",
                "device_id": "device_101"
            }
        )
        self.assertEqual(res_pending.status_code, 403)

        # 5. تفعيل واعتماد الحساب من قِبل الإدارة ثم تسجيل الدخول -> 200 + access_token
        pending_user = self.db.query(User).filter(User.email == "student_alpha@example.com").first()
        pending_user.approval_status = "approved"
        self.db.commit()

        res_login = self.client.post(
            "/api/auth/login",
            json={
                "email": "student_alpha@example.com",
                "password": "SecurePassword123!",
                "device_id": "device_101"
            },
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        self.assertEqual(res_login.status_code, 200)
        login_data = res_login.json()
        self.assertIn("access_token", login_data)
        self.assertEqual(login_data["user"]["email"], "student_alpha@example.com")

    # -------------------------------------------------------------
    # 2. اختبارات الأمان وحماية الصلاحيات (IDOR Protection)
    # -------------------------------------------------------------
    def test_02_purchase_idor_security(self):
        """
        التحقق الصارم من حماية تفاصيل الشراء ضد تسريب IDOR:
        - المستخدم A يملك العملية.
        - المستخدم B يحاول استعراض تفاصيل العملية -> يجب أن يحصل على 403 Forbidden.
        - المدير (Admin) مسموح له باستعراض تفاصيل العملية.
        """
        # إنشاء مستخدم A ومستخدم B
        pw_hash = hash_password("pass12345")
        user_a = User(
            email="buyer_a@example.com",
            password_hash=pw_hash,
            full_name="مشتري أ",
            role=UserRole.CUSTOMER,
            is_active=True,
            coins=100
        )
        user_b = User(
            email="buyer_b@example.com",
            password_hash=pw_hash,
            full_name="مشتري ب",
            role=UserRole.CUSTOMER,
            is_active=True,
            coins=100
        )
        self.db.add_all([user_a, user_b])
        self.db.commit()
        self.db.refresh(user_a)
        self.db.refresh(user_b)

        admin_user = self.db.query(User).filter(User.role == UserRole.ADMIN).first()

        # إنشاء مذكرة
        material = Material(
            title="مذكرة التفوق في الكيمياء",
            uploaded_by=admin_user.id,
            price=15.0,
            price_coins=15.0,
            original_pdf_path="storage/originals/chem.pdf",
            total_pages=60,
            is_published=True
        )
        self.db.add(material)
        self.db.commit()
        self.db.refresh(material)

        # إنشاء عملية شراء لمستخدم A
        purchase = Purchase(
            buyer_id=user_a.id,
            material_id=material.id,
            buyer_name_stamp="محمود عبد العزيز",
            buyer_phone_stamp="01098765432",
            watermark_text="نسخة مرخصة",
            amount_paid=15.0,
            status="completed"
        )
        self.db.add(purchase)
        self.db.commit()
        self.db.refresh(purchase)

        # توليد JWT Tokens
        token_a = create_access_token(user_id=user_a.id, role=user_a.role.value)
        token_b = create_access_token(user_id=user_b.id, role=user_b.role.value)
        token_admin = create_access_token(user_id=admin_user.id, role=admin_user.role.value)

        # 1. مستخدم A يستعلم عن مشترياته الخاصة -> 200 OK
        res_a = self.client.get(
            f"/api/purchases/{purchase.id}",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(res_a.json()["id"], purchase.id)
        self.assertEqual(res_a.json()["buyer_id"], user_a.id)

        # 2. مستخدم B يستعلم عن مشتريات A -> 403 Forbidden لمنع تسريب الـ IDOR
        res_b = self.client.get(
            f"/api/purchases/{purchase.id}",
            headers={"Authorization": f"Bearer {token_b}"}
        )
        self.assertEqual(
            res_b.status_code, 
            403, 
            f"Critical IDOR vulnerability! Expected 403, got {res_b.status_code}"
        )
        self.assertIn("ليس لديك صلاحية", res_b.json().get("detail", ""))

        # 3. المدير يستعلم عن العملية -> 200 OK
        res_admin = self.client.get(
            f"/api/purchases/{purchase.id}",
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.json()["id"], purchase.id)

    # -------------------------------------------------------------
    # 3. اختبارات إدارة وتتبع الأجهزة وبصمة المتصفح
    # -------------------------------------------------------------
    def test_03_device_fingerprinting_and_admin_management(self):
        """تسجيل الأجهزة، التحقق من الحظر، وإعادة التعيين بواسطة المسؤول"""
        student = self.db.query(User).filter(User.email == "student_alpha@example.com").first()
        admin = self.db.query(User).filter(User.role == UserRole.ADMIN).first()
        admin_token = create_access_token(user_id=admin.id, role=admin.role.value)

        # استعراض أجهزة الطالب من لوحة الإدارة
        res_devs = self.client.get(
            f"/api/users/admin/{student.id}/devices",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(res_devs.status_code, 200)
        devices = res_devs.json()
        self.assertTrue(len(devices) >= 1)
        device_record_id = devices[0]["id"]
        self.assertEqual(devices[0]["device_id"], "device_101")

        # تبديل حالة حظر الجهاز إلى محظور
        res_toggle = self.client.post(
            f"/api/users/admin/devices/{device_record_id}/toggle-block",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(res_toggle.status_code, 200)
        self.assertIn("محظور", res_toggle.json()["message"])

        # محاولة الطالب تسجيل الدخول من هذا الجهاز المحظور يجب أن تفشل مع 403
        res_blocked_login = self.client.post(
            "/api/auth/login",
            json={
                "email": "student_alpha@example.com",
                "password": "SecurePassword123!",
                "device_id": "device_101"
            }
        )
        self.assertEqual(res_blocked_login.status_code, 403)
        self.assertIn("محظور", res_blocked_login.json()["detail"])

        # إعادة تعيين وحذف جميع أجهزة الطالب بواسطة الأدمن
        res_reset = self.client.delete(
            f"/api/users/admin/{student.id}/devices",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(res_reset.status_code, 200)

        # التحقق من خلو قائمة الأجهزة المسجلة
        res_check = self.client.get(
            f"/api/users/admin/{student.id}/devices",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertEqual(len(res_check.json()), 0)

    # -------------------------------------------------------------
    # 4. اختبارات أمان العملات وباقات الصرف
    # -------------------------------------------------------------
    def test_04_role_based_access_control(self):
        """التأكد من منع الطلاب من الترقية لمدير أو تعديل باقات الصرف"""
        student = self.db.query(User).filter(User.email == "buyer_a@example.com").first()
        student_token = create_access_token(user_id=student.id, role=student.role.value)
        admin = self.db.query(User).filter(User.role == UserRole.ADMIN).first()
        admin_token = create_access_token(user_id=admin.id, role=admin.role.value)

        # محاولة طالب ترقية مستخدم لمدير
        res_prom = self.client.post(
            f"/api/coins/admin/users/{student.id}/make-admin",
            headers={"Authorization": f"Bearer {student_token}"}
        )
        self.assertEqual(res_prom.status_code, 403)

        # محاولة طالب إضافة باقة صرف
        level_data = {
            "threshold": 300.0,
            "reward_coins": 25.0
        }
        res_lvl_student = self.client.post(
            "/api/coins/admin/spending-levels",
            json=level_data,
            headers={"Authorization": f"Bearer {student_token}"}
        )
        self.assertEqual(res_lvl_student.status_code, 403)

        # المسؤول يمكنه إنشاء الباقة بنجاح
        res_lvl_admin = self.client.post(
            "/api/coins/admin/spending-levels",
            json=level_data,
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        self.assertIn(res_lvl_admin.status_code, [200, 201])

    # -------------------------------------------------------------
    # 5. اختبار العلامة المائية الرقمية غير المرئية (DCT Watermark)
    # -------------------------------------------------------------
    def test_05_dct_steganography_watermark(self):
        """تضمين رقم الشراء داخل مصفوفة التردد واستخراجه بدقة 100%"""
        test_img_path = os.path.join(self.test_dir.name, "test_dct_sample.png")

        # صورة اختبارية 256x256
        img = np.zeros((256, 256, 3), dtype=np.uint8)
        for r in range(256):
            img[r, :, 0] = int(r * 0.9)
            img[r, :, 1] = 140
            img[r, :, 2] = int(255 - r * 0.7)
        cv2.imwrite(test_img_path, img)

        purchase_id_to_hide = 554433

        # تضمين الختم
        embed_dct_watermark(test_img_path, purchase_id_to_hide, delta=16.0)

        # استخراج الختم
        recovered_id = extract_dct_watermark(test_img_path)
        self.assertEqual(
            recovered_id, 
            purchase_id_to_hide, 
            f"DCT extraction failed! Expected {purchase_id_to_hide}, got {recovered_id}"
        )

    # -------------------------------------------------------------
    # 6. اختبارات دوال التنسيق
    # -------------------------------------------------------------
    def test_06_formatting_utilities(self):
        """التحقق من توحيد صيغ الأسماء وأرقام الهواتف"""
        self.assertEqual(format_instructor_name("أ. وائل"), "أ / وائل")
        self.assertEqual(format_instructor_name("الاستاذ وائل"), "أ / وائل")
        self.assertEqual(format_instructor_name("مهندس كريم"), "مهندس كريم")
        self.assertEqual(format_instructor_name("دكتور نبيل"), "دكتور نبيل")
        self.assertEqual(format_contact_phone("01155443322"), "ت / 01155443322")
        self.assertEqual(format_contact_phone("هاتف / 01012345678"), "ت / 01012345678")

        # اختبار استخراج أرقام الهواتف لصفحة الغلاف بدون "ت /" أو "ت \"
        self.assertEqual(clean_phone_number("01012345678"), "01012345678")
        self.assertEqual(clean_phone_number("ت / 01012345678"), "01012345678")
        self.assertEqual(clean_phone_number("ت \\ 01012345678"), "01012345678")
        self.assertEqual(clean_phone_number("ت. 01012345678"), "01012345678")
        self.assertEqual(clean_phone_number("هاتف: 01012345678"), "01012345678")

        # اختبار النصوص الذكية للعلامة المائية واسم المذكرة (كلمة واحدة vs أكثر من كلمة)
        self.assertEqual(_get_smart_text("لا للنشر", "", "الرياضيات"), "لا للنشر")
        self.assertEqual(_get_smart_text("لا للنشر", "سلسلة", "الرياضيات"), "لا للنشر")
        self.assertEqual(_get_smart_text("ممنوع تداول المذكرة", "", "الفيزياء"), "ممنوع تداول المذكرة")
        self.assertEqual(_get_smart_text("تفوق", "سلسلة", "الرياضيات"), "سلسلة تفوق في الرياضيات")
        self.assertEqual(_get_smart_text("تفوق", "", "الرياضيات"), "تفوق في الرياضيات")

    # -------------------------------------------------------------
    # 7. اختبار عمليات الشراء المتتالية وتنسيق صفحة الغلاف
    # -------------------------------------------------------------
    def test_07_repeated_purchases_and_cover_stamp(self):
        """
        التحقق من:
        1. نجاح عمليات الشراء المتتالية لنفس المستخدم دون توقف أو أخطاء تكرار.
        2. ختم صفحة الغلاف برقم الهاتف النقي فقط بدون سوابق.
        """
        pw_hash = hash_password("pass12345")
        buyer = User(
            email="repeat_buyer@example.com",
            password_hash=pw_hash,
            full_name="مشتري مكرر",
            role=UserRole.CUSTOMER,
            is_active=True,
            coins=200
        )
        self.db.add(buyer)
        self.db.commit()
        self.db.refresh(buyer)

        admin_user = self.db.query(User).filter(User.role == UserRole.ADMIN).first()

        import fitz
        dummy_pdf_path = os.path.join(self.test_dir.name, "bio.pdf")
        test_pdf_doc = fitz.open()
        test_pdf_doc.new_page()
        test_pdf_doc.new_page()
        test_pdf_doc.save(dummy_pdf_path)
        test_pdf_doc.close()

        material = Material(
            title="مذكرة الأحياء الثانوية",
            uploaded_by=admin_user.id,
            price=20.0,
            price_coins=20.0,
            original_pdf_path=dummy_pdf_path,
            total_pages=2,
            is_published=True
        )
        self.db.add(material)
        self.db.commit()
        self.db.refresh(material)

        layout = StampLayout(
            material_id=material.id,
            created_by=admin_user.id,
            layout_type=LayoutType.DEFAULT,
            stamp_elements=[
                {"type": "name", "page": 0, "x_pct": 10, "y_pct": 10, "font_size": 12},
                {"type": "phone", "page": 0, "x_pct": 10, "y_pct": 15, "font_size": 12}
            ]
        )
        self.db.add(layout)
        self.db.commit()

        token = create_access_token(user_id=buyer.id, role=buyer.role.value)
        headers = {"Authorization": f"Bearer {token}"}

        # عملية الشراء الأولى (بالكوينز)
        p1_data = {
            "material_id": material.id,
            "buyer_name_stamp": "أحمد إبراهيم",
            "buyer_phone_stamp": "ت / 01011112222",
            "watermark_type": "text",
            "payment_method": "coins",
            "transaction_id": None
        }
        res1 = self.client.post("/api/purchases", json=p1_data, headers=headers)
        self.assertEqual(res1.status_code, 201)

        # عملية الشراء الثانية (لنفس المستخدم - كوينز بدون أي خطأ تعارض)
        p2_data = {
            "material_id": material.id,
            "buyer_name_stamp": "أحمد إبراهيم",
            "buyer_phone_stamp": "01011112222",
            "watermark_type": "text",
            "payment_method": "coins",
            "transaction_id": None
        }
        res2 = self.client.post("/api/purchases", json=p2_data, headers=headers)
        self.assertEqual(res2.status_code, 201)

        # التحقق من خصم الكوينز للمرتين (40 كوينز)
        self.db.refresh(buyer)
        self.assertEqual(float(buyer.coins), 160.0)

        # التحقق من أن رقم الهاتف على الغلاف يستخرج نقياً
        raw_phone_input = "ت \\ 01011112222"
        cover_phone = clean_phone_number(raw_phone_input)
        self.assertEqual(cover_phone, "01011112222")
        self.assertNotIn("ت", cover_phone)
        self.assertNotIn("\\", cover_phone)
        self.assertNotIn("/", cover_phone)

    def test_08_material_reviews_and_ratings(self):
        """اختبار دورة التقييمات والمراجعات: القيود والحسابات والجلب"""
        # 1. إنشاء مستخدم ومادة
        admin_user = self.db.query(User).filter(User.role == UserRole.ADMIN).first()
        reviewer = User(
            full_name="الأستاذ مصطفى كامل",
            email="teacher_mustafa@example.com",
            phone="01099998888",
            password_hash=hash_password("RevPass123!"),
            role=UserRole.CUSTOMER,
            coins=50.0
        )
        material = Material(
            title="مذكرة التفوق في الجيولوجيا",
            uploaded_by=admin_user.id,
            price=15.0,
            price_coins=15.0,
            original_pdf_path="storage/originals/geo.pdf",
            total_pages=30,
            is_published=True
        )
        self.db.add_all([reviewer, material])
        self.db.commit()

        token = create_access_token(user_id=reviewer.id, role=reviewer.role.value)
        headers = {"Authorization": f"Bearer {token}"}

        # 2. جلب التقييمات لمادة جديدة (يجب أن يكون فارغاً)
        res_empty = self.client.get(f"/api/materials/{material.id}/reviews")
        self.assertEqual(res_empty.status_code, 200)
        data_empty = res_empty.json()
        self.assertEqual(data_empty["total_count"], 0)
        self.assertEqual(data_empty["avg_overall"], 0.0)

        # 3. محاولة كتابة مراجعة قبل الشراء (يجب أن يرفض 403)
        rev_data = {
            "reviewer_title": "مدرس أول جيولوجيا",
            "content_quality_rating": 5,
            "print_formatting_rating": 4,
            "stamp_appearance_rating": 5,
            "comment": "مذكرة ممتازة وتنسيق رائع"
        }
        res_fail = self.client.post(f"/api/materials/{material.id}/reviews", json=rev_data, headers=headers)
        self.assertEqual(res_fail.status_code, 403)

        # 4. محاكاة شراء المادة وإكمالها
        purchase = Purchase(
            buyer_id=reviewer.id,
            material_id=material.id,
            buyer_name_stamp="الأستاذ مصطفى",
            buyer_phone_stamp="01099998888",
            amount_paid=15.0,
            status=PurchaseStatus.COMPLETED
        )
        self.db.add(purchase)
        self.db.commit()

        # 5. كتابة المراجعة بعد إتمام الشراء (يجب أن تنجح 200)
        res_rev = self.client.post(f"/api/materials/{material.id}/reviews", json=rev_data, headers=headers)
        self.assertEqual(res_rev.status_code, 200)
        rev_out = res_rev.json()
        self.assertEqual(rev_out["reviewer_name"], "الأستاذ مصطفى كامل")
        self.assertEqual(rev_out["overall_rating"], round((5 + 4 + 5) / 3.0, 2))

        # 6. جلب التقييمات مجدداً والتحقق من حساب المتوسطات
        res_list = self.client.get(f"/api/materials/{material.id}/reviews")
        self.assertEqual(res_list.status_code, 200)
        data_list = res_list.json()
        self.assertEqual(data_list["total_count"], 1)
        self.assertEqual(data_list["avg_content_quality"], 5.0)
        self.assertEqual(data_list["avg_print_formatting"], 4.0)
        self.assertEqual(data_list["avg_stamp_appearance"], 5.0)
        self.assertEqual(data_list["avg_overall"], round((5 + 4 + 5) / 3.0, 1))
        self.assertEqual(len(data_list["reviews"]), 1)
        self.assertEqual(data_list["reviews"][0]["comment"], "مذكرة ممتازة وتنسيق رائع")

    def test_09_patch_material_replace_pdf_preserves_metadata_and_stamps(self):
        """اختبار استبدال ملف الـ PDF عبر PATCH /api/materials/{id} مع الحفاظ على البيانات والأختام."""
        import fitz
        from app.config import settings

        admin_user = self.db.query(User).filter(User.role == UserRole.ADMIN).first()
        admin_token = create_access_token(user_id=admin_user.id, role=admin_user.role.value)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        old_doc = fitz.open()
        for i in range(2):
            p = old_doc.new_page(width=595, height=842)
            p.insert_text((72, 120), f"Old Draft Page {i + 1}", fontsize=15)
        old_bytes = old_doc.tobytes()
        old_doc.close()

        created_paths = []
        try:
            up_res = self.client.post(
                "/api/materials/",
                headers=admin_headers,
                data={
                    "title": "مذكرة الاستبدال ويب",
                    "tag": "كيمياء",
                    "description": "وصف ثابت لا يتغير",
                    "price": "30",
                    "price_coins": "30",
                    "compression_enabled": "true",
                },
                files={"pdf_file": ("draft_v1.pdf", old_bytes, "application/pdf")},
            )
            self.assertEqual(up_res.status_code, 201, up_res.text)
            mat_json = up_res.json()
            mat_id = mat_json["id"]
            cover_path = mat_json.get("cover_image_path")
            if cover_path:
                cov_abs = settings.resolve_storage_path(cover_path, "covers")
                if cov_abs:
                    created_paths.append(cov_abs)

            self.db.expire_all()
            mat_row = self.db.query(Material).filter(Material.id == mat_id).first()
            old_portable = mat_row.original_pdf_path
            old_abs = settings.resolve_storage_path(old_portable, "originals")
            if old_abs:
                created_paths.append(old_abs)
            self.assertEqual(mat_json["total_pages"], 2)
            self.assertTrue(old_abs and os.path.exists(old_abs))

            saved_elements = [
                {"type": "name", "page": None, "x_pct": 21.5, "y_pct": 31.5, "font_size": 15, "font_family": "cairo"},
                {"type": "phone", "page": None, "x_pct": 78.5, "y_pct": 81.5, "font_size": 13, "font_family": "cairo"},
            ]
            layout = StampLayout(
                material_id=mat_id,
                created_by=admin_user.id,
                layout_type=LayoutType.DEFAULT,
                stamp_elements=saved_elements,
            )
            self.db.add(layout)
            self.db.commit()

            new_doc = fitz.open()
            for i in range(3):
                p = new_doc.new_page(width=595, height=842)
                p.insert_text((72, 120), f"Repaired Content Page {i + 1}", fontsize=15)
            new_bytes = new_doc.tobytes()
            new_doc.close()

            patch_res = self.client.patch(
                f"/api/materials/{mat_id}",
                headers=admin_headers,
                data={"title": "مذكرة الاستبدال محدثة"},
                files={"pdf_file": ("repaired_v2.pdf", new_bytes, "application/pdf")},
            )
            self.assertEqual(patch_res.status_code, 200, patch_res.text)
            updated = patch_res.json()
            self.db.expire_all()
            mat_row_after = self.db.query(Material).filter(Material.id == mat_id).first()
            new_portable = mat_row_after.original_pdf_path
            new_abs = settings.resolve_storage_path(new_portable, "originals")
            if new_abs:
                created_paths.append(new_abs)

            self.assertEqual(updated["total_pages"], 3)
            self.assertEqual(updated["title"], "مذكرة الاستبدال محدثة")
            self.assertEqual(updated["tag"], "كيمياء")
            self.assertEqual(updated["description"], "وصف ثابت لا يتغير")
            self.assertEqual(updated.get("cover_image_path"), cover_path)
            self.assertEqual(float(updated["price"]), 30.0)
            self.assertEqual(float(updated["price_coins"]), 30.0)
            self.assertNotEqual(new_portable, old_portable)
            self.assertTrue(new_abs and os.path.exists(new_abs))
            self.assertFalse(os.path.exists(old_abs))

            # التحقق من رفض ملف غير صالح والحفاظ على النسخة السليمة
            bad_patch = self.client.patch(
                f"/api/materials/{mat_id}",
                headers=admin_headers,
                files={"pdf_file": ("bad.txt", b"invalid", "text/plain")},
            )
            self.assertEqual(bad_patch.status_code, 400)
            self.assertTrue(os.path.exists(new_abs))

            self.db.expire_all()
            persisted_layout = self.db.query(StampLayout).filter(StampLayout.material_id == mat_id).first()
            self.assertIsNotNone(persisted_layout)
            self.assertEqual(persisted_layout.stamp_elements, saved_elements)
        finally:
            for pth in created_paths:
                if pth and os.path.exists(pth):
                    try:
                        os.remove(pth)
                    except OSError:
                        pass

    # -------------------------------------------------------------
    # 10. اختبار أمان حساب الأدمن الأعلى: الحد الأقصى للأجهزة، الجلسات، وكلمة سر الاسترداد
    # -------------------------------------------------------------
    def test_10_supreme_admin_security_devices_sessions_and_recovery(self):
        """
        التحقق الكامل من:
        1. اقتصار إعدادات الأمان العليا على الأدمن الأعلى ومنع المشرف الفرعي (403).
        2. تحديد الحد الأقصى للأجهزة (max_devices) وفرضه على كلٍ من /auth/login و /v3/admin-vault/login.
        3. عرض الجلسات النشطة وإنهاء أي جلسة محددة (أو الكل ما عدا الحالية) مع إبطال التوكن لحظياً.
        4. حماية كلمة سر الاسترداد من التعديل بكلمة المرور العادية، واسترداد الحساب عند اختراقه مع طرد المخترق فوراً.
        """
        admin_user = self.db.query(User).filter(User.email == "admin_primary@example.com").first()
        self.assertIsNotNone(admin_user)
        admin_user.is_founding_admin = True
        self.db.commit()

        # إنشاء مشرف فرعي والتأكد من منعه عن إعدادات الأدمن الأعلى
        co_admin = User(
            email="co_admin_sec@example.com",
            password_hash=hash_password("coAdminPass123!"),
            full_name="مشرف فرعي للاختبار",
            role=UserRole.ADMIN,
            is_founding_admin=False,
            is_active=True,
            approval_status="approved",
        )
        self.db.add(co_admin)
        self.db.commit()
        self.db.refresh(co_admin)
        co_login = self.client.post(
            "/api/auth/login",
            json={"email": "co_admin_sec@example.com", "password": "coAdminPass123!", "device_id": "co-dev-1"},
        )
        self.assertEqual(co_login.status_code, 200, co_login.text)
        co_token = co_login.json()["access_token"]
        self.assertEqual(
            self.client.get("/api/auth/admin/security", headers={"Authorization": f"Bearer {co_token}"}).status_code,
            403,
        )

        # 1. دخول الأدمن الأعلى من جهازين عبر الويب وبوابة الأدمن
        r_d1 = self.client.post(
            "/api/auth/login",
            json={"email": "admin_primary@example.com", "password": "adminSecret123!", "device_id": "adm-web-1"},
        )
        self.assertEqual(r_d1.status_code, 200, r_d1.text)
        t1 = r_d1.json()["access_token"]
        self.assertTrue(r_d1.json()["user"]["is_supreme_admin"])

        r_d2 = self.client.post(
            "/api/v3/admin-vault/login",
            json={"password": "adminSecret123!", "device_id": "adm-vault-2"},
        )
        self.assertEqual(r_d2.status_code, 200, r_d2.text)
        t2 = r_d2.json()["access_token"]

        # 2. ضبط الحد الأقصى للأجهزة على 2 -> منع جهاز ثالث على كلتا البوابتين
        r_max = self.client.put(
            "/api/auth/admin/security/max-devices",
            headers={"Authorization": f"Bearer {t1}"},
            json={"max_devices": 2, "kick_excess": True},
        )
        self.assertEqual(r_max.status_code, 200, r_max.text)

        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"email": "admin_primary@example.com", "password": "adminSecret123!", "device_id": "adm-web-3"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/v3/admin-vault/login",
                json={"password": "adminSecret123!", "device_id": "adm-vault-3"},
            ).status_code,
            403,
        )

        # 3. جلب الجلسات وإنهاء جلسة adm-vault-2 مع إبقاء t1 فعّالاً
        r_ov = self.client.get("/api/auth/admin/security", headers={"Authorization": f"Bearer {t1}"})
        self.assertEqual(r_ov.status_code, 200, r_ov.text)
        sessions = r_ov.json()["sessions"]
        s1 = next((s for s in sessions if s["device_id"] == "adm-web-1"), None)
        s2 = next((s for s in sessions if s["device_id"] == "adm-vault-2"), None)
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        self.assertTrue(s1["is_current"])
        self.assertFalse(s2["is_current"])

        r_kill = self.client.delete(
            f"/api/auth/admin/security/sessions/{s2['session_id']}",
            headers={"Authorization": f"Bearer {t1}"},
        )
        self.assertEqual(r_kill.status_code, 200, r_kill.text)

        # توكن t2 المطرود يرفض فوراً بـ 401 على /auth/me وعلى نقاط v3 الإدارية
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {t2}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/v3/admin/overview", headers={"Authorization": f"Bearer {t2}"}).status_code,
            401,
        )
        # توكن t1 ما زال يعمل بشكل طبيعي
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {t1}"}).status_code,
            200,
        )

        # بما أنه تم تحرير مكان adm-vault-2، يستطيع adm-web-3 الدخول الآن ضمن الحد 2
        r_d3 = self.client.post(
            "/api/auth/login",
            json={"email": "admin_primary@example.com", "password": "adminSecret123!", "device_id": "adm-web-3"},
        )
        self.assertEqual(r_d3.status_code, 200, r_d3.text)
        t3 = r_d3.json()["access_token"]

        # إنهاء جميع الجلسات الأخرى من t1 يطرد t3 ويبقي t1
        r_kill_others = self.client.post(
            "/api/auth/admin/security/sessions/revoke-others",
            headers={"Authorization": f"Bearer {t1}"},
        )
        self.assertEqual(r_kill_others.status_code, 200, r_kill_others.text)
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {t3}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {t1}"}).status_code,
            200,
        )

        # 4. إعداد كلمة سر الاسترداد وحمايتها من التغيير بكلمة المرور العادية
        self.assertEqual(
            self.client.post(
                "/api/auth/admin/security/recovery-password",
                headers={"Authorization": f"Bearer {t1}"},
                json={"current_password": "WrongPass!", "new_recovery_password": "RecoveryMaster#2026"},
            ).status_code,
            403,
        )
        r_rec_set = self.client.post(
            "/api/auth/admin/security/recovery-password",
            headers={"Authorization": f"Bearer {t1}"},
            json={"current_password": "adminSecret123!", "new_recovery_password": "RecoveryMaster#2026"},
        )
        self.assertEqual(r_rec_set.status_code, 200, r_rec_set.text)

        # محاولة تغيير كلمة سر الاسترداد بعد تعيينها باستخدام كلمة المرور العادية فقط -> مرفوضة (403)
        self.assertEqual(
            self.client.post(
                "/api/auth/admin/security/recovery-password",
                headers={"Authorization": f"Bearer {t1}"},
                json={"current_password": "adminSecret123!", "new_recovery_password": "HackerOverride999"},
            ).status_code,
            403,
        )

        # محاكاة اختراق الحساب: دخول المخترق وتغيير كلمة المرور العادية
        r_hack_login = self.client.post(
            "/api/auth/login",
            json={"email": "admin_primary@example.com", "password": "adminSecret123!", "device_id": "hacker-dev-99"},
        )
        self.assertEqual(r_hack_login.status_code, 200, r_hack_login.text)
        hacker_token = r_hack_login.json()["access_token"]
        r_hack_pw = self.client.put(
            "/api/users/me",
            headers={"Authorization": f"Bearer {hacker_token}"},
            json={"password": "HackedPassword#999"},
        )
        self.assertEqual(r_hack_pw.status_code, 200, r_hack_pw.text)

        # المالك يستخدم نقطة الاسترداد بدون توكن (كلمة سر استرداد خاطئة -> 401)
        self.assertEqual(
            self.client.post(
                "/api/auth/admin/recover",
                json={
                    "recovery_password": "WrongRecovery",
                    "new_password": "OwnerCleanPass#2026",
                    "device_id": "owner-safe-dev",
                },
            ).status_code,
            401,
        )

        # استرداد ناجح بكلمة سر الاسترداد -> تعيين كلمة السر الجديدة + طرد المخترق وكافة الجلسات
        r_recover = self.client.post(
            "/api/auth/admin/recover",
            json={
                "recovery_password": "RecoveryMaster#2026",
                "new_password": "OwnerCleanPass#2026",
                "device_id": "owner-safe-dev",
            },
        )
        self.assertEqual(r_recover.status_code, 200, r_recover.text)
        recovered_token = r_recover.json()["access_token"]

        # جلسة المخترق وجلسة t1 القديمة مطرودة فوراً بـ 401 عبر رفع token_version وإنهاء الجلسات
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {hacker_token}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/v3/admin/overview", headers={"Authorization": f"Bearer {hacker_token}"}).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {t1}"}).status_code,
            401,
        )

        # كلمة مرور المخترق لم تعد تعمل، وكلمة مرور المالك المستردة تعمل + التوكن الجديد صالح
        self.assertEqual(
            self.client.get("/api/auth/me", headers={"Authorization": f"Bearer {recovered_token}"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                "/api/v3/admin-vault/login",
                json={"password": "HackedPassword#999", "device_id": "hacker-dev-99"},
            ).status_code,
            401,
        )
        r_relogin = self.client.post(
            "/api/v3/admin-vault/login",
            json={"password": "OwnerCleanPass#2026", "device_id": "owner-safe-dev"},
        )
        self.assertEqual(r_relogin.status_code, 200, r_relogin.text)


if __name__ == "__main__":
    unittest.main()

