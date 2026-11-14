package live.zpact.enterprise

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

/**
 * محرك التحديث الهوائي التلقائي (In-App OTA Self-Updater) للنسخ الثلاث:
 * - Admin Radar (`admin`)
 * - Teacher Store (`teacher`)
 * - Sender Team (`sender`)
 */
object AppUpdateManager {

    private val checkClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    private val downloadClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    @Volatile
    private var isUpdateDialogShowing = false

    @Volatile
    private var lastPromptedVersionCode = -1

    @Volatile
    private var pendingInstallFile: File? = null

    fun checkForUpdates(
        activity: AppCompatActivity,
        baseUrl: String,
        silentIfUpToDate: Boolean = true
    ) {
        val cleanBase = baseUrl.trim().trimEnd('/')
        if (cleanBase.isEmpty() || activity.isFinishing || activity.isDestroyed) return

        activity.lifecycleScope.launch {
            val info = withContext(Dispatchers.IO) {
                fetchUpdateMetadata(cleanBase)
            }

            if (activity.isFinishing || activity.isDestroyed) return@launch

            if (info == null) {
                if (!silentIfUpToDate) {
                    Toast.makeText(activity, "⚠️ تعذر التحقق من التحديثات حالياً", Toast.LENGTH_SHORT).show()
                }
                return@launch
            }

            if (!info.updateAvailable) {
                if (!silentIfUpToDate) {
                    Toast.makeText(
                        activity,
                        "✅ تطبيقك محدث لأحدث إصدار (${BuildConfig.VERSION_NAME})",
                        Toast.LENGTH_SHORT
                    ).show()
                }
                return@launch
            }

            if (isUpdateDialogShowing) return@launch
            if (silentIfUpToDate && !info.forceUpdate && lastPromptedVersionCode == info.latestVersionCode) {
                return@launch
            }

            lastPromptedVersionCode = info.latestVersionCode
            showLuxuryUpdateDialog(activity, info)
        }
    }

    fun resumePendingInstallIfPermitted(activity: Activity) {
        // لا حاجة لطلب أذونات الحزم بعد اعتماد التحديث الآمن عبر المتصفح القياسي
    }

    private data class UpdateMetadata(
        val latestVersionCode: Int,
        val latestVersionName: String,
        val updateAvailable: Boolean,
        val forceUpdate: Boolean,
        val title: String,
        val releaseNotes: String,
        val fileSizeMb: Double,
        val downloadUrl: String
    )

