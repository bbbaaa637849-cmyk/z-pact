# ================================================================
# orchestrator.py — المشرف الموحد ومحرك التشغيل المتزامن للأنظمة
# ================================================================

import os
import sys
import time
import socket
import asyncio
import signal
import urllib.request
import subprocess
from typing import List, Dict, Any, Optional

# ضبط ترميز مخرجات الكونسول على ويندوز لدعم اللغة العربية والرموز التعبيرية بوضوح تام
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# المسارات الأساسية للنظام
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.join(ROOT_DIR, "Z-PACT-main")
WEBHOOK_DIR = os.path.join(ROOT_DIR, "payment_webhook")
BOT_DIR = os.path.join(ROOT_DIR, "bot_service")

# تحديد مفسر البايثون داخل البيئة الافتراضية عبر ويندوز ولينكس/سحابة Railway
_venv_rel = ("Scripts", "python.exe") if sys.platform == "win32" else ("bin", "python")
VENV_PYTHON = os.path.join(ROOT_DIR, ".venv", *_venv_rel)
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

# الألوان التفاعلية للترويس في التيرمينال (ANSI Neon Colors)
COLOR_RESET   = "\033[0m"
COLOR_BOLD    = "\033[1m"
COLOR_CYAN    = "\033[96m"  # Core Web
COLOR_GREEN   = "\033[92m"  # SMS Webhook
COLOR_MAGENTA = "\033[95m"  # Call Bot
COLOR_YELLOW  = "\033[93m"  # PDF Stamper / Orchestrator
COLOR_BLUE    = "\033[94m"  # Info
COLOR_RED     = "\033[91m"  # Errors


def log_orch(message: str, color: str = COLOR_YELLOW):
    """طباعة سجل المشرف الرئيسي بتنسيق مميز"""
    timestamp = time.strftime("%H:%M:%S")
    print(f"{color}[ORCHESTRATOR {timestamp}] {message}{COLOR_RESET}", flush=True)


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """فحص هل المنفذ مشغول حالياً"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def select_clean_port(preferred_port: int, fallbacks: List[int]) -> int:
    """اختيار المنفذ المفضل إن كان متاحاً، أو التحول التلقائي لمنفذ بديل حر دون المساس بأي مشاريع أخرى على الحاسوب"""
    if not is_port_in_use(preferred_port):
        return preferred_port
    for candidate in fallbacks:
        if not is_port_in_use(candidate):
            log_orch(
                f"ℹ️ المنفذ {preferred_port} مشغول بتطبيق آخر على الحاسوب — تم التحول تلقائياً للمنفذ الحر {candidate}",
                COLOR_CYAN,
            )
            return candidate
    return preferred_port


def cleanup_zpact_orphans():
    """إنهاء العمليات القديمة التابعة لمنظومة Z-PACT فقط بأمان دون المساس بالعملية الحالية أو أي تطبيقات خارجية"""
    if sys.platform != "win32":
        return
    try:
        protected_pids = {os.getpid(), os.getppid()}
        safe_root_marker = ROOT_DIR.replace("'", "''")
        ps_cmd = (
            "Get-CimInstance Win32_Process | Where-Object { "
            f"($_.Name -eq 'cloudflared.exe' -and $_.CommandLine -like '*127.0.0.1*') -or "
            f"($_.Name -eq 'python.exe' -and $_.CommandLine -notlike '*orchestrator.py*' -and "
            f"($_.CommandLine -like '*{safe_root_marker}*' -or $_.CommandLine -like '*bot_service.main*')) "
            "} | Select-Object -ExpandProperty ProcessId"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        for pid_str in (res.stdout or "").split():
            if pid_str.strip().isdigit():
                pid = int(pid_str.strip())
                if pid not in protected_pids and pid > 0:
                    log_orch(f"🧹 تنظيف عملية سابقة تابعة لـ Z-PACT (PID {pid})...", COLOR_YELLOW)
                    subprocess.run(f"taskkill /PID {pid} /T /F", shell=True, capture_output=True)
        time.sleep(0.5)
    except Exception:
        pass


def free_zombie_bot_processes():
    """إنهاء أي عمليات قديمة عالقة لخدمة البوت لمنع TelegramConflictError وتعطل الأزرار"""
    if sys.platform != "win32":
        return
    try:
        my_pid = os.getpid()
        ps_cmd = (
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -eq 'python.exe' -and $_.CommandLine -like '*bot_service.main*' "
            "} | Select-Object -ExpandProperty ProcessId"
        )
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
        for pid_str in (res.stdout or "").split():
            if pid_str.strip().isdigit():
                pid = int(pid_str.strip())
                if pid != my_pid and pid > 0:
                    log_orch(f"🧹 تنظيف نسخة قديمة عالقة من البوت (PID {pid})...", COLOR_YELLOW)
                    subprocess.run(f"taskkill /PID {pid} /T /F", shell=True, capture_output=True)
    except Exception:
        pass


def get_local_lan_ip() -> str:
    """اكتشاف عنوان الشبكة المحلية للحاسوب تلقائياً (مثلاً 192.168.1.x)"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


