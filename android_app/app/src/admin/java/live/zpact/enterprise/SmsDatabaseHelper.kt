package live.zpact.enterprise

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper

/**
 * قاعدة بيانات محلية (SQLite) لتخزين جميع التحويلات المالية ومنع التكرار وإدارة طابور الأوفلاين
 */
class SmsDatabaseHelper(context: Context) :
    SQLiteOpenHelper(context.applicationContext, DATABASE_NAME, null, DATABASE_VERSION) {

    companion object {
        private const val DATABASE_NAME = "zpact_sms_radar.db"
        private const val DATABASE_VERSION = 2

        const val TABLE_SMS_LOGS = "sms_logs"
        const val COL_ID = "id"
        const val COL_SENDER = "sender"
        const val COL_BODY = "body"
        const val COL_TIMESTAMP = "timestamp"
        const val COL_EXTRACTED_AMOUNT = "extracted_amount"
        const val COL_EXTRACTED_PHONE = "extracted_phone"
        const val COL_PROVIDER = "provider"
        const val COL_SMS_HASH = "sms_hash"
        const val COL_STATUS = "dispatch_status"
        const val COL_RETRY_COUNT = "retry_count"
    }

    override fun onCreate(db: SQLiteDatabase) {
        val createQuery = """
            CREATE TABLE $TABLE_SMS_LOGS (
                $COL_ID INTEGER PRIMARY KEY AUTOINCREMENT,
                $COL_SENDER TEXT NOT NULL,
                $COL_BODY TEXT NOT NULL,
                $COL_TIMESTAMP INTEGER NOT NULL,
                $COL_EXTRACTED_AMOUNT REAL,
                $COL_EXTRACTED_PHONE TEXT,
                $COL_PROVIDER TEXT DEFAULT 'Transfer',
                $COL_SMS_HASH TEXT UNIQUE,
                $COL_STATUS TEXT NOT NULL,
                $COL_RETRY_COUNT INTEGER DEFAULT 0
            )
        """.trimIndent()
        db.execSQL(createQuery)
        db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_unique_hash ON $TABLE_SMS_LOGS($COL_SMS_HASH)")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        if (oldVersion < 2) {
            try {
                db.execSQL("ALTER TABLE $TABLE_SMS_LOGS ADD COLUMN $COL_PROVIDER TEXT DEFAULT 'Transfer'")
            } catch (_: Exception) {
            }
            try {
                db.execSQL("ALTER TABLE $TABLE_SMS_LOGS ADD COLUMN $COL_SMS_HASH TEXT")
                db.execSQL("CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_unique_hash ON $TABLE_SMS_LOGS($COL_SMS_HASH)")
            } catch (_: Exception) {
            }
        }
    }

    /**
     * إدراج الرسالة فقط إذا لم تكن محفوظة مسبقاً (لمنع تكرار رسائل الصندوق التاريخية)
     * يعيد Pair(id, isNewlyInserted)
     */
    @Synchronized
    fun insertIfAbsent(entry: SmsLogEntry): Pair<Long, Boolean> {
        val hash = entry.smsHash.ifBlank {
            FinancialSmsDetector.computeSmsHash(entry.sender, entry.timestamp, entry.body)
        }
        val db = writableDatabase

        val checkCursor = db.query(
            TABLE_SMS_LOGS,
            arrayOf(COL_ID),
            "$COL_SMS_HASH = ?",
            arrayOf(hash),
            null,
            null,
            null
        )
        checkCursor.use {
            if (it.moveToFirst()) {
                return Pair(it.getLong(0), false)
            }
        }

        val values = ContentValues().apply {
            put(COL_SENDER, entry.sender)
            put(COL_BODY, entry.body)
            put(COL_TIMESTAMP, entry.timestamp)
            put(COL_EXTRACTED_AMOUNT, entry.extractedAmount)
            put(COL_EXTRACTED_PHONE, entry.extractedPhone)
            put(COL_PROVIDER, entry.provider)
            put(COL_SMS_HASH, hash)
            put(COL_STATUS, entry.dispatchStatus)
            put(COL_RETRY_COUNT, entry.retryCount)
        }
        val newId = db.insertWithOnConflict(TABLE_SMS_LOGS, null, values, SQLiteDatabase.CONFLICT_IGNORE)
        return if (newId != -1L) {
            Pair(newId, true)
        } else {
            Pair(0L, false)
        }
    }

    @Synchronized
    fun updateStatus(id: Long, status: String, incrementRetry: Boolean = false) {
        if (id <= 0L) return
        val db = writableDatabase
        if (incrementRetry) {
            db.execSQL(
                "UPDATE $TABLE_SMS_LOGS SET $COL_RETRY_COUNT = $COL_RETRY_COUNT + 1 WHERE $COL_ID = ?",
                arrayOf(id.toString())
            )
        }
        val values = ContentValues().apply {
            put(COL_STATUS, status)
        }
        db.update(TABLE_SMS_LOGS, values, "$COL_ID = ?", arrayOf(id.toString()))
    }

    fun getPendingQueue(): List<SmsLogEntry> {
        val list = mutableListOf<SmsLogEntry>()
        val db = readableDatabase
        val cursor = db.query(
            TABLE_SMS_LOGS,
            null,
            "$COL_STATUS = ?",
            arrayOf(SmsLogEntry.STATUS_QUEUED),
            null,
            null,
            "$COL_TIMESTAMP ASC"
        )
        cursor.use {
            while (it.moveToNext()) {
                list.add(readCursorRow(it))
            }
        }
        return list
    }

    fun getAllLogs(limit: Int = 100): List<SmsLogEntry> {
        val list = mutableListOf<SmsLogEntry>()
        val db = readableDatabase
        val cursor = db.query(
            TABLE_SMS_LOGS,
            null,
            null,
            null,
            null,
            null,
            "$COL_TIMESTAMP DESC",
            limit.toString()
        )
        cursor.use {
            while (it.moveToNext()) {
                list.add(readCursorRow(it))
            }
        }
        return list
    }

    private fun readCursorRow(c: Cursor): SmsLogEntry {
        val amountIdx = c.getColumnIndexOrThrow(COL_EXTRACTED_AMOUNT)
        val providerIdx = c.getColumnIndex(COL_PROVIDER)
        val hashIdx = c.getColumnIndex(COL_SMS_HASH)
        return SmsLogEntry(
            id = c.getLong(c.getColumnIndexOrThrow(COL_ID)),
            sender = c.getString(c.getColumnIndexOrThrow(COL_SENDER)) ?: "Unknown",
            body = c.getString(c.getColumnIndexOrThrow(COL_BODY)) ?: "",
            timestamp = c.getLong(c.getColumnIndexOrThrow(COL_TIMESTAMP)),
            extractedAmount = if (c.isNull(amountIdx)) null else c.getDouble(amountIdx),
            extractedPhone = c.getString(c.getColumnIndexOrThrow(COL_EXTRACTED_PHONE)),
            provider = if (providerIdx >= 0 && !c.isNull(providerIdx)) c.getString(providerIdx) else "Transfer",
            smsHash = if (hashIdx >= 0 && !c.isNull(hashIdx)) c.getString(hashIdx) else "",
            dispatchStatus = c.getString(c.getColumnIndexOrThrow(COL_STATUS)) ?: SmsLogEntry.STATUS_QUEUED,
            retryCount = c.getInt(c.getColumnIndexOrThrow(COL_RETRY_COUNT))
        )
    }

    fun getPendingCount(): Int {
        val db = readableDatabase
        val cursor = db.rawQuery(
            "SELECT COUNT(*) FROM $TABLE_SMS_LOGS WHERE $COL_STATUS = ?",
            arrayOf(SmsLogEntry.STATUS_QUEUED)
        )
        cursor.use {
            if (it.moveToFirst()) return it.getInt(0)
        }
        return 0
    }

    fun getDispatchedCount(): Int {
        val db = readableDatabase
        val cursor = db.rawQuery(
            "SELECT COUNT(*) FROM $TABLE_SMS_LOGS WHERE $COL_STATUS = ?",
            arrayOf(SmsLogEntry.STATUS_DISPATCHED)
        )
        cursor.use {
            if (it.moveToFirst()) return it.getInt(0)
        }
        return 0
    }

    fun getTotalCount(): Int {
        val db = readableDatabase
        val cursor = db.rawQuery("SELECT COUNT(*) FROM $TABLE_SMS_LOGS", null)
        cursor.use {
            if (it.moveToFirst()) return it.getInt(0)
        }
        return 0
    }

    @Synchronized
    fun purgeInvalidOrSpamLogs(): Int {
        val db = writableDatabase
        var deletedCount = 0
        try {
            val cursor = db.query(TABLE_SMS_LOGS, arrayOf(COL_ID, COL_SENDER, COL_BODY, COL_TIMESTAMP), null, null, null, null, null)
            val idsToDelete = mutableListOf<Long>()
            cursor.use {
                while (it.moveToNext()) {
                    val id = it.getLong(0)
                    val sender = it.getString(1) ?: ""
                    val body = it.getString(2) ?: ""
                    val ts = it.getLong(3)
                    if (FinancialSmsDetector.inspect(sender, body, ts) == null) {
                        idsToDelete.add(id)
                    }
                }
            }
            for (delId in idsToDelete) {
                db.delete(TABLE_SMS_LOGS, "$COL_ID = ?", arrayOf(delId.toString()))
                deletedCount++
            }
        } catch (_: Exception) {
        }
        return deletedCount
    }

    @Synchronized
    fun clearLogs() {
        val db = writableDatabase
        db.delete(TABLE_SMS_LOGS, null, null)
    }
}

