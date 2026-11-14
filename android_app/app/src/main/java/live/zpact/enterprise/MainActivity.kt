package live.zpact.enterprise

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.view.View
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.RelativeLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.google.android.material.bottomnavigation.BottomNavigationView
import kotlinx.coroutines.launch

/**
 * الشاشة الرئيسية لتطبيقات منظومة Z-PACT v3.0 (الثلاثية):
 * - نسخة المدرسين (teacher): تفتح المتجر (/store) مباشرة بملء الشاشة بدون أي إعدادات أو أذونات SMS.
 * - نسخة المرسلين (sender): تفتح بوابة فريق الإرسال (/sender) مباشرة بملء الشاشة بدون أي أذونات SMS.
 * - نسخة الأدمن (admin): تفتح المنصة مع شريط حالة رادار الـ SMS والمراجعة التلقائية لصندوق الرسائل والتنقل السريع.
 * - تم إلغاء السحب للتحديث (Pull-to-Refresh) نهائياً، مع التحديث عبر الزر المخصص فقط + فحص OTA التلقائي.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var headerLayout: RelativeLayout
    private lateinit var headerDivider: View
    private lateinit var webContainer: FrameLayout
    private lateinit var webView: WebView
    private lateinit var progressBar: ProgressBar
    private lateinit var layoutError: LinearLayout
    private lateinit var tvErrorDetails: TextView
    private lateinit var btnRetry: Button
    private lateinit var btnErrorSettings: View
    private lateinit var bottomNav: BottomNavigationView
    private lateinit var pillRadarStatus: LinearLayout
    private lateinit var indicatorRadarDot: View
    private lateinit var tvRadarPillText: TextView
    private lateinit var btnRefreshPage: ImageButton
    private lateinit var btnServerSettings: ImageButton

    private lateinit var configManager: AppConfigManager
    private var fileUploadCallback: ValueCallback<Array<Uri>>? = null
    private var activePathSuffix: String = "/store"
    private var lastAutoHealAttemptMs: Long = 0L

    private val filePickerLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (fileUploadCallback == null) return@registerForActivityResult
        val results = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        fileUploadCallback?.onReceiveValue(results)
        fileUploadCallback = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        configManager = AppConfigManager.getInstance(this)
        activePathSuffix = when (BuildConfig.FLAVOR_ROLE) {
            "admin" -> "/admin"
            "sender" -> "/sender"
            else -> "/store"
        }

        initViews()
        applyFlavorChrome()
        setupWebView()
        setupListeners()
        setupBackNavigation()

        if (BuildConfig.IS_ADMIN_FLAVOR) {
            bottomNav.selectedItemId = R.id.navigation_admin_hub
            RadarPlatformHook.setupActivityHooks(this) { updateRadarPillUI() }
            updateRadarPillUI()
        }

        connectAndLoadPlatform(forceDiscovery = false)
    }

    override fun onResume() {
        super.onResume()
        AppUpdateManager.resumePendingInstallIfPermitted(this)
        if (BuildConfig.IS_ADMIN_FLAVOR) {
            // مراجعة تلقائية لصندوق رسائل الـ SMS فور فتح التطبيق لرصد أي تحويلات وصلت وهو مغلق
            RadarPlatformHook.onActivityResumed(this) { updateRadarPillUI() }
        }
        val base = configManager.getServerUrl()
        if (base.isNotEmpty()) {
            AppUpdateManager.checkForUpdates(this, baseUrl = base, silentIfUpToDate = true)
        }
    }

    private fun initViews() {
        headerLayout = findViewById(R.id.header_layout)
        headerDivider = findViewById(R.id.header_divider)
        webContainer = findViewById(R.id.web_container)
        webView = findViewById(R.id.web_view)
        progressBar = findViewById(R.id.progress_loading)
        layoutError = findViewById(R.id.layout_connection_error)
        tvErrorDetails = findViewById(R.id.tv_error_details)
        btnRetry = findViewById(R.id.btn_retry_connection)
        btnErrorSettings = findViewById(R.id.btn_error_open_settings)
        bottomNav = findViewById(R.id.bottom_navigation)
        pillRadarStatus = findViewById(R.id.pill_radar_status)
        indicatorRadarDot = findViewById(R.id.indicator_radar_dot)
        tvRadarPillText = findViewById(R.id.tv_radar_pill_text)
        btnRefreshPage = findViewById(R.id.btn_refresh_page)
        btnServerSettings = findViewById(R.id.btn_server_settings)
    }

    /**
     * تخصيص الواجهة حسب النسخة:
     * نسختا المدرس والمرسل تعملان بملء الشاشة للموقع فقط بدون أي أزرار كونفج أو رادار
     */
    private fun applyFlavorChrome() {
        if (!BuildConfig.IS_ADMIN_FLAVOR) {
            headerLayout.visibility = View.GONE
            headerDivider.visibility = View.GONE
            bottomNav.visibility = View.GONE
            btnErrorSettings.visibility = View.GONE

            val progressParams = progressBar.layoutParams as RelativeLayout.LayoutParams
            progressParams.removeRule(RelativeLayout.BELOW)
            progressParams.addRule(RelativeLayout.ALIGN_PARENT_TOP)
            progressBar.layoutParams = progressParams

            val webParams = webContainer.layoutParams as RelativeLayout.LayoutParams
            webParams.removeRule(RelativeLayout.ABOVE)
            webParams.addRule(RelativeLayout.ALIGN_PARENT_BOTTOM)
            webContainer.layoutParams = webParams
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        webView.overScrollMode = View.OVER_SCROLL_IF_CONTENT_SCROLLS
        webView.isVerticalScrollBarEnabled = true
        webView.isHorizontalScrollBarEnabled = false
        webView.isNestedScrollingEnabled = false
        val settings = webView.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.allowFileAccess = true
        settings.allowContentAccess = true
        settings.useWideViewPort = true
        settings.loadWithOverviewMode = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        settings.userAgentString = "${settings.userAgentString} ZPactAndroidApp/${BuildConfig.FLAVOR_ROLE}"

        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        val nativeBridge = ZPactNativeBridge(
            context = this,
            onOpenSettings = {
                if (BuildConfig.IS_ADMIN_FLAVOR) {
                    runOnUiThread {
                        RadarPlatformHook.showServerConfigDialog(this) {
                            connectAndLoadPlatform(forceDiscovery = false)
                        }
                    }
                }
            },
            onOpenRadar = {
                if (BuildConfig.IS_ADMIN_FLAVOR) {
                    runOnUiThread {
                        RadarPlatformHook.showRadarMonitorDialog(this) { updateRadarPillUI() }
                    }
                }
            },
            onReloadPage = {
                performExplicitReload()
            },
            onCheckUpdates = {
                val base = configManager.getServerUrl()
                if (base.isNotEmpty()) {
                    AppUpdateManager.checkForUpdates(this, baseUrl = base, silentIfUpToDate = false)
                }
            }
        )
        webView.addJavascriptInterface(nativeBridge, "ZPactNativeBridge")

        // دعم تنزيل المذكرات المختومة (PDF) وملفات الـ APK مباشرة على الهاتف
        webView.setDownloadListener { url, userAgent, contentDisposition, mimetype, _ ->
            try {
                val fileName = URLUtil.guessFileName(url, contentDisposition, mimetype)
                val req = DownloadManager.Request(Uri.parse(url)).apply {
                    setMimeType(mimetype)
                    CookieManager.getInstance().getCookie(url)?.let { cookie ->
                        if (cookie.isNotBlank()) addRequestHeader("Cookie", cookie)
                    }
                    addRequestHeader("User-Agent", userAgent)
                    setTitle(fileName)
                    setDescription(getString(R.string.download_started))
                    setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Z-PACT/$fileName")
                    setAllowedOverMetered(true)
                    setAllowedOverRoaming(true)
                }
                val dm = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
                dm.enqueue(req)
                Toast.makeText(this, "📥 جاري تنزيل $fileName في مجلد التنزيلات...", Toast.LENGTH_LONG).show()
            } catch (_: Exception) {
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                } catch (_: Exception) {
                }
            }
        }

        webView.webViewClient = object : WebViewClient() {
            private fun handleAppUrl(urlStr: String, targetUri: Uri): Boolean {
                val scheme = (targetUri.scheme ?: "").lowercase()
                val host = (targetUri.host ?: "").lowercase()

                // توجيه روابط واتساب وتليجرام والاتصال المباشر للتطبيقات الأصلية على الهاتف فوراً
                if (scheme in listOf("whatsapp", "tg", "tel", "mailto", "sms") ||
                    host in listOf("wa.me", "api.whatsapp.com", "t.me", "www.t.me", "telegram.me") ||
                    urlStr.contains("t.me/") || urlStr.startsWith("tg://")
                ) {
                    return try {
                        startActivity(Intent(Intent.ACTION_VIEW, targetUri))
                        true
                    } catch (_: Exception) {
                        Toast.makeText(this@MainActivity, "⚠️ التطبيق المطلوب غير مثبت على الهاتف", Toast.LENGTH_SHORT).show()
                        true
                    }
                }

                // تنزيل مباشر لروابط الـ APK عند الضغط عليها
                if (urlStr.contains("/download/apk/")) {
                    return try {
                        startActivity(Intent(Intent.ACTION_VIEW, targetUri))
                        true
                    } catch (_: Exception) {
                        false
                    }
                }
                return false
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val targetUri = request?.url ?: return false
                val handled = handleAppUrl(targetUri.toString(), targetUri)
                if (handled) return true
                return super.shouldOverrideUrlLoading(view, request)
            }

            @Deprecated("Deprecated in Java")
            override fun shouldOverrideUrlLoading(view: WebView?, url: String?): Boolean {
                if (url.isNullOrBlank()) return false
                val uri = Uri.parse(url)
                val handled = handleAppUrl(url, uri)
                if (handled) return true
                return super.shouldOverrideUrlLoading(view, url)
            }

            override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
                progressBar.isIndeterminate = false
                progressBar.visibility = View.VISIBLE
                layoutError.visibility = View.GONE
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                progressBar.visibility = View.GONE
                webView.evaluateJavascript(
                    """
                    (function() {
                        if (document.documentElement) document.documentElement.classList.add('android-native-app', 'flavor-${BuildConfig.FLAVOR_ROLE}');
                        if (document.body) document.body.classList.add('android-native-app', 'flavor-${BuildConfig.FLAVOR_ROLE}');
                    })();
                    """.trimIndent(),
                    null
                )
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                if (request?.isForMainFrame == true) {
                    val code = errorResponse?.statusCode ?: 0
                    // استشفاء ذاتي فوري إذا تغير رابط نفق Cloudflare (Error 1033 / 530 / 502)
                    if (code in listOf(502, 503, 504, 530)) {
                        triggerAutoHealOrShowError()
                    }
                }
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true) {
                    triggerAutoHealOrShowError()
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                if (!progressBar.isIndeterminate) {
                    progressBar.progress = newProgress
                    if (newProgress >= 100) progressBar.visibility = View.GONE
                }
            }

            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                fileUploadCallback?.onReceiveValue(null)
                fileUploadCallback = filePathCallback

                val intent = fileChooserParams?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "*/*"
                    addCategory(Intent.CATEGORY_OPENABLE)
                }
                return try {
                    filePickerLauncher.launch(intent)
                    true
                } catch (_: Exception) {
                    fileUploadCallback = null
                    false
                }
            }
        }
    }

    private fun performExplicitReload() {
        if (layoutError.visibility == View.VISIBLE || configManager.getServerUrl().isEmpty()) {
            connectAndLoadPlatform(forceDiscovery = true)
        } else {
            Toast.makeText(this, "🔄 جاري تحديث البيانات...", Toast.LENGTH_SHORT).show()
            webView.reload()
        }
    }

    private fun triggerAutoHealOrShowError() {
        val now = System.currentTimeMillis()
        if (now - lastAutoHealAttemptMs > 10_000L) {
            lastAutoHealAttemptMs = now
            connectAndLoadPlatform(forceDiscovery = true)
            return
        }
        progressBar.visibility = View.GONE
        layoutError.visibility = View.VISIBLE
        tvErrorDetails.text = when (BuildConfig.FLAVOR_ROLE) {
            "admin" -> "جاري البحث عن السيرفر... تأكد من تشغيل المنصة على الحاسوب واتصال الإنترنت ثم اضغط إعادة المحاولة."
            "sender" -> "تأكد من اتصال هاتفك بالإنترنت ثم اضغط إعادة المحاولة لفتح بوابة المرسلين."
            else -> "تأكد من اتصال هاتفك بالإنترنت ثم اضغط إعادة المحاولة لفتح منصة Z-PACT."
        }
    }

    private fun setupListeners() {
        btnRetry.setOnClickListener {
            connectAndLoadPlatform(forceDiscovery = true)
        }

        btnRefreshPage.setOnClickListener {
            performExplicitReload()
        }

        if (BuildConfig.IS_ADMIN_FLAVOR) {
            btnErrorSettings.setOnClickListener {
                RadarPlatformHook.showServerConfigDialog(this) {
                    connectAndLoadPlatform(forceDiscovery = false)
                }
            }

            btnServerSettings.setOnClickListener {
                RadarPlatformHook.showServerConfigDialog(this) {
                    connectAndLoadPlatform(forceDiscovery = false)
                }
            }

            pillRadarStatus.setOnClickListener {
                RadarPlatformHook.showRadarMonitorDialog(this) { updateRadarPillUI() }
            }

            bottomNav.setOnItemSelectedListener { item ->
                when (item.itemId) {
                    R.id.navigation_store -> {
                        activePathSuffix = "/store"
                        val base = configManager.getServerUrl()
                        if (base.isNotEmpty()) webView.loadUrl("$base/store") else connectAndLoadPlatform(false)
                        true
                    }
                    R.id.navigation_admin_hub -> {
                        activePathSuffix = "/admin"
                        val base = configManager.getServerUrl()
                        if (base.isNotEmpty()) webView.loadUrl("$base/admin") else connectAndLoadPlatform(false)
                        true
                    }
                    R.id.navigation_orders -> {
                        activePathSuffix = "/miniapp/?tg_id=8287744705"
                        val base = configManager.getServerUrl()
                        if (base.isNotEmpty()) webView.loadUrl("$base$activePathSuffix") else connectAndLoadPlatform(false)
                        true
                    }
                    R.id.navigation_radar -> {
                        RadarPlatformHook.showRadarMonitorDialog(this) { updateRadarPillUI() }
                        false
                    }
                    else -> false
                }
            }
        }
    }

    private fun connectAndLoadPlatform(forceDiscovery: Boolean) {
        layoutError.visibility = View.GONE
        progressBar.isIndeterminate = true
        progressBar.visibility = View.VISIBLE

        lifecycleScope.launch {
            val liveServer = ServerDiscoveryEngine.resolveLiveServer(
                context = this@MainActivity,
                forceRefresh = forceDiscovery
            )
            if (liveServer != null) {
                layoutError.visibility = View.GONE
                webView.loadUrl("$liveServer$activePathSuffix")
                AppUpdateManager.checkForUpdates(
                    activity = this@MainActivity,
                    baseUrl = liveServer,
                    silentIfUpToDate = true
                )
            } else {
                progressBar.visibility = View.GONE
                layoutError.visibility = View.VISIBLE
                tvErrorDetails.text = when (BuildConfig.FLAVOR_ROLE) {
                    "admin" -> "تعذر العثور على سيرفر Z-PACT تلقائياً حالياً.\nتأكد من تشغيل المنصة أو اتصال الإنترنت ثم اضغط إعادة المحاولة."
                    "sender" -> "يتعذر الوصول إلى بوابة المرسلين حالياً.\nتأكد من اتصالك بالإنترنت ثم اضغط إعادة المحاولة."
                    else -> "يتعذر الوصول إلى منصة Z-PACT في هذه اللحظة.\nتأكد من اتصالك بالإنترنت ثم اضغط إعادة المحاولة."
                }
            }
        }
    }

    private fun updateRadarPillUI() {
        if (!BuildConfig.IS_ADMIN_FLAVOR) return
        val isEnabled = configManager.isRadarEnabled()
        val count = configManager.getInterceptedCount()
        val isRunning = RadarPlatformHook.isRadarRunning(this)

        if (isEnabled && isRunning) {
            indicatorRadarDot.setBackgroundColor(ContextCompat.getColor(this, R.color.status_green))
            tvRadarPillText.text = if (count > 0) "رادار نشط: $count" else "رادار الـ SMS نشط 🟢"
        } else {
            indicatorRadarDot.setBackgroundColor(ContextCompat.getColor(this, R.color.status_red))
            tvRadarPillText.text = "الرادار متوقف 🔴"
        }
    }

    private fun setupBackNavigation() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })
    }
}