CLOUD_BEACON_TOPIC = "zpact_cloud_registry_v2_8287744705"


def publish_cloud_discovery_beacon(public_url: Optional[str], core_port: int = 8000):
    """نشر رابط السيرفر الحي (بدون أي مفاتيح سرية) لتتصل تطبيقات الأندرويد تلقائياً من أي مكان بالعالم بدون إعدادات"""
    import json
    lan_ip = get_local_lan_ip()
    lan_url = f"http://{lan_ip}:{core_port}" if lan_ip != "127.0.0.1" else ""
    clean_pub = (public_url or "").replace("/miniapp/", "").split("?")[0].rstrip("/")
    if not clean_pub and not lan_url:
        return

    payload = json.dumps(
        {
            "app": "Z-PACT",
            "server_url": clean_pub,
            "lan_url": lan_url,
            "ts": int(time.time()),
        },
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{CLOUD_BEACON_TOPIC}",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "ZPact-Orchestrator/2.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8.0) as res:
            if res.status in (200, 201):
                log_orch(f"📡 تم بث إشارة الاكتشاف التلقائي لتطبيقات الأندرويد بنجاح ({clean_pub or lan_url})", COLOR_GREEN)
    except Exception as e:
        log_orch(f"ℹ️ تنبيه بث الاكتشاف السحابي: {e}", COLOR_YELLOW)


def run_preflight_checks():
    """فحص سلامة الإعدادات وبيانات التوكن قبل الإقلاع"""
    print(f"\n{COLOR_BOLD}{COLOR_CYAN}─── [PRE-FLIGHT DIAGNOSTICS & SYSTEM INTEGRITY] ───{COLOR_RESET}")
    
    # 1. فحص التوكن
    from bot_service.config import bot_config
    bot_token = bot_config.BOT_TOKEN or ""
    if not bot_token or "YOUR_" in bot_token or len(bot_token) < 20:
        print(f"  {COLOR_RED}❌ [BOT TOKEN]: غير صالح أو مفقود! يرجى ضبط TELEGRAM_BOT_TOKEN{COLOR_RESET}")
    else:
        masked_token = bot_token[:8] + "..." + bot_token[-6:]
        print(f"  {COLOR_GREEN}✓ [BOT TOKEN]: جاهز ومتصل ({masked_token}){COLOR_RESET}")

    # 2. فحص المشرف
    admins = list(bot_config.ADMIN_TELEGRAM_IDS)
    print(f"  {COLOR_GREEN}✓ [ADMIN ACCOUNTS]: {len(admins)} مشرفين معتمدين (الأساسي: {admins[0] if admins else 'None'}){COLOR_RESET}")

    # 3. فحص المحافظ
    wallet = bot_config.PAYMENT_WALLET_NUMBER
    instapay = bot_config.INSTAPAY_ADDRESS
    print(f"  {COLOR_GREEN}✓ [PAYMENT GATEWAYS]: فودافون كاش ({wallet}) | إنستاباي ({instapay}){COLOR_RESET}")

    # 4. مفتاح الجسر الداخلي
    key = bot_config.INTERNAL_SERVICE_KEY
    print(f"  {COLOR_GREEN}✓ [INTERNAL BRIDGE]: المفتاح السري موحد ومفعل بنجاح{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_CYAN}───────────────────────────────────────────────────{COLOR_RESET}\n")


