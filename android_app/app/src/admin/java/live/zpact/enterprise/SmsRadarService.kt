package live.zpact.enterprise

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * خدمة العمل بالخلفية 24/7 لرصد التحويلات ومراجعة صندوق الـ SMS ومزامنته دورياً مع السيرفر
 */
class SmsRadarService : Service() {

    companion object {
        private const val TAG = "SmsRadarService"
        private const val NOTIFICATION_ID = 9021
        private const val CHANNEL_ID = "zpact_radar_channel"

        @Volatile
        var isServiceRunning: Boolean = false
            private set

        fun startRadarService(context: Context) {
            try {
                val appCtx = context.applicationContext
                val intent = Intent(appCtx, SmsRadarService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    appCtx.startForegroundService(intent)
                } else {
                    appCtx.startService(intent)
                }
            } catch (e: Exception) {
                Log.w(TAG, "تعذر تشغيل الخدمة بالخلفية مؤقتاً: ${e.message}")
            }
        }

        fun stopRadarService(context: Context) {
            try {
                val appCtx = context.applicationContext
                val intent = Intent(appCtx, SmsRadarService::class.java)
                appCtx.stopService(intent)
            } catch (_: Exception) {
            }
        }
    }

    private val serviceJob = Job()
    private val serviceScope = CoroutineScope(Dispatchers.IO + serviceJob)

    override fun onCreate() {
        super.onCreate()
        isServiceRunning = true
        createNotificationChannel()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIFICATION_ID,
                    buildNotification(),
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
                )
            } else {
                startForeground(NOTIFICATION_ID, buildNotification())
            }
        } catch (e: Exception) {
            Log.e(TAG, "تحذير بدء الواجهة الأمامية للخدمة: ${e.message}")
        }
        startRadarSyncLoop()
        Log.i(TAG, "🟢 تم تشغيل خدمة رادار المدفوعات والمزامنة التلقائية بالخلفية")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        updateNotification()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        isServiceRunning = false
        serviceJob.cancel()
        super.onDestroy()
    }

    /**
     * حلقة نبض ومراجعة ذكية كل 45 ثانية تراجع أحدث رسائل الـ SMS وتفرغ طابور الأوفلاين للسيرفر
     * مع حلقة سريعة كل 5 ثوانٍ لمعالجة وإرسال رموز الـ OTP المجدولة فورياً عبر شريحة الأدمن
     */
    private fun startRadarSyncLoop() {
        serviceScope.launch {
            while (isActive) {
                try {
                    val config = AppConfigManager.getInstance(applicationContext)
                    if (config.isRadarEnabled()) {
                        OtpSmsRelay.processQueuedOtps(applicationContext)
                    }
                } catch (_: Exception) {}
                delay(5_000L)
            }
        }

        serviceScope.launch {
            while (isActive) {
                try {
                    val config = AppConfigManager.getInstance(applicationContext)
                    if (config.isRadarEnabled()) {
                        SmsInboxScanner.syncInboxAndFlush(
                            context = applicationContext,
                            lookbackDays = 3,
                            maxMessages = 150
                        )
                    }
                    updateNotification()
                } catch (e: Exception) {
                    Log.e(TAG, "خطأ في دورة نبض الرادار: ${e.message}")
                }
                delay(45_000L)
            }
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val name = getString(R.string.radar_service_channel_name)
            val desc = getString(R.string.radar_service_channel_desc)
            val channel = NotificationChannel(
                CHANNEL_ID,
                name,
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = desc
                setShowBadge(false)
            }
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val config = AppConfigManager.getInstance(applicationContext)
        val count = config.getInterceptedCount()

        val openAppIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.radar_active_title))
            .setContentText("تم حفظ ومزامنة $count تحويل | يراقب المحافظ وإنستاباي والبنوك ⚡")
            .setSmallIcon(R.drawable.ic_radar)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    private fun updateNotification() {
        try {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.notify(NOTIFICATION_ID, buildNotification())
        } catch (_: Exception) {
        }
    }
}

