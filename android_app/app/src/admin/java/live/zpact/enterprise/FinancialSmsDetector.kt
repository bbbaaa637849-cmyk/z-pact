package live.zpact.enterprise

import java.security.MessageDigest
import java.util.regex.Pattern

/**
 * المحلل الذكي الصارم للتحويلات المالية الواردة الحقيقية فقط في مصر:
 * - فودافون كاش، إنستاباي، أورانج كاش، اتصالات كاش، WE Pay، والتحويلات البنكية اللحظية.
 * - حظر صارم بنسبة 100% لأي رسائل ترويجية أو إعلانات مسابقات وربح (مثل: كسبت، مبروك، اشحن، باقة، كاش باك، سحب على).
 * - اشتراط وجود صيغة استلام مالي مؤكدة + طرف محول منه + مبلغ حقيقي.
 */
object FinancialSmsDetector {

    private val ARABIC_DIGITS = charArrayOf('٠', '١', '٢', '٣', '٤', '٥', '٦', '٧', '٨', '٩')
    private val PERSIAN_DIGITS = charArrayOf('۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹')

    // قائمة سوداء صارمة: أي رسالة تحتوي على أي من هذه العبارات يتم استبعادها فوراً كرسالة ترويجية/إعلانية/شحن
    private val SPAM_AND_PROMO_KEYWORDS = listOf(
        // عبارات الربح والمسابقات والهدايا والعروض
        "كسبت", "مبروك", "اربح", "ربحت", "سحب على", "فرصة لربح", "مليونير",
        "هدية", "هديتك", "هدايا", "عجلة الحظ", "صندوق الحظ", "عرض خاص", "عروض",
        "خصم", "خصومات", "كاش باك", "cashback", "قسيمة", "كوبون", "تسوق",
        // الشحن والباقات ورصيد المكالمات والإنترنت
        "اشحن", "عند الشحن", "شحن رصيد", "تم شحن", "كارت شحن", "كروت شحن", "رصيد شحن",
        "باقة", "باقات", "تجديد باقة", "تم تجديد", "فليكس", "فليكسات", "فلكسات",
        "ميجا", "جيجا", "دقائق", "وحدات", "إنترنت", "انترنت", "سوشيال",
        // خدمات إضافية ومصاريف وفواتير
        "سلفني", "شكراً", "خدمة سلفني", "استهلاكك", "استهلكت",
        "فاتورة", "فاتورتك", "سداد فاتورة", "دفع فاتورة",
        // عمليات صادرة أو خصم مصاريف أو تحويل رصيد مكالمات
        "تم تحويل رصيد", "تحويل رصيد", "تم خصم", "تم سحب مبلغ", "سحب نقدي من",
        "مصاريف إدارية", "رسوم الخدمة فقط", "you have sent",
        // أكواد التحقق السري (OTP)
        "كود التحقق", "رمز التحقق", "الرقم السري المتغير", "لا تشارك هذا الكود",
        "verification code", "one time password", "otp is", "your otp"
    )

    // عبارات استلام مالي واردة إلزامية (يجب أن تحتوي الرسالة على إحداها بشكل صريح)
    private val MANDATORY_INCOMING_PHRASES = listOf(
        "تم استلام", "استلام مبلغ", "استلمت مبلغ", "استلمت", "وصلك مبلغ", "وصلتك حوالة",
        "تم إضافة مبلغ", "تم اضافه مبلغ", "تم إضافة", "تم اضافه", "تم إيداع مبلغ", "تم ايداع مبلغ",
        "تم إيداع", "تم ايداع", "تم تحويل مبلغ", "تحويل لحظي بمبلغ", "تحويل فوري بمبلغ",
        "you have received", "received egp", "has transferred to your", "credited to your account",
        "credited with"
    )

