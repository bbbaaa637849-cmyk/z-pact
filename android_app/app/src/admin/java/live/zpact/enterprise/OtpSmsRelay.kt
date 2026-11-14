package live.zpact.enterprise

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.telephony.SmsManager
import android.util.Log
import androidx.core.content.ContextCompat
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * محرك ترحيل وإرسال رموز الـ OTP تلقائياً عبر شريحة هاتف الأدمن (SIM Outbox Relay)
 * حصري لنسخة الأدمن (Z-PACT Admin Radar)
 */
object OtpSmsRelay {

    private const val TAG = "OtpSmsRelay"

    suspend fun processQueuedOtps(context: Context): Int = withContext(Dispatchers.IO) {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            return@withContext 0
        }

        val config = AppConfigManager.getInstance(context)
        val baseUrl = config.getServerUrl().trim().trimEnd('/')
        if (baseUrl.isBlank()) return@withContext 0

        var sentCount = 0
        try {
            val outboxUrl = URL("$baseUrl/api/internal/otp/outbox")
            val conn = (outboxUrl.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 5000
                readTimeout = 5000
                setRequestProperty("X-Internal-Service-Key", BuildConfig.DEFAULT_ADMIN_KEY)
            }

            if (conn.responseCode != 200) {
                conn.disconnect()
                return@withContext 0
            }

            val reader = BufferedReader(InputStreamReader(conn.inputStream))
            val responseText = reader.readText()
            reader.close()
            conn.disconnect()

            val rootObj = JsonParser.parseString(responseText).asJsonObject
            val items = rootObj.getAsJsonArray("outbox") ?: return@withContext 0

            @Suppress("DEPRECATION")
            val smsManager: SmsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                context.getSystemService(SmsManager::class.java) ?: SmsManager.getDefault()
            } else {
                SmsManager.getDefault()
            }

            for (element in items) {
                try {
                    val item = element.asJsonObject
                    val challengeId = item.get("id").asInt
                    val phone = item.get("phone").asString
                    val otpCode = item.get("otp_code").asString
                    val role = if (item.get("target_role").asString == "teacher") "المدرسين" else "المرسلين"

                    val messageBody = "رمز التحقق لمنصة Z-PACT ($role) هو: $otpCode (صالح لمدة 10 دقائق)."
                    val parts = smsManager.divideMessage(messageBody)
                    smsManager.sendMultipartTextMessage(phone, null, parts, null, null)

                    // Acknowledge dispatch to backend
                    ackOtpDispatch(baseUrl, challengeId)
                    sentCount++
                    Log.i(TAG, "📲 تم إرسال رسالة SMS برمز OTP للرقم $phone بنجاح")
                } catch (e: Exception) {
                    Log.w(TAG, "خطأ في إرسال OTP فردي: ${e.message}")
                }
            }
        } catch (e: Exception) {
            Log.d(TAG, "فحص صندوق OTP: ${e.message}")
        }
        sentCount
    }

    private fun ackOtpDispatch(baseUrl: String, challengeId: Int) {
        try {
            val ackUrl = URL("$baseUrl/api/internal/otp/outbox/$challengeId/ack")
            val conn = (ackUrl.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                connectTimeout = 4000
                readTimeout = 4000
                setRequestProperty("X-Internal-Service-Key", BuildConfig.DEFAULT_ADMIN_KEY)
            }
            conn.responseCode
            conn.disconnect()
        } catch (_: Exception) {}
    }
}

