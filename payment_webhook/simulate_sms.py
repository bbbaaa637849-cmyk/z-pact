# ================================================================
# payment_webhook/simulate_sms.py — أداة محاكاة رسائل الدفع والتحويلات
# ================================================================

import sys
import secrets
import urllib.request
import json

WEBHOOK_URL = "http://127.0.0.1:8001/webhook/sms"
WEBHOOK_SECRET = "zpact_sms_webhook_secret_2026"

def send_sms_payload(sender: str, text: str):
    """إرسال رسالة المحاكاة إلى سيرفر الويب هوك"""
    payload = {
        "sender": sender,
        "message": text,
        "token": WEBHOOK_SECRET
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=data_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Secret": WEBHOOK_SECRET
        }
    )

    print("\n---------------------------------------------------------")
    print(f"📤 إرسال رسالة تجريبية من: [{sender}]")
    print(f"📝 النص: \"{text}\"")
    print("---------------------------------------------------------")

    try:
        with urllib.request.urlopen(req, timeout=10.0) as res:
            res_data = json.loads(res.read().decode("utf-8"))
            print(f"✅ كود الاستجابة: {res.status}")
            print("📊 النتيجة:", json.dumps(res_data, indent=2, ensure_ascii=False))
            print("---------------------------------------------------------\n")
            return res_data
    except Exception as e:
        print(f"❌ تعذر الاتصال بمحرك الويب هوك: {e}")
        print("💡 تأكد من تشغيل orchestrator.py أو سيرفر payment_webhook على المنفذ 8001.\n")
        return None


def main():
    print("""
=========================================================
  ⚡ محاكي رسائل الدفع وتحويلات الكاش لمنظومة Z-PACT ⚡
=========================================================
1. محاكاة تحويل فودافون كاش (Vodafone Cash)
2. محاكاة تحويل إنستاباي بنكي (InstaPay)
3. محاكاة تحويل أورنج كاش (Orange Cash)
4. محاكاة تحويل اتصالات كاش (Etisalat Cash)
5. إدخال نص رسالة SMS مخصص بحرية
6. خروج
=========================================================
""")

    choice = input("👉 اختر رقم العملية (1-6): ").strip()
    if choice == "6" or not choice:
        print("مع السلامة!")
        return

    phone = input("📱 أدخل رقم هاتف الراسل (أو اضغط Enter للافتراضي 01012345678): ").strip()
    if not phone:
        phone = "01012345678"

    amount_str = input("💵 أدخل المبلغ المحول بالجنية (أو اضغط Enter لـ 150): ").strip()
    amount = float(amount_str) if amount_str else 150.0

    ref_code = secrets.token_hex(4).upper()

    if choice == "1":
        # Vodafone Cash
        msg = f"تم استلام مبلغ {amount:.2f} جنيه من رقم {phone} بنجاح. كود العملية {ref_code}"
        send_sms_payload("VodafoneCash", msg)

    elif choice == "2":
        # InstaPay
        msg = f"You received EGP {amount:.2f} from {phone} via InstaPay Ref: IP{ref_code}"
        send_sms_payload("InstaPay", msg)

    elif choice == "3":
        # Orange Cash
        msg = f"تم إضافة مبلغ {amount:.2f} جنيه من {phone} الى محفظة اورنچ كاش الخاصة بك رقم العملية {ref_code}"
        send_sms_payload("OrangeCash", msg)

    elif choice == "4":
        # Etisalat Cash
        msg = f"تم استلام مبلغ {amount:.2f} جنيه من رقم {phone} في محفظة اتصالات كاش كود {ref_code}"
        send_sms_payload("EtisalatCash", msg)

    elif choice == "5":
        # Custom SMS
        custom_text = input("✍️ اكتب نص الرسالة النصية بالكامل كما تصل من البنك أو المحفظة:\n> ").strip()
        if custom_text:
            send_sms_payload("CustomSender", custom_text)

    else:
        print("خيار غير صحيح!")

if __name__ == "__main__":
    main()
