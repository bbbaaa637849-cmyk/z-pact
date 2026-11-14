# 📱 Z-PACT Enterprise — Native Android Application (`android_app/`)

تطبيق أندرويد أصيل مبني بلغة **Kotlin** مع بنية هجينة تجمع بين قدرات نظام أندرويد العميقة (Native Hardware & Background Services) وواجهة النيون التفاعلية (WebView + Native Bridge).

---

## 🌟 الميزات والمكونات الأساسية (3-in-1 Super App)

### 1. 📡 رادار المدفوعات التلقائي بالخلفية (`SMS Payment Radar`)
- **`SmsBroadcastReceiver.kt`**: اعتراض رسائل الـ SMS اللحظية وفلترة رسائل فودافون كاش، إنستاباي، أورنج كاش، إتصالات كاش، وفوري.
- **`SmsRadarService.kt`**: خدمة `ForegroundService` تعمل 24/7 مع إشعار دائم: `🟢 رادار Z-PACT نشط — تم رصد X تحويلات`.
- **طابور الأوفلاين التلقائي (`Offline Retry Queue`)**: قاعدة بيانات SQLite محلية (`SmsDatabaseHelper.kt`) تحفظ أي رسالة وتخزنها فوراً، وإذا انقطع الإنترنت يعيد الرادار محاولة الإرسال تلقائياً بمجرد عودة الاتصال لضمان عدم ضياع أي تحويل.
- **`WebhookDispatcher.kt`**: توجيه التحويلات عبر HTTP POST إلى `/webhook/sms` أو `/api/internal/webhook/reconcile` مع مفتاح الأمان `X-Webhook-Secret`.

### 2. 🛍️ استوديو الختم ومتجر المذكرات للمدرسين والعملاء
- تصفح كتالوج المذكرات والطلب المباشر.
- **محاكي الختم الحي لورقة A4** ومعاينة العلامة المائية والشعار لحظياً.
- **رفع صور الإيصالات عبر الكاميرا والمعرض** من خلال `WebChromeClient.onShowFileChooser`.
- **تحميل المذكرات المختومة عبر `DownloadManager` الأصيل** ليتم حفظها مباشرة في `Downloads/Z-PACT/` مع إشعار نظام أندرويد عند اكتمال التنزيل.

### 3. 🛡️ غرفة عمليات المشرف والرادار المدمجة
- ضبط عنوان السيرفر (`http://192.168.x.x:8000` أو رابط Cloudflare HTTPS العام).
- زر تفعيل/إيقاف الرادار (`ON / OFF`).
- زر **"📩 إرسال رسالة فودافون كاش فحصية"** لاختبار جاهزية السيرفر فورياً من الهاتف.
- سجل حي للرسائل المرصودة على الجهاز مع حالتها (`✅ مسلّم للسيرفر` / `⏳ معلق بطابور الأوفلاين`).

---

## 📂 هيكلية المشروع

```text
android_app/
├── app/
│   ├── src/main/
│   │   ├── AndroidManifest.xml
│   │   ├── java/live/zpact/enterprise/
│   │   │   ├── MainActivity.kt
│   │   │   ├── SmsBroadcastReceiver.kt
│   │   │   ├── SmsRadarService.kt
│   │   │   ├── WebhookDispatcher.kt
│   │   │   ├── AppConfigManager.kt
│   │   │   ├── ZPactNativeBridge.kt
│   │   │   ├── SmsDatabaseHelper.kt
│   │   │   └── SmsLogEntry.kt
│   │   ├── res/
│   │   │   ├── drawable/ (أيقونات نيون وخلفيات متدرجة)
│   │   │   ├── layout/ (activity_main, dialog_server_config, dialog_radar_monitor, item_sms_log)
│   │   │   ├── menu/ (bottom_nav_menu)
│   │   │   ├── values/ (colors.xml, strings.xml, themes.xml)
│   │   │   └── xml/ (file_paths.xml)
│   │   └── assets/webapp/ (نسخة أوفلاين مدمجة من واجهة المتجر)
│   ├── build.gradle.kts
│   └── proguard-rules.pro
├── gradle/wrapper/gradle-wrapper.properties
├── build.gradle.kts
├── settings.gradle.kts
├── gradle.properties
├── gradlew.bat
└── build_apk.bat
```

---

## 🛠️ كيفية بناء وتشغيل التطبيق

### الطريقة الأولى: عبر Android Studio (الموصى بها)
1. افتح برنامج **Android Studio**.
2. اختر **Open** ثم حدد مجلد `android_app`.
3. انتظر ثوانٍ لاكتمال مزامنة الـ Gradle.
4. اضغط على زر **Run ▶** لتشغيله على هاتفك أو المحاكي.
5. لإنشاء ملف الـ APK: اذهب إلى **Build > Build Bundle(s) / APK(s) > Build APK(s)**.

### الطريقة الثانية: عبر سطر الأوامر (Command Line)
اضغط نقراً مزدوجاً على ملف:
**`android_app\build_apk.bat`**
(أو نفذ أمر `.\gradlew.bat assembleDebug` داخل مجلد `android_app`).
وسينتج ملف الـ APK داخل المسار:
`android_app\app\build\outputs\apk\debug\app-debug.apk`

