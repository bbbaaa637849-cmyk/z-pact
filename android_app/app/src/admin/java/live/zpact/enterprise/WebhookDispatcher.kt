package live.zpact.enterprise

import android.content.Context
import android.util.Log
import com.google.gson.JsonObject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * محرك الإرسال والمزامنة الشبكية لرسائل الـ SMS مع سيرفر Z-PACT مع إعادة الاكتشاف التلقائي للسيرفر
 */
object WebhookDispatcher {

    private const val TAG = "WebhookDispatcher"
    private val JSON_MEDIA_TYPE = "application/json; charset=utf-8".toMediaType()

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .retryOnConnectionFailure(true)
        .build()

    /**
     * حفظ الرسالة في SQLite (بدون تكرار) وإرسالها فوراً إلى السيرفر
     */
    suspend fun dispatchSuspend(context: Context, entry: SmsLogEntry): Pair<Boolean, String> =
        withContext(Dispatchers.IO) {
            val db = SmsDatabaseHelper(context)
            val config = AppConfigManager.getInstance(context)

            val (logId, isNew) = if (entry.id == 0L) {
                db.insertIfAbsent(entry)
            } else {
                Pair(entry.id, true)
            }

            if (logId <= 0L) {
                return@withContext Pair(true, "الرسالة مسجلة ومزامنة مسبقاً")
            }

            // التأكد من وجود رابط سيرفر حي
            if (config.getServerUrl().isEmpty()) {
                ServerDiscoveryEngine.resolveLiveServer(context, forceRefresh = true)
            }

            var result = executeHttpDispatch(config, entry)

            // إذا تغير رابط نفق Cloudflare، نكتشف الرابط الجديد تلقائياً ونعيد الإرسال فوراً!
            if (!result.first) {
                val refreshedUrl = ServerDiscoveryEngine.resolveLiveServer(context, forceRefresh = true)
                if (!refreshedUrl.isNullOrEmpty()) {
                    result = executeHttpDispatch(config, entry)
                }
            }

            if (result.first) {
                db.updateStatus(logId, SmsLogEntry.STATUS_DISPATCHED)
                if (isNew) {
                    config.incrementInterceptedCount(1)
                }
                Log.i(TAG, "✓ تم حفظ ومزامنة التحويل مع السيرفر بنجاح [ID: $logId]")
            } else {
                db.updateStatus(logId, SmsLogEntry.STATUS_QUEUED, incrementRetry = true)
                Log.w(TAG, "⏳ تعذر الوصول للسيرفر حالياً، حُفظ التحويل بطابور الأوفلاين: ${result.second}")
            }
            result
        }

    fun dispatch(context: Context, entry: SmsLogEntry, callback: ((Boolean, String) -> Unit)? = null) {
        CoroutineScope(Dispatchers.IO).launch {
            val res = dispatchSuspend(context, entry)
            if (callback != null) {
                withContext(Dispatchers.Main) {
                    callback(res.first, res.second)
                }
            }
        }
    }

