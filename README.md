---
title: Z-PACT Enterprise
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# منصة Z-PACT Enterprise (النسخة الشاملة)

مستودع خاص (`Private`) يضم المنظومة الكاملة جاهزة للعمل على جهاز آخر:
- **`Z-PACT-main/`**: السيرفر الأساسي (FastAPI + PDF Stamper + SQLite DB في `storage/webpdf_elite.db`).
- **`bot_service/`**: بوت تيليجرام الإداري والخدمي (دعم الدفعات Excel/Word، تركيب الإطارات الشفافة، وتعديل الخطوط والأحجام).
- **`webapp_ui/`**: واجهة Telegram MiniApp واستوديو الموبايل لتحديد الإحداثيات والخطوط باللمس.
- **`payment_webhook/`**: مستقبل ومحلل رسائل الدفع.
- **`android_app/`** + **`Z-PACT-*.apk`**: كود تطبيقات الأندرويد الثلاثية والنسخ المبنية في الجذر.
- **`Z-PACT_Accounts_And_Batch_Guide.md`**: الدليل الشامل للحسابات وكلمات المرور وأعمدة ملفات الدفعات.

---

## التشغيل على اللاب الثاني في خطوتين

1. **استنساخ المستودع:**
   ```powershell
   git clone https://github.com/onnooffo637/ASSZ-PACT.git
   cd ASSZ-PACT
   ```
2. **تجهيز البيئة الافتراضية والمكتبات تلقائياً (مرة واحدة):**
   - شغّل ملف **`تجهيز_اللاب_الثاني.bat`** (ينشئ `.venv` ويثبت `requirements.txt` ويجهز `cloudflared.exe` إن أمكن).
3. **تشغيل المنظومة:**
   - التشغيل المحلي الخفيف (بدون استهلاك زائد للمعالج): **`تشغيل_محلي_خفيف.bat`**
   - التشغيل المحلي بالكامل بدون نفق خارجي (لا يتطلب `cloudflared.exe`):
     ```powershell
     .\.venv\Scripts\python.exe orchestrator.py --local
     ```

