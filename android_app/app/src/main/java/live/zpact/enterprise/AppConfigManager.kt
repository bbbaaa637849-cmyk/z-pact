package live.zpact.enterprise

import android.content.Context
import android.content.SharedPreferences
import android.os.Build

/**
 * مدير إعدادات التطبيق وتكوينات الربط التلقائي بالخادم
 */
class AppConfigManager private constructor(context: Context) {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    companion object {
        private const val PREFS_NAME = "zpact_enterprise_config_v2"

        private const val KEY_SERVER_URL = "key_server_url"
        private const val KEY_WEBHOOK_SECRET = "key_webhook_secret"
        private const val KEY_ADMIN_KEY = "key_admin_key"
        private const val KEY_RADAR_ENABLED = "key_radar_enabled"
        private const val KEY_INTERCEPTED_COUNT = "key_intercepted_count"
        private const val KEY_LAST_INTERCEPT_TIME = "key_last_intercept_time"
        private const val KEY_LAST_INBOX_SYNC_TIME = "key_last_inbox_sync_time"

        @Volatile
        private var instance: AppConfigManager? = null

        fun getInstance(context: Context): AppConfigManager {
            return instance ?: synchronized(this) {
                instance ?: AppConfigManager(context).also { instance = it }
            }
        }

        fun isRunningOnEmulator(): Boolean {
            return (Build.FINGERPRINT.startsWith("generic")
                    || Build.FINGERPRINT.lowercase().contains("emulator")
                    || Build.MODEL.contains("google_sdk")
                    || Build.MODEL.contains("Emulator")
                    || Build.MODEL.contains("Android SDK built for"))
        }
    }

    /**
     * تنظيف رابط السيرفر تلقائياً من أي مسارات فرعية
     */
    fun normalizeServerUrl(raw: String): String {
        var clean = raw.trim().substringBefore("?").substringBefore("#").trimEnd('/')
        val suffixes = listOf("/miniapp", "/store", "/sender", "/login", "/admin", "/webhook/sms", "/api/health")
        for (suffix in suffixes) {
            if (clean.endsWith(suffix, ignoreCase = true)) {
                clean = clean.dropLast(suffix.length).trimEnd('/')
            }
        }
        if (!isRunningOnEmulator() && (clean.contains("10.0.2.2") || clean.contains("127.0.0.1") || clean.contains("localhost"))) {
            return ""
        }
        return clean
    }

    fun getServerUrl(): String {
        val raw = prefs.getString(KEY_SERVER_URL, "") ?: ""
        val clean = normalizeServerUrl(raw)
        if (clean.isNotEmpty()) {
            return clean
        }
        return BuildConfig.DEFAULT_SERVER_URL
    }

    fun setServerUrl(url: String) {
        val cleanUrl = normalizeServerUrl(url)
        prefs.edit().putString(KEY_SERVER_URL, cleanUrl).apply()
    }

    fun getStoreWebsiteUrl(): String {
        val base = getServerUrl()
        return if (base.isNotEmpty()) "$base/store" else ""
    }

    fun getMiniAppUrl(): String {
        val base = getServerUrl()
        return if (base.isNotEmpty()) "$base/miniapp/" else ""
    }

    fun getSmsWebhookUrl(): String {
        val base = getServerUrl()
        return if (base.isNotEmpty()) "$base/webhook/sms" else ""
    }

    fun getHealthUrl(): String {
        val base = getServerUrl()
        return if (base.isNotEmpty()) "$base/api/health" else ""
    }

    fun getWebhookSecret(): String {
        val saved = prefs.getString(KEY_WEBHOOK_SECRET, null)
        return if (!saved.isNullOrBlank()) saved else BuildConfig.DEFAULT_WEBHOOK_SECRET
    }

    fun setWebhookSecret(secret: String) {
        prefs.edit().putString(KEY_WEBHOOK_SECRET, secret.trim()).apply()
    }

    fun getAdminKey(): String {
        val saved = prefs.getString(KEY_ADMIN_KEY, null)
        return if (!saved.isNullOrBlank()) saved else BuildConfig.DEFAULT_ADMIN_KEY
    }

    fun setAdminKey(key: String) {
        prefs.edit().putString(KEY_ADMIN_KEY, key.trim()).apply()
    }

    fun isRadarEnabled(): Boolean {
        return prefs.getBoolean(KEY_RADAR_ENABLED, true)
    }

    fun setRadarEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_RADAR_ENABLED, enabled).apply()
    }

    fun getInterceptedCount(): Int {
        return prefs.getInt(KEY_INTERCEPTED_COUNT, 0)
    }

    fun incrementInterceptedCount(delta: Int = 1) {
        if (delta <= 0) return
        val current = getInterceptedCount()
        prefs.edit()
            .putInt(KEY_INTERCEPTED_COUNT, current + delta)
            .putLong(KEY_LAST_INTERCEPT_TIME, System.currentTimeMillis())
            .apply()
    }

    fun setInterceptedCount(count: Int) {
        prefs.edit().putInt(KEY_INTERCEPTED_COUNT, count.coerceAtLeast(0)).apply()
    }

    fun resetInterceptedCount() {
        prefs.edit().putInt(KEY_INTERCEPTED_COUNT, 0).apply()
    }

    fun getLastInterceptTime(): Long {
        return prefs.getLong(KEY_LAST_INTERCEPT_TIME, 0L)
    }

    fun getLastInboxSyncTime(): Long {
        return prefs.getLong(KEY_LAST_INBOX_SYNC_TIME, 0L)
    }

    fun setLastInboxSyncTime(ts: Long) {
        prefs.edit().putLong(KEY_LAST_INBOX_SYNC_TIME, ts).apply()
    }
}
