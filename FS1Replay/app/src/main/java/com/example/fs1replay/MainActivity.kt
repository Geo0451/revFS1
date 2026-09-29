package com.example.fs1replay

import android.Manifest
import android.bluetooth.*
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import java.util.UUID

val SERVICE_UUID: UUID = UUID.fromString("c2e6ffb0-e966-1000-8000-bef9c223df6a")
val CHAR_WRITE_UUID: UUID = UUID.fromString("c2e6ffb1-e966-1000-8000-bef9c223df6a")
val CHAR_NOTIFY_UUID: UUID = UUID.fromString("c2e6ffb2-e966-1000-8000-bef9c223df6a")
// AT-command write channel (plain ASCII "AT+..." commands). Same on every FS1.
val CHAR_AT_WRITE_UUID: UUID = UUID.fromString("c2e6ffb3-e966-1000-8000-bef9c223df6a")
val CHAR_AT_NOTIFY_UUID: UUID = UUID.fromString("c2e6ffb4-e966-1000-8000-bef9c223df6a")
val CHAR_STATUS_NOTIFY_UUID: UUID = UUID.fromString("c2e6ffb6-e966-1000-8000-bef9c223df6a")
val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

private const val WRITE_RESPONSE_TIMEOUT_MS = 5000L
private const val QUEUE_BUSY_RETRIES = 50
private const val BLOCK_ACK_TIMEOUT_MS = 3000L
private const val BLOCK_MAX_RETRIES = 5

// --- Hardcoded AT setup/close sequence, captured verbatim from a real transfer (at_pre.txt /
// at_post.txt). Confirmed identical in every capture taken so far, so it no longer needs to be
// supplied in the writes file - the app owns it and rebuilds it fresh every time.
// Three fields get substituted at send time by buildAtWrite() below:
//   AT+SUT:<unix time>   - replaced with the phone's current clock (must NOT be replayed frozen)
//   AT+STZ:<minutes>     - replaced with the phone's current timezone offset
//   AT+SUM:<n>           - replaced based on the detected upload type (see sumValueForType)
private val PRE_AT: List<String> = listOf(
    "AT+SPS:AND", "AT+GST?", "AT+BDQ:5711,6593", "AT+MTU?", "AT+MTU:244",
    "AT+GBS?", "AT+GBS?",
    "AT+STZ:330", "AT+SUT:1790430072", "AT+STZ:330", "AT+SUT:1790430072",
    "AT+SHM:12", "AT+SHM:12",
    "AT+CCS:1",
    "AT+CFD:M,1000", "AT+CFD:M,1000", "AT+CFD:M,1000",
    "AT+CCS:0",
    "AT+CFD:M,1000",
    "AT+CCS:0",
    "AT+CFD:M,1000",
    "AT+CCS:1",
    "AT+CFD:M,1000", "AT+CFD:M,1000",
    "AT+CCS:0", "AT+CCS:1", "AT+CCS:0",
    "AT+SUM:1",
    "AT+CCS:1"
)
private val POST_AT: List<String> = listOf("AT+CCS:0")

// AT+SUM's value has been seen to depend on the upload's message type, not on file size or
// block count: type 0xF4 (store-authored face, incl. the widget table) used AT+SUM:1 in every
// capture so far, type 0xF3 (custom-photo face, different internal format) used AT+SUM:20.
// UNCONFIRMED for any type byte other than these two - falls back to 1 and logs a warning.
private fun sumValueForType(typ: Int): Int = when (typ) {
    0xF4 -> 1
    0xF3 -> 20
    else -> 1
}

class MainActivity : AppCompatActivity() {

    private lateinit var logView: TextView
    private lateinit var macInput: EditText
    private lateinit var fileLabel: TextView
    private lateinit var connectButton: Button
    private lateinit var replayButton: Button
    private lateinit var pickFileButton: Button

    private var gatt: BluetoothGatt? = null
    private var writeChar: BluetoothGattCharacteristic? = null
    private var atChar: BluetoothGattCharacteristic? = null
    private var pendingWrite: CompletableDeferred<Boolean>? = null
    private var pendingBlockAck: CompletableDeferred<Int>? = null
    private val scope = CoroutineScope(Dispatchers.Main + SupervisorJob())

