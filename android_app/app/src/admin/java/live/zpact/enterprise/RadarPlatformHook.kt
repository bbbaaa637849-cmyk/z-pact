package live.zpact.enterprise

import android.Manifest
import android.annotation.SuppressLint
import android.app.Dialog
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.gson.JsonObject
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * وحدة التحكم الكاملة بنسخة الأدمن والرادار الأسطورية:
 * - طلب الأذونات وتشغيل الرادار بالخلفية
 * - الفحص التلقائي لصندوق رسائل الهاتف عند كل فتح للتطبيق (Historical Catch-Up)
 * - لوحة مراقبة الرادار ومزامنة كل الرسائل بضغطة زر
 */
object RadarPlatformHook {

    private var lastResumeSyncAtMs: Long = 0L

    fun setupActivityHooks(activity: AppCompatActivity, onStatusChanged: () -> Unit) {
        val config = AppConfigManager.getInstance(activity)

        val permLauncher = activity.registerForActivityResult(
            ActivityResultContracts.RequestMultiplePermissions()
        ) { permissions ->
            val smsGranted = permissions[Manifest.permission.RECEIVE_SMS] == true ||
                    permissions[Manifest.permission.READ_SMS] == true
            if (smsGranted && config.isRadarEnabled()) {
                SmsRadarService.startRadarService(activity)
                runAutomaticInboxCatchUp(activity, lookbackDays = 30, showToastAlways = true, onStatusChanged = onStatusChanged)
            } else {
                onStatusChanged()
            }
        }

        val requiredPerms = mutableListOf(
            Manifest.permission.RECEIVE_SMS,
            Manifest.permission.READ_SMS,
            Manifest.permission.SEND_SMS
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requiredPerms.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        val missing = requiredPerms.filter {
            ContextCompat.checkSelfPermission(activity, it) != PackageManager.PERMISSION_GRANTED
        }

        if (missing.isNotEmpty()) {
            permLauncher.launch(missing.toTypedArray())
        } else if (config.isRadarEnabled()) {
            SmsRadarService.startRadarService(activity)
        }
    }

    fun onActivityResumed(activity: AppCompatActivity, onStatusChanged: () -> Unit) {
        val config = AppConfigManager.getInstance(activity)
        val db = SmsDatabaseHelper(activity)
        db.purgeInvalidOrSpamLogs()
        config.setInterceptedCount(db.getTotalCount())

        if (!config.isRadarEnabled()) {
            onStatusChanged()
            return
        }
        if (!SmsRadarService.isServiceRunning) {
            SmsRadarService.startRadarService(activity)
        }

        val now = System.currentTimeMillis()
        // منع الفحص المكرر خلال أقل من 5 ثوانٍ فقط، مع فحص كل الرسائل الفائتة فور إعادة فتح التطبيق!
        if (now - lastResumeSyncAtMs < 5_000L) {
            onStatusChanged()
            return
        }
        lastResumeSyncAtMs = now
        val isFirstHistoricalAudit = config.getLastInboxSyncTime() == 0L
        val daysToScan = if (isFirstHistoricalAudit) 30 else 14
        runAutomaticInboxCatchUp(
            activity = activity,
            lookbackDays = daysToScan,
            showToastAlways = false,
            onStatusChanged = onStatusChanged
        )
    }

    private fun runAutomaticInboxCatchUp(
        activity: AppCompatActivity,
        lookbackDays: Int,
        showToastAlways: Boolean,
        onStatusChanged: () -> Unit
    ) {
        activity.lifecycleScope.launch {
            val summary = SmsInboxScanner.syncInboxAndFlush(
                context = activity,
                lookbackDays = lookbackDays,
                maxMessages = 500
            )
            onStatusChanged()

            if (!summary.hasPermission) return@launch

            if (summary.newlyCapturedTransfers > 0 || summary.syncedToServerNow > 0) {
                val msg = "✅ رادار Z-PACT راجع رسائل الهاتف:\n" +
                        "تم اكتشاف ${summary.newlyCapturedTransfers} تحويل جديد ومزامنة ${summary.syncedToServerNow} مع السيرفر!"
                Toast.makeText(activity, msg, Toast.LENGTH_LONG).show()
            } else if (showToastAlways) {
                val pendingInfo = if (summary.remainingOfflineQueue > 0) {
                    " (${summary.remainingOfflineQueue} بانتظار اتصال السيرفر)"
                } else {
                    ""
                }
                Toast.makeText(
                    activity,
                    "📡 تمت مراجعة ${summary.scannedSmsCount} رسالة بالهاتف — كل التحويلات محفوظة ومزامنة بنجاح!$pendingInfo",
                    Toast.LENGTH_SHORT
                ).show()
            }
        }
    }

    @Suppress("UNUSED_PARAMETER")
    fun isRadarRunning(context: Context): Boolean {
        return SmsRadarService.isServiceRunning
    }

    fun showServerConfigDialog(activity: AppCompatActivity, onServerChanged: () -> Unit) {
        val config = AppConfigManager.getInstance(activity)
        val dialog = Dialog(activity, R.style.Theme_ZPactEnterprise_Dialog)
        dialog.setContentView(R.layout.dialog_server_config)

        val etUrl = dialog.findViewById<EditText>(R.id.et_server_url)
        val etSecret = dialog.findViewById<EditText>(R.id.et_webhook_secret)
        val etAdminKey = dialog.findViewById<EditText>(R.id.et_admin_key)
        val tvResult = dialog.findViewById<TextView>(R.id.tv_test_result)
        val btnTest = dialog.findViewById<Button>(R.id.btn_test_connection)
        val btnSave = dialog.findViewById<Button>(R.id.btn_save_config)

        etUrl.setText(config.getServerUrl())
        etSecret.setText(config.getWebhookSecret())
        etAdminKey.setText(config.getAdminKey())

        btnTest.setOnClickListener {
            tvResult.visibility = View.VISIBLE
            tvResult.text = "⏳ جاري الاكتشاف التلقائي وفحص الاتصال بالسيرفر..."
            val typedUrl = etUrl.text.toString().trim()
            WebhookDispatcher.testServerConnection(activity, typedUrl.ifEmpty { null }) { success, msg ->
                etUrl.setText(config.getServerUrl())
                tvResult.text = msg
                tvResult.setTextColor(
                    ContextCompat.getColor(activity, if (success) R.color.status_green else R.color.status_red)
                )
            }
        }

        btnSave.setOnClickListener {
            val newUrl = etUrl.text.toString().trim()
            val newSecret = etSecret.text.toString().trim()
            val newAdmin = etAdminKey.text.toString().trim()

            config.setServerUrl(newUrl)
            if (newSecret.isNotEmpty()) config.setWebhookSecret(newSecret)
            if (newAdmin.isNotEmpty()) config.setAdminKey(newAdmin)

            Toast.makeText(activity, "💾 تم حفظ إعدادات السيرفر بنجاح!", Toast.LENGTH_SHORT).show()
            dialog.dismiss()
            onServerChanged()
        }

        dialog.show()
    }

    fun showRadarMonitorDialog(activity: AppCompatActivity, onStatusChanged: () -> Unit) {
        val config = AppConfigManager.getInstance(activity)
        val dialog = Dialog(activity, R.style.Theme_ZPactEnterprise_Dialog)
        dialog.setContentView(R.layout.dialog_radar_monitor)

        val switchToggle = dialog.findViewById<MaterialSwitch>(R.id.switch_radar_toggle)
        val tvStateDesc = dialog.findViewById<TextView>(R.id.tv_radar_state_desc)
        val tvCountIntercepted = dialog.findViewById<TextView>(R.id.tv_count_intercepted)
        val tvCountQueue = dialog.findViewById<TextView>(R.id.tv_count_pending_queue)
        val btnSyncInbox = dialog.findViewById<Button>(R.id.btn_sync_inbox_sms)
        val btnTestSms = dialog.findViewById<Button>(R.id.btn_trigger_test_sms)
        val btnClear = dialog.findViewById<TextView>(R.id.btn_clear_sms_logs)
        val rvLogs = dialog.findViewById<RecyclerView>(R.id.rv_sms_logs)
        val btnClose = dialog.findViewById<ImageButton>(R.id.btn_close_radar_dialog)

        val db = SmsDatabaseHelper(activity)
        val adapter = SmsLogAdapter(db.getAllLogs())
        rvLogs.layoutManager = LinearLayoutManager(activity)
        rvLogs.adapter = adapter

        fun refreshCounts() {
            val isEnabled = config.isRadarEnabled()
            switchToggle.isChecked = isEnabled
            tvStateDesc.text = if (isEnabled) "🟢 الرادار نشط ويرصد ويحفظ كل التحويلات" else "🔴 الرادار متوقف حالياً"
            tvStateDesc.setTextColor(
                ContextCompat.getColor(activity, if (isEnabled) R.color.status_green else R.color.status_red)
            )
            tvCountIntercepted.text = db.getTotalCount().toString()
            tvCountQueue.text = db.getPendingCount().toString()
            adapter.updateItems(db.getAllLogs())
            onStatusChanged()
        }

        refreshCounts()

        switchToggle.setOnCheckedChangeListener { _, isChecked ->
            config.setRadarEnabled(isChecked)
            if (isChecked) {
                SmsRadarService.startRadarService(activity)
                Toast.makeText(activity, "🟢 تم تفعيل رادار التحويلات بالخلفية", Toast.LENGTH_SHORT).show()
            } else {
                SmsRadarService.stopRadarService(activity)
                Toast.makeText(activity, "🔴 تم إيقاف رادار التحويلات", Toast.LENGTH_SHORT).show()
            }
            refreshCounts()
        }

        btnSyncInbox.setOnClickListener {
            btnSyncInbox.isEnabled = false
            btnSyncInbox.text = "⏳ جاري فحص ومزامنة رسائل الهاتف..."
            activity.lifecycleScope.launch {
                val summary = SmsInboxScanner.syncInboxAndFlush(
                    context = activity,
                    lookbackDays = 60,
                    maxMessages = 800
                )
                btnSyncInbox.isEnabled = true
                btnSyncInbox.text = "🔄 مراجعة ومزامنة كل رسائل الـ SMS بالهاتف"
                refreshCounts()

                val report = if (!summary.hasPermission) {
                    "⚠️ يرجى منح إذن قراءة رسائل الـ SMS أولاً ليتمكن الرادار من المراجعة!"
                } else {
                    "✅ اكتمل الفحص الشامل (${summary.scannedSmsCount} رسالة):\n" +
                            "تحويلات جديدة: ${summary.newlyCapturedTransfers} | رُفعت للسيرفر: ${summary.syncedToServerNow}"
                }
                Toast.makeText(activity, report, Toast.LENGTH_LONG).show()
            }
        }

        btnTestSms.setOnClickListener {
            btnTestSms.isEnabled = false
            Toast.makeText(activity, "⏳ جاري إرسال تحويل فودافون كاش تجريبي للسيرفر...", Toast.LENGTH_SHORT).show()
            WebhookDispatcher.dispatchTestSms(activity) { _, msg ->
                btnTestSms.isEnabled = true
                Toast.makeText(activity, msg, Toast.LENGTH_LONG).show()
                refreshCounts()
            }
        }

        btnClear.setOnClickListener {
            db.clearLogs()
            config.resetInterceptedCount()
            refreshCounts()
            Toast.makeText(activity, "تم مسح السجل المحلي (التحويلات بالسيرفر تظل محفوظة بأمان)", Toast.LENGTH_SHORT).show()
        }

        btnClose.setOnClickListener {
            dialog.dismiss()
        }

        dialog.show()
    }

    fun getRadarStatusJson(context: Context): String {
        val config = AppConfigManager.getInstance(context)
        val db = SmsDatabaseHelper(context)
        return JsonObject().apply {
            addProperty("enabled", config.isRadarEnabled())
            addProperty("serviceRunning", SmsRadarService.isServiceRunning)
            addProperty("isAdminFlavor", true)
            addProperty("interceptedCount", config.getInterceptedCount())
            addProperty("pendingCount", db.getPendingCount())
            addProperty("totalDbLogs", db.getTotalCount())
            addProperty("serverUrl", config.getServerUrl())
        }.toString()
    }

    fun toggleRadarFromJs(context: Context, enable: Boolean): Boolean {
        val config = AppConfigManager.getInstance(context)
        config.setRadarEnabled(enable)
        if (enable) {
            SmsRadarService.startRadarService(context)
        } else {
            SmsRadarService.stopRadarService(context)
        }
        return config.isRadarEnabled()
    }

    fun sendTestWebhookFromJs(context: Context): String {
        WebhookDispatcher.dispatchTestSms(context) { _, _ -> }
        return "جاري إرسال رسالة الفحص إلى السيرفر..."
    }

    private class SmsLogAdapter(private var items: List<SmsLogEntry>) :
        RecyclerView.Adapter<SmsLogAdapter.ViewHolder>() {

        private val timeFormat = SimpleDateFormat("MM/dd HH:mm", Locale.getDefault())

        class ViewHolder(v: View) : RecyclerView.ViewHolder(v) {
            val tvSender: TextView = v.findViewById(R.id.tv_item_sender)
            val tvTime: TextView = v.findViewById(R.id.tv_item_time)
            val tvBody: TextView = v.findViewById(R.id.tv_item_body)
            val tvAmount: TextView = v.findViewById(R.id.tv_item_amount)
            val tvStatus: TextView = v.findViewById(R.id.tv_item_status)
        }

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
            val v = LayoutInflater.from(parent.context).inflate(R.layout.item_sms_log, parent, false)
            return ViewHolder(v)
        }

        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            val item = items[position]
            holder.tvSender.text = "${item.provider} • ${item.sender}"
            holder.tvTime.text = timeFormat.format(Date(item.timestamp))
            holder.tvBody.text = item.body
            holder.tvAmount.text = item.extractedAmount?.let { "$it ج.م" } ?: "غير محدد"

            if (item.dispatchStatus == SmsLogEntry.STATUS_DISPATCHED) {
                holder.tvStatus.text = "✅ محفوظ بالسيرفر"
                holder.tvStatus.setTextColor(ContextCompat.getColor(holder.itemView.context, R.color.status_green))
            } else {
                holder.tvStatus.text = "⏳ محفوظ محلياً — بانتظار المزامنة (${item.retryCount})"
                holder.tvStatus.setTextColor(ContextCompat.getColor(holder.itemView.context, R.color.status_yellow))
            }
        }

        override fun getItemCount(): Int = items.size

        @SuppressLint("NotifyDataSetChanged")
        fun updateItems(newItems: List<SmsLogEntry>) {
            items = newItems
            notifyDataSetChanged()
        }
    }
}

