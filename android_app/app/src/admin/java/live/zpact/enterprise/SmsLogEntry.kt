package live.zpact.enterprise

/**
 * نموذج بيانات رسالة الـ SMS المالية المرصودة وحالتها في طابور المزامنة مع السيرفر
 */
data class SmsLogEntry(
    val id: Long = 0,
    val sender: String,
    val body: String,
    val timestamp: Long = System.currentTimeMillis(),
    val extractedAmount: Double? = null,
    val extractedPhone: String? = null,
    val provider: String = "Transfer",
    val smsHash: String = "",
    var dispatchStatus: String = STATUS_QUEUED,
    var retryCount: Int = 0
) {
    companion object {
        const val STATUS_QUEUED = "QUEUED"
        const val STATUS_DISPATCHED = "DISPATCHED"
        const val STATUS_FAILED = "FAILED"
    }
}