    private val AMOUNT_PATTERNS = listOf(
        Pattern.compile("""(?:تم استلام|استلام|بمبلغ|مبلغ|بقيمة|قيمة|إيداع|ايداع|إضافة|اضافة)\s*:?\s*([\d,]+(?:\.\d{1,2})?)\s*(?:جنيه|ج\.م|جم|egp|le)?""", Pattern.CASE_INSENSITIVE),
        Pattern.compile("""(?:egp|le)\s*([\d,]+(?:\.\d{1,2})?)""", Pattern.CASE_INSENSITIVE),
        Pattern.compile("""([\d,]+(?:\.\d{1,2})?)\s*(?:جنيه مصري|جنيه|ج\.م|جم|egp|le)""", Pattern.CASE_INSENSITIVE)
    )

    private val PHONE_PATTERN = Pattern.compile("""(?:\+?20|0020)?(01[0125][0-9]{8})""")
    private val IPA_PATTERN = Pattern.compile("""([A-Za-z0-9._-]+@[A-Za-z0-9._-]+)""")
    private val SENDER_NAME_AR = Pattern.compile("""من\s+(?:رقم\s+|حساب\s+)?([^\n\r،,.]+?)(?:\s+إلى|\s+الى|\s+بنجاح|\s+رصيدك|\s+رقم مرجعي|\s+كود العملية|$)""")
    private val SENDER_NAME_EN = Pattern.compile("""from\s+([^\n\r,.]+?)(?:\s+to\s+your|\s+ref|\s+tx|\s+balance|$)""", Pattern.CASE_INSENSITIVE)
    private val REF_NUM_PATTERN = Pattern.compile("""(?:كود العملية|رقم العملية|رقم المعاملة|رقم مرجعي|الرقم المرجعي|مرجع|Txn ID|Transaction ID|Ref)[\s:#]*([0-9A-Za-z_-]{4,})""", Pattern.CASE_INSENSITIVE)

    /**
     * توحيد الأرقام العربية/الفارسية إلى الأرقام القياسية 0-9
     */
    fun normalizeText(raw: String): String {
        val sb = StringBuilder(raw.length)
        for (ch in raw) {
            val arIdx = ARABIC_DIGITS.indexOf(ch)
            if (arIdx >= 0) {
                sb.append(('0'.code + arIdx).toChar())
                continue
            }
            val faIdx = PERSIAN_DIGITS.indexOf(ch)
            if (faIdx >= 0) {
                sb.append(('0'.code + faIdx).toChar())
                continue
            }
            sb.append(ch)
        }
        return sb.toString().trim()
    }

    /**
     * فحص الرسالة وإرجاع SmsLogEntry فقط إذا كانت رسالة تحويل مالي واردة حقيقية، أو null لأي رسالة عادية أو ترويجية أو شحن.
     */
    fun inspect(sender: String, rawBody: String, timestampMillis: Long): SmsLogEntry? {
        if (rawBody.isBlank()) return null
        val normalizedBody = normalizeText(rawBody)
        val lowerBody = normalizedBody.lowercase()
        val lowerSender = sender.lowercase()

        // 1. فلتر الحظر الفوري: استبعاد أي رسائل دعائية أو مسابقات أو ربح أو شحن رصيد
        if (SPAM_AND_PROMO_KEYWORDS.any { lowerBody.contains(it) }) {
            return null
        }

        // 2. التحقق الإلزامي من وجود صيغة استلام/تحويل وارد صريحة
        val hasIncomingPhrase = MANDATORY_INCOMING_PHRASES.any { lowerBody.contains(it) }
        if (!hasIncomingPhrase) {
            return null
        }

        // 3. إذا كانت الصيغة "تم تحويل مبلغ"، نتأكد أنها واردة وليست صادرة
        if (lowerBody.contains("تم تحويل") && !lowerBody.contains("تم استلام")) {
            val isIncomingTransfer = lowerBody.contains("إلى محفظتك") || lowerBody.contains("الى محفظتك") ||
                    lowerBody.contains("إلى حسابك") || lowerBody.contains("الى حسابك") ||
                    lowerBody.contains("في محفظتك") || lowerBody.contains("في حسابك") ||
                    lowerBody.contains("لحسابك") || lowerBody.contains("لحسابكم") ||
                    lowerBody.contains("من رقم") || lowerBody.contains("من 01") ||
                    lowerBody.contains("from ")
            if (!isIncomingTransfer) {
                return null
            }
        }

        // 4. استخراج المبلغ والتأكد من صحته (أكبر من 0 وأقل من 500,000 ج.م)
        val extractedAmount = extractAmount(normalizedBody) ?: return null
        if (extractedAmount <= 0.0) return null

        // 5. استخراج هوية الراسل أو رقم العملية المرجعي (يجب توفر أحدهما على الأقل لضمان أنها رسالة تحويل حقيقية)
        val extractedSender = extractSenderIdentifier(normalizedBody)
        val refNum = extractReferenceNumber(normalizedBody)
        if (extractedSender.isNullOrBlank() && refNum.isNullOrBlank()) {
            return null
        }

        val provider = detectProvider(lowerSender, lowerBody)
        val smsHash = computeSmsHash(sender, timestampMillis, normalizedBody)

        return SmsLogEntry(
            sender = sender.ifBlank { provider },
            body = normalizedBody,
            timestamp = timestampMillis,
            extractedAmount = extractedAmount,
            extractedPhone = extractedSender,
            provider = provider,
            smsHash = smsHash,
            dispatchStatus = SmsLogEntry.STATUS_QUEUED
        )
    }