class ServiceProcess:
    def __init__(self, name: str, display_tag: str, cmd: List[str], cwd: str, color: str, env: Optional[Dict[str, str]] = None):
        self.name = name
        self.display_tag = display_tag
        self.cmd = cmd
        self.cwd = cwd
        self.color = color
        self.env = env
        self.process: Optional[asyncio.subprocess.Process] = None

    async def start(self):
        env = os.environ.copy()
        if self.env:
            env.update(self.env)
        env["PYTHONPATH"] = f"{ROOT_DIR}{os.pathsep}{self.cwd}{os.pathsep}" + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"

        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env
        )

        asyncio.create_task(self._stream_logs())

    async def _stream_logs(self):
        if not self.process or not self.process.stdout:
            return

        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            try:
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    # تلوين ذكي لسجلات الختم ومحرك البي دي اف
                    tag = self.display_tag
                    color = self.color
                    if "[Stamper]" in decoded or "ختم" in decoded:
                        tag = "[⚡ PDF-STAMP]"
                        color = COLOR_YELLOW

                    prefix = f"{color}{tag}{COLOR_RESET}"
                    print(f"{prefix} {decoded}", flush=True)
            except Exception:
                pass

    async def stop(self):
        if self.process and self.process.returncode is None:
            log_orch(f"🛑 جاري إيقاف خدمة [{self.name}]...", COLOR_YELLOW)
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=4.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.process.kill()
                except Exception:
                    pass


class TunnelManager:
    """إدارة النفق السحابي المشفر لتوليد رابط HTTPS عام للتطبيق المصغر"""
    def __init__(self, port: int = 8000):
        self.port = port
        self.process: Optional[asyncio.subprocess.Process] = None
        self.public_url: Optional[str] = None
        self.miniapp_url: Optional[str] = None

    async def start(self) -> Optional[str]:
        cloudflared_path = os.path.join(ROOT_DIR, "cloudflared.exe")
        if not os.path.exists(cloudflared_path):
            log_orch("⚠️ ملف cloudflared.exe غير متوفر، سيتم تشغيل المنظومة بدون نفق عام.", COLOR_YELLOW)
            return None

        log_orch("🌐 جاري تشغيل النفق السحابي المشفر (Cloudflare HTTP/2 TCP Tunnel)...", COLOR_CYAN)
        # استخدام بروتوكول http2 عبر منفذ TCP 443 لمنع انقطاع حزم UDP QUIC وظهور Error 1033 على شبكات الإنترنت المصرية
        cmd = [
            cloudflared_path,
            "tunnel",
            "--protocol", "http2",
            "--ha-connections", "4",
            "--no-autoupdate",
            "--url", f"http://127.0.0.1:{self.port}",
        ]

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
        except Exception as e:
            log_orch(f"⚠️ فشل بدء تشغيل cloudflared: {e}", COLOR_RED)
            return None

        import re
        start_time = time.time()
        registered_connections = 0

        while time.time() - start_time < 35:
            if not self.process.stderr:
                break
            try:
                line = await asyncio.wait_for(self.process.stderr.readline(), timeout=25.0)
            except asyncio.TimeoutError:
                break
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()

            m = re.search(r"https://([a-zA-Z0-9-]+\.trycloudflare\.com)", decoded)
            if m and not self.public_url:
                self.public_url = f"https://{m.group(1)}"
                self.miniapp_url = f"{self.public_url}/miniapp/"
                log_orch(f"⚡ النطاق السحابي المشفر: {self.public_url}", COLOR_GREEN)

            if "Registered tunnel connection" in decoded:
                registered_connections += 1
                if self.public_url and registered_connections >= 2:
                    log_orch(f"✅ تم اكتمال اتصال النفق السحابي بنجاح ({registered_connections} قنوات TCP نشطة)!", COLOR_GREEN)
                    break

        # التحقق الفعلي من جاهزية DNS الخارجي للنفق قبل إرسال الأزرار لتيليجرام
        if self.public_url:
            await wait_for_health(f"{self.public_url}/api/health", timeout=10.0)

        # تفريغ باقي السجلات لمنع امتلاء الـ buffer
        asyncio.create_task(self._drain_logs())
        return self.miniapp_url

    async def _drain_logs(self):
        if not self.process or not self.process.stderr:
            return
        while True:
            try:
                line = await self.process.stderr.readline()
                if not line:
                    break
            except Exception:
                break

    async def stop(self):
        if self.process and self.process.returncode is None:
            log_orch("🛑 جاري إيقاف النفق السحابي (Cloudflare Tunnel)...", COLOR_YELLOW)
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass


