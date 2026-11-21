package com.iskander.aitrade

import android.app.Activity
import android.os.Bundle
import android.graphics.Color
import android.view.Gravity
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView

class MainActivity : Activity() {
    private val bgColor = Color.rgb(7, 28, 23)
    private val gold = Color.rgb(216, 182, 106)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val savedUrl = getPreferences(MODE_PRIVATE).getString("dashboard_url", null)
        if (savedUrl.isNullOrBlank()) showConnectScreen() else openDashboard(savedUrl)
    }

    private fun showConnectScreen() {
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(36, 72, 36, 36)
            setBackgroundColor(bgColor)
            gravity = Gravity.CENTER_VERTICAL
        }
        val title = TextView(this).apply {
            text = "ISKANDER AI TRADE"
            textSize = 26f
            setTextColor(gold)
        }
        val description = TextView(this).apply {
            text = "Connect to your deployed paper-trading dashboard."
            textSize = 15f
            setTextColor(Color.LTGRAY)
            setPadding(0, 16, 0, 24)
        }
        val url = EditText(this).apply {
            hint = "https://your-service.onrender.com"
            setSingleLine(true)
            setTextColor(Color.WHITE)
            setHintTextColor(Color.LTGRAY)
        }
        val connect = Button(this).apply {
            text = "CONNECT"
            setOnClickListener {
                val value = url.text.toString().trim().trimEnd('/')
                if (value.startsWith("https://")) {
                    getPreferences(MODE_PRIVATE).edit().putString("dashboard_url", value).apply()
                    openDashboard(value)
                } else {
                    url.error = "Enter a valid HTTPS dashboard URL"
                }
            }
        }
        layout.addView(title)
        layout.addView(description)
        layout.addView(url)
        layout.addView(connect)
        setContentView(layout)
    }

    private fun openDashboard(baseUrl: String) {
        val web = WebView(this).apply {
            setBackgroundColor(bgColor)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = false
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean {
                    return !url.startsWith(baseUrl)
                }
            }
            loadUrl(baseUrl)
        }
        setContentView(web)
    }

    @Deprecated("Deprecated in Android API")
    override fun onBackPressed() {
        showConnectScreen()
    }
}