    private fun fetchUpdateMetadata(cleanBase: String): UpdateMetadata? {
        return try {
            val url = "$cleanBase/api/app-updates/check?flavor=${BuildConfig.FLAVOR_ROLE}&version_code=${BuildConfig.VERSION_CODE}"
            val req = Request.Builder()
                .url(url)
                .header("Accept", "application/json")
                .header("Cache-Control", "no-cache")
                .get()
                .build()

            checkClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return null
                val raw = resp.body?.string().orEmpty()
                val json = JsonParser.parseString(raw).asJsonObject
                val dlRaw = json.get("download_url")?.asString.orEmpty()
                val resolvedDl = if (dlRaw.startsWith("http://") || dlRaw.startsWith("https://")) {
                    // استبدال الـ origin بـ cleanBase لضمان التحميل عبر نفس النفق أو الـ LAN الفعال
                    val pathIdx = dlRaw.indexOf("/download/apk/")
                    if (pathIdx >= 0) "$cleanBase${dlRaw.substring(pathIdx)}" else dlRaw
                } else {
                    "$cleanBase${if (dlRaw.startsWith("/")) "" else "/"}$dlRaw"
                }

                UpdateMetadata(
                    latestVersionCode = json.get("latest_version_code")?.asInt ?: BuildConfig.VERSION_CODE,
                    latestVersionName = json.get("latest_version_name")?.asString ?: "3.0.0",
                    updateAvailable = json.get("update_available")?.asBoolean ?: false,
                    forceUpdate = json.get("force_update")?.asBoolean ?: false,
                    title = json.get("title")?.asString ?: "🚀 تحديث جديد متاح لمنظومة Z-PACT",
                    releaseNotes = json.get("release_notes")?.asString ?: "تحسينات جوهرية في الأداء والسرعة وميزات جديدة.",
                    fileSizeMb = json.get("file_size_mb")?.asDouble ?: 0.0,
                    downloadUrl = resolvedDl
                )
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun showLuxuryUpdateDialog(activity: AppCompatActivity, info: UpdateMetadata) {
        isUpdateDialogShowing = true
        val density = activity.resources.displayMetrics.density
        fun dp(v: Int): Int = (v * density).toInt()

        val cardBg = GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(22).toFloat()
            setColor(Color.parseColor("#0D121F"))
            setStroke(dp(1), Color.parseColor("#00F5D4"))
        }

        val container = LinearLayout(activity).apply {
            orientation = LinearLayout.VERTICAL
            background = cardBg
            setPadding(dp(22), dp(22), dp(22), dp(18))
        }

        val tvBadge = TextView(activity).apply {
            text = "⚡ إصدار جديد v${info.latestVersionName} ${if (info.fileSizeMb > 0) "• ${info.fileSizeMb} MB" else ""}"
            setTextColor(Color.parseColor("#00F5D4"))
            textSize = 12f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
        }

        val tvTitle = TextView(activity).apply {
            text = info.title
            setTextColor(Color.parseColor("#F8FAFC"))
            textSize = 18f
            typeface = Typeface.DEFAULT_BOLD
            gravity = Gravity.CENTER
            setPadding(0, dp(8), 0, dp(10))
        }

        val tvNotes = TextView(activity).apply {
            text = info.releaseNotes
            setTextColor(Color.parseColor("#CBD5E1"))
            textSize = 14f
            gravity = Gravity.CENTER
            setLineSpacing(dp(3).toFloat(), 1.0f)
            setPadding(dp(6), dp(6), dp(6), dp(18))
        }

        val btnPrimaryBg = GradientDrawable(
            GradientDrawable.Orientation.LEFT_RIGHT,
            intArrayOf(Color.parseColor("#00F5D4"), Color.parseColor("#10B981"))
        ).apply {
            cornerRadius = dp(14).toFloat()
        }

        val btnUpdateNow = Button(activity).apply {
            text = "🚀 تحديث وتثبيت الآن تلقائياً"
            setTextColor(Color.parseColor("#061018"))
            textSize = 15f
            typeface = Typeface.DEFAULT_BOLD
            background = btnPrimaryBg
            isAllCaps = false
        }

        container.addView(tvBadge)
        container.addView(tvTitle)
        container.addView(tvNotes)
        container.addView(
            btnUpdateNow,
            LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(48))
        )

        var dialog: AlertDialog? = null

        if (!info.forceUpdate) {
            val btnLater = TextView(activity).apply {
                text = "تذكيري لاحقاً"
                setTextColor(Color.parseColor("#94A3B8"))
                textSize = 13f
                gravity = Gravity.CENTER
                setPadding(0, dp(14), 0, dp(4))
                setOnClickListener {
                    dialog?.dismiss()
                }
            }
            container.addView(btnLater)
        }

        dialog = AlertDialog.Builder(activity)
            .setView(container)
            .setCancelable(!info.forceUpdate)
            .setOnDismissListener {
                isUpdateDialogShowing = false
            }
            .create()

        dialog.window?.setBackgroundDrawableResource(android.R.color.transparent)

        btnUpdateNow.setOnClickListener {
            dialog.dismiss()
            try {
                val intent = Intent(Intent.ACTION_VIEW, Uri.parse(info.downloadUrl)).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                activity.startActivity(intent)
            } catch (e: Exception) {
                Toast.makeText(activity, "⚠️ تعذر فتح رابط التحميل: ${e.message}", Toast.LENGTH_LONG).show()
            }
        }

        dialog.show()
    }
}

