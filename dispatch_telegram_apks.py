import os
import sys
import httpx

BOT_TOKEN = "9095089179:AAFiTxEzAYn_poUUGXuRf_l0NnsL2W1twkM"
ADMIN_CHAT_ID = "8287744705"

def send_file(apk_path: str, caption: str):
    if not os.path.isfile(apk_path):
        print(f"File not found: {apk_path}")
        return False
    size_mb = os.path.getsize(apk_path) / (1024 * 1024)
    print(f"Uploading {os.path.basename(apk_path)} ({size_mb:.2f} MB) to {ADMIN_CHAT_ID}...")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    with open(apk_path, "rb") as f:
        files = {"document": (os.path.basename(apk_path), f, "application/vnd.android.package-archive")}
        data = {
            "chat_id": ADMIN_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML"
        }
        resp = httpx.post(url, data=data, files=files, timeout=120)
        if resp.status_code == 200 and resp.json().get("ok"):
            print(f" Successfully dispatched {os.path.basename(apk_path)}")
            return True
        else:
            print(f" Failed: {resp.status_code} - {resp.text}")
            return False

if __name__ == "__main__":
    teacher_caption = (
        "<b>🎓 منصة Z-PACT الرسمية للمدرسين والمتجر v3.3.2</b>\n\n"
        "🛡️ <b>تحديث الأمان المعتمد وحل مشكلة Play Protect نهائياً:</b>\n"
        "✓ تم تنظيف التطبيق بالكامل من أي أذونات خطيرة (إزالة أذونات تثبيت الحزم الخارجية والتخزين القديم).\n"
        "✓ لا يطلب التطبيق إلا إذن الإنترنت العادي، متوافق 100% مع معايير حماية جوجل بلاي.\n"
        "✓ دعم كامل لاختيار اسم السلسلة والعلامة المائية (نص أو صورة) وتصفح المتجر بسلاسة."
    )
    send_file("Z-PACT-Teacher-Store.apk", teacher_caption)

    sender_caption = (
        "<b>🚀 تطبيق فريق المرسلين المعتمد v3.3.2</b>\n\n"
        "🛡️ <b>تحديث الأمان النظيف:</b>\n"
        "✓ متوافق 100% مع حماية جوجل بلاي Play Protect بدون أي تحذيرات.\n"
        "✓ متابعة الطلبات السريعة وحساب العمولات لحظياً.\n"
        "✓ اتصال تلقائي مشفر بالسيرفر بدون أي إعدادات."
    )
    send_file("Z-PACT-Sender-Team.apk", sender_caption)

    admin_caption = (
        "<b>👑 Z-PACT Enterprise Admin Radar v3.3.2 (مركز القيادة والرادار)</b>\n\n"
        "🛡️ <b>حل مشكلة الرادار وفلترة الرسائل العشوائية:</b>\n"
        "✓ يلتقط حصرياً رسائل استلام الأموال النقدية الحقيقية مع التحقق من رقم الراسل أو كود العملية.\n"
        "✓ حظر تام لكافة رسائل العروض والمسابقات وشحن الرصيد والرسائل الترويجية من كافة الشركات.\n"
        "✓ تنظيف تلقائي فوري لكافة السجلات العشوائية السابقة بمجرد فتح التطبيق.\n"
        "✓ ربط مباشر وتلقائي للميني آب والمتجر."
    )
    send_file("Z-PACT-Admin-Radar.apk", admin_caption)

