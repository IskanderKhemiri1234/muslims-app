package com.iskander.aitrade

import android.app.Activity
import android.app.AlertDialog
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val bg = Color.rgb(7, 28, 23)
    private val gold = Color.rgb(216, 182, 106)
    private val muted = Color.rgb(169, 189, 181)
    private val green = Color.rgb(120, 214, 161)
    private val red = Color.rgb(255, 146, 136)
    private val worker = Executors.newSingleThreadExecutor()
    private val ui = Handler(Looper.getMainLooper())
    private var baseUrl = ""
    private var token = ""
    private lateinit var root: LinearLayout
    private lateinit var notice: TextView
    private lateinit var urlField: EditText
    private lateinit var tokenField: EditText
    private lateinit var cycle: Button
    private lateinit var portfolio: LinearLayout
    private lateinit var marketList: LinearLayout
    private lateinit var positionList: LinearLayout
    private lateinit var auditList: LinearLayout
    private lateinit var tradeList: LinearLayout
    private lateinit var testnetState: TextView
    private lateinit var liveState: TextView
    private lateinit var liveOrdersText: TextView
    private lateinit var liveSymbol: EditText
    private lateinit var liveAmount: EditText
    private lateinit var livePrice: EditText
    private lateinit var liveCancelId: EditText
    private lateinit var liveSide: Spinner
    private lateinit var liveType: Spinner
    private lateinit var tradeSymbol: EditText
    private lateinit var tradeQuantity: EditText
    private lateinit var tradeEntry: EditText
    private lateinit var tradeStop: EditText
    private lateinit var tradeTarget: EditText

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        baseUrl = getPreferences(MODE_PRIVATE).getString("dashboard_url", "") ?: ""
        login()
    }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun panel() = GradientDrawable().apply {
        setColor(Color.rgb(13, 42, 34)); cornerRadius = dp(16).toFloat()
        setStroke(dp(1), Color.rgb(40, 88, 68))
    }
    private fun label(s: String, size: Float = 14f, color: Int = Color.WHITE) =
        TextView(this).apply { text = s; textSize = size; setTextColor(color) }
    private fun layout() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; setPadding(dp(18), dp(22), dp(18), dp(24))
        setBackgroundColor(bg)
    }
    private fun params() = LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(10) }
    private fun makeButton(s: String, fn: () -> Unit) = Button(this).apply {
        text = s; isAllCaps = false; setTextColor(bg)
        background = GradientDrawable().apply { setColor(gold); cornerRadius = dp(12).toFloat() }
        setOnClickListener { fn() }
    }

    private fun login() {
        root = layout().apply { gravity = Gravity.CENTER_VERTICAL }
        root.addView(label("ISKANDER AI TRADE", 27f, gold))
        root.addView(label("Paper · Testnet · Manual Spot Live command center", 14f, muted).apply {
            setPadding(0, dp(8), 0, dp(18))
        })
        root.addView(label("MANUAL LIVE  •  DISARMED BY DEFAULT", 12f, green))
        urlField = EditText(this).apply {
            hint = "https://your-paper-api.example"; setSingleLine(true); setText(baseUrl)
            setTextColor(Color.WHITE); setHintTextColor(muted); background = panel()
        }
        tokenField = EditText(this).apply {
            hint = "Dashboard access token"; setSingleLine(true); inputType = 129
            setTextColor(Color.WHITE); setHintTextColor(muted); background = panel()
        }
        root.addView(urlField, params()); root.addView(tokenField, params())
        root.addView(makeButton("Connect securely") { connect() }, params())
        notice = label("HTTPS only. The token is kept in memory and is not saved.", 12f, muted)
        root.addView(notice, params())
        setContentView(root)
    }

    private fun connect() {
        val address = urlField.text.toString().trim().trimEnd('/')
        val secret = tokenField.text.toString().trim()
        if (!address.startsWith("https://") || secret.isBlank()) {
            notice.text = "Enter a valid HTTPS address and dashboard token."
            notice.setTextColor(red); return
        }
        baseUrl = address; token = secret
        notice.text = "Authenticating…"; notice.setTextColor(gold)
        worker.execute {
            try {
                api("/health", auth = false)
                val s = api("/api/v1/status"); val m = api("/api/v1/markets")
                ui.post {
                    getPreferences(MODE_PRIVATE).edit().putString("dashboard_url", baseUrl).apply()
                    dashboard(s, m)
                }
            } catch (e: Exception) {
                ui.post { notice.text = "Connection failed: " + e.message; notice.setTextColor(red) }
            }
        }
    }

    private fun api(path: String, method: String = "GET", auth: Boolean = true,
                    payload: JSONObject? = null): JSONObject {
        val c = URL(baseUrl + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method; c.connectTimeout = 12000; c.readTimeout = 15000
            c.setRequestProperty("Accept", "application/json")
            if (auth) c.setRequestProperty("Authorization", "Bearer " + token)
            if (method == "POST") {
                c.doOutput = true
                if (payload != null) {
                    c.setRequestProperty("Content-Type", "application/json")
                    c.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
                }
            }
            val code = c.responseCode
            val stream = if (code in 200..299) c.inputStream else c.errorStream
            val body = stream?.bufferedReader()?.use { it.readText() } ?: ""
            if (code !in 200..299) {
                val detail = try { JSONObject(body).optString("detail", "HTTP " + code) }
                    catch (_: Exception) { "HTTP " + code }
                throw IllegalStateException(detail)
            }
            return JSONObject(body)
        } finally { c.disconnect() }
    }

    private fun section(title: String) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL; background = panel()
        setPadding(dp(14), dp(14), dp(14), dp(14))
        addView(label(title, 17f, gold))
    }
    private fun row(target: LinearLayout, left: String, right: String, color: Int = Color.WHITE) {
        val line = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(7), 0, dp(7))
        }
        line.addView(label(left, 13f, muted), LinearLayout.LayoutParams(0, -2, 1f))
        line.addView(label(right, 13f, color))
        target.addView(line)
    }
    private fun number(value: Double, digits: Int) =
        String.format(Locale.US, "%." + digits + "f", value)

    private fun field(obj: JSONObject, key: String): String =
        if (!obj.has(key) || obj.isNull(key)) "—" else obj.opt(key)?.toString() ?: "—"

    private fun dashboard(s: JSONObject, m: JSONObject) {
        root = layout()
        val scroll = ScrollView(this)
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        body.addView(label("ISKANDER AI TRADE", 25f, gold))
        body.addView(label("Connected native control center", 13f, muted))
        notice = label("Secure connection established.", 13f, green)
        notice.background = panel(); notice.setPadding(dp(12), dp(12), dp(12), dp(12))
        body.addView(notice, params())
        portfolio = section("Portfolio"); body.addView(portfolio, params())
        marketList = section("Market watch"); body.addView(marketList, params())
        positionList = section("Open positions"); body.addView(positionList, params())
        auditList = section("Accounting integrity"); body.addView(auditList, params())
        tradeList = section("Recent paper trades"); body.addView(tradeList, params())
        body.addView(livePanel(), params())
        val controls = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        controls.addView(makeButton("Refresh") { refresh() }, LinearLayout.LayoutParams(0, dp(50), 1f))
        cycle = makeButton("Run paper cycle") { runCycle() }
        controls.addView(cycle, LinearLayout.LayoutParams(0, dp(50), 1f))
        body.addView(controls, params())
        body.addView(makeButton("Disconnect") { token = ""; login() }, params())
        body.addView(label("Paper · Testnet · Manual Spot Live (separately armed)", 11f, muted), params())
        scroll.addView(body); root.addView(scroll); setContentView(root)
        render(s, m)
        refreshLiveState()
    }

    private fun render(s: JSONObject, m: JSONObject) {
        portfolio.removeAllViews(); portfolio.addView(label("Portfolio", 17f, gold))
        row(portfolio, "Virtual capital", number(s.optDouble("virtual_capital_usdt", 70.0), 2) + " USDT")
        row(portfolio, "Available cash", field(s, "cash_usdt") + " USDT")
        row(portfolio, "Daily net P/L", field(s, "daily_pnl_usdt") + " USDT")
        row(portfolio, "Realized net P/L", field(s, "total_realized_pnl_usdt") + " USDT")
        row(portfolio, "Open positions", (s.optJSONObject("positions")?.length() ?: 0).toString())
        val snapshot = s.optJSONObject("accounting") ?: JSONObject()
        val marksComplete = snapshot.optBoolean("equity_includes_unrealized", false)
        row(portfolio, "Market value", if (marksComplete) field(snapshot, "market_value") + " USDT" else "Unavailable")
        row(portfolio, "Unrealized net P/L", if (marksComplete) field(snapshot, "unrealized_pnl") + " USDT" else "Unavailable")
        row(portfolio, "Portfolio equity", if (marksComplete) field(snapshot, "equity") + " USDT" else "Incomplete")

        marketList.removeAllViews(); marketList.addView(label("Market watch", 17f, gold))
        val ms = m.optJSONArray("markets") ?: JSONArray()
        for (i in 0 until ms.length()) {
            val item = ms.optJSONObject(i) ?: continue
            row(marketList, field(item, "symbol"), if (item.has("error")) field(item, "error") else field(item, "price"))
        }

        positionList.removeAllViews(); positionList.addView(label("Open positions", 17f, gold))
        val ps = s.optJSONObject("positions") ?: JSONObject()
        val keys = ps.keys(); var found = false
        while (keys.hasNext()) {
            found = true; val symbol = keys.next(); val p = ps.optJSONObject(symbol) ?: JSONObject()
            row(positionList, symbol, "Qty " + field(p, "quantity") + " @ " + field(p, "entry_price"))
        }
        if (!found) positionList.addView(label("No open positions.", 12f, muted))

        auditList.removeAllViews(); auditList.addView(label("Accounting integrity", 17f, gold))
        val a = s.optJSONObject("accounting") ?: JSONObject()
        val result = a.optString("status", "UNAVAILABLE")
        row(auditList, "Reconciliation", result, if (result == "PASS") green else if (result == "FAIL") red else gold)
        row(auditList, "Cash / expected", field(a, "cash") + " / " + field(a, "expected_cash"))
        row(auditList, "Fees paid", field(a, "fees_paid"))
        row(auditList, "FIFO realized P/L", field(a, "fifo_realized_pnl"))
        val issues = a.optJSONArray("issues") ?: JSONArray()
        for (i in 0 until issues.length()) auditList.addView(label("• " + issues.optString(i), 12f, red))
        if (issues.length() == 0) {
            val summary = when (result) {
                "PASS" -> "All available accounting checks passed."
                "INCOMPLETE" -> "Accounting is incomplete: one or more required market marks are unavailable."
                "FAIL" -> "Accounting failed. Review the server ledger before another cycle."
                else -> "Accounting report unavailable. Do not assume balances are reconciled."
            }
            auditList.addView(label(summary, 12f, if (result == "FAIL") red else muted))
        }

        tradeList.removeAllViews(); tradeList.addView(label("Recent paper trades", 17f, gold))
        val history = s.optJSONArray("trade_history") ?: JSONArray()
        if (history.length() == 0) tradeList.addView(label("No trades yet.", 12f, muted))
        for (i in (history.length() - 1 downTo maxOf(0, history.length() - 10))) {
            val t = history.optJSONObject(i) ?: continue
            row(tradeList, field(t, "symbol") + " · " + field(t, "side"), field(t, "price"))
        }
    }

    private fun decimalField(hintText: String) = EditText(this).apply {
        hint = hintText
        setSingleLine(true)
        inputType = 8194
        setTextColor(Color.WHITE)
        setHintTextColor(muted)
        background = panel()
    }

    private fun testnetPanel(): LinearLayout {
        val box = section("Binance Spot Testnet · Sandbox only")
        box.addView(label("Configure a pair, entry, quantity, stop-loss and take-profit. Saving does not arm the trade.", 12f, muted), params())
        tradeSymbol = decimalField("Pair, e.g. BTC/USDT").apply { setText("BTC/USDT"); inputType = 1 }
        tradeQuantity = decimalField("Quantity")
        tradeEntry = decimalField("Entry price")
        tradeStop = decimalField("Stop-loss price")
        tradeTarget = decimalField("Take-profit price")
        for (field in listOf(tradeSymbol, tradeQuantity, tradeEntry, tradeStop, tradeTarget)) {
            box.addView(field, params())
        }
        box.addView(makeButton("Save Testnet trade settings") {
            val payload = JSONObject().apply {
                put("symbol", tradeSymbol.text.toString().trim())
                put("quantity", tradeQuantity.text.toString().trim())
                put("entry_price", tradeEntry.text.toString().trim())
                put("stop_loss_price", tradeStop.text.toString().trim())
                put("take_profit_price", tradeTarget.text.toString().trim())
            }
            performTestnet("/api/v1/testnet/trades", payload)
        }, params())
        box.addView(makeButton("Refresh Testnet balances and journal") {
            refreshTestnetState()
        }, params())
        box.addView(makeButton("Run Testnet monitor cycle") {
            performTestnet("/api/v1/testnet/cycle")
        }, params())
        box.addView(makeButton("Arm first saved Testnet trade") {
            worker.execute {
                try {
                    val state = api("/api/v1/testnet/trades")
                    val trades = state.optJSONArray("trades") ?: JSONArray()
                    if (trades.length() == 0) throw IllegalStateException("Save a trade first.")
                    val trade = trades.optJSONObject(0) ?: JSONObject()
                    val id = trade.optString("trade_id")
                    if (id.isBlank()) throw IllegalStateException("Trade identifier is missing.")
                    api("/api/v1/testnet/trades/" + id + "/enable", "POST")
                    val refreshed = api("/api/v1/testnet/trades")
                    ui.post { renderTestnetState(refreshed) }
                } catch (e: Exception) {
                    ui.post { testnetState.text = "Arm failed: " + e.message; testnetState.setTextColor(red) }
                }
            }
        }, params())
        box.addView(makeButton("EMERGENCY STOP") {
            performTestnet("/api/v1/testnet/emergency-stop")
        }, params())
        box.addView(makeButton("Release emergency-stop latch") {
            performTestnet("/api/v1/testnet/emergency-stop/release")
        }, params())
        testnetState = label("Testnet journal not loaded. Emergency stop defaults to ACTIVE.", 12f, gold)
        box.addView(testnetState, params())
        return box
    }


    private fun livePanel(): LinearLayout {
        val box = section("Binance Spot · Manual Live")
        box.addView(label("REAL MONEY · MANUAL ORDERS ONLY · NO AUTOMATIC STRATEGY", 12f, red), params())
        box.addView(label("Live keys stay on the server. Emergency stop defaults ON. Orders are sent only after explicit review and confirmation.", 12f, muted), params())
        liveSymbol = decimalField("Pair, e.g. BTC/USDT").apply { setText("BTC/USDT"); inputType = 1 }
        liveAmount = decimalField("Base quantity")
        livePrice = decimalField("Limit price (required for LIMIT)")
        liveCancelId = decimalField("Client order ID to cancel")
        liveSide = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, listOf("BUY", "SELL"))
        }
        liveType = Spinner(this).apply {
            adapter = ArrayAdapter(this@MainActivity, android.R.layout.simple_spinner_dropdown_item, listOf("LIMIT", "MARKET"))
        }
        box.addView(liveSymbol, params())
        box.addView(liveSide, params())
        box.addView(liveType, params())
        box.addView(liveAmount, params())
        box.addView(livePrice, params())
        box.addView(liveCancelId, params())
        box.addView(makeButton("Refresh Live orders and fills") { refreshLiveState() }, params())
        box.addView(makeButton("Arm manual Live") { liveAction("/api/v1/live/arm", "Manual Live armed. No order has been sent.") }, params())
        box.addView(makeButton("Disarm manual Live") { liveAction("/api/v1/live/disarm", "Manual Live disarmed.") }, params())
        box.addView(makeButton("Release Live emergency stop") {
            AlertDialog.Builder(this).setTitle("Release emergency stop?")
                .setMessage("This only releases the stop latch. Manual trading remains disarmed.")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Release") { _, _ -> liveAction("/api/v1/live/emergency-stop/release", "Live stop released; still disarmed.") }
                .show()
        }, params())
        box.addView(makeButton("EMERGENCY STOP") {
            AlertDialog.Builder(this).setTitle("EMERGENCY STOP")
                .setMessage("Disarm manual Live and block new real-money orders immediately?")
                .setNegativeButton("Cancel", null)
                .setPositiveButton("STOP") { _, _ -> liveAction("/api/v1/live/emergency-stop", "Live emergency stop is active.") }
                .show()
        }, params())
        box.addView(makeButton("Review and submit REAL order") { reviewLiveOrder() }, params())
        box.addView(makeButton("Cancel selected Live order") {
            val id = liveCancelId.text.toString().trim()
            if (id.isBlank()) {
                liveState.text = "Enter the client order ID first."
                liveState.setTextColor(red)
            } else {
                AlertDialog.Builder(this).setTitle("Cancel Live order?")
                    .setMessage("Cancel " + id + " and reconcile any partial fills?")
                    .setNegativeButton("Cancel", null)
                    .setPositiveButton("Confirm cancellation") { _, _ ->
                        liveAction("/api/v1/live/orders/" + id + "/cancel", "Cancel request completed; verify final status.")
                    }.show()
            }
        }, params())
        box.addView(makeButton("Reconcile Live orders") { liveAction("/api/v1/live/reconcile", "Reconciliation request completed; verify journal.") }, params())
        liveState = label("Live state not loaded. Emergency stop is expected to be ACTIVE.", 12f, gold)
        box.addView(liveState, params())
        liveOrdersText = label("No Live order journal loaded.", 12f, muted)
        box.addView(liveOrdersText, params())
        return box
    }

    private fun liveAction(path: String, success: String) {
        worker.execute {
            try {
                api(path, "POST")
                val state = api("/api/v1/live/orders")
                ui.post { renderLiveState(state); notice.text = success; notice.setTextColor(green) }
            } catch (e: Exception) {
                ui.post { liveState.text = "Live control failed: " + e.message; liveState.setTextColor(red) }
            }
        }
    }

    private fun refreshLiveState() {
        worker.execute {
            try {
                val state = api("/api/v1/live/orders")
                ui.post { renderLiveState(state) }
            } catch (e: Exception) {
                ui.post { liveState.text = "Live refresh failed: " + e.message; liveState.setTextColor(red) }
            }
        }
    }

    private fun renderLiveState(state: JSONObject) {
        val stopped = state.optBoolean("emergency_stop", true)
        val armed = state.optBoolean("manual_armed", false)
        liveState.text = "Spot Live · Emergency stop: " + (if (stopped) "ACTIVE" else "released") +
            " · Manual armed: " + armed + " · Daily realized P/L: " +
            state.optString("daily_realized_pnl_quote", "—")
        liveState.setTextColor(if (stopped) gold else if (armed) red else green)
        val orders = state.optJSONArray("orders") ?: JSONArray()
        val lines = (0 until orders.length()).mapNotNull { i ->
            orders.optJSONObject(i)?.let { o ->
                o.optString("symbol") + " · " + o.optString("side") + " " +
                    o.optString("order_type") + " · " + o.optString("filled") +
                    "/" + o.optString("amount") + " · " + o.optString("status") +
                    " · " + o.optString("client_order_id")
            }
        }
        liveOrdersText.text = if (lines.isEmpty()) "No Live orders recorded." else lines.joinToString("\n")
    }

    private fun reviewLiveOrder() {
        val symbol = liveSymbol.text.toString().trim()
        val amount = liveAmount.text.toString().trim()
        val price = livePrice.text.toString().trim()
        val side = if (liveSide.selectedItemPosition == 0) "buy" else "sell"
        val type = if (liveType.selectedItemPosition == 0) "limit" else "market"
        if (symbol.isBlank() || amount.isBlank() || (type == "limit" && price.isBlank())) {
            liveState.text = "Enter pair, quantity and limit price when using LIMIT."
            liveState.setTextColor(red); return
        }
        val summary = "REAL BINANCE SPOT ORDER\n" + side.uppercase(Locale.US) + " " +
            type.uppercase(Locale.US) + "\n" + symbol + " · quantity " + amount +
            if (type == "limit") " · limit " + price else " · market price"
        AlertDialog.Builder(this)
            .setTitle("Confirm real-money order")
            .setMessage(summary + "\n\nThis order can execute immediately and use real funds.")
            .setNegativeButton("Cancel", null)
            .setPositiveButton("Submit REAL order") { _, _ ->
                val requestId = UUID.randomUUID().toString()
                val payload = JSONObject().apply {
                    put("request_id", requestId)
                    put("symbol", symbol)
                    put("side", side)
                    put("order_type", type)
                    put("amount", amount)
                    if (type == "limit") put("price", price) else put("price", JSONObject.NULL)
                }
                worker.execute {
                    try {
                        api("/api/v1/live/orders", "POST", payload = payload)
                        val state = api("/api/v1/live/orders")
                        ui.post {
                            renderLiveState(state)
                            notice.text = "Order request recorded. Verify exchange order and fills."
                            notice.setTextColor(green)
                        }
                    } catch (e: Exception) {
                        ui.post {
                            liveState.text = "Order result uncertain or rejected: " + e.message +
                                ". Reconcile before any retry."
                            liveState.setTextColor(red)
                        }
                    }
                }
            }.show()
    }

    private fun refreshTestnetState() {
        worker.execute {
            try {
                val state = api("/api/v1/testnet/trades")
                ui.post { renderTestnetState(state) }
            } catch (e: Exception) {
                ui.post { testnetState.text = "Testnet refresh failed: " + e.message; testnetState.setTextColor(red) }
            }
        }
    }

    private fun renderTestnetState(state: JSONObject) {
        val stopped = state.optBoolean("emergency_stop", true)
        val trades = state.optJSONArray("trades")?.length() ?: 0
        val orders = state.optJSONArray("orders")?.length() ?: 0
        val positions = state.optJSONArray("positions")?.length() ?: 0
        val balances = state.optJSONArray("balances") ?: JSONArray()
        val balanceText = (0 until balances.length()).mapNotNull { i ->
            balances.optJSONObject(i)?.let { b ->
                b.optString("asset") + " free " + b.optString("free", "—") +
                    " / total " + b.optString("total", "—")
            }
        }.joinToString(" · ")
        testnetState.text = "Testnet only · Emergency stop: " +
            (if (stopped) "ACTIVE" else "released") + " · Trades: " + trades +
            " · Orders: " + orders + " · Positions: " + positions +
            if (balanceText.isBlank()) " · Balances not synced yet" else " · " + balanceText
        testnetState.setTextColor(if (stopped) gold else green)
    }

    private fun performTestnet(path: String, payload: JSONObject? = null) {
        notice.text = "Sending authenticated Testnet control request…"
        worker.execute {
            try {
                val result = api(path, "POST", payload = payload)
                val state = api("/api/v1/testnet/trades")
                ui.post {
                    val halted = state.optBoolean("emergency_stop", true)
                    val orderCount = state.optJSONArray("orders")?.length() ?: 0
                    renderTestnetState(state)
                    notice.text = "Testnet response: " + result.optString("status", "saved") +
                        " · No real-money orders are enabled."
                    notice.setTextColor(if (halted) gold else green)
                }
            } catch (e: Exception) {
                ui.post { notice.text = "Testnet request failed: " + e.message; notice.setTextColor(red) }
            }
        }
    }

    private fun refresh() = perform("Refreshing account…", false)
    private fun runCycle() = perform("Running one paper-only cycle…", true)
    private fun perform(message: String, runCycle: Boolean) {
        notice.text = message
        if (runCycle) cycle.isEnabled = false
        worker.execute {
            try {
                if (runCycle) api("/api/v1/paper/cycle", "POST")
                val s = api("/api/v1/status"); val m = api("/api/v1/markets")
                ui.post {
                    render(s, m); notice.text = if (runCycle) "Paper cycle complete. No live order sent." else "Dashboard refreshed."
                    notice.setTextColor(green); cycle.isEnabled = true
                }
            } catch (e: Exception) {
                ui.post { notice.text = "Request failed: " + e.message; notice.setTextColor(red); if (runCycle) cycle.isEnabled = true }
            }
        }
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }
}