    // The file the user picked via the system file picker (works with any provider - Downloads,
    // Drive, a browser's download list, etc. - no more adb push / app-external-files-dir).
    private var pickedUri: Uri? = null

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        if (results.values.all { it }) log("Permissions granted.")
        else log("Permissions DENIED - connect will fail. Grant them in Settings > Apps.")
    }

    private val filePickerLauncher = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri ->
        if (uri == null) { log("File pick cancelled."); return@registerForActivityResult }
        try {
            contentResolver.takePersistableUriPermission(uri, android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
        } catch (e: SecurityException) { /* some providers don't support persistable perms - fine, we still have it for now */ }
        pickedUri = uri
        fileLabel.text = displayNameFor(uri) ?: uri.lastPathSegment ?: uri.toString()
        log("Picked file: ${fileLabel.text}")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        logView = findViewById(R.id.logView)
        macInput = findViewById(R.id.macInput)
        fileLabel = findViewById(R.id.fileLabel)
        connectButton = findViewById(R.id.connectButton)
        replayButton = findViewById(R.id.replayButton)
        pickFileButton = findViewById(R.id.pickFileButton)
        requestPermissions()
        connectButton.setOnClickListener { connect() }
        replayButton.setOnClickListener { replay() }
        pickFileButton.setOnClickListener { filePickerLauncher.launch(arrayOf("*/*")) }
    }

    private fun displayNameFor(uri: Uri): String? {
        return try {
            contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        } catch (e: Exception) { null }
    }

    private fun requestPermissions() {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 31) {
            perms += Manifest.permission.BLUETOOTH_SCAN
            perms += Manifest.permission.BLUETOOTH_CONNECT
        } else perms += Manifest.permission.ACCESS_FINE_LOCATION
        val need = perms.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (need.isNotEmpty()) permissionLauncher.launch(need.toTypedArray())
    }

    private fun log(msg: String) {
        runOnUiThread {
            logView.append("$msg\n")
            (logView.parent as? android.widget.ScrollView)?.post {
                (logView.parent as android.widget.ScrollView).fullScroll(android.view.View.FOCUS_DOWN)
            }
        }
    }

    private fun hasConnectPerm(): Boolean {
        if (Build.VERSION.SDK_INT < 31) return true
        return ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT) ==
                PackageManager.PERMISSION_GRANTED
    }

    private fun connect() {
        if (!hasConnectPerm()) { log("Missing BLUETOOTH_CONNECT permission."); requestPermissions(); return }
        val mac = macInput.text.toString().trim()
        val adapter = (getSystemService(BLUETOOTH_SERVICE) as BluetoothManager).adapter
        if (adapter == null || !adapter.isEnabled) { log("Bluetooth not available/enabled."); return }
        val device: BluetoothDevice = try { adapter.getRemoteDevice(mac) }
        catch (e: IllegalArgumentException) { log("Bad MAC address: $mac"); return }
        log("Connecting to $mac ...")
        gatt = device.connectGatt(this, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
    }

    private fun propsToString(p: Int): String {
        val out = mutableListOf<String>()
        if (p and BluetoothGattCharacteristic.PROPERTY_READ != 0) out += "READ"
        if (p and BluetoothGattCharacteristic.PROPERTY_WRITE != 0) out += "WRITE"
        if (p and BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE != 0) out += "WRITE_NR"
        if (p and BluetoothGattCharacteristic.PROPERTY_NOTIFY != 0) out += "NOTIFY"
        if (p and BluetoothGattCharacteristic.PROPERTY_INDICATE != 0) out += "INDICATE"
        return out.joinToString(",")
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (!hasConnectPerm()) return
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                log("Connected. Discovering services...")
                g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH)
                g.discoverServices()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                log("Disconnected (status=$status).")
                runOnUiThread { replayButton.isEnabled = false }
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (!hasConnectPerm()) return
            log("=== Full GATT table ===")
            for (s in g.services) {
                log("service ${s.uuid}")
                for (c in s.characteristics) log("   char ${c.uuid}  props=[${propsToString(c.properties)}]")
            }
            log("=== end GATT table ===")

            val service = g.getService(SERVICE_UUID)
            if (service == null) { log("Service $SERVICE_UUID not found - see table above."); return }
            writeChar = service.getCharacteristic(CHAR_WRITE_UUID)
            if (writeChar == null) { log("Write characteristic $CHAR_WRITE_UUID not found in service."); return }

            atChar = service.getCharacteristic(CHAR_AT_WRITE_UUID)
            if (atChar == null) {
                log("WARNING: AT characteristic $CHAR_AT_WRITE_UUID not found in service - " +
                        "AT commands would go to the data channel and the transfer will fail.")
            } else log("AT characteristic found: $CHAR_AT_WRITE_UUID")

            log("Enabling notifications...")
            scope.launch {
                val notifyUuids = listOf(CHAR_NOTIFY_UUID, CHAR_AT_NOTIFY_UUID, CHAR_STATUS_NOTIFY_UUID)
                for (uuid in notifyUuids) {
                    var found: BluetoothGattCharacteristic? = null
                    for (s in g.services) { val c = s.getCharacteristic(uuid); if (c != null) { found = c; break } }
                    if (found == null) { log("  notify char $uuid not present, skipping"); continue }
                    g.setCharacteristicNotification(found, true)
                    val cccd = found.getDescriptor(CCCD_UUID)
                    if (cccd != null) {
                        if (Build.VERSION.SDK_INT >= 33) g.writeDescriptor(cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                        else {
                            @Suppress("DEPRECATION") cccd.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                            @Suppress("DEPRECATION") g.writeDescriptor(cccd)
                        }
                        log("  subscribed to $uuid")
                    } else log("  $uuid has no CCCD descriptor, cannot subscribe")
                    delay(200)
                }
                log("Ready. Pick a file and tap Replay.")
                runOnUiThread { replayButton.isEnabled = true }
            }
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic, value: ByteArray) {
            val tag = when (characteristic.uuid) {
                CHAR_NOTIFY_UUID -> "data"
                CHAR_AT_NOTIFY_UUID -> "AT-reply"
                CHAR_STATUS_NOTIFY_UUID -> "status"
                else -> characteristic.uuid.toString()
            }
            val asText = value.toString(Charsets.US_ASCII).filter { it.code in 32..126 }
            log("<- [$tag] ${value.joinToString("") { "%02x".format(it) }}" + if (asText.isNotEmpty()) "  (\"$asText\")" else "")

            if (characteristic.uuid == CHAR_NOTIFY_UUID && value.size >= 18 &&
                value[0] == 0xFF.toByte() && value[1] == 0xFF.toByte() &&
                value[2] == 'B'.code.toByte() && value[3] == 'G'.code.toByte()) {
                val blockIndex = ((value[16].toInt() and 0xFF) shl 8) or (value[17].toInt() and 0xFF)
                pendingBlockAck?.complete(blockIndex)
            }
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, characteristic: BluetoothGattCharacteristic, status: Int) {
            pendingWrite?.complete(status == BluetoothGatt.GATT_SUCCESS)
        }
    }

    private enum class WriteOutcome { SUCCESS, FAILED, TIMEOUT }

    private fun isHeaderWrite(data: ByteArray) =
        data.size == 20 && data[0] == 0xFF.toByte() && data[1] == 0xFF.toByte() &&
                data[2] == 'B'.code.toByte() && data[3] == 'G'.code.toByte()

    private fun isAtCommand(data: ByteArray) =
        data.size >= 3 && data[0] == 'A'.code.toByte() && data[1] == 'T'.code.toByte() && data[2] == '+'.code.toByte()

    private data class Block(val header: ByteArray, val frames: List<ByteArray>, val index: Int)

    // Builds one AT-command write from a template string, live-patching the two fields that must
    // never be replayed frozen, and the AT+SUM value once we know which upload type this is.
    private fun buildAtWrite(template: String, sumValue: Int): ByteArray {
        val nowSec = System.currentTimeMillis() / 1000
        val tzOffsetMin = java.util.TimeZone.getDefault().getOffset(System.currentTimeMillis()) / 60000
        val text = when {
            template.startsWith("AT+SUT:") -> "AT+SUT:$nowSec"
            template.startsWith("AT+STZ:") -> "AT+STZ:$tzOffsetMin"
            template.startsWith("AT+SUM:") -> "AT+SUM:$sumValue"
            else -> template
        }
        // Every captured AT+ write we've seen ends with a single trailing 0x00 - keep that exact
        // shape; a previous bug (extra null on AT+SUT) broke the transfer, see git history.
        return text.toByteArray(Charsets.US_ASCII) + byteArrayOf(0)
    }

    private fun hexToBytes(hex: String): ByteArray {
        val clean = hex.trim()
        val out = ByteArray(clean.length / 2)
        for (idx in out.indices) out[idx] = ((Character.digit(clean[idx * 2], 16) shl 4) + Character.digit(clean[idx * 2 + 1], 16)).toByte()
        return out
    }

    private fun replay() {
        val g = gatt ?: return
        val ch = writeChar ?: return
        if (!hasConnectPerm()) { log("Missing permission."); return }
        val uri = pickedUri
        if (uri == null) { log("No file picked - tap \"Choose file...\" first."); return }

        val rawLines: List<String> = try {
            contentResolver.openInputStream(uri)?.bufferedReader()?.readLines() ?: emptyList()
        } catch (e: Exception) { log("Failed to read file: ${e.message}"); return }
        if (rawLines.isEmpty()) { log("File is empty or unreadable."); return }

        // Accept "<n>\t<hex>" (capture/export format) or bare hex, one per line. Anything that
        // isn't a 20-byte BG header or a continuation frame is dropped - in particular, any AT+
        // lines already in the file are ignored, since the app now supplies its own AT wrapping.
        val allWrites = rawLines.mapNotNull { line ->
            val t = line.trim(); if (t.isEmpty()) return@mapNotNull null
            val hex = if (t.contains('\t')) t.substringAfter('\t') else t
            if (hex.isEmpty() || hex.length % 2 != 0) return@mapNotNull null
            try { hexToBytes(hex) } catch (e: Exception) { null }
        }
        val transferWrites = mutableListOf<ByteArray>()
        var i = 0
        while (i < allWrites.size) {
            val w = allWrites[i]
            if (isHeaderWrite(w)) {
                transferWrites += w; i++
                while (i < allWrites.size && !isHeaderWrite(allWrites[i]) && !isAtCommand(allWrites[i])) {
                    transferWrites += allWrites[i]; i++
                }
            } else i++
        }
        if (transferWrites.isEmpty()) { log("No BG-protocol writes found in this file."); return }

        val firstHeader = transferWrites.first { isHeaderWrite(it) }
        val typ = firstHeader[7].toInt() and 0xFF
        val sumValue = sumValueForType(typ)
        log("Detected upload type 0x%02X -> AT+SUM:%d".format(typ, sumValue) +
                if (typ != 0xF3 && typ != 0xF4) " (unrecognised type, using default - verify on hardware)" else "")

        val setupWrites = PRE_AT.map { buildAtWrite(it, sumValue) }
        val trailingWrites = POST_AT.map { buildAtWrite(it, sumValue) }
        log("Built ${setupWrites.size} setup + ${trailingWrites.size} closing AT writes " +
                "(hardcoded sequence, live time/timezone, detected SUM).")

        val blocks = mutableListOf<Block>()
        i = 0
        while (i < transferWrites.size) {
            val header = transferWrites[i]; i++
            val frames = mutableListOf<ByteArray>()
            while (i < transferWrites.size && !isHeaderWrite(transferWrites[i])) { frames += transferWrites[i]; i++ }
            val idx = ((header[16].toInt() and 0xFF) shl 8) or (header[17].toInt() and 0xFF)
            blocks += Block(header, frames, idx)
        }
        log("Loaded ${blocks.size} file-transfer blocks from the picked file.")

        log("Starting replay...")
        runOnUiThread { replayButton.isEnabled = false }

        scope.launch {
            var ok = true

            suspend fun sendSingle(data: ByteArray, label: String): Boolean {
                val target = if (isAtCommand(data) && atChar != null) atChar!! else ch
                val outcome = writeOne(g, target, data, noResponse = false)
                when (outcome) {
                    WriteOutcome.FAILED -> { log("$label FAILED."); return false }
                    WriteOutcome.TIMEOUT -> log("$label: no GATT ack within ${WRITE_RESPONSE_TIMEOUT_MS}ms - continuing.")
                    WriteOutcome.SUCCESS -> {}
                }
                if (isAtCommand(data)) log("-> AT: ${String(data).trimEnd('\u0000')}")
                delay(10)
                return true
            }

            for (data in setupWrites) { if (!sendSingle(data, "setup write")) { ok = false; break } }

            if (ok) {
                for ((bi, block) in blocks.withIndex()) {
                    var attempt = 0
                    var acked = false
                    while (attempt < BLOCK_MAX_RETRIES && !acked) {
                        attempt++
                        val deferred = CompletableDeferred<Int>()
                        pendingBlockAck = deferred

                        val headerOutcome = writeOne(g, ch, block.header, noResponse = false)
                        if (headerOutcome == WriteOutcome.FAILED) { log("Block ${block.index} header FAILED (attempt $attempt)."); continue }

                        for (frame in block.frames) {
                            val frameOutcome = writeOne(g, ch, frame, noResponse = true)
                            if (frameOutcome == WriteOutcome.FAILED) { log("Block ${block.index} frame FAILED (attempt $attempt).") }
                        }

                        val gotIndex = withTimeoutOrNull(BLOCK_ACK_TIMEOUT_MS) { deferred.await() }
                        pendingBlockAck = null
                        if (gotIndex == block.index) {
                            acked = true
                        } else if (gotIndex != null) {
                            log("Block ${block.index}: got ack for block $gotIndex instead, retrying (attempt $attempt).")
                        } else {
                            log("Block ${block.index}: no ack within ${BLOCK_ACK_TIMEOUT_MS}ms, retrying (attempt $attempt).")
                        }
                    }
                    if (!acked) { log("Block ${block.index} FAILED after $BLOCK_MAX_RETRIES attempts. Stopping."); ok = false; break }
                    if ((bi + 1) % 10 == 0) log("  block ${bi + 1}/${blocks.size}")
                }
            }

            if (ok) for (data in trailingWrites) { if (!sendSingle(data, "trailing write")) { ok = false; break } }

            if (ok) log("Done. ${blocks.size} blocks confirmed by device ack, plus setup/trailing writes.")
            runOnUiThread { replayButton.isEnabled = true }
        }
    }

    private suspend fun writeOne(g: BluetoothGatt, ch: BluetoothGattCharacteristic, data: ByteArray, noResponse: Boolean): WriteOutcome {
        if (noResponse) {
            repeat(QUEUE_BUSY_RETRIES) {
                val queued = if (Build.VERSION.SDK_INT >= 33) {
                    g.writeCharacteristic(ch, data, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE) == BluetoothStatusCodes.SUCCESS
                } else {
                    @Suppress("DEPRECATION") ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    @Suppress("DEPRECATION") ch.value = data
                    @Suppress("DEPRECATION") g.writeCharacteristic(ch)
                }
                if (queued) return WriteOutcome.SUCCESS
                delay(3)
            }
            return WriteOutcome.FAILED
        }
        repeat(QUEUE_BUSY_RETRIES) {
            val deferred = CompletableDeferred<Boolean>()
            pendingWrite = deferred
            val queued = if (Build.VERSION.SDK_INT >= 33) {
                g.writeCharacteristic(ch, data, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS
            } else {
                @Suppress("DEPRECATION") ch.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                @Suppress("DEPRECATION") ch.value = data
                @Suppress("DEPRECATION") g.writeCharacteristic(ch)
            }
            if (!queued) { pendingWrite = null; delay(15); return@repeat }
            val result = withTimeoutOrNull(WRITE_RESPONSE_TIMEOUT_MS) { deferred.await() }
            return when (result) { true -> WriteOutcome.SUCCESS; false -> WriteOutcome.FAILED; null -> WriteOutcome.TIMEOUT }
        }
        return WriteOutcome.FAILED
    }
}