    private fun executeHttpDispatch(config: AppConfigManager, entry: SmsLogEntry): Pair<Boolean, String> {
        val url = config.getSmsWebhookUrl()
        if (url.isEmpty()) {
            return Pair(false, "رابط السيرفر غير مكتشف بعد")
        }
        return try {
            val payload = JsonObject().apply {
                addProperty("sender", entry.sender)
                addProperty("body", entry.body)
                addProperty("message", entry.body)
                addProperty("timestamp", entry.timestamp)
                addProperty("provider", entry.provider)
                addProperty("source", "android_native_radar")
            }

            val requestBody = payload.toString().toRequestBody(JSON_MEDIA_TYPE)
            val request = Request.Builder()
                .url(url)
                .addHeader("Content-Type", "application/json")
                .addHeader("X-Webhook-Secret", config.getWebhookSecret())
                .addHeader("X-Internal-Service-Key", config.getAdminKey())
                .post(requestBody)
                .build()

            httpClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (response.isSuccessful) {
                    Pair(true, "تمت المزامنة مع السيرفر (${response.code})")
                } else {
                    Pair(false, "استجابة غير ناجحة (${response.code}): ${body.take(120)}")
                }
            }
        } catch (e: Exception) {
            Pair(false, "خطأ اتصال شبكي: ${e.message}")
        }
    }

    /**
     * تفريغ ومزامنة كل الرسائل المحفوظة في طابور الأوفلاين وإرجاع عدد الرسائل التي وصلت للسيرفر
     */
    suspend fun flushPendingQueueSuspend(context: Context): Int = withContext(Dispatchers.IO) {
        val db = SmsDatabaseHelper(context)
        val config = AppConfigManager.getInstance(context)
        val pendingList = db.getPendingQueue()
        if (pendingList.isEmpty()) return@withContext 0

        if (config.getServerUrl().isEmpty()) {
            ServerDiscoveryEngine.resolveLiveServer(context, forceRefresh = true)
        }

        var syncedNow = 0
        var refreshedOnce = false

        for (item in pendingList) {
            var result = executeHttpDispatch(config, item)
            if (!result.first && !refreshedOnce) {
                refreshedOnce = true
                val newUrl = ServerDiscoveryEngine.resolveLiveServer(context, forceRefresh = true)
                if (!newUrl.isNullOrEmpty()) {
                    result = executeHttpDispatch(config, item)
                }
            }

            if (result.first) {
                db.updateStatus(item.id, SmsLogEntry.STATUS_DISPATCHED)
                syncedNow++
            } else {
                db.updateStatus(item.id, SmsLogEntry.STATUS_QUEUED, incrementRetry = true)
                break
            }
        }

        // تحديث عداد التحويلات الإجمالي ليطابق عدد الرسائل المرسلة فعلياً
        val totalDispatched = db.getDispatchedCount()
        if (totalDispatched > config.getInterceptedCount()) {
            config.incrementInterceptedCount(totalDispatched - config.getInterceptedCount())
        }

        syncedNow
    }

    fun testServerConnection(context: Context, candidateUrl: String? = null, callback: (Boolean, String) -> Unit) {
        CoroutineScope(Dispatchers.IO).launch {
            val config = AppConfigManager.getInstance(context)
            if (!candidateUrl.isNullOrBlank()) {
                config.setServerUrl(candidateUrl)
            }
            var targetBase = config.getServerUrl()
            if (targetBase.isEmpty()) {
                targetBase = ServerDiscoveryEngine.resolveLiveServer(context, forceRefresh = true).orEmpty()
            }

            if (targetBase.isEmpty()) {
                withContext(Dispatchers.Main) {
                    callback(false, "❌ تعذر اكتشاف السيرفر تلقائياً — تأكد من تشغيل orchestrator.py")
                }
                return@launch
            }

            val ok = ServerDiscoveryEngine.isServerHealthySync(targetBase)
            val finalMsg = if (ok) {
                "✅ متصل بالسيرفر بنجاح:\n$targetBase"
            } else {
                val healed = ServerDiscoveryEngine.resolveLiveServer(context, forceRefresh = true)
                if (healed != null) {
                    "✅ تم التحديث التلقائي والاتصال بـ:\n$healed"
                } else {
                    "❌ تعذر الوصول إلى $targetBase"
                }
            }
            val finalSuccess = finalMsg.startsWith("✅")
            withContext(Dispatchers.Main) {
                callback(finalSuccess, finalMsg)
            }
        }
    }

    fun dispatchTestSms(context: Context, callback: (Boolean, String) -> Unit) {
        val testNumber = "01097671808"
        val testOpId = "VF-" + System.currentTimeMillis().toString().takeLast(6)
        val mockBody = "تم استلام مبلغ 120.00 جنيه من رقم $testNumber بنجاح. كود العملية $testOpId. رصيدك الحالي هو 540.00 جنيه."
        val now = System.currentTimeMillis()

        val entry = SmsLogEntry(
            sender = "VF-Cash",
            body = mockBody,
            timestamp = now,
            extractedAmount = 120.0,
            extractedPhone = testNumber,
            provider = "VodafoneCash",
            smsHash = FinancialSmsDetector.computeSmsHash("VF-Cash", now, mockBody) + "_$testOpId"
        )

        dispatch(context, entry, callback)
    }
}