    private fun detectProvider(lowerSender: String, lowerBody: String): String {
        return when {
            lowerSender.contains("vf") || lowerSender.contains("vodafone") || lowerBody.contains("فودافون كاش") -> "VodafoneCash"
            lowerSender.contains("instapay") || lowerBody.contains("انستاباي") || lowerBody.contains("إنستاباي") || lowerBody.contains("تحويل لحظي") -> "InstaPay"
            lowerSender.contains("orange") || lowerBody.contains("أورانج") || lowerBody.contains("اورنج") -> "OrangeCash"
            lowerSender.contains("etisalat") || lowerSender.contains("e&") || lowerBody.contains("اتصالات كاش") -> "EtisalatCash"
            lowerSender.contains("we") || lowerBody.contains("we pay") -> "WEPay"
            lowerSender.contains("cib") || lowerSender.contains("nbe") || lowerSender.contains("misr") || lowerSender.contains("qnb") -> "BankTransfer"
            else -> "WalletTransfer"
        }
    }

    private fun extractAmount(text: String): Double? {
        for (pattern in AMOUNT_PATTERNS) {
            val m = pattern.matcher(text)
            if (m.find()) {
                val rawNum = m.group(1)?.replace(",", "") ?: continue
                val parsed = rawNum.toDoubleOrNull()
                if (parsed != null && parsed > 0.0 && parsed < 500_000.0) {
                    return parsed
                }
            }
        }
        return null
    }

    private fun extractSenderIdentifier(text: String): String? {
        val phoneMatcher = PHONE_PATTERN.matcher(text)
        if (phoneMatcher.find()) {
            return phoneMatcher.group(1)
        }
        val ipaMatcher = IPA_PATTERN.matcher(text)
        if (ipaMatcher.find()) {
            return ipaMatcher.group(1)
        }
        val arName = SENDER_NAME_AR.matcher(text)
        if (arName.find()) {
            val candidate = arName.group(1)?.trim()
            if (!candidate.isNullOrBlank() && candidate.length in 3..60) return candidate
        }
        val enName = SENDER_NAME_EN.matcher(text)
        if (enName.find()) {
            val candidate = enName.group(1)?.trim()
            if (!candidate.isNullOrBlank() && candidate.length in 3..60) return candidate
        }
        return null
    }

    private fun extractReferenceNumber(text: String): String? {
        val m = REF_NUM_PATTERN.matcher(text)
        if (m.find()) {
            return m.group(1)?.trim()
        }
        return null
    }

    /**
     * بصمة SHA-256 حتمية لكل رسالة لمنع حفظ أو إرسال نفس الرسالة مرتين أبداً
     */
    fun computeSmsHash(sender: String, timestampMillis: Long, body: String): String {
        val minuteBucket = timestampMillis / 60_000L
        val canonical = "${sender.trim().lowercase()}|$minuteBucket|${normalizeText(body)}"
        val digest = MessageDigest.getInstance("SHA-256").digest(canonical.toByteArray(Charsets.UTF_8))
        return digest.joinToString("") { "%02x".format(it) }
    }
}

