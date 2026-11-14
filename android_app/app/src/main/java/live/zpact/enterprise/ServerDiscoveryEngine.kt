package live.zpact.enterprise

import android.content.Context
import android.util.Log
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.concurrent.TimeUnit

/**
 * محرك الاكتشاف الذاتي للسيرفر (Zero-Config Auto-Discovery Engine)
 * يتصل تلقائياً بسيرفر Z-PACT عبر الإنترنت (Cloud Beacon) أو الشبكة المحلية (LAN) بدون أي تدخل يدوي.
 */
object ServerDiscoveryEngine {

    private const val TAG = "ServerDiscovery"

    private val probeClient = OkHttpClient.Builder()
        .connectTimeout(2500, TimeUnit.MILLISECONDS)
        .readTimeout(3000, TimeUnit.MILLISECONDS)
        .followRedirects(true)
        .build()

    private val lanClient = OkHttpClient.Builder()
        .connectTimeout(850, TimeUnit.MILLISECONDS)
        .readTimeout(1200, TimeUnit.MILLISECONDS)
        .build()

    private val beaconClient = OkHttpClient.Builder()
        .connectTimeout(5000, TimeUnit.MILLISECONDS)
        .readTimeout(5000, TimeUnit.MILLISECONDS)
        .build()

    /**
     * التحقق الفعلي أن الرابط يشير إلى منصة Z-PACT شغالة وليس صفحة خطأ Cloudflare 1033
     */
    fun isServerHealthySync(baseUrl: String, fastLan: Boolean = false): Boolean {
        val clean = baseUrl.trim().trimEnd('/')
        if (clean.isEmpty()) return false
        return try {
            val req = Request.Builder()
                .url("$clean/api/health")
                .header("Accept", "application/json")
                .get()
                .build()
            val client = if (fastLan) lanClient else probeClient
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val body = resp.body?.string().orEmpty()
                body.contains("WebPDF Elite") || body.contains("\"status\":\"ok\"")
            }
        } catch (_: Exception) {
            false
        }
    }

    /**
     * اكتشاف وحفظ رابط السيرفر الحي تلقائياً
     */
    suspend fun resolveLiveServer(context: Context, forceRefresh: Boolean = false): String? =
        withContext(Dispatchers.IO) {
            val config = AppConfigManager.getInstance(context)

            // 1. فحص الرابط المحفوظ مسبقاً أولاً لسرعة الفتح اللحظية
            if (!forceRefresh) {
                val cached = config.getServerUrl()
                if (cached.isNotEmpty() && isServerHealthySync(cached)) {
                    return@withContext cached
                }
            }

            // 2. فحص العناوين المضمنة في التطبيق وعناوين منارة السحابة بالتوازي
            val candidates = LinkedHashSet<String>()
            val defaultCloud = BuildConfig.DEFAULT_SERVER_URL.trim()
            if (defaultCloud.isNotEmpty()) candidates.add(defaultCloud)
            val defaultLan = BuildConfig.DEFAULT_LAN_URL.trim()
            if (defaultLan.isNotEmpty()) candidates.add(defaultLan)
            candidates.addAll(fetchCloudBeaconCandidates(config))

            if (candidates.isNotEmpty()) {
                val liveMatch = probeOrderedCandidates(candidates.toList())
                if (liveMatch != null) {
                    config.setServerUrl(liveMatch)
                    Log.i(TAG, "✓ تم الاتصال التلقائي بالسيرفر: $liveMatch")
                    return@withContext liveMatch
                }
            }

            // 3. في حال العمل على محاكي أندرويد على نفس الحاسوب
            if (AppConfigManager.isRunningOnEmulator()) {
                val emuUrl = "http://10.0.2.2:8000"
                if (isServerHealthySync(emuUrl, fastLan = true)) {
                    config.setServerUrl(emuUrl)
                    return@withContext emuUrl
                }
            }

            // 4. المسح التلقائي للشبكة المحلية (Wi-Fi LAN Fallback) عند انقطاع الإنترنت الخارجي
            val lanDiscovered = scanLocalWifiSubnet()
            if (lanDiscovered != null) {
                config.setServerUrl(lanDiscovered)
                Log.i(TAG, "✓ تم اكتشاف السيرفر تلقائياً على شبكة الـ Wi-Fi: $lanDiscovered")
                return@withContext lanDiscovered
            }

            null
        }

    private fun fetchCloudBeaconCandidates(config: AppConfigManager): List<String> {
        val topic = BuildConfig.CLOUD_BEACON_TOPIC.trim()
        if (topic.isEmpty()) return emptyList()

        val candidates = LinkedHashSet<String>()
        try {
            val url = "https://ntfy.sh/$topic/json?poll=1&since=all"
            val req = Request.Builder()
                .url(url)
                .header("Cache-Control", "no-cache")
                .get()
                .build()

            beaconClient.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return emptyList()
                val rawLines = resp.body?.string().orEmpty()
                    .lineSequence()
                    .map { it.trim() }
                    .filter { it.isNotEmpty() }
                    .toList()

                // قراءة السجلات من الأحدث إلى الأقدم
                for (line in rawLines.asReversed()) {
                    try {
                        val outer = JsonParser.parseString(line).asJsonObject
                        if (outer.get("event")?.asString != "message") continue
                        val msgStr = outer.get("message")?.asString ?: continue
                        val payload = JsonParser.parseString(msgStr).asJsonObject

                        payload.get("server_url")?.asString?.let { sUrl ->
                            val clean = config.normalizeServerUrl(sUrl)
                            if (clean.isNotEmpty()) candidates.add(clean)
                        }
                        payload.get("lan_url")?.asString?.let { lUrl ->
                            val clean = config.normalizeServerUrl(lUrl)
                            if (clean.isNotEmpty()) candidates.add(clean)
                        }
                        if (candidates.size >= 8) break
                    } catch (_: Exception) {
                        // تجاهل أي سطر غير مطابق
                    }
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "تعذر استعلام منارة السحابة: ${e.message}")
        }
        return candidates.toList()
    }

    private suspend fun probeOrderedCandidates(candidates: List<String>): String? = coroutineScope {
        val checks = candidates.map { candidate ->
            async(Dispatchers.IO) {
                candidate to isServerHealthySync(candidate)
            }
        }.awaitAll()
        checks.firstOrNull { it.second }?.first
    }

    private suspend fun scanLocalWifiSubnet(): String? = coroutineScope {
        val subnetPrefix = getDeviceSubnetPrefix() ?: return@coroutineScope null
        val hostOctets = (1..60).toList() + (100..130).toList()

        for (port in listOf(8000, 8088)) {
            for (batch in hostOctets.chunked(30)) {
                val results = batch.map { host ->
                    async(Dispatchers.IO) {
                        val candidate = "http://$subnetPrefix.$host:$port"
                        if (isServerHealthySync(candidate, fastLan = true)) candidate else null
                    }
                }.awaitAll()
                val winner = results.firstOrNull { it != null }
                if (winner != null) return@coroutineScope winner
            }
        }
        null
    }

    private fun getDeviceSubnetPrefix(): String? {
        return try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return null
            for (intf in interfaces) {
                if (!intf.isUp || intf.isLoopback) continue
                for (addr in intf.inetAddresses) {
                    if (addr is Inet4Address && !addr.isLoopbackAddress && addr.isSiteLocalAddress) {
                        val ip = addr.hostAddress ?: continue
                        val parts = ip.split(".")
                        if (parts.size == 4) {
                            return "${parts[0]}.${parts[1]}.${parts[2]}"
                        }
                    }
                }
            }
            null
        } catch (_: Exception) {
            null
        }
    }
}

