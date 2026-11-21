plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.iskander.aitrade"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.iskander.aitrade"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.2.0"
    }
}
