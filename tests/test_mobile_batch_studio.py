# ================================================================
# tests/test_mobile_batch_studio.py — اختبارات الدفعات والخطوط والإطارات واستوديو الموبايل
# ================================================================

import io
import os
import sys
import tempfile
import unittest

import fitz
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(ROOT_DIR, "Z-PACT-main")
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)

from openpyxl import Workbook

import stamp_settings
from bot_service.services.batch_parser import (
    normalize_phone_value,
    parse_uploaded_batch_file,
    parse_text_teacher_entries,
    match_material_by_ref,
    build_excel_template_bytes,
    build_word_template_bytes,
)

from app.config import settings
from app.models import Base, User, UserRole
from app.database import get_db
from app.auth import create_access_token
import app.database as app_db
import app.stamper as stamper_mod
import app.routers.internal_bridge as bridge_mod
import app.routers.materials as materials_mod
import app.routers.purchases as purchases_mod
import app.routers.ecosystem_v3 as eco_mod
from app.crud import hash_password
from main import app


def _make_sample_pdf_bytes(pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 120), f"Sample Z-PACT Page {i + 1}", fontsize=18)
    data = doc.tobytes()
    doc.close()
    return data


def _make_bordered_white_frame_png_bytes() -> bytes:
    img = Image.new("RGB", (400, 560), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 399, 559], outline=(13, 71, 161), width=18)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestBatchParserAndStamper(unittest.TestCase):

    def test_01_phone_normalization_and_arabic_numerals(self):
        self.assertEqual(normalize_phone_value(1097671808), "01097671808")
        self.assertEqual(normalize_phone_value("1097671808.0"), "01097671808")
        self.assertEqual(normalize_phone_value("+20 103-288-1402"), "01032881402")
        self.assertEqual(normalize_phone_value("٠١٠٩٧٦٧١٨٠٨"), "01097671808")

    def test_02_excel_word_csv_text_batch_parsing(self):
        sample_mats = [
            {"id": 10, "title": "مذكرة الفيزياء الحديثة - ثالثة ثانوي", "tag": "فيزياء", "price": 150},
            {"id": 22, "title": "بنك أسئلة التفاضل والتكامل", "tag": "رياضيات", "price": 120},
        ]

        xlsx_bytes = build_excel_template_bytes(sample_mats)
        xlsx_rows, fmt = parse_uploaded_batch_file("teachers.xlsx", xlsx_bytes)
        self.assertIn("Excel", fmt)
        self.assertGreaterEqual(len(xlsx_rows), 2)
        self.assertEqual(xlsx_rows[0]["phone"], "01012345678")
        self.assertEqual(xlsx_rows[0]["teacher_name"], "د. أحمد محمد علي")

        docx_bytes = build_word_template_bytes(sample_mats)
        docx_rows, fmt2 = parse_uploaded_batch_file("teachers.docx", docx_bytes)
        self.assertIn("Word", fmt2)
        self.assertGreaterEqual(len(docx_rows), 2)
        self.assertEqual(docx_rows[1]["teacher_name"], "أ. محمد إبراهيم")

        csv_bytes = "الاسم,الهاتف,المذكرة,السلسلة\nأ/ سارة إبراهيم,1097671808,الفيزياء,سلسلة التفوق\n".encode("utf-8")
        csv_rows, fmt3 = parse_uploaded_batch_file("list.csv", csv_bytes)
        self.assertIn("CSV", fmt3)
        self.assertEqual(len(csv_rows), 1)
        self.assertEqual(csv_rows[0]["phone"], "01097671808")

        raw_lines = "أ/ خالد يوسف | 01032881402 | التفاضل والتكامل | سلسلة النخبة\nم/ نور | 01122334455 | 10"
        text_rows = parse_text_teacher_entries(raw_lines)
        self.assertEqual(len(text_rows), 2)
        self.assertEqual(text_rows[0]["material_ref"], "التفاضل والتكامل")
        self.assertEqual(text_rows[0]["series_name"], "سلسلة النخبة")

    def test_03_fuzzy_material_matcher(self):
        mats = [
            {"id": 5, "title": "مذكرة الفيزياء الكهربائية - تالتة ثانوي"},
            {"id": 9, "title": "كتاب النحو الشامل للأستاذ"},
        ]
        m, how = match_material_by_ref("9", mats)
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], 9)
        self.assertEqual(how, "exact_id")

        m2, how2 = match_material_by_ref("الفيزياء الكهربائية", mats)
        self.assertIsNotNone(m2)
        self.assertEqual(m2["id"], 5)

        m3, _ = match_material_by_ref("النحو الشامل", mats)
        self.assertIsNotNone(m3)
        self.assertEqual(m3["id"], 9)

    def test_04_transparent_frame_stripping_and_multi_font_preview(self):
        fonts = stamper_mod.list_available_fonts()
        keys = {f["key"] for f in fonts}
        self.assertIn("cairo", keys)
        self.assertIn("tahoma", keys)

        with tempfile.TemporaryDirectory() as tmpdir:
            frame_out = os.path.join(tmpdir, "frame_rgba.png")
            stamper_mod.process_frame_image_to_transparent_png(
                _make_bordered_white_frame_png_bytes(),
                frame_out,
            )
            self.assertTrue(os.path.exists(frame_out))
            with Image.open(frame_out) as im:
                self.assertEqual(im.mode, "RGBA")
                w, h = im.size
                border_px = im.getpixel((5, 5))
                center_px = im.getpixel((w // 2, h // 2))
                self.assertGreater(border_px[3], 200)
                self.assertEqual(center_px[3], 0)

            pdf_path = os.path.join(tmpdir, "sample.pdf")
            with open(pdf_path, "wb") as f:
                f.write(_make_sample_pdf_bytes(2))

            elements = stamper_mod.get_default_stamp_elements("tahoma")
            png_bytes = stamper_mod.render_stamped_page_preview_png_sync(
                material_path=pdf_path,
                stamp_layout=elements,
                page_num=1,
                buyer_name="أ/ معلم الاختبار",
                buyer_phone="01097671808",
                doc_name_stamp="سلسلة الاختبار",
                watermark_text="Z-PACT",
                page_frame_path=frame_out,
                apply_stamps=True,
            )
            self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(len(png_bytes), 2000)

    def test_05_stamp_settings_and_flexible_batch_entries(self):
        # Verify central config booleans and validation combinations
        self.assertTrue(stamp_settings.ADMIN_ALL_STAMP_FIELDS_OPTIONAL)
        self.assertTrue(stamp_settings.USER_REQUIRE_ANY_AVAILABLE_STAMP_FIELD)
        self.assertTrue(stamp_settings.ALLOW_NAME_ONLY)
        self.assertTrue(stamp_settings.ALLOW_PHONE_ONLY)
        self.assertTrue(stamp_settings.ALLOW_BOOKLET_OR_SERIES_ONLY)
        self.assertTrue(stamp_settings.is_admin_telegram_id("8287744705"))

        # Non-admin combinations: Name+Phone, Name+Series, Name-only, Booklet/Series-only, Phone-only
        self.assertEqual(stamp_settings.validate_stamp_fields(buyer_name_stamp="أ/ أحمد", buyer_phone_stamp="01012345678", is_admin=False), (True, None))
        self.assertEqual(stamp_settings.validate_stamp_fields(buyer_name_stamp="أ/ أحمد", series_name="سلسلة التفوق", is_admin=False), (True, None))
        self.assertEqual(stamp_settings.validate_stamp_fields(buyer_name_stamp="أ/ أحمد", is_admin=False), (True, None))
        self.assertEqual(stamp_settings.validate_stamp_fields(series_name="مذكرة الفيزياء", is_admin=False), (True, None))
        self.assertEqual(stamp_settings.validate_stamp_fields(doc_name_stamp="مذكرة الأوائل", is_admin=False), (True, None))
        self.assertEqual(stamp_settings.validate_stamp_fields(buyer_phone_stamp="01012345678", is_admin=False), (True, None))

        # Non-admin all-empty -> rejected; Admin all-empty -> allowed
        ok_empty_user, err_empty_user = stamp_settings.validate_stamp_fields(buyer_name_stamp="", buyer_phone_stamp="", is_admin=False)
        self.assertFalse(ok_empty_user)
        self.assertTrue(bool(err_empty_user))
        ok_empty_admin, err_empty_admin = stamp_settings.validate_stamp_fields(buyer_name_stamp="", buyer_phone_stamp="", is_admin=True)
        self.assertTrue(ok_empty_admin)
        self.assertIsNone(err_empty_admin)

        # Text batch: Name+Phone, Name+Booklet, Name-only, Phone-only
        text_input = "\n".join([
            "أ/ خالد جمال | 01011223344",
            "أ/ سارة محمود | سلسلة القمة",
            "أ/ يوسف فقط",
            "01122334455",
        ])
        parsed_text = parse_text_teacher_entries(text_input, is_admin=False)
        self.assertEqual(len(parsed_text), 4)
        self.assertFalse(any(e.get("_incomplete") for e in parsed_text))
        self.assertEqual(parsed_text[1]["teacher_name"], "أ/ سارة محمود")
        self.assertEqual(parsed_text[1]["series_name"], "سلسلة القمة")
        self.assertEqual(parsed_text[2]["teacher_name"], "أ/ يوسف فقط")
        self.assertEqual(parsed_text[3]["phone"], "01122334455")

        # Excel batch: Name-only, Series-only, Phone-only, and font-only row (incomplete for user, allowed for admin)
        wb = Workbook()
        ws = wb.active
        ws.append(["اسم المدرس", "الهاتف", "السلسلة", "اسم المذكرة", "حجم الخط"])
        ws.append(["أ/ كريم فقط", "", "", "", ""])
        ws.append(["", "", "سلسلة المراجعة", "فيزياء 3ث", ""])
        ws.append(["", "01234567890", "", "", ""])
        ws.append(["", "", "", "", "18"])
        buf = io.BytesIO()
        wb.save(buf)

        user_excel, _ = parse_uploaded_batch_file("flex.xlsx", buf.getvalue(), is_admin=False)
        self.assertEqual(len(user_excel), 4)
        self.assertEqual(sum(1 for e in user_excel if e.get("_incomplete")), 1)  # 4th row has only font_size, no stamp fields
        self.assertEqual(sum(1 for e in user_excel if not e.get("_incomplete")), 3)

        admin_excel, _ = parse_uploaded_batch_file("flex.xlsx", buf.getvalue(), is_admin=True)
        self.assertEqual(len(admin_excel), 4)
        self.assertEqual(sum(1 for e in admin_excel if e.get("_incomplete")), 0)


class TestIsolatedMobileStudioBridgeAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            cls.test_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        except TypeError:
            cls.test_dir = tempfile.TemporaryDirectory()

        cls.orig_storage_prop = type(settings).STORAGE_DIR
        cls.orig_stamper_output = stamper_mod.OUTPUT_DIR
        cls.orig_stamper_preview = stamper_mod.PREVIEW_DIR
        cls.orig_frames_dir = stamper_mod.FRAMES_DIR
        cls.orig_materials_upload = materials_mod.UPLOAD_DIR
        cls.orig_materials_covers = materials_mod.COVERS_DIR
        cls.orig_materials_frames = materials_mod.FRAMES_DIR
        cls.orig_db_session_local = app_db.SessionLocal
        cls.orig_bridge_session_local = bridge_mod.SessionLocal
        cls.orig_stamper_session_local = stamper_mod.SessionLocal

        test_storage = os.path.join(cls.test_dir.name, "storage")
        for sub in ("stamped", "previews", "frames", "originals", "covers"):
            os.makedirs(os.path.join(test_storage, sub), exist_ok=True)
        type(settings).STORAGE_DIR = property(lambda self: test_storage)
        stamper_mod.OUTPUT_DIR = os.path.join(test_storage, "stamped")
        stamper_mod.PREVIEW_DIR = os.path.join(test_storage, "previews")
        stamper_mod.FRAMES_DIR = os.path.join(test_storage, "frames")
        materials_mod.UPLOAD_DIR = os.path.join(test_storage, "originals")
        materials_mod.COVERS_DIR = os.path.join(test_storage, "covers")
        materials_mod.FRAMES_DIR = os.path.join(test_storage, "frames")

        db_path = os.path.join(cls.test_dir.name, "studio_test.db")
        cls.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        cls.TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=cls.engine)
        Base.metadata.create_all(bind=cls.engine)

        app_db.SessionLocal = cls.TestingSession
        bridge_mod.SessionLocal = cls.TestingSession
        stamper_mod.SessionLocal = cls.TestingSession

        db = cls.TestingSession()
        admin = User(
            full_name="Admin Tester",
            email="studio_admin@zpact.test",
            phone="01097671808",
            password_hash=hash_password("AdminTest123!"),
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(admin)
        db.commit()
        db.close()

        def override_get_db():
            s = cls.TestingSession()
            try:
                yield s
            finally:
                s.close()

        cls.prev_override = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)
        cls.headers = {"X-Internal-Service-Key": settings.INTERNAL_SERVICE_KEY}

    @classmethod
    def tearDownClass(cls):
        if cls.prev_override is not None:
            app.dependency_overrides[get_db] = cls.prev_override
        else:
            app.dependency_overrides.pop(get_db, None)
        app_db.SessionLocal = cls.orig_db_session_local
        bridge_mod.SessionLocal = cls.orig_bridge_session_local
        stamper_mod.SessionLocal = cls.orig_stamper_session_local
        type(settings).STORAGE_DIR = cls.orig_storage_prop
        stamper_mod.OUTPUT_DIR = cls.orig_stamper_output
        stamper_mod.PREVIEW_DIR = cls.orig_stamper_preview
        stamper_mod.FRAMES_DIR = cls.orig_frames_dir
        materials_mod.UPLOAD_DIR = cls.orig_materials_upload
        materials_mod.COVERS_DIR = cls.orig_materials_covers
        materials_mod.FRAMES_DIR = cls.orig_materials_frames
        try:
            cls.engine.dispose()
        except Exception:
            pass
        try:
            cls.test_dir.cleanup()
        except Exception:
            pass

    def test_01_miniapp_and_admin_html_studio_assets(self):
        res_mini = self.client.get("/miniapp/")
        self.assertEqual(res_mini.status_code, 200)
        self.assertIn("mobile_coord_studio_card", res_mini.text)
        self.assertIn("admin_studio.js", res_mini.text)

        res_js = self.client.get("/miniapp/js/admin_studio.js")
        self.assertEqual(res_js.status_code, 200)
        self.assertIn("initMobileAdminStudio", res_js.text)

        res_adm = self.client.get("/admin")
        self.assertEqual(res_adm.status_code, 200)
        self.assertIn("prop-font", res_adm.text)

    def test_02_upload_material_frame_layout_adjust_preview_and_sequential_stamp(self):
        # 1. Fonts list
        f_res = self.client.get("/api/internal/admin/fonts", headers=self.headers)
        self.assertEqual(f_res.status_code, 200)
        self.assertGreaterEqual(len(f_res.json()["fonts"]), 3)

        # 2. Upload material with optional transparent frame
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={
                "title": "مذكرة اختبار الاستوديو",
                "tag": "فيزياء",
                "price": "150",
                "description": "وصف تجريبي كامل",
                "font_family": "cairo",
            },
            files={
                "file": ("booklet.pdf", _make_sample_pdf_bytes(2), "application/pdf"),
                "page_frame": ("frame.png", _make_bordered_white_frame_png_bytes(), "image/png"),
            },
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_data = up_res.json()
        mat_id = mat_data["id"]
        self.assertEqual(mat_data["total_pages"], 2)
        self.assertTrue(mat_data.get("page_frame_path"))

        # 3. Get layout
        lay_res = self.client.get(f"/api/internal/admin/materials/{mat_id}/layout", headers=self.headers)
        self.assertEqual(lay_res.status_code, 200)
        elements = lay_res.json()["stamp_elements"]
        self.assertGreaterEqual(len(elements), 4)

        # 4. Quick adjust font size & position
        adj_res = self.client.post(
            f"/api/internal/admin/materials/{mat_id}/quick-font-adjust",
            headers=self.headers,
            json={"target_type": "name", "font_family": "tahoma", "font_size": 22, "dx_pct": 2.0},
        )
        self.assertEqual(adj_res.status_code, 200, adj_res.text)
        updated_els = adj_res.json()["stamp_elements"]
        name_el = next(e for e in updated_els if e["type"] == "name")
        self.assertEqual(name_el["font_size"], 22.0)
        self.assertEqual(name_el["font_family"], "tahoma")

        # 5. Render live preview PNG
        prev_res = self.client.post(
            f"/api/internal/admin/materials/{mat_id}/preview-page",
            headers=self.headers,
            json={"page": 1, "stamp_elements": updated_els, "apply_stamps": True},
        )
        self.assertEqual(prev_res.status_code, 200)
        self.assertEqual(prev_res.headers["content-type"], "image/png")
        self.assertTrue(prev_res.content.startswith(b"\x89PNG"))

        # 6. Replace & delete frame
        fr2_res = self.client.post(
            f"/api/internal/admin/materials/{mat_id}/frame",
            headers=self.headers,
            files={"frame_image": ("frame2.png", _make_bordered_white_frame_png_bytes(), "image/png")},
        )
        self.assertEqual(fr2_res.status_code, 200)
        del_fr_res = self.client.delete(f"/api/internal/admin/materials/{mat_id}/frame", headers=self.headers)
        self.assertEqual(del_fr_res.status_code, 200)
        self.assertIsNone(del_fr_res.json()["page_frame_path"])

        # 7. Direct sequential batch stamp
        st_res = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "buyer_name_stamp": "أ/ حسام الدين",
                "buyer_phone_stamp": "01097671808",
                "series_name": "سلسلة التفوق",
                "watermark_text": "حسام",
                "font_family": "cairo",
            },
        )
        self.assertEqual(st_res.status_code, 200, st_res.text)
        st_data = st_res.json()
        self.assertEqual(st_data["status"], "completed")
        dl_res = self.client.get(f"/api/internal/orders/{st_data['order_code']}/download", headers=self.headers)
        self.assertEqual(dl_res.status_code, 200)
        self.assertTrue(dl_res.content.startswith(b"%PDF"))

    def test_03_flexible_stamp_rules_and_admin_optional_bypass(self):
        # 1. Public stamp settings & server-info exposure
        pub_res = self.client.get("/api/public/stamp-settings")
        self.assertEqual(pub_res.status_code, 200)
        pub_cfg = pub_res.json()
        self.assertTrue(pub_cfg["admin_all_stamp_fields_optional"])
        self.assertTrue(pub_cfg["user_require_any_available_stamp_field"])

        info_res = self.client.get("/api/public/server-info")
        self.assertEqual(info_res.status_code, 200)
        self.assertIn("stamp_settings", info_res.json())

        # 2. Ensure isolated SessionLocal on purchases & ecosystem_v3 during test
        orig_purchases_sl = getattr(purchases_mod, "SessionLocal", None)
        orig_eco_sl = getattr(eco_mod, "SessionLocal", None)
        if "SessionLocal" in vars(purchases_mod):
            purchases_mod.SessionLocal = self.TestingSession
        if "SessionLocal" in vars(eco_mod):
            eco_mod.SessionLocal = self.TestingSession

        try:
            # 3. Upload sample PDF material
            up_res = self.client.post(
                "/api/internal/admin/materials/upload",
                headers=self.headers,
                data={"title": "مذكرة اختبار المرونة", "tag": "أحياء", "price": "100", "price_coins": "100"},
                files={"file": ("flex_mat.pdf", _make_sample_pdf_bytes(1), "application/pdf")},
            )
            self.assertEqual(up_res.status_code, 200, up_res.text)
            mat_id = up_res.json()["id"]

            # 4. Prepare Admin + Normal User JWTs
            db = self.TestingSession()
            try:
                admin_user = db.query(User).filter(User.email == "studio_admin@zpact.test").first()
                admin_user.coins = 5000
                normal_user = User(
                    full_name="Normal Teacher",
                    email="teacher_flex@zpact.test",
                    phone="01155667788",
                    password_hash=hash_password("TeacherTest123!"),
                    role=UserRole.CUSTOMER,
                    coins=5000,
                    is_active=True,
                )
                db.add(normal_user)
                db.commit()
                db.refresh(normal_user)
                admin_token = create_access_token(admin_user.id, admin_user.role.value)
                user_token = create_access_token(normal_user.id, normal_user.role.value)
            finally:
                db.close()

            admin_jwt_headers = {"Authorization": f"Bearer {admin_token}"}
            user_jwt_headers = {"Authorization": f"Bearer {user_token}"}

            # 5. /api/purchases/ non-admin any available vs all empty; admin all empty
            for idx, payload in enumerate([
                {"material_id": mat_id, "buyer_name_stamp": "أ/ أحمد فقط", "payment_method": "coins"},
                {"material_id": mat_id, "buyer_phone_stamp": "01022334455", "payment_method": "coins"},
                {"material_id": mat_id, "doc_name_stamp": "مذكرة المراجعة", "payment_method": "coins"},
                {"material_id": mat_id, "buyer_name_stamp": "أ/ علي", "series_name": "سلسلة النور", "payment_method": "coins"},
            ], start=1):
                r = self.client.post("/api/purchases/", headers=user_jwt_headers, json=payload)
                self.assertIn(r.status_code, (200, 201), f"case {idx}: {r.text}")

            r_empty_user = self.client.post(
                "/api/purchases/",
                headers=user_jwt_headers,
                json={"material_id": mat_id, "buyer_name_stamp": "", "buyer_phone_stamp": "", "payment_method": "coins"},
            )
            self.assertEqual(r_empty_user.status_code, 400, r_empty_user.text)

            r_empty_admin = self.client.post(
                "/api/purchases/",
                headers=admin_jwt_headers,
                json={"material_id": mat_id, "buyer_name_stamp": "", "buyer_phone_stamp": "", "payment_method": "coins"},
            )
            self.assertIn(r_empty_admin.status_code, (200, 201), r_empty_admin.text)

            # 6. /api/internal/teachers/profile
            tp_user_ok = self.client.post(
                "/api/internal/teachers/profile",
                headers=self.headers,
                json={"telegram_user_id": "44556677", "series_title": "سلسلة البيان"},
            )
            self.assertEqual(tp_user_ok.status_code, 200, tp_user_ok.text)

            tp_user_empty = self.client.post(
                "/api/internal/teachers/profile",
                headers=self.headers,
                json={"telegram_user_id": "44556678", "teacher_name": "", "primary_phone": ""},
            )
            self.assertEqual(tp_user_empty.status_code, 400, tp_user_empty.text)

            tp_admin_empty = self.client.post(
                "/api/internal/teachers/profile",
                headers=self.headers,
                json={"telegram_user_id": "8287744705", "teacher_name": "", "primary_phone": ""},
            )
            self.assertEqual(tp_admin_empty.status_code, 200, tp_admin_empty.text)

            # 7. /api/internal/orders/create
            ord_user_phone = self.client.post(
                "/api/internal/orders/create",
                headers=self.headers,
                json={"telegram_chat_id": "44556677", "telegram_user_id": "44556677", "material_id": mat_id, "total_amount": 100.0, "buyer_phone_stamp": "01099887766"},
            )
            self.assertEqual(ord_user_phone.status_code, 200, ord_user_phone.text)

            ord_user_empty = self.client.post(
                "/api/internal/orders/create",
                headers=self.headers,
                json={"telegram_chat_id": "44556677", "telegram_user_id": "44556677", "material_id": mat_id, "total_amount": 100.0, "buyer_name_stamp": "", "buyer_phone_stamp": ""},
            )
            self.assertEqual(ord_user_empty.status_code, 400, ord_user_empty.text)

            ord_admin_empty = self.client.post(
                "/api/internal/orders/create",
                headers=self.headers,
                json={"telegram_chat_id": "8287744705", "telegram_user_id": "8287744705", "material_id": mat_id, "total_amount": 100.0, "buyer_name_stamp": "", "buyer_phone_stamp": ""},
            )
            self.assertEqual(ord_admin_empty.status_code, 200, ord_admin_empty.text)

            # 8. /api/internal/orders/direct-stamp-sync
            for body in [
                {"material_id": mat_id, "buyer_name_stamp": "أ/ سامح", "telegram_user_id": "44556677", "is_admin": False},
                {"material_id": mat_id, "series_name": "سلسلة الأوائل فقط", "telegram_user_id": "44556677", "is_admin": False},
                {"material_id": mat_id, "buyer_phone_stamp": "01200001111", "telegram_user_id": "44556677", "is_admin": False},
            ]:
                r = self.client.post("/api/internal/orders/direct-stamp-sync", headers=self.headers, json=body)
                self.assertEqual(r.status_code, 200, r.text)

            ds_user_empty = self.client.post(
                "/api/internal/orders/direct-stamp-sync",
                headers=self.headers,
                json={"material_id": mat_id, "buyer_name_stamp": "", "buyer_phone_stamp": "", "telegram_user_id": "44556677", "is_admin": False},
            )
            self.assertEqual(ds_user_empty.status_code, 400, ds_user_empty.text)

            # Even with is_admin=True in payload, unverified non-admin telegram_user_id must stay blocked if all empty
            ds_spoof_empty = self.client.post(
                "/api/internal/orders/direct-stamp-sync",
                headers=self.headers,
                json={"material_id": mat_id, "buyer_name_stamp": "", "buyer_phone_stamp": "", "telegram_user_id": "44556677", "is_admin": True},
            )
            self.assertEqual(ds_spoof_empty.status_code, 400, ds_spoof_empty.text)

            # Verified Admin Telegram ID succeeds with all stamp fields empty
            ds_admin_empty = self.client.post(
                "/api/internal/orders/direct-stamp-sync",
                headers=self.headers,
                json={"material_id": mat_id, "buyer_name_stamp": "", "buyer_phone_stamp": "", "telegram_user_id": "8287744705", "is_admin": True},
            )
            self.assertEqual(ds_admin_empty.status_code, 200, ds_admin_empty.text)

            # 9. /api/v3/bundles/checkout
            bnd_series = self.client.post(
                "/api/v3/bundles/checkout",
                headers=self.headers,
                data={
                    "material_ids_json": f"[{mat_id}]",
                    "series_name": "سلسلة البرهان فقط",
                    "payment_mode": "receipt_pending",
                    "from_wallet_phone": "01055556666",
                    "telegram_user_id": "44556677",
                    "telegram_chat_id": "44556677",
                },
            )
            self.assertEqual(bnd_series.status_code, 200, bnd_series.text)

            bnd_empty_user = self.client.post(
                "/api/v3/bundles/checkout",
                headers=self.headers,
                data={
                    "material_ids_json": f"[{mat_id}]",
                    "buyer_name_stamp": "",
                    "buyer_phone_stamp": "",
                    "payment_mode": "receipt_pending",
                    "from_wallet_phone": "01055556666",
                    "telegram_user_id": "44556677",
                    "telegram_chat_id": "44556677",
                },
            )
            self.assertEqual(bnd_empty_user.status_code, 400, bnd_empty_user.text)

            bnd_empty_admin = self.client.post(
                "/api/v3/bundles/checkout",
                headers=self.headers,
                data={
                    "material_ids_json": f"[{mat_id}]",
                    "buyer_name_stamp": "",
                    "buyer_phone_stamp": "",
                    "payment_mode": "receipt_pending",
                    "from_wallet_phone": "01055556666",
                    "telegram_user_id": "8287744705",
                    "telegram_chat_id": "8287744705",
                    "is_admin": "true",
                },
            )
            self.assertEqual(bnd_empty_admin.status_code, 200, bnd_empty_admin.text)
        finally:
            if "SessionLocal" in vars(purchases_mod) and orig_purchases_sl is not None:
                purchases_mod.SessionLocal = orig_purchases_sl
            if "SessionLocal" in vars(eco_mod) and orig_eco_sl is not None:
                eco_mod.SessionLocal = orig_eco_sl

    def test_04_unrestricted_edge_stamp_coordinates(self):
        # 1. Upload sample PDF material
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة تجربة الإحداثيات الحرة", "tag": "فيزياء", "price": "50", "price_coins": "50"},
            files={"file": ("edge_coords.pdf", _make_sample_pdf_bytes(1), "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_id = up_res.json()["id"]

        # 2. Save doc_name far to the right (98.5%) via studio layout API
        elements = [
            {
                "type": "doc_name",
                "x_pct": 98.5,
                "y_pct": 12.0,
                "font_size": 18,
                "font_family": "cairo",
                "color": "#1D4ED8",
                "page": 0,
            },
            {
                "type": "name",
                "x_pct": 99.0,
                "y_pct": 94.0,
                "font_size": 16,
                "font_family": "tahoma",
                "color": "#111827",
                "page": 0,
            },
        ]
        put_res = self.client.put(
            f"/api/internal/admin/materials/{mat_id}/layout",
            headers=self.headers,
            json={"layout_type": "default_text", "stamp_elements": elements},
        )
        self.assertEqual(put_res.status_code, 200, put_res.text)
        saved_doc = next(e for e in put_res.json()["stamp_elements"] if e["type"] == "doc_name")
        self.assertEqual(saved_doc["x_pct"], 98.5)

        # 3. Quick-adjust doc_name further right past 98% (to 105.0%) without clamping back left
        adj_res = self.client.post(
            f"/api/internal/admin/materials/{mat_id}/quick-font-adjust",
            headers=self.headers,
            json={"target_type": "doc_name", "dx_pct": 6.5},
        )
        self.assertEqual(adj_res.status_code, 200, adj_res.text)
        adj_doc = next(e for e in adj_res.json()["stamp_elements"] if e["type"] == "doc_name")
        self.assertEqual(adj_doc["x_pct"], 105.0)

        # Confirm persisted via GET layout
        get_res = self.client.get(f"/api/internal/admin/materials/{mat_id}/layout", headers=self.headers)
        self.assertEqual(get_res.status_code, 200, get_res.text)
        persisted_doc = next(e for e in get_res.json()["stamp_elements"] if e["type"] == "doc_name")
        self.assertEqual(persisted_doc["x_pct"], 105.0)

        # 4. Also confirm Web Designer POST /api/layouts/ saves far-right doc_name with Admin JWT
        db = self.TestingSession()
        try:
            admin_user = db.query(User).filter(User.email == "studio_admin@zpact.test").first()
            admin_jwt = create_access_token(admin_user.id, admin_user.role.value)
        finally:
            db.close()

        web_res = self.client.post(
            "/api/layouts/",
            headers={"Authorization": f"Bearer {admin_jwt}"},
            json={
                "material_id": mat_id,
                "layout_type": "default_text",
                "stamp_elements": [
                    {
                        "type": "doc_name",
                        "x_pct": 96.4,
                        "y_pct": 8.5,
                        "font_size": 20,
                        "font_family": "cairo",
                        "color": "#000000",
                        "page": 0,
                    }
                ],
            },
        )
        self.assertIn(web_res.status_code, (200, 201), web_res.text)
        web_doc = next(e for e in web_res.json()["stamp_elements"] if e["type"] == "doc_name")
        self.assertEqual(web_doc["x_pct"], 96.4)

        # 5. Live preview PNG and direct PDF stamping render cleanly with right-edge doc_name
        prev_res = self.client.post(
            f"/api/internal/admin/materials/{mat_id}/preview-page",
            headers=self.headers,
            json={"page": 1, "stamp_elements": adj_res.json()["stamp_elements"], "apply_stamps": True},
        )
        self.assertEqual(prev_res.status_code, 200, prev_res.text)
        self.assertTrue(prev_res.content.startswith(b"\x89PNG"))

        st_res = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "series_name": "مذكرة الفيزياء الحديثة — الجزء الأول",
                "buyer_name_stamp": "أحمد علي",
                "is_admin": True,
            },
        )
        self.assertEqual(st_res.status_code, 200, st_res.text)
        self.assertEqual(st_res.json()["status"], "completed")

    def test_05_admin_bot_never_missing_data(self):
        from bot_service.handlers.admin_studio import _render_batch_summary_text_and_kb

        # 1. Verify Admin batch summary UI never outputs "بيانات ناقصة" or "| ناقص:"
        summary_text, _kb = _render_batch_summary_text_and_kb({
            "batch_entries": [
                {"idx": 1, "teacher_name": "", "phone": "", "matched_material_id": 1, "matched_material_title": "مذكرة"},
                {"idx": 2, "font_size": 16.0, "matched_material_id": None},
            ],
            "batch_source": "Excel (.xlsx)",
            "batch_default_mat_id": 1,
            "batch_default_mat_title": "مذكرة",
        })
        self.assertNotIn("بيانات ناقصة", summary_text)
        self.assertNotIn("ناقص:", summary_text)
        self.assertIn("جاهز للتنفيذ: <code>2</code>", summary_text)

        # 2. Validate exact order.py stamp_settings call signature (must not pass invalid kwargs)
        ok_sig, err_sig = stamp_settings.validate_stamp_fields(
            buyer_name_stamp="",
            buyer_phone_stamp="",
            buyer_phone_stamp_2=None,
            series_name=None,
            doc_name_stamp=None,
            watermark_text=None,
            is_admin=True,
        )
        self.assertTrue(ok_sig)
        self.assertIsNone(err_sig)

        # 3. Upload sample material and verify internal endpoints accept completely empty stamp fields for any admin (including co-admin via is_admin=True)
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة اختبار أدمن البوت", "tag": "كيمياء", "price": "80", "price_coins": "80"},
            files={"file": ("bot_admin_free.pdf", _make_sample_pdf_bytes(1), "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_id = up_res.json()["id"]

        # Direct stamp, profile, and order with all empty stamp fields by co-admin id ("999888777") registered via add_admin
        stamp_settings.register_admin_telegram_id("999888777")
        try:
            coadmin_stamp = self.client.post(
                "/api/internal/orders/direct-stamp-sync",
                headers=self.headers,
                json={
                    "material_id": mat_id,
                    "buyer_name_stamp": "",
                    "buyer_phone_stamp": "",
                    "telegram_chat_id": "999888777",
                    "telegram_user_id": "999888777",
                    "is_admin": True,
                },
            )
            self.assertEqual(coadmin_stamp.status_code, 200, coadmin_stamp.text)
            self.assertEqual(coadmin_stamp.json()["status"], "completed")

            # Non-admin with empty stamp fields MUST still fail (400)
            non_admin_stamp = self.client.post(
                "/api/internal/orders/direct-stamp-sync",
                headers=self.headers,
                json={
                    "material_id": mat_id,
                    "buyer_name_stamp": "",
                    "buyer_phone_stamp": "",
                    "telegram_chat_id": "44556677",
                    "telegram_user_id": "44556677",
                    "is_admin": False,
                },
            )
            self.assertEqual(non_admin_stamp.status_code, 400, non_admin_stamp.text)

            # Profile + Order internal endpoints accept empty data for verified co-admin
            prof_admin = self.client.post(
                "/api/internal/teachers/profile",
                headers=self.headers,
                json={
                    "telegram_user_id": "999888777",
                    "teacher_name": "",
                    "primary_phone": "",
                    "is_admin": True,
                },
            )
            self.assertEqual(prof_admin.status_code, 200, prof_admin.text)

            ord_admin = self.client.post(
                "/api/internal/orders/create",
                headers=self.headers,
                json={
                    "material_id": mat_id,
                    "telegram_chat_id": "999888777",
                    "telegram_user_id": "999888777",
                    "buyer_name_stamp": "",
                    "buyer_phone_stamp": "",
                    "total_amount": 80.0,
                    "is_admin": True,
                },
            )
            self.assertEqual(ord_admin.status_code, 200, ord_admin.text)
        finally:
            stamp_settings.ADMIN_TELEGRAM_IDS.discard(999888777)

    def test_11_headerless_first_order_without_phone_and_admin_bundle_no_phone(self):
        # 1. Headerless Excel: 1st row has Name + Booklet + Series (NO phone), 2nd row has Name + Phone
        wb = Workbook()
        ws = wb.active
        ws.append(["أ/ علي محمود", "مذكرة الرياضيات", "سلسلة التفوق"])
        ws.append(["أ/ خالد حسن", "01012345678", ""])
        buf = io.BytesIO()
        wb.save(buf)

        rows, _ = parse_uploaded_batch_file("no_header_first_no_phone.xlsx", buf.getvalue(), is_admin=True)
        self.assertEqual(len(rows), 2, f"Expected first row without phone not to be dropped as header: {rows}")
        self.assertEqual(rows[0].get("teacher_name"), "أ/ علي محمود")
        self.assertFalse(rows[0].get("phone"))

        # Headerless 2-column Excel where row 1 has "أ/ علي" and "مذكرة الفيزياء" (must NOT trigger single_col_fallback)
        wb2 = Workbook()
        ws2 = wb2.active
        ws2.append(["أ/ علي", "مذكرة الفيزياء"])
        ws2.append(["أ/ حسن", "01099887766"])
        buf2 = io.BytesIO()
        wb2.save(buf2)

        rows2, _ = parse_uploaded_batch_file("two_col_no_header.xlsx", buf2.getvalue(), is_admin=True)
        self.assertEqual(len(rows2), 2, f"Expected 2 rows in headerless 2-col Excel: {rows2}")
        self.assertEqual(rows2[0].get("teacher_name"), "أ/ علي")
        self.assertEqual(rows2[0].get("material_ref") or rows2[0].get("series_name"), "مذكرة الفيزياء")

        # 2. Text batch (pipe-separated): 3+ parts without phone, explicit '-' skip, and header skip
        pipe_input = "\n".join([
            "أحمد علي | مذكرة اختبار | سلسلة التفوق",
            "سارة محمود | بدون | 10 | سلسلة النور",
            "الاسم | الهاتف | المذكرة",
            "ندى خالد | - | مذكرة الكيمياء",
        ])
        parsed_pipe = parse_text_teacher_entries(pipe_input, is_admin=True)
        self.assertEqual(len(parsed_pipe), 3, f"Unexpected pipe parse rows: {parsed_pipe}")
        self.assertEqual(parsed_pipe[0].get("teacher_name"), "أحمد علي")
        self.assertEqual(parsed_pipe[0].get("material_ref"), "مذكرة اختبار")
        self.assertEqual(parsed_pipe[0].get("series_name"), "سلسلة التفوق")
        self.assertFalse(parsed_pipe[0].get("phone"))

        self.assertEqual(parsed_pipe[1].get("teacher_name"), "سارة محمود")
        self.assertFalse(parsed_pipe[1].get("phone"))
        self.assertEqual(parsed_pipe[1].get("material_ref"), "10")
        self.assertEqual(parsed_pipe[1].get("series_name"), "سلسلة النور")

        self.assertEqual(parsed_pipe[2].get("teacher_name"), "ندى خالد")
        self.assertFalse(parsed_pipe[2].get("phone"))
        self.assertEqual(parsed_pipe[2].get("material_ref"), "مذكرة الكيمياء")

        # 3. /api/v3/bundles/checkout: Admin without from_wallet_phone or buyer_phone_stamp succeeds (200)
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة اختبار سلة بدون هاتف", "tag": "أحياء", "price": "50", "price_coins": "50"},
            files={"file": ("bundle_no_phone.pdf", _make_sample_pdf_bytes(1), "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_id = up_res.json()["id"]

        stamp_settings.register_admin_telegram_id("999888777")
        try:
            admin_bundle = self.client.post(
                "/api/v3/bundles/checkout",
                headers=self.headers,
                data={
                    "material_ids_json": f"[{mat_id}]",
                    "buyer_name_stamp": "",
                    "buyer_phone_stamp": "",
                    "series_name": "سلسلة القمة",
                    "from_wallet_phone": "",
                    "payment_mode": "manual",
                    "telegram_user_id": "999888777",
                    "telegram_chat_id": "999888777",
                },
            )
            self.assertEqual(admin_bundle.status_code, 200, admin_bundle.text)

            non_admin_bundle = self.client.post(
                "/api/v3/bundles/checkout",
                headers=self.headers,
                data={
                    "material_ids_json": f"[{mat_id}]",
                    "buyer_name_stamp": "أ/ عميل عادي",
                    "buyer_phone_stamp": "",
                    "from_wallet_phone": "",
                    "payment_mode": "manual",
                    "telegram_user_id": "44556677",
                    "telegram_chat_id": "44556677",
                },
            )
            self.assertEqual(non_admin_bundle.status_code, 400, non_admin_bundle.text)
        finally:
            stamp_settings.ADMIN_TELEGRAM_IDS.discard(999888777)

    def test_12_cover_page_doc_name_no_series_bold_and_default_size(self):
        import unicodedata

        # 1. Direct unit checks for _extract_cover_name
        self.assertEqual(stamper_mod._extract_cover_name("سلسلة البرهان في الرياضيات", "الرياضيات"), "البرهان")
        self.assertEqual(stamper_mod._extract_cover_name("سلسلة الصقر شاهين", "الرياضيات"), "الصقر شاهين")
        self.assertEqual(stamper_mod._extract_cover_name("سلسلة التفوق", "الرياضيات"), "التفوق")
        self.assertEqual(stamper_mod._extract_cover_name("البرهان في الرياضيات", "الرياضيات"), "البرهان")
        self.assertEqual(stamper_mod._extract_cover_name("المؤسس", "الرياضيات"), "المؤسس")

        # 2. Upload 2-page PDF and configure cover doc_name (page=1, size=44) + inner doc_name (page=0, excluded=[1], size=15)
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة اختبار غلاف الوسام", "tag": "الرياضيات", "price": "100", "price_coins": "100"},
            files={"file": ("cover_test.pdf", _make_sample_pdf_bytes(2), "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_id = up_res.json()["id"]

        layout_elements = [
            {
                "type": "doc_name",
                "page": 1,
                "x_pct": 48.2,
                "y_pct": 25.1,
                "font_size": 44.0,
                "font_family": "cairo",
                "color": "#ff0000",
                "opacity": 1.0,
                "rotation": 0.0,
                "excluded_pages": [],
                "pattern": "single",
                "z_index_mode": "foreground",
            },
            {
                "type": "doc_name",
                "page": 0,
                "x_pct": 77.9,
                "y_pct": 3.1,
                "font_size": 15.0,
                "font_family": "cairo",
                "color": "#ff0000",
                "opacity": 1.0,
                "rotation": 0.0,
                "excluded_pages": [1],
                "pattern": "single",
                "z_index_mode": "foreground",
            },
        ]
        save_res = self.client.put(
            f"/api/internal/admin/materials/{mat_id}/layout",
            headers=self.headers,
            json={"layout_type": "default_text", "stamp_elements": layout_elements},
        )
        self.assertEqual(save_res.status_code, 200, save_res.text)

        # 3. Direct sync stamp even with explicit font_size=16 (must preserve 44.0 default size on cover doc_name)
        stamp_res = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "buyer_name_stamp": "أ/ أحمد",
                "buyer_phone_stamp": "",
                "series_name": "سلسلة البرهان في الرياضيات",
                "watermark_type": "none",
                "font_size": 16,
                "is_admin": True,
            },
        )
        self.assertEqual(stamp_res.status_code, 200, stamp_res.text)
        dl_res = self.client.get(stamp_res.json()["download_url"], headers=self.headers)
        self.assertEqual(dl_res.status_code, 200, dl_res.text)

        doc = fitz.open(stream=dl_res.content, filetype="pdf")
        try:
            p1_spans = [
                sp
                for b in doc[0].get_text("dict").get("blocks", [])
                for l in b.get("lines", [])
                for sp in l.get("spans", [])
            ]
            p2_spans = [
                sp
                for b in doc[1].get_text("dict").get("blocks", [])
                for l in b.get("lines", [])
                for sp in l.get("spans", [])
            ]
            p1_text = unicodedata.normalize("NFKC", " ".join(sp["text"] for sp in p1_spans))
            p2_text = unicodedata.normalize("NFKC", " ".join(sp["text"] for sp in p2_spans))

            # Cover page (page 1): standalone word WITHOUT "سلسلة", keeps 44.0pt, renders Cairo without NotoNaskh fallback, and applies PDF Bold (2 Tr)
            self.assertIn("البرهان", p1_text)
            self.assertNotIn("سلسلة", p1_text)
            self.assertNotIn("الرياضيات", p1_text)

            cover_spans = [sp for sp in p1_spans if abs(sp["size"] - 44.0) < 0.2]
            self.assertTrue(cover_spans, f"Expected cover doc_name at 44.0pt, got spans: {p1_spans}")
            for sp in cover_spans:
                self.assertIn("Cairo", sp["font"])
                self.assertNotIn("Noto", sp["font"])
            self.assertIn(b"2 Tr", doc[0].read_contents())

            # Inner page (page 2): keeps full series text with explicit body size 16.0pt and does not apply 2 Tr
            self.assertIn("البرهان", p2_text)
            self.assertIn("سلسلة", p2_text)
            inner_spans = [sp for sp in p2_spans if abs(sp["size"] - 16.0) < 0.2]
            self.assertTrue(inner_spans, f"Expected inner doc_name at explicit 16.0pt, got spans: {p2_spans}")
            self.assertNotIn(b"2 Tr", doc[1].read_contents())
        finally:
            doc.close()

        # 4. Also verify multi-word series without "في": "سلسلة الصقر شاهين" with Tahoma native bold font
        stamp_res_2 = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "buyer_name_stamp": "أ/ أحمد",
                "buyer_phone_stamp": "",
                "series_name": "سلسلة الصقر شاهين",
                "watermark_type": "none",
                "font_family": "tahoma",
                "is_admin": True,
            },
        )
        self.assertEqual(stamp_res_2.status_code, 200, stamp_res_2.text)
        dl_res_2 = self.client.get(stamp_res_2.json()["download_url"], headers=self.headers)
        self.assertEqual(dl_res_2.status_code, 200, dl_res_2.text)
        doc2 = fitz.open(stream=dl_res_2.content, filetype="pdf")
        try:
            p1_spans_2 = [
                sp
                for b in doc2[0].get_text("dict").get("blocks", [])
                for l in b.get("lines", [])
                for sp in l.get("spans", [])
            ]
            p1_text_2 = unicodedata.normalize("NFKC", " ".join(sp["text"] for sp in p1_spans_2))
            self.assertIn("الصقر", p1_text_2)
            self.assertIn("شاهين", p1_text_2)
            self.assertNotIn("سلسلة", p1_text_2)
            cover_spans_2 = [sp for sp in p1_spans_2 if abs(sp["size"] - 44.0) < 0.2]
            self.assertTrue(cover_spans_2)
            self.assertTrue(any("Tahoma" in sp["font"] and "Bold" in sp["font"] for sp in cover_spans_2), cover_spans_2)
        finally:
            doc2.close()

    def test_13_materials_catalog_excel_and_file_exists_filter(self):
        import importlib
        import openpyxl
        from app.models import Material
        from bot_service.services.batch_parser import (
            filter_usable_materials,
            build_materials_catalog_excel_bytes,
        )

        # 1. Verify /api/internal/materials populates file_exists accurately (True when present, False when missing)
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة الهندسة التحليلية", "tag": "الرياضيات", "price": "0", "price_coins": "0"},
            files={"file": ("geo_catalog.pdf", _make_sample_pdf_bytes(4), "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        live_id = up_res.json()["id"]

        db = self.TestingSession()
        try:
            missing_mat = Material(
                uploaded_by=1,
                title="Test Material Missing",
                tag="الرياضيات",
                description="missing file test",
                price=0.0,
                price_coins=0.0,
                original_pdf_path=os.path.join(self.test_dir.name, "does_not_exist_999.pdf"),
                total_pages=2,
                is_published=True,
                compression_enabled=True,
            )
            db.add(missing_mat)
            db.commit()
            db.refresh(missing_mat)
            missing_id = missing_mat.id
        finally:
            db.close()

        list_res = self.client.get("/api/internal/materials?include_unpublished=true", headers=self.headers)
        self.assertEqual(list_res.status_code, 200, list_res.text)
        by_id = {m["id"]: m for m in list_res.json()["materials"]}
        self.assertTrue(by_id[live_id]["file_exists"])
        self.assertFalse(by_id[missing_id]["file_exists"])

        # 2. Verify filter_usable_materials keeps live files and excludes missing/test entries
        usable = filter_usable_materials(list_res.json()["materials"])
        usable_ids = {m["id"] for m in usable}
        self.assertIn(live_id, usable_ids)
        self.assertNotIn(missing_id, usable_ids)

        # 3. Verify Excel catalog workbook structure, RTL sheets, and exact titles/IDs
        xlsx_bytes = build_materials_catalog_excel_bytes(
            usable,
            source_names_by_id={live_id: "geo_catalog.pdf"},
        )
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
        self.assertEqual(wb.sheetnames, ["المذكرات والخامات", "دفعات جاهزة بهذه الخامات"])

        ws_cat = wb["المذكرات والخامات"]
        self.assertTrue(ws_cat.views.sheetView[0].rightToLeft)
        cat_rows = [row for row in ws_cat.iter_rows(min_row=2, values_only=True) if any(row)]
        self.assertTrue(any(r[0] == live_id and r[1] == "مذكرة الهندسة التحليلية" and r[6] == "geo_catalog.pdf" for r in cat_rows), cat_rows)

        ws_batch = wb["دفعات جاهزة بهذه الخامات"]
        self.assertTrue(ws_batch.views.sheetView[0].rightToLeft)
        batch_rows = [row for row in ws_batch.iter_rows(min_row=2, values_only=True) if any(row)]
        self.assertTrue(any(r[2] == "مذكرة الهندسة التحليلية" for r in batch_rows), batch_rows)

        # 4. Verify all updated bot modules import cleanly without errors
        for mod_name in (
            "bot_service.keyboards.inline",
            "bot_service.handlers.admin_studio",
            "bot_service.handlers.admin",
            "bot_service.handlers.catalog",
        ):
            importlib.import_module(mod_name)

    def test_14_railway_portable_paths_and_env_autodetect(self):
        """التحقق من قابلية المسارات للنقل عبر لينكس/ويندوز وتهيئة Railway التلقائية بدون متغيرات يدوية"""
        from app.config import settings
        from bot_service import config as bot_cfg_mod

        # 1. تحضير ملف فعلي داخل مجلد originals في بيئة الاختبار
        orig_dir = os.path.join(settings.STORAGE_DIR, "originals")
        os.makedirs(orig_dir, exist_ok=True)
        fname = "portable_railway_probe.pdf"
        expected_abs = os.path.abspath(os.path.join(orig_dir, fname))
        with open(expected_abs, "wb") as f:
            f.write(_make_sample_pdf_bytes())

        win_abs = rf"C:\Users\SABER\Downloads\OldPC\Z-PACT-main\storage\originals\{fname}"
        self.assertEqual(os.path.abspath(settings.resolve_storage_path(win_abs)), expected_abs)
        self.assertEqual(os.path.abspath(settings.resolve_storage_path(f"storage/originals/{fname}")), expected_abs)
        self.assertEqual(os.path.abspath(settings.resolve_storage_path(f"/storage/originals/{fname}")), expected_abs)
        self.assertEqual(settings.to_portable_storage_path(win_abs), f"storage/originals/{fname}")

        # 2. التحقق من اشتقاق رابط Railway تلقائياً وتغلبّه على أي رابط trycloudflare قديم
        saved_env = {k: os.environ.get(k) for k in ("RAILWAY_PUBLIC_DOMAIN", "RAILWAY_STATIC_URL", "MINIAPP_URL", "PORT")}
        prev_miniapp = bot_cfg_mod.bot_config.MINIAPP_URL
        try:
            os.environ["RAILWAY_PUBLIC_DOMAIN"] = "zpact-cloud.up.railway.app"
            os.environ["PORT"] = "9090"
            bot_cfg_mod.bot_config.MINIAPP_URL = "https://stale-tunnel.trycloudflare.com/miniapp/"
            live_url = bot_cfg_mod.bot_config.get_live_miniapp_url(cache_bust=False)
            self.assertEqual(live_url, "https://zpact-cloud.up.railway.app/miniapp/")
            busted_url = bot_cfg_mod.bot_config.get_live_miniapp_url(cache_bust=True)
            self.assertTrue(busted_url.startswith("https://zpact-cloud.up.railway.app/miniapp/?v="))
        finally:
            bot_cfg_mod.bot_config.MINIAPP_URL = prev_miniapp
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_15_crash_recovery_and_persistent_sqlite_fsm(self):
        """التحقق من استمرارية حالات البوت في SQLite واستئناف طلبات الختم المنقطعة بعد إعادة التشغيل"""
        import asyncio
        from aiogram.fsm.storage.base import StorageKey
        from bot_service.sqlite_fsm import SQLiteStorage
        from app.models import BotOrder, Material, OrderFulfillmentStatus, Purchase, PurchaseStatus, StampLayout, LayoutType
        from app.recovery import collect_and_heal_interrupted_jobs_sync
        from app.stamper import get_default_stamp_elements, stamp_pdf

        # 1. محاكاة إغلاق وفتح SQLiteStorage وسط محادثة تيليجرام
        fsm_db = os.path.join(self.test_dir.name, "test_bot_fsm.db")
        s_key = StorageKey(bot_id=9095, chat_id=8287744705, user_id=8287744705)

        async def _fsm_roundtrip():
            store1 = SQLiteStorage(fsm_db)
            await store1.set_state(s_key, "OrderFlow:waiting_receipt")
            await store1.set_data(s_key, {"buyer_name_stamp": "أ/ محمود ربيع", "material_id": 18})
            await store1.close()

            store2 = SQLiteStorage(fsm_db)
            state_after = await store2.get_state(s_key)
            data_after = await store2.get_data(s_key)
            await store2.close()
            return state_after, data_after

        restored_state, restored_data = asyncio.run(_fsm_roundtrip())
        self.assertEqual(restored_state, "OrderFlow:waiting_receipt")
        self.assertEqual(restored_data.get("buyer_name_stamp"), "أ/ محمود ربيع")
        self.assertEqual(restored_data.get("material_id"), 18)

        # 2. محاكاة انقطاع السيرفر أثناء ختم طلب (PROCESSING / PROCESSING_STAMP) ثم الاستشفاء عند الإقلاع
        from app.config import settings
        orig_dir = os.path.join(settings.STORAGE_DIR, "originals")
        os.makedirs(orig_dir, exist_ok=True)
        pdf_file = os.path.join(orig_dir, "recovery_probe.pdf")
        with open(pdf_file, "wb") as f:
            f.write(_make_sample_pdf_bytes(pages=2))

        db = self.TestingSession()
        try:
            mat = Material(
                uploaded_by=1,
                title="مذكرة اختبار الاستشفاء",
                tag="فيزياء",
                description="recovery test",
                price=50.0,
                price_coins=50.0,
                original_pdf_path="storage/originals/recovery_probe.pdf",
                total_pages=2,
                is_published=True,
                compression_enabled=True,
            )
            db.add(mat)
            db.commit()
            db.refresh(mat)

            lay = StampLayout(
                material_id=mat.id,
                created_by=1,
                layout_type=LayoutType.DEFAULT_TEXT,
                stamp_elements=get_default_stamp_elements("cairo"),
            )
            db.add(lay)
            db.commit()
            db.refresh(lay)

            purchase = Purchase(
                buyer_id=1,
                material_id=mat.id,
                stamp_layout_id=lay.id,
                buyer_name_stamp="أ/ طارق سعيد",
                buyer_phone_stamp="01012345678",
                doc_name_stamp="سلسلة الإبداع",
                amount_paid=50.0,
                status=PurchaseStatus.PROCESSING,
                transaction_id="ORD-REC-01",
            )
            db.add(purchase)
            db.commit()
            db.refresh(purchase)

            order = BotOrder(
                order_code="ORD-REC-01",
                telegram_chat_id="8287744705",
                telegram_user_id="8287744705",
                user_id=1,
                material_id=mat.id,
                purchase_id=purchase.id,
                buyer_name_stamp="أ/ طارق سعيد",
                buyer_phone_stamp="01012345678",
                doc_name_stamp="سلسلة الإبداع",
                series_name="سلسلة الإبداع",
                total_amount=50.0,
                status=OrderFulfillmentStatus.PROCESSING_STAMP,
            )
            db.add(order)
            db.commit()
            p_id = purchase.id
            o_id = order.id
        finally:
            db.close()

        resumed_jobs = collect_and_heal_interrupted_jobs_sync()
        job = next((j for j in resumed_jobs if j["purchase_id"] == p_id), None)
        self.assertIsNotNone(job)

        asyncio.run(stamp_pdf(**job))

        db2 = self.TestingSession()
        try:
            p_after = db2.query(Purchase).filter(Purchase.id == p_id).first()
            o_after = db2.query(BotOrder).filter(BotOrder.id == o_id).first()
            self.assertEqual(p_after.status, PurchaseStatus.COMPLETED)
            self.assertEqual(o_after.status, OrderFulfillmentStatus.READY_FOR_DELIVERY)
            self.assertTrue(p_after.output_pdf_path and os.path.isfile(p_after.output_pdf_path))
        finally:
            db2.close()

    def test_16_pdf_compression_bloat_removal_and_bulk_hidden_marks(self):
        """التحقق من إزالة XML/StructTreeRoot، وتقليص الخطوط (subset)، ومنع الشفافية الزائدة، وتسجيل العلامات المخفية دفعة واحدة"""
        import fitz
        from app.models import HiddenMark, Material, Purchase

        # 1. بناء ملف PDF اختباري مثقّل بملفات XML مرفقة وشجرة وسوم StructTreeRoot
        src_doc = fitz.open()
        for i in range(2):
            page = src_doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"Sample Page {i + 1}", fontsize=14)
        src_doc.embfile_add("equation-1.xml", b"<math><mi>x</mi><mo>=</mo><mn>1</mn></math>")
        src_doc.embfile_add("equation-2.xml", b"<math><mi>y</mi><mo>=</mo><mn>2</mn></math>")
        struct_xref = src_doc.get_new_xref()
        src_doc.update_object(struct_xref, "<< /Type /StructTreeRoot /K [] >>")
        src_doc.xref_set_key(src_doc.pdf_catalog(), "StructTreeRoot", f"{struct_xref} 0 R")
        bloated_bytes = src_doc.tobytes()
        src_doc.close()

        # 2. رفع المذكرة والتأكد من تنظيف البنية التلقائي فور الرفع
        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة اختبار الضغط", "tag": "رياضيات", "price": "60", "price_coins": "60"},
            files={"file": ("bloated_math.pdf", bloated_bytes, "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_id = up_res.json()["id"]

        db = self.TestingSession()
        try:
            mat = db.query(Material).filter(Material.id == mat_id).first()
            self.assertIsNotNone(mat)
            from app.config import settings
            orig_rel = mat.original_pdf_path.replace("\\", "/")
            if orig_rel.startswith("storage/"):
                orig_abs = os.path.join(settings.STORAGE_DIR, orig_rel[len("storage/"):])
            else:
                orig_abs = orig_rel
            self.assertTrue(os.path.isfile(orig_abs), f"Missing original at {orig_abs}")
            with fitz.open(orig_abs) as orig_doc:
                self.assertEqual(len(orig_doc), 2)
                self.assertEqual(orig_doc.embfile_count(), 0)
                st_type, _ = orig_doc.xref_get_key(orig_doc.pdf_catalog(), "StructTreeRoot")
                self.assertEqual(st_type, "null")
        finally:
            db.close()

        # 3. تشغيل الختم الفوري (One-Pass) والتحقق من صغر الحجم وسرعة القراءة والعلامات المخفية
        stamp_res = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "buyer_name_stamp": "أ/ عمر محمود",
                "buyer_phone_stamp": "01011223344",
                "series_name": "سلسلة التفوق",
            },
        )
        self.assertEqual(stamp_res.status_code, 200, stamp_res.text)
        data = stamp_res.json()
        self.assertEqual(data["status"], "completed")
        p_id = data["purchase_id"]

        db2 = self.TestingSession()
        try:
            purchase = db2.query(Purchase).filter(Purchase.id == p_id).first()
            self.assertIsNotNone(purchase)
            out_path = purchase.output_pdf_path
            self.assertTrue(out_path and os.path.isfile(out_path))

            # Cairo + Helvetica بعد subset_fonts يجب أن يكون صغيراً جداً (< 120KB)
            out_size = os.path.getsize(out_path)
            self.assertLess(out_size, 120 * 1024, f"Stamped PDF unexpectedly large ({out_size} bytes)")

            with fitz.open(out_path) as out_doc:
                self.assertEqual(len(out_doc), 2)
                self.assertEqual(out_doc.embfile_count(), 0)
                st_out, _ = out_doc.xref_get_key(out_doc.pdf_catalog(), "StructTreeRoot")
                self.assertEqual(st_out, "null")
                # التأكد من عدم وجود مجموعات شفافية alpha (ca 0.05) في الموارد
                for page in out_doc:
                    res_str = out_doc.xref_object(page.xref) or ""
                    self.assertNotIn("fitzca", res_str)
                # العلامة المخفية لا تزال مقروءة عبر النص العادي
                self.assertIn(f"E-{p_id}-", out_doc[0].get_text("text"))

            marks = db2.query(HiddenMark).filter(HiddenMark.purchase_id == p_id).all()
            self.assertEqual(len(marks), 8)  # صفحتان × 4 زوايا
            self.assertTrue(all(m.mark_code.startswith(f"E-{p_id}-") for m in marks))
        finally:
            db2.close()

    def test_17_smart_titles_dynamic_font_scaling_and_batch_logo_watermarks(self):
        """التحقق من: عدم تكرار (أ / مستر)، تصغير الخط الديناميكي للنصوص الطويلة في كل الأختام، ودعم لوجو المدرسين في الإكسيل والختم المباشر."""
        from PIL import Image
        import openpyxl
        import unicodedata
        from app.models import Purchase
        from app.stamper import _compute_dynamic_font_size, _get_font
        from bot_service.services.batch_parser import parse_excel_bytes, is_logo_watermark_token

        # 1) بناء مذكرة ثنائية الصفحة
        pdf_buf = io.BytesIO()
        doc = fitz.open()
        for idx in range(2):
            pg = doc.new_page(width=595, height=842)
            pg.insert_text((80, 120), f"Sample Page {idx + 1}", fontsize=16)
        doc.save(pdf_buf)
        doc.close()

        up_res = self.client.post(
            "/api/internal/admin/materials/upload",
            headers=self.headers,
            data={"title": "مذكرة المسميات والتصغير", "tag": "الرياضيات", "price": "50", "price_coins": "50"},
            files={"file": ("dynamic_scale.pdf", pdf_buf.getvalue(), "application/pdf")},
        )
        self.assertEqual(up_res.status_code, 200, up_res.text)
        mat_id = up_res.json()["id"]

        # إعداد تخطيط نصي يتضمن ختم غلاف page=1 (44pt) وأختام داخلية (14pt) وعلامة مائية قطرية (100pt)
        text_layout = [
            {"type": "doc_name", "x_pct": 50, "y_pct": 55, "font_size": 44, "color": "#0F172A", "font_family": "cairo", "is_bold": True, "page": 1, "excluded_pages": [], "opacity": 1.0, "rotation": 0},
            {"type": "name", "x_pct": 75, "y_pct": 4, "font_size": 14, "color": "#0F172A", "font_family": "cairo", "page": 0, "excluded_pages": [1], "opacity": 0.9, "rotation": 0},
            {"type": "phone", "x_pct": 25, "y_pct": 4, "font_size": 14, "color": "#0F172A", "font_family": "cairo", "page": 0, "excluded_pages": [1], "opacity": 0.9, "rotation": 0},
            {"type": "doc_name", "x_pct": 75, "y_pct": 96, "font_size": 14, "color": "#0F172A", "font_family": "cairo", "page": 0, "excluded_pages": [1], "opacity": 0.9, "rotation": 0},
            {"type": "watermark", "x_pct": 50, "y_pct": 50, "font_size": 100, "color": "#DC2626", "font_family": "cairo", "pattern": "diagonal", "page": 0, "excluded_pages": [1], "opacity": 0.2, "rotation": -45},
        ]
        save_res = self.client.put(
            f"/api/internal/admin/materials/{mat_id}/layout",
            headers=self.headers,
            json={"layout_type": "default_text", "stamp_elements": text_layout},
        )
        self.assertEqual(save_res.status_code, 200, save_res.text)

        # 2أ) ختم بنصوص قصيرة (<= 3 كلمات): مستر محمد الجويلي (بدون أ /) وبنفس الحجم الافتراضي
        short_res = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "buyer_name_stamp": "مستر محمد الجويلي",
                "buyer_phone_stamp": "01012345678",
                "series_name": "البرهان",
                "watermark_text": "التفوق",
            },
        )
        self.assertEqual(short_res.status_code, 200, short_res.text)
        short_pid = short_res.json()["purchase_id"]

        # 2ب) ختم بنصوص طويلة (> 4 كلمات): تصغير ديناميكي في الاسم واسم المذكرة والغلاف والهاتف والعلامة المائية
        long_res = self.client.post(
            "/api/internal/orders/direct-stamp-sync",
            headers=self.headers,
            json={
                "material_id": mat_id,
                "buyer_name_stamp": "أستاذة سارة محمود عبد الرحمن الشافعي",
                "buyer_phone_stamp": "01012345678",
                "buyer_phone_stamp_2": "01122334455",
                "series_name": "المراجعة النهائية الشاملة في الفيزياء الحديثة للثانوية",
                "watermark_text": "نسخة المراجعة النهائية الشاملة للطلاب المتفوقين جدا",
            },
        )
        self.assertEqual(long_res.status_code, 200, long_res.text)
        long_pid = long_res.json()["purchase_id"]

        db2 = self.TestingSession()
        try:
            short_p = db2.query(Purchase).filter(Purchase.id == short_pid).first()
            long_p = db2.query(Purchase).filter(Purchase.id == long_pid).first()
            self.assertTrue(short_p and os.path.isfile(short_p.output_pdf_path))
            self.assertTrue(long_p and os.path.isfile(long_p.output_pdf_path))

            def _span_sizes(pdf_file_path: str, page_idx: int) -> list[tuple[str, float, tuple]]:
                out = []
                with fitz.open(pdf_file_path) as d:
                    for blk in d[page_idx].get_text("dict")["blocks"]:
                        for ln in blk.get("lines", []):
                            for sp in ln.get("spans", []):
                                sp_txt = unicodedata.normalize("NFKC", str(sp.get("text") or ""))
                                out.append((sp_txt, float(sp["size"]), sp.get("bbox", (0, 0, 0, 0))))
                return out

            short_p2_spans = _span_sizes(short_p.output_pdf_path, 1)
            short_p2_text = "\n".join(t for t, _, _ in short_p2_spans)
            self.assertIn("مستر", short_p2_text)
            self.assertNotIn("أ / مستر", short_p2_text)
            # الاسم القصير (3 كلمات) يظل على حجم 14pt
            name_sizes_short = [sz for t, sz, _ in short_p2_spans if "الجويلي" in t or "مستر" in t]
            self.assertTrue(any(abs(sz - 14.0) < 0.25 for sz in name_sizes_short), short_p2_spans)

            # الغلاف القصير ("البرهان") يظل ~44pt
            short_p1_spans = _span_sizes(short_p.output_pdf_path, 0)
            self.assertTrue(any(sz >= 42.0 for t, sz, _ in short_p1_spans if "البرهان" in t or sz >= 42.0), short_p1_spans)

            # النصوص الطويلة: تحافظ على (أستاذة) بدون (أ /) وتصغر ديناميكياً تحت 14pt في الداخل وتحت 44pt في الغلاف وتحت 100pt في العلامة المائية
            long_p2_spans = _span_sizes(long_p.output_pdf_path, 1)
            long_p2_text = "\n".join(t for t, _, _ in long_p2_spans)
            self.assertIn("أستاذة", long_p2_text)
            self.assertNotIn("أ / أستاذة", long_p2_text)

            small_stamps = [sz for t, sz, _ in long_p2_spans if ("سارة" in t or "الفيزياء" in t or "الشافعي" in t)]
            self.assertTrue(small_stamps and all(sz < 13.0 for sz in small_stamps), long_p2_spans)
            wm_stamps = [sz for t, sz, _ in long_p2_spans if sz > 20.0]
            self.assertTrue(wm_stamps and all(sz < 95.0 for sz in wm_stamps), long_p2_spans)

            long_p1_spans = _span_sizes(long_p.output_pdf_path, 0)
            cover_long = [(t, sz, bb) for t, sz, bb in long_p1_spans if sz > 18.0]
            self.assertTrue(cover_long and all(sz < 40.0 for _, sz, _ in cover_long), long_p1_spans)
            self.assertTrue(all(bb[2] <= 600.0 for _, _, bb in long_p1_spans), long_p1_spans)

            # التحقق المباشر من التصغير الديناميكي في أختام الهاتف الطويلة أيضاً
            c_font = _get_font("cairo")
            self.assertEqual(
                _compute_dynamic_font_size(c_font, "01012345678", "01012345678", 14.0, 595.0, 842.0, "phone", 25.0, "single"),
                14.0,
            )
            self.assertLess(
                _compute_dynamic_font_size(
                    c_font,
                    "01012345678 - 01122334455 - 01233445566",
                    "01012345678 - 01122334455 - 01233445566",
                    14.0,
                    595.0,
                    842.0,
                    "phone",
                    25.0,
                    "single",
                ),
                13.0,
            )

            # 3) اختبار الإكسيل لكلمة (لوجو / صورة) + رفع لوجو + ختم علامة مائية بصورة
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(["اسم المدرس", "رقم الهاتف", "المذكرة", "اسم السلسلة", "العلامة المائية"])
            ws.append(["مستر محمد الجويلي", "01012345678", "مذكرة المسميات والتصغير", "سلسلة التفوق", "لوجو"])
            ws.append(["أستاذة سارة محمود", "01198765432", "مذكرة المسميات والتصغير", "", "صورة لوجو"])
            ws.append(["د. خالد حسن", "01200001111", "مذكرة المسميات والتصغير", "", "نسخة خاصة"])
            xls_buf = io.BytesIO()
            wb.save(xls_buf)

            self.assertTrue(is_logo_watermark_token("لوجو"))
            self.assertTrue(is_logo_watermark_token("صورة"))
            self.assertFalse(is_logo_watermark_token("نسخة خاصة"))

            parsed = parse_excel_bytes(xls_buf.getvalue(), is_admin=True)
            self.assertEqual(len(parsed), 3)
            self.assertEqual(parsed[0].get("watermark_type"), "logo")
            self.assertIsNone(parsed[0].get("watermark_text"))
            self.assertEqual(parsed[1].get("watermark_type"), "logo")
            self.assertIsNone(parsed[1].get("watermark_text"))
            self.assertEqual(parsed[2].get("watermark_text"), "نسخة خاصة")
            self.assertNotEqual(parsed[2].get("watermark_type"), "logo")

            # إنشاء صورة لوجو بسيطة ورفعها عبر نقطة الرفع الجديدة
            img = Image.new("RGB", (140, 140), (255, 255, 255))
            for x in range(30, 110):
                for y in range(30, 110):
                    img.putpixel((x, y), (220, 38, 38))
            logo_buf = io.BytesIO()
            img.save(logo_buf, format="PNG")

            up_logo = self.client.post(
                "/api/internal/admin/logos/upload",
                headers=self.headers,
                files={"logo_image": ("teacher_logo.png", logo_buf.getvalue(), "image/png")},
            )
            self.assertEqual(up_logo.status_code, 200, up_logo.text)
            wm_logo_path = up_logo.json()["watermark_logo_path"]
            self.assertTrue(wm_logo_path.startswith("/storage/logos/"))

            logo_stamp_res = self.client.post(
                "/api/internal/orders/direct-stamp-sync",
                headers=self.headers,
                json={
                    "material_id": mat_id,
                    "buyer_name_stamp": parsed[0]["teacher_name"],
                    "buyer_phone_stamp": parsed[0]["phone"],
                    "series_name": parsed[0]["series_name"],
                    "watermark_type": "logo",
                    "watermark_logo_path": wm_logo_path,
                },
            )
            self.assertEqual(logo_stamp_res.status_code, 200, logo_stamp_res.text)
            logo_pid = logo_stamp_res.json()["purchase_id"]
            logo_p = db2.query(Purchase).filter(Purchase.id == logo_pid).first()
            self.assertEqual(logo_p.watermark_logo_path, wm_logo_path)
            with fitz.open(logo_p.output_pdf_path) as ld:
                total_imgs = sum(len(p.get_images()) for p in ld)
                self.assertGreater(total_imgs, 0, "Expected uploaded logo image XObject in stamped PDF")
                full_txt = unicodedata.normalize("NFKC", "\n".join(p.get_text("text") for p in ld))
                self.assertNotIn("لوجو", full_txt)
        finally:
            db2.close()



if __name__ == "__main__":
    unittest.main(verbosity=2)
