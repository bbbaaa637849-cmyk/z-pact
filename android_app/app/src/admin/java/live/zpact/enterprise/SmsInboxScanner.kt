package live.zpact.enterprise

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Telephony
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * ماسح ومراجع صندوق رسائل الـ SMS بالهاتف (Historical Inbox Catch-Up Engine)
 * يضمن عدم ضياع أي تحويل وصل أثناء إغلاق التطبيق أو انطفاء الهاتف أو انقطاع الشبكة،
 * ويحفظه محلياً ويرفعه إلى السيرفر تلقائياً بدون أي تكرار.
 */
object SmsInboxScanner {

    private const val TAG = "SmsInboxScanner"
    private val scanMutex = Mutex()

    data class InboxSyncSummary(
        val hasPermission: Boolean,
        val scannedSmsCount: Int,
        val newlyCapturedTransfers: Int,
        val syncedToServerNow: Int,
        val remainingOfflineQueue: Int
    )

    suspend fun syncInboxAndFlush(
        context: Context,
        lookbackDays: Int = 14,
        maxMessages: Int = 500
    ): InboxSyncSummary = withContext(Dispatchers.IO) {
        val appCtx = context.applicationContext
        val hasReadSms = ContextCompat.checkSelfPermission(
            appCtx,
            Manifest.permission.READ_SMS
        ) == PackageManager.PERMISSION_GRANTED

        if (!hasReadSms) {
            return@withContext InboxSyncSummary(
                hasPermission = false,
                scannedSmsCount = 0,
                newlyCapturedTransfers = 0,
                syncedToServerNow = 0,
                remainingOfflineQueue = 0
            )
        }

        scanMutex.withLock {
            val db = SmsDatabaseHelper(appCtx)
            val config = AppConfigManager.getInstance(appCtx)

            // تنظيف أي رسائل دعائية أو غير صحيحة كانت قد سُجلت سابقاً بالخطأ
            val purgedCount = db.purgeInvalidOrSpamLogs()
            config.setInterceptedCount(db.getTotalCount())
            if (purgedCount > 0) {
                Log.i(TAG, "🧹 تم تنظيف وحذف $purgedCount رسالة إعلانية/شحن قديمة من قاعدة البيانات المحلية.")
            }

            val cutoffMillis = System.currentTimeMillis() - (lookbackDays.toLong() * 24L * 60L * 60L * 1000L)

            val discoveredEntries = mutableListOf<SmsLogEntry>()
            var scanned = 0

            try {
                val inboxUri: Uri = Telephony.Sms.Inbox.CONTENT_URI
                val projection = arrayOf(
                    Telephony.Sms.ADDRESS,
                    Telephony.Sms.BODY,
                    Telephony.Sms.DATE
                )
                val selection = "${Telephony.Sms.DATE} >= ?"
                val selectionArgs = arrayOf(cutoffMillis.toString())
                val sortOrder = "${Telephony.Sms.DATE} DESC"

                appCtx.contentResolver.query(
                    inboxUri,
                    projection,
                    selection,
                    selectionArgs,
                    sortOrder
                )?.use { cursor ->
                    val addrIdx = cursor.getColumnIndex(Telephony.Sms.ADDRESS)
                    val bodyIdx = cursor.getColumnIndex(Telephony.Sms.BODY)
                    val dateIdx = cursor.getColumnIndex(Telephony.Sms.DATE)

                    while (cursor.moveToNext() && scanned < maxMessages) {
                        scanned++
                        val sender = if (addrIdx >= 0 && !cursor.isNull(addrIdx)) cursor.getString(addrIdx) else "Unknown"
                        val body = if (bodyIdx >= 0 && !cursor.isNull(bodyIdx)) cursor.getString(bodyIdx) else ""
                        val ts = if (dateIdx >= 0 && !cursor.isNull(dateIdx)) cursor.getLong(dateIdx) else System.currentTimeMillis()

                        val parsed = FinancialSmsDetector.inspect(sender, body, ts)
                        if (parsed != null) {
                            discoveredEntries.add(parsed)
                        }
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "خطأ أثناء قراءة صندوق رسائل الـ SMS: ${e.message}")
            }

            // إدراج الرسائل من الأقدم إلى الأحدث للحفاظ على التسلسل الزمني الدقيق للتحويلات
            var newlyInserted = 0
            for (entry in discoveredEntries.asReversed()) {
                val (_, isNew) = db.insertIfAbsent(entry)
                if (isNew) {
                    newlyInserted++
                }
            }

            // رفع ومزامنة كل التحويلات غير المرسلة إلى السيرفر فوراً
            val syncedNow = WebhookDispatcher.flushPendingQueueSuspend(appCtx)
            val remainingPending = db.getPendingCount()
            config.setLastInboxSyncTime(System.currentTimeMillis())
            config.setInterceptedCount(db.getTotalCount())

            if (newlyInserted > 0 || syncedNow > 0) {
                Log.i(
                    TAG,
                    "✅ مراجعة الصندوق اكتملت: فُحصت $scanned رسالة | جديدة: $newlyInserted | زومنت مع السيرفر: $syncedNow"
                )
            }

            InboxSyncSummary(
                hasPermission = true,
                scannedSmsCount = scanned,
                newlyCapturedTransfers = newlyInserted,
                syncedToServerNow = syncedNow,
                remainingOfflineQueue = remainingPending
            )
        }
    }
}

