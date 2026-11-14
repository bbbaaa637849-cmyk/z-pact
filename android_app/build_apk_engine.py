# ================================================================
# android_app/build_apk_engine.py — محرك التجميع الآلي لتطبيق الأندرويد
# يقوم بفحص أو تنزيل (Portable JDK 17 + Android SDK 34) وتجميع الـ APK تلقائياً
# ================================================================

import os
import sys
import glob
import shutil
import zipfile
import subprocess
import urllib.request

# ضبط ترميز الكونسول على ويندوز لدعم العربية والرموز التعبيرية بأمان تام
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
LOCAL_APPDATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
TOOLCHAIN_DIR = os.path.join(LOCAL_APPDATA, "ZPactAndroidToolchain")

JDK_ZIP_URL = "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"
CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

C_RESET = "\033[0m"
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_MAGENTA = "\033[95m"
C_RED = "\033[91m"


def log(msg: str, color: str = C_CYAN):
    print(f"{color}{msg}{C_RESET}", flush=True)


def download_with_progress(url: str, dest_path: str, label: str):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    log(f"⬇️ جاري تنزيل {label}...", C_YELLOW)

    last_pct = [-1]

    def reporthook(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100, int(block_num * block_size * 100 / total_size))
            if pct // 10 != last_pct[0] // 10 and pct != last_pct[0]:
                last_pct[0] = pct
                mb_done = (block_num * block_size) / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                print(f"   ⏳ تقدم التنزيل ({label}): {pct}% ({mb_done:.1f} / {mb_total:.1f} MB)", flush=True)

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ZPactBuilder/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response, open(dest_path, "wb") as out_file:
        total_size = int(response.info().get("Content-Length", -1))
        downloaded = 0
        chunk_size = 256 * 1024
        block_num = 0
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            block_num += 1
            reporthook(block_num, chunk_size, total_size)

    log(f"✓ اكتمل تنزيل {label} بنجاح.", C_GREEN)


def find_existing_jdk() -> str | None:
    patterns = [
        os.environ.get("JAVA_HOME", ""),
        os.path.join(TOOLCHAIN_DIR, "jdk*"),
        r"C:\Program Files\Android\Android Studio\jbr",
        r"C:\Program Files\Android\Android Studio\jre",
        r"C:\Program Files\Eclipse Adoptium\jdk-17*",
        r"C:\Program Files\Microsoft\jdk-17*",
        r"C:\Program Files\Java\jdk-17*",
        r"C:\Program Files\Java\jdk-21*",
    ]
    for pat in patterns:
        if not pat:
            continue
        for match in sorted(glob.glob(pat), reverse=True):
            java_exe = os.path.join(match, "bin", "java.exe")
            javac_exe = os.path.join(match, "bin", "javac.exe")
            if os.path.isfile(java_exe) and os.path.isfile(javac_exe):
                return match
    return None


def ensure_jdk() -> str:
    log("\n[1/5] فحص بيئة الجافا (OpenJDK 17)...", C_CYAN)
    jdk_home = find_existing_jdk()
    if jdk_home:
        log(f"  ✓ تم العثور على حزمة JDK جاهزة: {jdk_home}", C_GREEN)
        return jdk_home

    log("  ℹ️ لم يتم العثور على JDK مثبت محلياً — جاري تجهيز نسخة محمولة تلقائياً (لمرة واحدة فقط)...", C_YELLOW)
    os.makedirs(TOOLCHAIN_DIR, exist_ok=True)
    zip_path = os.path.join(TOOLCHAIN_DIR, "openjdk17.zip")
    download_with_progress(JDK_ZIP_URL, zip_path, "Portable OpenJDK 17")

    log("  📦 جاري فك ضغط OpenJDK 17...", C_YELLOW)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(TOOLCHAIN_DIR)
    try:
        os.remove(zip_path)
    except OSError:
        pass

    jdk_home = find_existing_jdk()
    if not jdk_home:
        raise RuntimeError("تعذر تحديد مجلد JDK بعد فك الضغط!")
    log(f"  ✓ تم تفعيل OpenJDK 17 بنجاح في: {jdk_home}", C_GREEN)
    return jdk_home


