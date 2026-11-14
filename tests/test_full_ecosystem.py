# ================================================================
# tests/test_full_ecosystem.py — الفحص الشامل لمنظومة Z-PACT v3.0 بالكامل
# ================================================================

import io
import json
import os
import secrets
import sys
import unittest
import zipfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_DIR = os.path.join(ROOT_DIR, "Z-PACT-main")
WEBHOOK_DIR = os.path.join(ROOT_DIR, "payment_webhook")

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)

from fastapi.testclient import TestClient
import main as core_main
from payment_webhook.matcher import payment_matcher


class TestFullEcosystem(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.core_client = TestClient(core_main.app)
        cls.core_client.__enter__()
        cls.internal_key = "zpact_internal_bridge_secret_key_2026"
        cls.headers = {"X-Internal-Service-Key": cls.internal_key}

    @classmethod
    def tearDownClass(cls):
        try:
            cls.core_client.__exit__(None, None, None)
        except Exception:
            pass

    def test_01_core_health_and_miniapp_assets(self):
        """1. التحقق من سلامة المنصة وتخديم ملفات الـ Mini App & PWA وبوابة المرسلين"""
        res = self.core_client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "ok")

        res_miniapp = self.core_client.get("/miniapp/")
        self.assertEqual(res_miniapp.status_code, 200)
        self.assertIn("Z-PACT", res_miniapp.text)
        self.assertIn("telegram_app.js", res_miniapp.text)

        res_sender_page = self.core_client.get("/sender")
        self.assertEqual(res_sender_page.status_code, 200)
        self.assertIn("/v3/sender/auth", res_sender_page.text)

        res_css = self.core_client.get("/miniapp/css/miniapp.css")
        self.assertEqual(res_css.status_code, 200)
        self.assertIn("Cairo", res_css.text)

        res_api_js = self.core_client.get("/miniapp/js/api.js")
        self.assertEqual(res_api_js.status_code, 200)
        self.assertIn("ApiService", res_api_js.text)

        res_app_js = self.core_client.get("/miniapp/js/telegram_app.js")
        self.assertEqual(res_app_js.status_code, 200)
        self.assertIn("window.Telegram", res_app_js.text)

        print("  ✓ [Test 1] Core Health, MiniApp & Sender Portal Assets: PASS")

    def test_02_internal_bridge_catalog_and_security(self):
        """2. التحقق من أمان الجسر الداخلي وحماية المفتاح وجلب الكتالوج"""
        unauth = self.core_client.get("/api/internal/materials")
        self.assertEqual(unauth.status_code, 403)

        res = self.core_client.get("/api/internal/materials", headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("materials", data)
        self.assertIn("total", data)
        self.assertGreaterEqual(data["total"], 1)
        print(f"  ✓ [Test 2] Internal Bridge Security & Catalog ({data['total']} materials): PASS")

    def test_03_teacher_profile_crud(self):
        """3. التحقق من إنشاء وتحديث واسترجاع الملف التعريفي للمدرس"""
        test_uid = "8899776655"
        profile_payload = {
            "telegram_user_id": test_uid,
            "telegram_username": "test_teacher_bot",
            "teacher_name": "أ/ محمد المهندس التجريبي",
            "subject_specialty": "الرياضيات المتقدمة",
            "primary_phone": "01012345678",
            "secondary_phone": "01234567890",
            "series_title": "سلسلة القمة التجريبية",
            "watermark_type": "text",
            "watermark_text": "خاص بطلاب مستر محمد"
        }

        res_save = self.core_client.post("/api/internal/teachers/profile", headers=self.headers, json=profile_payload)
        self.assertEqual(res_save.status_code, 200)
        saved = res_save.json()
        self.assertEqual(saved["teacher_name"], "أ/ محمد المهندس التجريبي")

        res_get = self.core_client.get(f"/api/internal/teachers/profile?telegram_user_id={test_uid}", headers=self.headers)
        self.assertEqual(res_get.status_code, 200)
        retrieved = res_get.json()
        self.assertEqual(retrieved["primary_phone"], "01012345678")
        print("  ✓ [Test 3] Teacher Profile Upsert & Retrieve: PASS")

    def test_04_payment_webhook_parsers(self):
        """4. التحقق من محللات رسائل SMS لمزودي الخدمة في مصر"""
        res1 = payment_matcher.parse_message("تم استلام مبلغ 150.00 جنيه من رقم 01027585126 بنجاح. كود العملية 987654321")
        self.assertIsNotNone(res1)
        self.assertEqual(res1.source_provider, "VodafoneCash")
        self.assertEqual(res1.amount, 150.0)
        self.assertEqual(res1.sender, "01027585126")

        res2 = payment_matcher.parse_message("You received EGP 200.00 from AHMED via InstaPay Ref: IP778899")
        self.assertIsNotNone(res2)
        self.assertEqual(res2.source_provider, "InstaPay")
        self.assertEqual(res2.amount, 200.0)

        res3 = payment_matcher.parse_message("تم إضافة مبلغ 75 جنيه من 01223344556 الى محفظة اورنچ كاش الخاصة بك")
        self.assertIsNotNone(res3)
        self.assertEqual(res3.source_provider, "OrangeCash")
        self.assertEqual(res3.amount, 75.0)

        # فحص رفض الرسائل الإعلانية والعشوائية وشحن الرصيد والمسابقات
        spam_samples = [
            "مبروك كسبت 500 ميجا صالحة لمدة 3 ايام عند الشحن ب 10 جنيه من اورنچ",
            "اشحن ب 50 جنيه واحصل على قسيمة شراء بقيمة 20 جنيه من كارفور",
            "تم تجديد باقة فليكس ب 70 جنيه ورصيدك الحالي 5 جنيه",
            "تم شحن رصيد بمبلغ 20 جنيه بنجاح. رصيدك الحالي 15 جنيه",
            "كاش باك 50% على مشترياتك بحد أقصى 100 جنيه عند الدفع بالمحفظة",
            "عزيزي العميل، تم خصم 50 جنيه مصاريف اشتراك الخدمة الشهرية",
            "لقد ربحت هدية مجانية 1000 وحدة صالحة حتى نهاية اليوم",
            "استمتع بضعف الباقة عند الشحن بـ 30 جنيه أو أكثر"
        ]
        for spam in spam_samples:
            parsed_spam = payment_matcher.parse_message(spam)
            self.assertIsNone(parsed_spam, f"Failed to reject spam/promo SMS: {spam}")

        print("  ✓ [Test 4] Payment SMS Parsers (Vodafone, InstaPay, Orange + Anti-Spam/Promo Rejection): PASS")

    def test_05_end_to_end_order_and_sms_reconciliation(self):
        """5. الفحص المتكامل لإنشاء الطلب ومطابقة الـ SMS والختم والتحميل"""
        mat_res = self.core_client.get("/api/internal/materials", headers=self.headers)
        mat = mat_res.json()["materials"][0]
        mat_id = mat["id"]

        test_phone = "01099887766"
        order_payload = {
            "material_id": mat_id,
            "telegram_chat_id": "1122334455",
            "telegram_user_id": "1122334455",
            "buyer_name_stamp": "أ/ أحمد شاكر الفاحص",
            "buyer_phone_stamp": test_phone,
            "doc_name_stamp": "سلسلة الفحص الشامل",
            "watermark_text": "نسخة فحص الجودة",
            "total_amount": 120.0,
            "payment_method": "vodafone_cash",
            "claimed_sender_phone": test_phone
        }

        res_order = self.core_client.post("/api/internal/orders/create", headers=self.headers, json=order_payload)
        self.assertEqual(res_order.status_code, 200)
        order = res_order.json()
        order_code = order["order_code"]
        self.assertEqual(order["status"], "pending_payment")

        ref_code = f"VF-{secrets.token_hex(4).upper()}"
        sms_text = f"تم استلام مبلغ 120.00 جنيه من رقم {test_phone} بنجاح. كود العملية {ref_code}"

        reconcile_payload = {
            "raw_message": sms_text,
            "source_provider": "VodafoneCash",
            "extracted_amount": 120.0,
            "extracted_sender": test_phone,
            "reference_number": ref_code
        }

        rec_res = self.core_client.post("/api/internal/webhook/reconcile", headers=self.headers, json=reconcile_payload)
        self.assertEqual(rec_res.status_code, 200)
        rec_data = rec_res.json()
        self.assertEqual(rec_data.get("status"), "reconciled")
        self.assertEqual(rec_data.get("order_code"), order_code)

        status_res = self.core_client.get(f"/api/internal/orders/{order_code}/status", headers=self.headers)
        self.assertEqual(status_res.status_code, 200)
        updated_order = status_res.json()
        self.assertIn(updated_order["status"], ["payment_confirmed", "processing_stamp", "ready_for_delivery"])

        dup_res = self.core_client.post("/api/internal/webhook/reconcile", headers=self.headers, json=reconcile_payload)
        self.assertEqual(dup_res.status_code, 200)
        self.assertEqual(dup_res.json().get("status"), "ignored")
        self.assertEqual(dup_res.json().get("reason"), "duplicate_reference")

        self.core_client.post(
            f"/api/internal/orders/{order_code}/admin-action",
            headers=self.headers,
            json={"action": "deliver", "admin_notes": "Automated unit test completed"}
        )

        print(f"  ✓ [Test 5] End-to-End Order & SMS Reconciliation ({order_code}): PASS")

    def test_06_bot_service_modules(self):
        """6. التحقق من اكتمال هياكل البوت وجميع الموجهات ولوحات المفاتيح وسلة الشراء"""
        import bot_service.main
        import bot_service.config
        import bot_service.keyboards.reply
        import bot_service.keyboards.inline
        import bot_service.services.bridge_client
        import bot_service.services.notifier
        import bot_service.states.order_flow

        main_kb = bot_service.keyboards.reply.get_main_keyboard(is_admin=True)
        self.assertTrue(len(main_kb.keyboard) >= 3)

        sup_kb = bot_service.keyboards.inline.get_support_keyboard()
        self.assertTrue(len(sup_kb.inline_keyboard) >= 3)

        adm_kb = bot_service.keyboards.inline.get_admin_main_keyboard()
        self.assertTrue(len(adm_kb.inline_keyboard) >= 4)

        self.assertTrue(hasattr(bot_service.states.order_flow, "BundleCartStates"))
        print("  ✓ [Test 6] Bot Service Modules, Bundle Cart & Admin V3 Keyboards: PASS")

    def test_07_single_port_sms_webhook_mounting(self):
        """7. التحقق من دمج وعمل موجه الـ SMS Webhook مباشرة داخل المنصة الأساسية"""
        webhook_headers = {"X-Webhook-Secret": "zpact_sms_webhook_secret_2026"}
        ref = secrets.token_hex(4).upper()
        test_sms = f"تم استلام مبلغ 75.00 جنيه من رقم 01011223344 بنجاح. كود العملية VF-{ref}"

        res = self.core_client.post("/webhook/sms", headers=webhook_headers, json={"message": test_sms})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "processed")
        self.assertEqual(data.get("parsed", {}).get("amount"), 75.0)

        health_res = self.core_client.get("/health")
        self.assertEqual(health_res.status_code, 200)
        print("  ✓ [Test 7] Single-Port SMS Webhook Native: PASS")

    def test_08_admin_material_and_broadcast_endpoints(self):
        """8. التحقق من مخارج إدارة المذكرات والأسعار والإذاعة العامة للأدمن"""
        mat_res = self.core_client.get("/api/internal/materials", headers=self.headers)
        mat = mat_res.json()["materials"][0]
        mat_id = mat["id"]
        original_price = float(mat["price"])

        new_price = original_price + 5.0
        up_res = self.core_client.put(
            f"/api/internal/admin/materials/{mat_id}",
            headers=self.headers,
            json={"price": new_price}
        )
        self.assertEqual(up_res.status_code, 200)
        self.assertEqual(float(up_res.json()["price"]), new_price)

        self.core_client.put(
            f"/api/internal/admin/materials/{mat_id}",
            headers=self.headers,
            json={"price": original_price}
        )

        bc_res = self.core_client.get("/api/internal/admin/broadcast-recipients", headers=self.headers)
        self.assertEqual(bc_res.status_code, 200)
        self.assertIn("total_recipients", bc_res.json())
        print("  ✓ [Test 8] Admin Material Management & Broadcast APIs: PASS")

    def test_09_android_triple_flavor_project_integrity(self):
        """9. التحقق من معمارية الأندرويد الثلاثية (Admin + Teacher + Sender) وإلغاء السحب للتحديث + OTA"""
        android_dir = os.path.join(ROOT_DIR, "android_app")
        app_dir = os.path.join(android_dir, "app")
        main_dir = os.path.join(app_dir, "src", "main")
        admin_dir = os.path.join(app_dir, "src", "admin")
        teacher_dir = os.path.join(app_dir, "src", "teacher")
        sender_dir = os.path.join(app_dir, "src", "sender")

        main_pkg = os.path.join(main_dir, "java", "live", "zpact", "enterprise")
        teacher_pkg = os.path.join(teacher_dir, "java", "live", "zpact", "enterprise")
        sender_pkg = os.path.join(sender_dir, "java", "live", "zpact", "enterprise")

        with open(os.path.join(app_dir, "build.gradle.kts"), "r", encoding="utf-8") as f:
            gradle_content = f.read()
            self.assertIn("live.zpact.enterprise.admin", gradle_content)
            self.assertIn("live.zpact.store", gradle_content)
            self.assertIn("live.zpact.sender", gradle_content)
            self.assertIn("FLAVOR_ROLE", gradle_content)

        # التحقق من إلغاء SwipeRefreshLayout نهائياً ووجود زر التحديث المخصص في الواجهة الأصلية
        layout_path = os.path.join(main_dir, "res", "layout", "activity_main.xml")
        with open(layout_path, "r", encoding="utf-8") as f:
            layout_xml = f.read()
            self.assertNotIn("<androidx.swiperefreshlayout.widget.SwipeRefreshLayout", layout_xml)
            self.assertNotIn("swipe_refresh_layout", layout_xml)
            self.assertIn("btn_refresh_page", layout_xml)

        # التحقق من وجود جميع ملفات الكوتلن الأساسية بما فيها AppUpdateManager
        for kf in [
            "AppConfigManager.kt",
            "ServerDiscoveryEngine.kt",
            "MainActivity.kt",
            "ZPactNativeBridge.kt",
            "AppUpdateManager.kt",
        ]:
            self.assertTrue(os.path.isfile(os.path.join(main_pkg, kf)), f"Missing shared Kotlin file: {kf}")

        self.assertTrue(os.path.isfile(os.path.join(teacher_pkg, "RadarPlatformHook.kt")))
        self.assertTrue(os.path.isfile(os.path.join(sender_pkg, "RadarPlatformHook.kt")))
        print("  ✓ [Test 9] Triple-Flavor Android Architecture (No Pull-to-Refresh + Native OTA): PASS")

    def test_10_triple_apk_binary_security_audit(self):
        """10. الفحص الجنائي الثنائي للنسخ الثلاث (الأدمن + المدرسين + المرسلين بدون أذونات SMS)"""
        gh_workflow = os.path.join(ROOT_DIR, ".github", "workflows", "build_android_apk.yml")
        with open(gh_workflow, "r", encoding="utf-8") as f:
            wf_content = f.read()
            self.assertIn("assembleAdminDebug", wf_content)
            self.assertIn("assembleTeacherDebug", wf_content)
            self.assertIn("assembleSenderDebug", wf_content)

        admin_apk = os.path.join(ROOT_DIR, "Z-PACT-Admin-Radar.apk")
        teacher_apk = os.path.join(ROOT_DIR, "Z-PACT-Teacher-Store.apk")
        sender_apk = os.path.join(ROOT_DIR, "Z-PACT-Sender-Team.apk")

        for apk_file in [admin_apk, teacher_apk, sender_apk]:
            self.assertTrue(os.path.isfile(apk_file), f"Missing APK: {apk_file}")
            self.assertGreater(os.path.getsize(apk_file), 1_000_000)

        # فحص جنائي ثنائي لكل من نسخة المدرسين ونسخة المرسلين: صفر أذونات SMS وصفر مفاتيح سرية
        for clean_apk in [teacher_apk, sender_apk]:
            with zipfile.ZipFile(clean_apk, "r") as zf:
                blobs = b"".join(
                    zf.read(name)
                    for name in zf.namelist()
                    if name == "AndroidManifest.xml" or name.endswith(".dex")
                )
                manifest_utf16 = zf.read("AndroidManifest.xml").decode("utf-16le", errors="ignore")
                self.assertNotIn("RECEIVE_SMS", manifest_utf16)
                self.assertNotIn("READ_SMS", manifest_utf16)
                self.assertNotIn(b"SmsBroadcastReceiver", blobs)
                self.assertNotIn(b"SmsInboxScanner", blobs)
                self.assertNotIn(b"zpact_sms_webhook_secret_2026", blobs)
                self.assertNotIn(b"zpact_internal_bridge_secret_key_2026", blobs)
                self.assertIn(b"AppUpdateManager", blobs)

        # فحص نسخة الأدمن: تحتوي على رادار الـ SMS + محرك الـ OTA
        with zipfile.ZipFile(admin_apk, "r") as zf_admin:
            admin_blobs = b"".join(zf_admin.read(n) for n in zf_admin.namelist() if n.endswith(".dex"))
            admin_manifest_utf16 = zf_admin.read("AndroidManifest.xml").decode("utf-16le", errors="ignore")
            self.assertIn("RECEIVE_SMS", admin_manifest_utf16)
            self.assertIn("READ_SMS", admin_manifest_utf16)
            self.assertIn(b"SmsInboxScanner", admin_blobs)
            self.assertIn(b"AppUpdateManager", admin_blobs)

        # التحقق من نقاط تحميل الـ APK الثلاث
        for flv in ["admin", "teacher", "sender"]:
            dl = self.core_client.get(f"/download/apk/{flv}")
            self.assertEqual(dl.status_code, 200)

        print("  ✓ [Test 10] Triple APK Binary Security Audit (Admin + Clean Teacher + Clean Sender): PASS")

    def test_11_v3_controlled_teacher_serial_and_vip_activation(self):
        """11. فحص التسجيل المحكم للمدرسين بالهاتف وقفل السيريال وتفعيل VIP والموافقة الفورية"""
        rnd = str(secrets.randbelow(89999999) + 10000000)
        t_phone = f"010{rnd}"
        t_dev = f"ZP-TCH-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"

        # 1. تسجيل مدرس جديد بدون VIP -> يكون بانتظار موافقة الإدارة (pending_approval) ولا يمنح توكن عشوائي
        r1 = self.core_client.post("/api/v3/auth/teacher-quick", json={
            "phone": t_phone,
            "full_name": "أ/ سامح الفيزيائي",
            "device_id": t_dev,
        })
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["status"], "pending_approval")
        user_id = r1.json()["user_id"]

        # 2. اعتماد المدرس من لوحة الأدمن بضغطة زر
        r_app = self.core_client.post(
            f"/api/v3/admin/teachers/{user_id}/action",
            headers=self.headers,
            json={"action": "approve"},
        )
        self.assertEqual(r_app.status_code, 200)

        # 3. الاستعلام الفوري يمنح المدرس التوكن تلقائياً من نفس الجهاز
        r_poll = self.core_client.get(f"/api/v3/auth/teacher-status?phone={t_phone}&device_id={t_dev}")
        self.assertEqual(r_poll.status_code, 200)
        self.assertEqual(r_poll.json()["status"], "approved")
        self.assertIn("access_token", r_poll.json())

        # 4. محاولة الدخول بنفس الرقم من جهاز آخر مختلف بدون PIN -> لا يمنح توكن ويطلب PIN، ومع PIN خاطئ -> 401
        r_spoof = self.core_client.post("/api/v3/auth/teacher-quick", json={
            "phone": t_phone,
            "device_id": "ZP-TCH-FAKE-9999",
        })
        self.assertEqual(r_spoof.status_code, 200)
        self.assertEqual(r_spoof.json()["status"], "require_pin")
        self.assertNotIn("access_token", r_spoof.json())

        r_wrong_pin = self.core_client.post("/api/v3/auth/teacher-quick", json={
            "phone": t_phone,
            "device_id": "ZP-TCH-FAKE-9999",
            "pin": "0000",
        })
        self.assertIn(r_wrong_pin.status_code, [401, 403])

        # 5. توليد سيريال VIP وتفعيل مدرس جديد في ثانية واحدة بدون انتظار
        gen_vip = self.core_client.post(
            "/api/v3/admin/serials/generate",
            headers=self.headers,
            json={"count": 1, "bonus_coins": 25.0, "note": "Test VIP"},
        )
        self.assertEqual(gen_vip.status_code, 200)
        vip_code = gen_vip.json()["codes"][0]

        vip_phone = f"011{secrets.randbelow(89999999) + 10000000}"
        r_vip = self.core_client.post("/api/v3/auth/teacher-quick", json={
            "phone": vip_phone,
            "full_name": "أ/ طارق VIP",
            "device_id": f"ZP-TCH-{secrets.token_hex(4).upper()}",
            "serial_code": vip_code,
        })
        self.assertEqual(r_vip.status_code, 200)
        self.assertEqual(r_vip.json()["status"], "approved")
        self.assertIn("access_token", r_vip.json())
        print("  ✓ [Test 11] Controlled Teacher Registration, Hardware Serial Lock & VIP Activation: PASS")

    def test_12_v3_sender_team_orders_commissions_and_settlement(self):
        """12. فحص منظومة المرسلين: تسجيل بالهاتف، قفل السيريال، طلبات واتساب، العمولات المخصصة، والتسوية"""
        s_phone = f"012{secrets.randbelow(89999999) + 10000000}"
        s_dev = f"ZP-SND-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}"

        # 1. تسجيل مرسل برقم الهاتف فقط -> pending_approval / pending
        r_reg = self.core_client.post("/api/v3/sender/auth", json={
            "phone": s_phone,
            "display_name": "كابتن عمر — فريق الإرسال",
            "device_id": s_dev,
        })
        self.assertEqual(r_reg.status_code, 200)
        self.assertIn(r_reg.json()["status"], ["pending", "pending_approval"])
        sender_id = r_reg.json()["sender"]["id"]

        # 2. موافقة الأدمن وتحديد عمولة المرسل (20 ج.م لكل ملف)
        r_adm = self.core_client.post(
            f"/api/v3/admin/senders/{sender_id}/action",
            headers=self.headers,
            json={"action": "approve", "commission_per_pdf": 20.0, "commission_percent": 0.0},
        )
        self.assertEqual(r_adm.status_code, 200)

        # 3. دخول المرسل من جهازه المعتمد + التحقق من رفض أي جهاز غريب (403)
        r_ok = self.core_client.post("/api/v3/sender/auth", json={"phone": s_phone, "device_id": s_dev})
        self.assertEqual(r_ok.status_code, 200)
        self.assertEqual(r_ok.json()["status"], "approved")
        s_token = r_ok.json()["sender_token"]

        r_bad_dev = self.core_client.post("/api/v3/sender/auth", json={"phone": s_phone, "device_id": "ZP-SND-HACK-0000"})
        self.assertEqual(r_bad_dev.status_code, 403)

        # 4. إنشاء طلب مباشر من المرسل للمدرس (التحويل استلمه المرسل على محفظته الخاصة)
        ws_res = self.core_client.get(
            "/api/v3/sender/workspace",
            headers={"X-Sender-Token": s_token, "X-Device-Serial": s_dev},
        )
        self.assertEqual(ws_res.status_code, 200)
        mat = ws_res.json()["catalog"][0]
        mat_id = mat["id"]
        gross_paid = round(float(mat["price"] or 100.0) + 50.0, 2)

        create_res = self.core_client.post(
            "/api/v3/sender/orders/create",
            headers={"X-Sender-Token": s_token, "X-Device-Serial": s_dev},
            data={
                "material_ids_json": json.dumps([mat_id]),
                "buyer_name_stamp": "أ/ خالد المندوه",
                "buyer_phone_stamp": "01055443322",
                "doc_name_stamp": "سلسلة التفوق",
                "watermark_text": "خاص بطلاب أ/ خالد",
                "from_wallet_phone": "01055443322",
                "to_wallet_phone": s_phone,  # محفظة المرسل الخاصة
                "transferred_amount": str(gross_paid),
            },
        )
        self.assertEqual(create_res.status_code, 200)
        created_data = create_res.json()
        self.assertEqual(created_data["pdfs_count"], 1)
        earned_comm = float(created_data["sender_commission"])
        self.assertGreater(earned_comm, 0.0)

        # صافي المستحق للأدمن = المبلغ المحصل - عمولة المرسل (لأن المرسل استلم الفلوس على محفظته)
        expected_due = round(gross_paid - earned_comm, 2)
        self.assertAlmostEqual(float(created_data["sender"]["net_due_to_admin"]), expected_due, places=2)

        # 5. تسوية الحساب المالي من لوحة الأدمن
        settle_res = self.core_client.post(
            f"/api/v3/admin/senders/{sender_id}/settle",
            headers=self.headers,
            json={"amount": expected_due, "notes": "تسوية فودافون كاش"},
        )
        self.assertEqual(settle_res.status_code, 200)
        self.assertAlmostEqual(float(settle_res.json()["sender"]["net_due_to_admin"]), 0.0, places=2)
        print("  ✓ [Test 12] Sender Portal, Hardware Serial Lock, Commissions & Settlement: PASS")

    def test_13_v3_multi_pdf_bundle_checkout_and_sms_auto_reconcile(self):
        """13. فحص سلة شراء عدة مذكرات معاً (بصورة التحويل + المطابقة التلقائية لرادار الـ SMS)"""
        mat_res = self.core_client.get("/api/internal/materials", headers=self.headers)
        published_mats = [m for m in mat_res.json()["materials"] if m.get("is_published", True)]
        mats = published_mats[:2]
        mat_ids = [m["id"] for m in mats]
        expected_sum = round(sum(float(m["price"] or 0.0) for m in mats), 2)
        sender_wallet = f"010{secrets.randbelow(89999999) + 10000000}"

        # إنشاء سلة بصورة إيصال تحويل وهمية (PNG Header) في وضع receipt_pending ثم مطابقتها تلقائياً بالـ SMS
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        res_bundle = self.core_client.post(
            "/api/v3/bundles/checkout",
            data={
                "material_ids_json": json.dumps(mat_ids),
                "buyer_name_stamp": "أ/ محمود الجوهري",
                "buyer_phone_stamp": sender_wallet,
                "doc_name_stamp": "سلسلة النخبة",
                "watermark_text": "سري للغاية",
                "payment_mode": "receipt_pending",
                "from_wallet_phone": sender_wallet,
                "to_wallet_phone": "01097671808",
            },
            files={"receipt_image": ("receipt.png", io.BytesIO(fake_png), "image/png")},
        )
        self.assertEqual(res_bundle.status_code, 200)
        b_data = res_bundle.json()
        bundle_code = b_data["bundle_code"]
        self.assertEqual(b_data["pdfs_count"], len(mat_ids))

        # محاكاة وصول رسالة SMS على رادار الأدمن بنفس إجمالي السلة ورقم المحول منه -> مطابقة وختم السلة بالكامل!
        ref_code = f"VF-BND-{secrets.token_hex(3).upper()}"
        rec_res = self.core_client.post(
            "/api/internal/webhook/reconcile",
            headers=self.headers,
            json={
                "raw_message": f"تم استلام مبلغ {expected_sum} جنيه من رقم {sender_wallet} كود {ref_code}",
                "source_provider": "VodafoneCash",
                "extracted_amount": expected_sum,
                "extracted_sender": sender_wallet,
                "reference_number": ref_code,
            },
        )
        self.assertEqual(rec_res.status_code, 200)
        self.assertEqual(rec_res.json().get("status"), "reconciled")

        # تنظيف الطلبات التجريبية إلى delivered
        for ocode in b_data.get("order_codes", []):
            self.core_client.post(
                f"/api/internal/orders/{ocode}/admin-action",
                headers=self.headers,
                json={"action": "deliver", "admin_notes": "Bundle test completed"},
            )
        print(f"  ✓ [Test 13] Multi-PDF Bundle Cart ({bundle_code}) + Receipt Upload + Auto SMS Match: PASS")

    def test_14_v3_ota_auto_updater_and_admin_overview(self):
        """14. فحص نظام التحديث الهوائي التلقائي (OTA) للنسخ الثلاث ولوحة القيادة الشاملة"""
        # 1. فحص إصدار حالي أعلى -> لا يوجد تحديث
        c_init = self.core_client.get("/api/app-updates/check?flavor=sender&version_code=999")
        self.assertEqual(c_init.status_code, 200)
        self.assertFalse(c_init.json()["update_available"])

        # 2. التحقق من ظهور التحديث للإصدارات الأقدم مع رابط التحميل المباشر
        c_old = self.core_client.get("/api/app-updates/check?flavor=sender&version_code=1")
        self.assertEqual(c_old.status_code, 200)
        self.assertTrue(c_old.json()["update_available"])
        self.assertIn("/download/apk/sender", c_old.json()["download_url"])

        # 3. التحقق من لوحة القيادة التنفيذية V3 للأدمن
        ov = self.core_client.get("/api/v3/admin/overview", headers=self.headers)
        self.assertEqual(ov.status_code, 200)
        ov_data = ov.json()
        self.assertIn("summary", ov_data)
        self.assertIn("senders", ov_data)
        self.assertIn("app_releases", ov_data)
        self.assertEqual(len(ov_data["app_releases"]), 3)
        print("  ✓ [Test 14] OTA Auto-Updater & Executive V3 Admin Overview: PASS")

    def test_15_v31_admin_vault_and_otp_ecosystem(self):
        """15. فحص الإصدار v3.1: خزانة الأدمن السيادية بكلمة السر، حوكمة أول أدمن، ورموز OTP للمدرسين والمرسلين"""
        # 1. فحص بوابة الأدمن السيادية والحماية من التهيئة خارج تطبيق الأدمن
        st0 = self.core_client.get("/api/v3/admin-vault/status")
        self.assertEqual(st0.status_code, 200)

        # محاولة تهيئة أول أدمن بدون إثبات تطبيق الأدمن -> يجب أن ترجع 403
        r_unauth_setup = self.core_client.post(
            "/api/v3/admin-vault/setup-founding",
            json={"password": "MasterAdmin2026", "admin_label": "الأدمن المؤسس", "device_id": "ZP-WEB-FAKE"},
        )
        self.assertEqual(r_unauth_setup.status_code, 403)

        # تهيئة أول أدمن بإثبات تطبيق الأدمن الرسمي
        adm_headers = {
            "X-Admin-App-Proof": self.internal_key,
            "X-Device-Serial": "ZP-ADM-0001",
        }
        r_setup = self.core_client.post(
            "/api/v3/admin-vault/setup-founding",
            headers=adm_headers,
            json={"password": "MasterAdmin2026", "admin_label": "الأدمن المؤسس السيادي", "device_id": "ZP-ADM-0001"},
        )
        self.assertIn(r_setup.status_code, [200, 409])  # 200 if fresh or 409 if already set

        # 2. دخول الأدمن بكلمة السر فقط (بدون إيميل وبدون رقم هاتف)
        r_adm_login = self.core_client.post(
            "/api/v3/admin-vault/login",
            json={"password": "MasterAdmin2026", "device_id": "ZP-ADM-0001"},
        )
        self.assertEqual(r_adm_login.status_code, 200)
        adm_token = r_adm_login.json()["access_token"]
        self.assertTrue(r_adm_login.json()["is_founding_admin"])

        # 3. طلب حساب أدمن إضافي من هاتف آخر + قفل الموافقة على أول أدمن
        co_dev = "ZP-ADM-COAD-9999"
        co_headers = {
            "X-Admin-App-Proof": self.internal_key,
            "X-Device-Serial": co_dev,
        }
        r_co_req = self.core_client.post(
            "/api/v3/admin-vault/request-co-admin",
            headers=co_headers,
            json={"admin_label": "مشرف الدعم الفني", "password": "CoAdminPass2026", "device_id": co_dev},
        )
        self.assertEqual(r_co_req.status_code, 200)
        co_id = r_co_req.json()["user_id"]
        self.assertEqual(r_co_req.json()["status"], "pending_founding_approval")

        # محاولة دخول المشرف الإضافي قبل موافقة أول أدمن -> 403
        r_co_login_fail = self.core_client.post(
            "/api/v3/admin-vault/login",
            json={"password": "CoAdminPass2026", "device_id": co_dev},
        )
        self.assertEqual(r_co_login_fail.status_code, 403)

        # موافقة أول أدمن على المشرف الإضافي
        r_co_approve = self.core_client.post(
            f"/api/v3/admin-vault/co-admins/{co_id}/action",
            headers={"Authorization": f"Bearer {adm_token}"},
            json={"action": "approve"},
        )
        self.assertEqual(r_co_approve.status_code, 200)

        # استعلام الجهاز الآخر اللحظي -> أصبح approved ولديه توكن
        r_co_poll = self.core_client.get(
            f"/api/v3/admin-vault/poll-co-admin?user_id={co_id}&device_id={co_dev}",
            headers=co_headers,
        )
        self.assertEqual(r_co_poll.status_code, 200)
        self.assertEqual(r_co_poll.json()["status"], "approved")
        self.assertIn("access_token", r_co_poll.json())

        # 4. منظومة توثيق المدرسين: طلب OTP + استلام تليجرام + تسجيل الدخول
        rnd_t = str(secrets.randbelow(89999999) + 10000000)
        t_phone = f"010{rnd_t}"
        t_pass = "TeacherSecretPass123"

        r_otp_req = self.core_client.post(
            "/api/v3/otp/request",
            json={"phone": t_phone, "target_role": "teacher", "purpose": "register"},
        )
        self.assertEqual(r_otp_req.status_code, 200)
        ch_token = r_otp_req.json()["challenge_token"]

        # استلام كود الـ OTP عبر الجسر الداخلي (Telegram Claim)
        r_claim = self.core_client.post(
            "/api/internal/otp/claim-telegram",
            headers=self.headers,
            json={"challenge_token": ch_token, "telegram_user_id": "8287744705", "telegram_username": "tuser"},
        )
        self.assertEqual(r_claim.status_code, 200)
        otp_code = r_claim.json()["otp_code"]
        self.assertEqual(len(otp_code), 6)

        # محاولة التسجيل بكود خاطئ -> 400
        r_bad_otp = self.core_client.post(
            "/api/v3/auth/teacher-register",
            json={
                "phone": t_phone,
                "full_name": "أ/ مجدي رياض",
                "password": t_pass,
                "challenge_token": ch_token,
                "otp_code": "000000",
                "device_id": "ZP-TCH-DEV-1",
            },
        )
        self.assertEqual(r_bad_otp.status_code, 400)

        # التسجيل بالكود الصحيح
        r_reg_ok = self.core_client.post(
            "/api/v3/auth/teacher-register",
            json={
                "phone": t_phone,
                "full_name": "أ/ مجدي رياض",
                "password": t_pass,
                "challenge_token": ch_token,
                "otp_code": otp_code,
                "device_id": "ZP-TCH-DEV-1",
            },
        )
        self.assertEqual(r_reg_ok.status_code, 200)
        self.assertEqual(r_reg_ok.json()["status"], "pending_approval")
        reg_uid = r_reg_ok.json()["user_id"]

        # موافقة الأدمن
        self.core_client.post(
            f"/api/v3/admin/teachers/{reg_uid}/action",
            headers=self.headers,
            json={"action": "approve"},
        )

        # تسجيل دخول المدرس بكلمة السر من نفس الجهاز المعتمد
        r_t_login = self.core_client.post(
            "/api/v3/auth/teacher-login",
            json={"phone": t_phone, "password": t_pass, "device_id": "ZP-TCH-DEV-1"},
        )
        self.assertEqual(r_t_login.status_code, 200)
        self.assertEqual(r_t_login.json()["status"], "approved")
        self.assertIn("access_token", r_t_login.json())

        # 5. منظومة توثيق المرسلين: طلب OTP + صندوق الإرسال Outbox + تسجيل ودخول
        rnd_s = str(secrets.randbelow(89999999) + 10000000)
        s_phone = f"012{rnd_s}"
        s_pass = "SenderSecretPass456"

        r_s_otp = self.core_client.post(
            "/api/v3/otp/request",
            json={"phone": s_phone, "target_role": "sender", "purpose": "register"},
        )
        self.assertEqual(r_s_otp.status_code, 200)
        s_ch_tok = r_s_otp.json()["challenge_token"]

        # فحص وجود الكود في طابور شريحة الأدمن Outbox
        r_outbox = self.core_client.get("/api/internal/otp/outbox", headers=self.headers)
        self.assertEqual(r_outbox.status_code, 200)
        outbox_items = r_outbox.json().get("outbox", [])
        matched_item = next((it for it in outbox_items if it["phone"] == s_phone), None)
        self.assertIsNotNone(matched_item)
        s_otp_code = matched_item["otp_code"]

        # تسجيل المرسل وتوثيقه
        r_s_reg = self.core_client.post(
            "/api/v3/sender/register",
            json={
                "phone": s_phone,
                "display_name": "مرسل النخبة",
                "password": s_pass,
                "challenge_token": s_ch_tok,
                "otp_code": s_otp_code,
                "device_id": "ZP-SND-DEV-1",
            },
        )
        self.assertEqual(r_s_reg.status_code, 200)
        s_obj = r_s_reg.json()["sender"]

        # موافقة الأدمن
        self.core_client.post(
            f"/api/v3/admin/senders/{s_obj['id']}/action",
            headers=self.headers,
            json={"action": "approve"},
        )

        # تسجيل دخول المرسل بكلمة السر
        r_s_login = self.core_client.post(
            "/api/v3/sender/login",
            json={"phone": s_phone, "password": s_pass, "device_id": "ZP-SND-DEV-1"},
        )
        self.assertEqual(r_s_login.status_code, 200)
        self.assertEqual(r_s_login.json()["status"], "approved")
        self.assertIn("sender_token", r_s_login.json())

        # 6. الفحص الأمني الثنائي الصارم لملفات الـ APK (Zero-SMS Permissions on Teacher & Sender)
        apk_paths = [
            ("teacher", os.path.join(ROOT_DIR, "Z-PACT-Teacher-Store.apk")),
            ("sender", os.path.join(ROOT_DIR, "Z-PACT-Sender-Team.apk")),
            ("admin", os.path.join(ROOT_DIR, "Z-PACT-Admin-Radar.apk")),
        ]
        for role, apk_p in apk_paths:
            if os.path.isfile(apk_p):
                with zipfile.ZipFile(apk_p, 'r') as zf:
                    # فحص بيان التطبيق في الملفات الثنائية (AXML StringPool is UTF-16LE)
                    manifest_data = zf.read("AndroidManifest.xml").decode("utf-16le", errors="ignore")
                    if role in ("teacher", "sender"):
                        self.assertNotIn("RECEIVE_SMS", manifest_data)
                        self.assertNotIn("READ_SMS", manifest_data)
                        self.assertNotIn("SEND_SMS", manifest_data)
                    else:
                        self.assertIn("RECEIVE_SMS", manifest_data)
                        self.assertIn("READ_SMS", manifest_data)
                        self.assertIn("SEND_SMS", manifest_data)

        print("  ✓ [Test 15] Sovereign Admin Vault, Founding Sovereignty, Phone+Password+OTP & Zero-SMS Cleanliness: PASS")


if __name__ == "__main__":
    print("\n===============================================================================")
    print("🚀 بدء الفحص الشامل لمنظومة Z-PACT v3.0 بالكامل (End-to-End Verification)")
    print("===============================================================================")
    unittest.main(verbosity=2)