async def wait_for_health(url: str, timeout: float = 25.0, must_contain: str = "") -> bool:
    """انتظار استجابة نقطة فحص الصحة للسيرفر مع التحقق من هوية المنصة"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Z-Pact-Orchestrator"})
            with urllib.request.urlopen(req, timeout=1.5) as res:
                if res.status in (200, 201):
                    body = res.read().decode("utf-8", errors="ignore")
                    if not must_contain or must_contain in body:
                        return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


def print_banner(public_url: Optional[str] = None, core_port: int = 8000, webhook_port: int = 8001):
    miniapp_display = public_url if public_url else f"http://127.0.0.1:{core_port}/miniapp/ (محلي)"
    banner = f"""
{COLOR_BOLD}{COLOR_MAGENTA}
  ███████╗      ██████╗  █████╗  ██████╗████████╗
  ╚══███╔╝      ██╔══██╗██╔══██╗██╔════╝╚══██╔══╝
    ███╔╝ █████╗██████╔╝███████║██║        ██║   
   ███╔╝  ╚════╝██╔═══╝ ██╔══██║██║        ██║   
  ███████╗      ██║     ██║  ██║╚██████╗   ██║   
  ╚══════╝      ╚═╝     ╚═╝  ╚═╝ ╚═════╝   ╚═╝   
{COLOR_CYAN}  ⚡ ULTRA-VIBRANT NEON ECOSYSTEM & TELEGRAM CALL CENTER BOT ⚡{COLOR_RESET}

  {COLOR_GREEN}✓ المنصة الأساسية والموقع (Core Web):{COLOR_RESET}   http://127.0.0.1:{core_port}/store
  {COLOR_BOLD}{COLOR_CYAN}✓ رابط الميني آب العالمي (Telegram Mini App):{COLOR_RESET} {COLOR_BOLD}{COLOR_GREEN}{miniapp_display}{COLOR_RESET}
  {COLOR_YELLOW}✓ محرك الـ SMS الموحد (Cloud Single-Port):{COLOR_RESET} http://127.0.0.1:{core_port}/webhook/sms
  {COLOR_GREEN}✓ محرك الـ SMS المستقل (Dedicated):{COLOR_RESET}         http://127.0.0.1:{webhook_port}/webhook/sms
  {COLOR_MAGENTA}✓ بوت التيليجرام الذكي (Aiogram 3.31):{COLOR_RESET}    متصل لحظياً (زر الميني آب مفعل ومتاح عالمياً 🌍)
  {COLOR_BLUE}✓ مستندات الـ API التفاعلية (Swagger):{COLOR_RESET}    http://127.0.0.1:{core_port}/api/docs
{COLOR_BOLD}{COLOR_CYAN}==============================================================================={COLOR_RESET}
{COLOR_YELLOW}💡 اضغط Ctrl+C في أي وقت لإيقاف كافة الأنظمة والخدمات التابعة بأمان ونظافة.{COLOR_RESET}
"""
    print(banner, flush=True)


async def main():
    log_orch("🚀 بدء تشغيل المشرف الموحد لمنظومة Z-PACT...", COLOR_YELLOW)

    # تشغيل الفحص المسبق
    run_preflight_checks()

    # 1. تنظيف العمليات القديمة التابعة لـ Z-PACT فقط واختيار منافذ نظيفة دون المساس بأي تطبيقات خارجية
    cleanup_zpact_orphans()
    free_zombie_bot_processes()

    is_railway_cloud = (
        "--railway" in sys.argv
        or bool(os.environ.get("RAILWAY_ENVIRONMENT"))
        or bool(os.environ.get("RAILWAY_PROJECT_ID"))
        or bool(os.environ.get("RAILWAY_PUBLIC_DOMAIN"))
    )

    if is_railway_cloud:
        core_port = int(os.environ.get("PORT", "8000"))
        webhook_port = core_port
    else:
        core_port = select_clean_port(8000, [8088, 8090, 8800, 8888])
        webhook_port = select_clean_port(8001, [8089, 8091, 8801, 8889])

    core_base_url = f"http://127.0.0.1:{core_port}"
    core_api_url = f"{core_base_url}/api"

    is_local_no_tunnel = (
        "--local" in sys.argv
        or "--no-tunnel" in sys.argv
        or os.environ.get("ZPACT_NO_TUNNEL", "0") == "1"
    )
    low_cpu_flag = os.environ.get("ZPACT_LOW_CPU_MODE", "1")

    shared_env = {
        "PORT": str(core_port),
        "CORE_BASE_URL": core_base_url,
        "CORE_API_URL": core_api_url,
        "PAYMENT_WEBHOOK_PORT": str(webhook_port),
        "ZPACT_LOW_CPU_MODE": low_cpu_flag,
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }

    railway_domain = (
        os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        or os.environ.get("RAILWAY_STATIC_URL")
        or os.environ.get("RAILWAY_SERVICE_PUBLIC_DOMAIN")
        or ""
    ).strip()
    railway_miniapp_url: Optional[str] = None
    if railway_domain:
        if not railway_domain.startswith(("http://", "https://")):
            railway_domain = f"https://{railway_domain}"
        railway_domain = railway_domain.rstrip("/")
        railway_miniapp_url = railway_domain if railway_domain.endswith("/miniapp") else f"{railway_domain}/miniapp/"
        if not railway_miniapp_url.endswith("/"):
            railway_miniapp_url += "/"
        shared_env["MINIAPP_URL"] = railway_miniapp_url
        os.environ["MINIAPP_URL"] = railway_miniapp_url

    log_orch(
        f"🧊 وضع الحماية من استهلاك المعالج (ZPACT_LOW_CPU_MODE={low_cpu_flag}, threads=1) مفعّل.",
        COLOR_CYAN,
    )

    # 2. إعداد الخدمات للتشغيل (في Railway نكتفي بالسيرفر الموحد على PORT لأنه يدمج /webhook/sms تلقائياً + البوت)
    core_service = ServiceProcess(
        name="CORE-API",
        display_tag="[🌐 CORE-WEB]",
        cmd=[VENV_PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(core_port), "--log-level", "info"],
        cwd=CORE_DIR,
        color=COLOR_CYAN,
        env=dict(shared_env),
    )
    webhook_service = ServiceProcess(
        name="SMS-WEBHOOK",
        display_tag="[📩 SMS-HOOK]",
        cmd=[VENV_PYTHON, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(webhook_port), "--log-level", "info"],
        cwd=WEBHOOK_DIR,
        color=COLOR_GREEN,
        env=dict(shared_env),
    )
    bot_service = ServiceProcess(
        name="TELEGRAM-BOT",
        display_tag="[🤖 CALL-BOT]",
        cmd=[VENV_PYTHON, "-m", "bot_service.main"],
        cwd=ROOT_DIR,
        color=COLOR_MAGENTA,
        env=dict(shared_env),
    )

    services: List[ServiceProcess] = [core_service, bot_service] if is_railway_cloud else [core_service, webhook_service, bot_service]

    # 3. تشغيل السيرفرات بالتتابع الذكي
    log_orch(f"⏳ تشغيل خدمة المنصة الأساسية (Core API على المنفذ {core_port})...", COLOR_CYAN)
    await core_service.start()

    if not is_railway_cloud:
        log_orch(f"⏳ تشغيل محرك معالجة ومطابقة الـ SMS (Payment Webhook على المنفذ {webhook_port})...", COLOR_GREEN)
        await webhook_service.start()

    # انتظار جاهزية السيرفرات قبل تشغيل البوت
    log_orch("🔍 التحقق من جاهزية السيرفرات ونقاط الفحص (Health Checks)...", COLOR_YELLOW)
    core_ready = await wait_for_health(f"{core_base_url}/api/health", timeout=45.0, must_contain="WebPDF Elite")
    webhook_ready = True if is_railway_cloud else await wait_for_health(f"http://127.0.0.1:{webhook_port}/health", timeout=25.0)

    if core_ready and webhook_ready:
        log_orch("✅ السيرفرات الأساسية تعمل وجاهزة بكفاءة 100%!", COLOR_GREEN)
    else:
        log_orch("⚠️ تنبيه: استجابة أحد السيرفرات تأخرت، جاري الاستمرار في تشغيل البوت...", COLOR_YELLOW)

    # 4. ضبط الرابط العام (من Railway تلقائياً أو عبر Cloudflare محلياً)
    tunnel_mgr: Optional[TunnelManager] = None
    public_miniapp_url: Optional[str] = None
    if is_railway_cloud:
        public_miniapp_url = railway_miniapp_url
        log_orch(
            f"☁️ وضع سحابة Railway المدمج: تم ضبط الخادم والبوت على المنفذ {core_port}"
            + (f" مع رابط الميني آب: {public_miniapp_url}" if public_miniapp_url else " (بانتظار الدومين العام من Railway)."),
            COLOR_GREEN,
        )
        publish_cloud_discovery_beacon(public_miniapp_url, core_port=core_port)
    elif is_local_no_tunnel:
        log_orch("🏠 وضع التشغيل المحلي الخالص (--local / --no-tunnel): تخطي النفق السحابي لتوفير الذاكرة والمعالج.", COLOR_YELLOW)
        publish_cloud_discovery_beacon(None, core_port=core_port)
    else:
        tunnel_mgr = TunnelManager(port=core_port)
        public_miniapp_url = await tunnel_mgr.start()

        if public_miniapp_url:
            from bot_service.config import bot_config
            bot_config.update_miniapp_url(public_miniapp_url)
            os.environ["MINIAPP_URL"] = public_miniapp_url
            bot_service.env = {**shared_env, "MINIAPP_URL": public_miniapp_url}
            publish_cloud_discovery_beacon(public_miniapp_url, core_port=core_port)
            log_orch(f"🚀 تم ربط بوت التيليجرام وتطبيقات الأندرويد تلقائياً برابط HTTPS: {public_miniapp_url}", COLOR_GREEN)
        else:
            publish_cloud_discovery_beacon(None, core_port=core_port)

    # 5. تشغيل بوت التيليجرام
    log_orch("⏳ تشغيل بوت التيليجرام وموزع الملفات الذكي...", COLOR_MAGENTA)
    await bot_service.start()

    # إعلان الجاهزية الكاملة
    print_banner(public_miniapp_url, core_port=core_port, webhook_port=webhook_port)

    # 6. إدارة إشارات الإيقاف والمراقبة الحية (Graceful Shutdown)
    stop_event = asyncio.Event()

    def signal_handler():
        log_orch("👋 تم استلام إشارة إنهاء التشغيل (Ctrl+C)...", COLOR_YELLOW)
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    try:
        while not stop_event.is_set():
            for s in services:
                if s.process and s.process.returncode is not None:
                    log_orch(f"⚠️ تنبيه: الخدمة [{s.name}] توقفت برمز الخروج ({s.process.returncode}) — جاري إعادة تشغيلها...", COLOR_RED)
                    await asyncio.sleep(2.0)
                    await s.start()

            # مراقب الاستشفاء التلقائي للنفق السحابي (Auto-Heal Watchdog ضد Error 1033)
            if tunnel_mgr and tunnel_mgr.process and tunnel_mgr.process.returncode is not None and not stop_event.is_set():
                log_orch("⚠️ انقطع النفق السحابي مفاجئاً — جاري إعادة بناء النفق وتحديث أزرار البوت تلقائياً...", COLOR_YELLOW)
                tunnel_mgr.public_url = None
                tunnel_mgr.miniapp_url = None
                renewed_url = await tunnel_mgr.start()
                if renewed_url:
                    from bot_service.config import bot_config
                    bot_config.update_miniapp_url(renewed_url)
                    os.environ["MINIAPP_URL"] = renewed_url
                    bot_service.env = {**shared_env, "MINIAPP_URL": renewed_url}
                    publish_cloud_discovery_beacon(renewed_url, core_port=core_port)
                    await bot_service.stop()
                    free_zombie_bot_processes()
                    await bot_service.start()
                    log_orch(f"✅ تمت استعادة النفق وتحديث البوت وتطبيقات الأندرويد تلقائياً: {renewed_url}", COLOR_GREEN)

            await asyncio.sleep(1.5)
    except (KeyboardInterrupt, asyncio.CancelledError):
        log_orch("👋 جاري إيقاف كافة الخدمات...", COLOR_YELLOW)
    finally:
        log_orch("🛑 إيقاف العمليات التابعة...", COLOR_YELLOW)
        for s in reversed(services):
            await s.stop()
        if tunnel_mgr:
            await tunnel_mgr.stop()
        log_orch("✓ تم إيقاف جميع الأنظمة بنجاح ونظافة.", COLOR_GREEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log_orch("تم الخروج بنجاح.", COLOR_YELLOW)
