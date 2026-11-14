package live.zpact.enterprise

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.widget.Toast
import com.google.gson.JsonObject
import java.security.MessageDigest
import java.util.UUID

/**
 * جسر التواصل عالي الأداء بين واجهة الويب (WebView) ونظام أندرويد الأصيل (v3.0)
 */
class ZPactNativeBridge(
    private val context: Context,
    private val onOpenSettings: () -> Unit,
    private val onOpenRadar: () -> Unit,
    private val onReloadPage: () -> Unit = {},
    private val onCheckUpdates: () -> Unit = {}
) {
    private val mainHandler = Handler(Looper.getMainLooper())

    @JavascriptInterface
    fun isNativeAndroid(): Boolean = true

    @JavascriptInterface
    fun isAdminFlavor(): Boolean = BuildConfig.IS_ADMIN_FLAVOR

    @JavascriptInterface
    fun getAdminAppProof(): String = if (BuildConfig.IS_ADMIN_FLAVOR) BuildConfig.DEFAULT_ADMIN_KEY else ""

    @JavascriptInterface
    fun getFlavorRole(): String = BuildConfig.FLAVOR_ROLE

    @JavascriptInterface
    fun getAppVersion(): String = BuildConfig.VERSION_NAME

    @JavascriptInterface
    fun getVersionCode(): Int = BuildConfig.VERSION_CODE

    /**
     * توليد بصمة سيريال عتادية ثابتة وفريدة للجهاز لمنع مشاركة الحسابات وتسهيل التفعيل السريع
     */
    @SuppressLint("HardwareIds")
    @JavascriptInterface
    fun getDeviceSerial(): String {
        return try {
            val prefs = context.applicationContext.getSharedPreferences("zpact_hw_identity", Context.MODE_PRIVATE)
            val cachedKey = "hw_serial_${BuildConfig.FLAVOR_ROLE}"
            val existing = prefs.getString(cachedKey, null)
            if (!existing.isNullOrBlank()) return existing

            var rawAndroidId = try {
                Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID) ?: ""
            } catch (_: Exception) {
                ""
            }
            if (rawAndroidId.length < 6 || rawAndroidId == "9774d56d682e549c") {
                var fallback = prefs.getString("fallback_uuid", null)
                if (fallback.isNullOrBlank()) {
                    fallback = UUID.randomUUID().toString()
                    prefs.edit().putString("fallback_uuid", fallback).apply()
                }
                rawAndroidId = fallback
            }

            val seed = "ZPACT_V3:$rawAndroidId:${Build.BRAND}:${Build.BOARD}:${BuildConfig.FLAVOR_ROLE}"
            val digest = MessageDigest.getInstance("SHA-256").digest(seed.toByteArray(Charsets.UTF_8))
            val hex = digest.joinToString("") { "%02X".format(it) }

            val prefix = when (BuildConfig.FLAVOR_ROLE) {
                "admin" -> "ZP-ADM"
                "sender" -> "ZP-SND"
                else -> "ZP-TCH"
            }
            val serial = "$prefix-${hex.substring(0, 4)}-${hex.substring(4, 8)}"
            prefs.edit().putString(cachedKey, serial).apply()
            serial
        } catch (_: Exception) {
            "ZP-APP-0000"
        }
    }

    @JavascriptInterface
    fun getAppInfo(): String {
        val config = AppConfigManager.getInstance(context)
        return JsonObject().apply {
            addProperty("flavor", BuildConfig.FLAVOR_ROLE)
            addProperty("versionName", BuildConfig.VERSION_NAME)
            addProperty("versionCode", BuildConfig.VERSION_CODE)
            addProperty("isAdminFlavor", BuildConfig.IS_ADMIN_FLAVOR)
            addProperty("deviceSerial", getDeviceSerial())
            addProperty("serverUrl", config.getServerUrl())
        }.toString()
    }

    @JavascriptInterface
    fun showToast(message: String) {
        mainHandler.post {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        }
    }

    @JavascriptInterface
    fun reloadPage() {
        mainHandler.post {
            onReloadPage()
        }
    }

    @JavascriptInterface
    fun checkForUpdatesNow() {
        mainHandler.post {
            onCheckUpdates()
        }
    }

    @JavascriptInterface
    fun openExternalUrl(url: String) {
        if (url.isBlank()) return
        mainHandler.post {
            try {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url.trim())).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                context.startActivity(intent)
            } catch (e: Exception) {
                Toast.makeText(context, "⚠️ تعذر فتح الرابط الخارجي: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    @JavascriptInterface
    fun vibrate(durationMs: Long) {
        if (!BuildConfig.IS_ADMIN_FLAVOR) return
        try {
            val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
                vm.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator.vibrate(
                    VibrationEffect.createOneShot(
                        durationMs.coerceAtMost(500L),
                        VibrationEffect.DEFAULT_AMPLITUDE
                    )
                )
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(durationMs.coerceAtMost(500L))
            }
        } catch (_: Exception) {
            // تجاهل الأجهزة التي لا تدعم الاهتزاز
        }
    }

    @JavascriptInterface
    fun downloadPdf(downloadUrl: String, fileName: String) {
        mainHandler.post {
            try {
                val config = AppConfigManager.getInstance(context)
                val fullUrl = if (downloadUrl.startsWith("http://") || downloadUrl.startsWith("https://")) {
                    downloadUrl
                } else {
                    "${config.getServerUrl()}${if (downloadUrl.startsWith("/")) "" else "/"}$downloadUrl"
                }

                val cleanName = fileName.replace(Regex("[\\\\/:*?\"<>|]"), "_").trim()
                val safeFileName = if (cleanName.lowercase().endsWith(".pdf")) cleanName else "$cleanName.pdf"

                val request = DownloadManager.Request(Uri.parse(fullUrl)).apply {
                    setTitle("المذكرة المختومة: $safeFileName")
                    setDescription("جاري تنزيل ملف الـ PDF من منصة Z-PACT...")
                    setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Z-PACT/$safeFileName")
                    setMimeType("application/pdf")
                    CookieManager.getInstance().getCookie(fullUrl)?.let { cookie ->
                        if (cookie.isNotBlank()) addRequestHeader("Cookie", cookie)
                    }
                    val adminKey = config.getAdminKey()
                    if (adminKey.isNotBlank()) {
                        addRequestHeader("X-Internal-Service-Key", adminKey)
                    }
                    setAllowedOverMetered(true)
                    setAllowedOverRoaming(true)
                }

                val manager = context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                manager.enqueue(request)

                vibrate(80)
                Toast.makeText(
                    context,
                    "📥 بدأ تنزيل $safeFileName في مجلد التنزيلات (Downloads/Z-PACT)",
                    Toast.LENGTH_LONG
                ).show()
            } catch (e: Exception) {
                Toast.makeText(context, "⚠️ تعذر بدء التنزيل: ${e.message}", Toast.LENGTH_SHORT).show()
            }
        }
    }

    @JavascriptInterface
    fun openServerSettings() {
        if (BuildConfig.IS_ADMIN_FLAVOR) {
            onOpenSettings()
        }
    }

    @JavascriptInterface
    fun openRadarDashboard() {
        if (BuildConfig.IS_ADMIN_FLAVOR) {
            onOpenRadar()
        }
    }

    @JavascriptInterface
    fun getRadarStatus(): String {
        return RadarPlatformHook.getRadarStatusJson(context)
    }

    @JavascriptInterface
    fun toggleRadar(enable: Boolean): Boolean {
        return RadarPlatformHook.toggleRadarFromJs(context, enable)
    }

    @JavascriptInterface
    fun sendTestWebhook(): String {
        return RadarPlatformHook.sendTestWebhookFromJs(context)
    }
}
