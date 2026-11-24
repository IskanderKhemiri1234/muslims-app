package com.iskander.aitrade

import android.app.Activity
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
        root.addView(label("Native paper trading command center", 14f, muted).apply {
            setPadding(0, dp(8), 0, dp(18))
        })
        root.addView(label("PAPER ONLY  •  NO LIVE ORDERS", 12f, green))
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

    private fun api(path: String, method: String = "GET", auth: Boolean = true): JSONObject {
        val c = URL(baseUrl + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method; c.connectTimeout = 12000; c.readTimeout = 15000
            c.setRequestProperty("Accept", "application/json")
            if (auth) c.setRequestProperty("Authorization", "Bearer " + token)
            if (method == "POST") c.doOutput = true
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
        val controls = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        controls.addView(makeButton("Refresh") { refresh() }, LinearLayout.LayoutParams(0, dp(50), 1f))
        cycle = makeButton("Run paper cycle") { runCycle() }
        controls.addView(cycle, LinearLayout.LayoutParams(0, dp(50), 1f))
        body.addView(controls, params())
        body.addView(makeButton("Disconnect") { token = ""; login() }, params())
        body.addView(label("Public market data · Authenticated account · Paper execution only", 11f, muted), params())
        scroll.addView(body); root.addView(scroll); setContentView(root)
        render(s, m)
    }

    private fun render(s: JSONObject, m: JSONObject) {
        portfolio.removeAllViews(); portfolio.addView(label("Portfolio", 17f, gold))
        row(portfolio, "Virtual capital", number(s.optDouble("virtual_capital_usdt", 70.0), 2) + " USDT")
        row(portfolio, "Available cash", field(s, "cash_usdt") + " USDT")
        row(portfolio, "Daily net P/L", field(s, "daily_pnl_usdt") + " USDT")
        row(portfolio, "Realized net P/L", field(s, "total_realized_pnl_usdt") + " USDT")
        row(portfolio, "Open positions", (s.optJSONObject("positions")?.length() ?: 0).toString())

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
