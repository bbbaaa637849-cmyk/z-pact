package live.zpact.enterprise

import android.content.Context
import androidx.appcompat.app.AppCompatActivity
import com.google.gson.JsonObject

/**
 * نسخة المدرسين النظيفة 100% — بدون أي أذونات أو أكواد SMS لضمان الأمان الكامل وعدم تصنيف التطبيق كبرمجية مشبوهة
 */
@Suppress("UNUSED_PARAMETER")
object RadarPlatformHook {

    fun setupActivityHooks(activity: AppCompatActivity, onStatusChanged: () -> Unit) {
        // لا توجد أي أذونات مطلوبة في نسخة المدرس
    }

    fun onActivityResumed(activity: AppCompatActivity, onStatusChanged: () -> Unit) {
        // لا يوجد فحص رسائل في نسخة المدرس
    }

    fun isRadarRunning(context: Context): Boolean = false

    fun showRadarMonitorDialog(activity: AppCompatActivity, onStatusChanged: () -> Unit) {
        // غير متاح في نسخة المدرس
    }

    fun showServerConfigDialog(activity: AppCompatActivity, onServerChanged: () -> Unit) {
        // الاتصال يتم تلقائياً في نسخة المدرس دون أي إعدادات يدوية
    }

    fun getRadarStatusJson(context: Context): String {
        val config = AppConfigManager.getInstance(context)
        return JsonObject().apply {
            addProperty("enabled", false)
            addProperty("serviceRunning", false)
            addProperty("isAdminFlavor", false)
            addProperty("serverUrl", config.getServerUrl())
        }.toString()
    }

    fun toggleRadarFromJs(context: Context, enable: Boolean): Boolean = false

    fun sendTestWebhookFromJs(context: Context): String = "غير متاح في نسخة المدرسين"
}