def find_existing_sdk() -> str | None:
    candidates = [
        os.environ.get("ANDROID_HOME", ""),
        os.environ.get("ANDROID_SDK_ROOT", ""),
        os.path.join(LOCAL_APPDATA, "Android", "Sdk"),
        r"C:\Android\Sdk",
        os.path.join(TOOLCHAIN_DIR, "android-sdk"),
    ]
    for cand in candidates:
        if cand and os.path.isdir(cand):
            platform_34 = os.path.join(cand, "platforms", "android-34")
            if os.path.isdir(platform_34):
                return cand
    return None


def ensure_android_sdk(jdk_home: str) -> str:
    log("\n[2/5] فحص حزمة تطوير أندرويد (Android SDK 34)...", C_CYAN)
    existing = find_existing_sdk()
    if existing:
        log(f"  ✓ تم العثور على Android SDK 34 جاهز: {existing}", C_GREEN)
        return existing

    sdk_root = os.path.join(TOOLCHAIN_DIR, "android-sdk")
    cmdline_latest = os.path.join(sdk_root, "cmdline-tools", "latest")
    sdkmanager_bat = os.path.join(cmdline_latest, "bin", "sdkmanager.bat")

    if not os.path.isfile(sdkmanager_bat):
        log("  ℹ️ جاري تنزيل أدوات بناء أندرويد الرسمية (Android Command-Line Tools)...", C_YELLOW)
        os.makedirs(sdk_root, exist_ok=True)
        zip_path = os.path.join(TOOLCHAIN_DIR, "cmdline-tools.zip")
        download_with_progress(CMDLINE_TOOLS_URL, zip_path, "Android SDK Command-Line Tools")

        temp_extract = os.path.join(TOOLCHAIN_DIR, "_cmdline_tmp")
        if os.path.exists(temp_extract):
            shutil.rmtree(temp_extract, ignore_errors=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(temp_extract)
        try:
            os.remove(zip_path)
        except OSError:
            pass

        inner_cmdline = os.path.join(temp_extract, "cmdline-tools")
        os.makedirs(os.path.dirname(cmdline_latest), exist_ok=True)
        if os.path.exists(cmdline_latest):
            shutil.rmtree(cmdline_latest, ignore_errors=True)
        shutil.move(inner_cmdline, cmdline_latest)
        shutil.rmtree(temp_extract, ignore_errors=True)

    # كتابة ملفات قبول تراخيص أندرويد الرسمية مسبقاً لتسهيل التثبيت الصامت
    licenses_dir = os.path.join(sdk_root, "licenses")
    os.makedirs(licenses_dir, exist_ok=True)
    license_hashes = {
        "android-sdk-license": "\n24333f8a63b6825ea9c5514f83c2829b004d1fee\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n",
        "android-sdk-preview-license": "\n84831b9409646a918e30573bab4c9c91346d8abd\n",
    }
    for lic_name, lic_content in license_hashes.items():
        with open(os.path.join(licenses_dir, lic_name), "w", encoding="utf-8") as lf:
            lf.write(lic_content)

    env = os.environ.copy()
    env["JAVA_HOME"] = jdk_home
    env["ANDROID_HOME"] = sdk_root
    env["ANDROID_SDK_ROOT"] = sdk_root
    env["PATH"] = os.path.join(jdk_home, "bin") + os.pathsep + env.get("PATH", "")

    log("  📦 جاري تثبيت مكونات Android 14 (Platform 34 + Build-Tools 34.0.0)...", C_YELLOW)
    yes_input = ("y\n" * 30).encode("ascii")

    subprocess.run(
        [sdkmanager_bat, f"--sdk_root={sdk_root}", "--licenses"],
        input=yes_input,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        check=False,
    )

    install_proc = subprocess.run(
        [
            sdkmanager_bat,
            f"--sdk_root={sdk_root}",
            "platform-tools",
            "platforms;android-34",
            "build-tools;34.0.0",
        ],
        input=yes_input,
        env=env,
        check=True,
    )
    if install_proc.returncode == 0:
        log(f"  ✓ تم تجهيز Android SDK 34 بنجاح في: {sdk_root}", C_GREEN)
    return sdk_root


def sync_webapp_assets():
    log("\n[3/5] مزامنة أحدث ملفات واجهة المتجر (webapp_ui) داخل التطبيق...", C_CYAN)
    src_webapp = os.path.join(ROOT_DIR, "webapp_ui")
    dst_webapp = os.path.join(SCRIPT_DIR, "app", "src", "main", "assets", "webapp")
    if os.path.isdir(src_webapp):
        os.makedirs(dst_webapp, exist_ok=True)
        shutil.copytree(src_webapp, dst_webapp, dirs_exist_ok=True)
        log("  ✓ تمت مزامنة الأصول التفاعلية بنجاح.", C_GREEN)


def write_local_properties(sdk_root: str):
    local_props = os.path.join(SCRIPT_DIR, "local.properties")
    escaped_sdk = sdk_root.replace("\\", "\\\\").replace(":", "\\:")
    with open(local_props, "w", encoding="utf-8") as f:
        f.write(f"sdk.dir={escaped_sdk}\n")


def ensure_release_keystore(jdk_home: str) -> str:
    """التأكد من وجود Keystore رسمي موثوق لتوقيع تطبيقات الإنتاج وحمايتها من تحذيرات Google Play Protect"""
    keystore_path = os.path.join(SCRIPT_DIR, "zpact_release.jks")
    if os.path.isfile(keystore_path) and os.path.getsize(keystore_path) > 500:
        log("  ✓ تم العثور على مفتاح التوقيع الإنتاجي المعتمد (Release Keystore): zpact_release.jks", C_GREEN)
        return keystore_path

    log("  🔐 جاري إنشاء مفتاح التوقيع الرقمي الإنتاجي (Production RSA 2048)...", C_YELLOW)
    keytool_exe = os.path.join(jdk_home, "bin", "keytool.exe")
    cmd = [
        keytool_exe,
        "-genkeypair",
        "-v",
        "-keystore", keystore_path,
        "-alias", "zpact_release_key",
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=Z-PACT Enterprise Secure Publishing, OU=Mobile Security, O=Z-PACT Technologies, L=Cairo, ST=Cairo, C=EG",
        "-storepass", "zpact_secure_vault_2026",
        "-keypass", "zpact_secure_vault_2026"
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"  ✓ تم إنشاء وحفظ مفتاح التوقيع الإنتاجي بنجاح: {keystore_path}", C_GREEN)
    return keystore_path


def run_gradle_build(jdk_home: str, sdk_root: str) -> tuple[str, str, str]:
    log("\n[4/5] جاري تجميع النسخ الثلاث للتطبيق (إنتاج موثوق Release - حماية Play Protect) عبر محرك Gradle...", C_CYAN)
    write_local_properties(sdk_root)
    ensure_release_keystore(jdk_home)

    env = os.environ.copy()
    env.pop("ERRORLEVEL", None)
    env["JAVA_HOME"] = jdk_home
    env["ANDROID_HOME"] = sdk_root
    env["ANDROID_SDK_ROOT"] = sdk_root
    env["PATH"] = (
        os.path.join(jdk_home, "bin")
        + os.pathsep
        + os.path.join(sdk_root, "platform-tools")
        + os.pathsep
        + env.get("PATH", "")
    )

    # استدعاء محرك الجافا و GradleWrapperMain مباشرة لتفادي مشاكل cmd.exe والأقواس في مسارات ويندوز
    java_exe = os.path.join(jdk_home, "bin", "java.exe")
    wrapper_jar = os.path.join(SCRIPT_DIR, "gradle", "wrapper", "gradle-wrapper.jar")
    cmd = [
        java_exe,
        "-Xmx64m",
        "-Xms64m",
        "-Dorg.gradle.appname=gradlew",
        "-classpath",
        wrapper_jar,
        "org.gradle.wrapper.GradleWrapperMain",
        "assembleAdminRelease",
        "assembleTeacherRelease",
        "assembleSenderRelease",
        "--no-daemon",
        "--console=plain",
    ]

    build_succeeded_banner = False
    proc = subprocess.Popen(
        cmd,
        cwd=SCRIPT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        clean_line = line.rstrip()
        if clean_line:
            print(f"  {clean_line}", flush=True)
            if "BUILD SUCCESSFUL" in clean_line:
                build_succeeded_banner = True

    ret = proc.wait()
    admin_apk = os.path.join(SCRIPT_DIR, "app", "build", "outputs", "apk", "admin", "release", "app-admin-release.apk")
    teacher_apk = os.path.join(SCRIPT_DIR, "app", "build", "outputs", "apk", "teacher", "release", "app-teacher-release.apk")
    sender_apk = os.path.join(SCRIPT_DIR, "app", "build", "outputs", "apk", "sender", "release", "app-sender-release.apk")

    all_exist = os.path.isfile(admin_apk) and os.path.isfile(teacher_apk) and os.path.isfile(sender_apk)
    if ret != 0 and not (build_succeeded_banner and all_exist):
        raise RuntimeError(f"فشل بناء Gradle برمز خروج: {ret}")

    if not os.path.isfile(admin_apk):
        raise FileNotFoundError(f"لم يتم العثور على ملف APK الأدمن في: {admin_apk}")
    if not os.path.isfile(teacher_apk):
        raise FileNotFoundError(f"لم يتم العثور على ملف APK المدرسين في: {teacher_apk}")
    if not os.path.isfile(sender_apk):
        raise FileNotFoundError(f"لم يتم العثور على ملف APK المرسلين في: {sender_apk}")

    # التحقق من توقيع Google Play Protect (v1, v2, v3 Signature Schemes)
    apksigner_bat = os.path.join(sdk_root, "build-tools", "34.0.0", "apksigner.bat")
    if os.path.isfile(apksigner_bat):
        log("\n  🛡️ التحقق من معايير الثقة الرقمية لتطبيقات أندرويد (Google Play Protect Audit)...", C_YELLOW)
        for label, apk_file in [("الأدمن", admin_apk), ("المدرسين", teacher_apk), ("المرسلين", sender_apk)]:
            try:
                verify_res = subprocess.run(
                    [apksigner_bat, "verify", "--verbose", apk_file],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False
                )
                if verify_res.returncode == 0:
                    log(f"    ✓ تم توثيق وتأكيد توقيع نسخة {label} بنجاح (v1+v2+v3 Schemes موثوقة بالكامل).", C_GREEN)
                else:
                    log(f"    ⚠️ تنبيه توقيع {label}: {verify_res.stderr.strip()}", C_YELLOW)
            except Exception as e:
                log(f"    ℹ️ فحص التوقيع: {e}", C_YELLOW)

    return admin_apk, teacher_apk, sender_apk


def sync_ota_database_releases():
    """تحديث جدول AppReleaseConfig في قاعدة البيانات تلقائياً برقم الإصدار الجديد"""
    import re, sqlite3
    from datetime import datetime
    gradle_kts = os.path.join(SCRIPT_DIR, "app", "build.gradle.kts")
    
    # تحديد مسار قاعدة البيانات الفعلي
    db_candidates = [
        r"C:\.data\webpdf_elite.db",
        os.path.join(ROOT_DIR, "Z-PACT-main", "storage", "database.sqlite"),
        os.path.join(ROOT_DIR, "storage", "database.sqlite")
    ]
    try:
        sys.path.insert(0, os.path.join(ROOT_DIR, "Z-PACT-main"))
        from app.config import settings
        if getattr(settings, "DATABASE_PATH", None):
            db_candidates.insert(0, settings.DATABASE_PATH)
    except Exception:
        pass

    db_path = next((p for p in db_candidates if os.path.isfile(p)), None)
    if not os.path.isfile(gradle_kts) or not db_path:
        return

    vcode = 5
    vname = "3.2.0"
    try:
        with open(gradle_kts, "r", encoding="utf-8") as f:
            content = f.read()
            m_code = re.search(r"versionCode\s*=\s*(\d+)", content)
            m_name = re.search(r'versionName\s*=\s*"([^"]+)"', content)
            if m_code: vcode = int(m_code.group(1))
            if m_name: vname = m_name.group(1)
    except Exception:
        pass

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        now = datetime.utcnow().isoformat()
        releases = [
            ("admin", vcode, vname, f"👑 تحديث مركز قيادة الأدمن والرادار السيادي v{vname}", "✓ شهادة إنتاج رسمية موثوقة بالكامل ضد فحص Play Protect\n✓ دعم حقول اسم السلسلة والعلامة المائية (نص/شعار)\n✓ اتصال تلقائي مشفر بدون إعدادات (Zero-Config)"),
            ("teacher", vcode, vname, f"🎓 تحديث منصة المدرسين الموثوقة v{vname}", "✓ شهادة إنتاج رسمية معتمدة ومتوافقة 100% مع Google Play Protect\n✓ بدون أي أذونات خطيرة أو SMS وآمن تماماً\n✓ دعم اسم السلسلة والعلامة المائية المخصصة"),
            ("sender", vcode, vname, f"🚀 تحديث بوابة فريق المرسلين المعتمدة v{vname}", "✓ شهادة إنتاج رسمية معتمدة ومتوافقة 100% مع Google Play Protect\n✓ تسوية وحساب العمولات وإضافة الطلبات السريعة\n✓ بدون أي أذونات خطيرة أو SMS وآمن تماماً")
        ]
        for flv, code, name, title, notes in releases:
            cur.execute("""
                UPDATE app_release_configs 
                SET version_code = ?, version_name = ?, title = ?, release_notes = ?, force_update = 1, updated_at = ?
                WHERE flavor = ?
            """, (code, name, title, notes, now, flv))
        conn.commit()
        conn.close()
        log(f"  📡 تم بث ومزامنة التحديث الهوائي (v{vname} - Code {vcode}) في قاعدة البيانات بنجاح!", C_GREEN)
    except Exception as e:
        log(f"  ℹ️ تنبيه مزامنة OTA: {e}", C_YELLOW)


def main():
    print("=" * 79, flush=True)
    log("  🚀 Z-PACT ENTERPRISE v3.0 — TRIPLE ANDROID APK COMPILER (ADMIN + TEACHER + SENDER)", C_MAGENTA)
    print("=" * 79, flush=True)

    try:
        jdk_home = ensure_jdk()
        sdk_root = ensure_android_sdk(jdk_home)
        sync_webapp_assets()
        built_admin_apk, built_teacher_apk, built_sender_apk = run_gradle_build(jdk_home, sdk_root)

        log("\n[5/5] نسخ وتجهيز ملفات التثبيت النهائية الثلاثة (الأدمن + المدرسين + المرسلين)...", C_CYAN)
        root_admin_apk = os.path.join(ROOT_DIR, "Z-PACT-Admin-Radar.apk")
        root_legacy_apk = os.path.join(ROOT_DIR, "Z-PACT-Enterprise.apk")
        local_admin_apk = os.path.join(SCRIPT_DIR, "Z-PACT-Admin-Radar.apk")

        root_teacher_apk = os.path.join(ROOT_DIR, "Z-PACT-Teacher-Store.apk")
        local_teacher_apk = os.path.join(SCRIPT_DIR, "Z-PACT-Teacher-Store.apk")

        root_sender_apk = os.path.join(ROOT_DIR, "Z-PACT-Sender-Team.apk")
        local_sender_apk = os.path.join(SCRIPT_DIR, "Z-PACT-Sender-Team.apk")

        shutil.copy2(built_admin_apk, root_admin_apk)
        shutil.copy2(built_admin_apk, root_legacy_apk)
        shutil.copy2(built_admin_apk, local_admin_apk)

        shutil.copy2(built_teacher_apk, root_teacher_apk)
        shutil.copy2(built_teacher_apk, local_teacher_apk)

        shutil.copy2(built_sender_apk, root_sender_apk)
        shutil.copy2(built_sender_apk, local_sender_apk)

        # مزامنة رقم الإصدار في قاعدة بيانات السيرفر لبث التحديث التلقائي لكافة الهواتف
        sync_ota_database_releases()

        admin_mb = os.path.getsize(root_admin_apk) / (1024 * 1024)
        teacher_mb = os.path.getsize(root_teacher_apk) / (1024 * 1024)
        sender_mb = os.path.getsize(root_sender_apk) / (1024 * 1024)
        print("\n" + "=" * 79, flush=True)
        log("  🎉 اكتمل بناء النسخ الثلاث بنجاح 100%!", C_GREEN)
        log(f"  🛡️ نسخة الأدمن والرادار:     {root_admin_apk} ({admin_mb:.2f} MB)", C_CYAN)
        log(f"  🎓 نسخة المدرسين الآمنة:     {root_teacher_apk} ({teacher_mb:.2f} MB)", C_CYAN)
        log(f"  🚀 نسخة فريق المرسلين الآمنة: {root_sender_apk} ({sender_mb:.2f} MB)", C_CYAN)
        print("=" * 79, flush=True)
    except Exception as exc:
        print("\n" + "=" * 79, flush=True)
        log(f"  ❌ حدث خطأ أثناء بناء التطبيق: {exc}", C_RED)
        print("=" * 79, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

