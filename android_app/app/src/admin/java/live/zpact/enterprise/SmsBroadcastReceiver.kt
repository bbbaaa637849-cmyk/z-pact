package live.zpact.enterprise

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/**
 * مستقبل رسائل الـ SMS اللحظي بأعلى أولوية لرصد جميع التحويلات المالية وتوجيهها للسيرفر فوراً
 */
class SmsBroadcastReceiver : BroadcastReceiver() {

    companion object {
        private const val TAG = "SmsRadarReceiver"
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return

        val appCtx = context.applicationContext
        val config = AppConfigManager.getInstance(appCtx)
        if (!config.isRadarEnabled()) {
            return
        }

        val messages = Telephony.Sms.Intents.getMessagesFromIntent(intent)
        if (messages.isNullOrEmpty()) return

        val sender = messages[0].displayOriginatingAddress ?: "Unknown"
        val fullBody = messages.joinToString("") { it.displayMessageBody ?: "" }
        val timestamp = messages[0].timestampMillis

        val detectedEntry = FinancialSmsDetector.inspect(sender, fullBody, timestamp) ?: return
        Log.i(TAG, "⚡ تم التقاط تحويل مالي جديد لحظياً من: $sender (${detectedEntry.extractedAmount} ج.م)")

        val pendingResult = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                WebhookDispatcher.dispatchSuspend(appCtx, detectedEntry)
                if (!SmsRadarService.isServiceRunning) {
                    SmsRadarService.startRadarService(appCtx)
                }
            } catch (e: Exception) {
                Log.e(TAG, "خطأ أثناء معالجة الرسالة اللحظية: ${e.message}")
            } finally {
                pendingResult.finish()
            }
        }
    }
}

