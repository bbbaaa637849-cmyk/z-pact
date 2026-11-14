package live.zpact.enterprise

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * مستقبل بدء تشغيل الهاتف لإعادة تفعيل رادار Z-PACT تلقائياً فور فتح الموبايل
 */
class BootCompletedReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        if (action == Intent.ACTION_BOOT_COMPLETED || action == "android.intent.action.QUICKBOOT_POWERON") {
            val config = AppConfigManager.getInstance(context)
            if (config.isRadarEnabled()) {
                Log.i("BootReceiver", "🔄 إعادة تشغيل رادار Z-PACT تلقائياً بعد إقلاع الهاتف...")
                SmsRadarService.startRadarService(context)
            }
        }
    }
}

