plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "live.zpact.enterprise"
    compileSdk = 34

    defaultConfig {
        applicationId = "live.zpact.enterprise"
        minSdk = 24
        targetSdk = 34
        versionCode = 10
        versionName = "3.3.2"

        buildConfigField("String", "DEFAULT_SERVER_URL", "\"https://directions-all-holder-both.trycloudflare.com\"")
        buildConfigField("String", "DEFAULT_LAN_URL", "\"http://192.168.1.15:8088\"")

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables {
            useSupportLibrary = true
        }
    }

    flavorDimensions += "role"
    productFlavors {
        create("admin") {
            dimension = "role"
            applicationId = "live.zpact.enterprise.admin"
            versionNameSuffix = "-ADMIN-RADAR"
            buildConfigField("String", "FLAVOR_ROLE", "\"admin\"")
            buildConfigField("Boolean", "IS_ADMIN_FLAVOR", "true")
            buildConfigField("String", "CLOUD_BEACON_TOPIC", "\"zpact_cloud_registry_v2_8287744705\"")
            buildConfigField("String", "DEFAULT_WEBHOOK_SECRET", "\"zpact_sms_webhook_secret_2026\"")
            buildConfigField("String", "DEFAULT_ADMIN_KEY", "\"zpact_internal_bridge_secret_key_2026\"")
        }
        create("teacher") {
            dimension = "role"
            applicationId = "live.zpact.store"
            versionNameSuffix = "-TEACHER-STORE"
            buildConfigField("String", "FLAVOR_ROLE", "\"teacher\"")
            buildConfigField("Boolean", "IS_ADMIN_FLAVOR", "false")
            buildConfigField("String", "CLOUD_BEACON_TOPIC", "\"zpact_cloud_registry_v2_8287744705\"")
            buildConfigField("String", "DEFAULT_WEBHOOK_SECRET", "\"\"")
            buildConfigField("String", "DEFAULT_ADMIN_KEY", "\"\"")
        }
        create("sender") {
            dimension = "role"
            applicationId = "live.zpact.sender"
            versionNameSuffix = "-SENDER-TEAM"
            buildConfigField("String", "FLAVOR_ROLE", "\"sender\"")
            buildConfigField("Boolean", "IS_ADMIN_FLAVOR", "false")
            buildConfigField("String", "CLOUD_BEACON_TOPIC", "\"zpact_cloud_registry_v2_8287744705\"")
            buildConfigField("String", "DEFAULT_WEBHOOK_SECRET", "\"\"")
            buildConfigField("String", "DEFAULT_ADMIN_KEY", "\"\"")
        }
    }

    signingConfigs {
        create("release") {
            storeFile = file("../zpact_release.jks")
            storePassword = "zpact_secure_vault_2026"
            keyAlias = "zpact_release_key"
            keyPassword = "zpact_secure_vault_2026"
            enableV1Signing = true
            enableV2Signing = true
            enableV3Signing = true
            enableV4Signing = false
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            isDebuggable = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            signingConfig = signingConfigs.getByName("release")
        }
        debug {
            isDebuggable = true
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }

    buildFeatures {
        viewBinding = false
        buildConfig = true
    }
}

dependencies {
    // AndroidX & UI
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.cardview:cardview:1.0.0")

    // Coroutines & Lifecycle
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.7.3")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.lifecycle:lifecycle-service:2.7.0")

    // Networking (OkHttp & Gson)
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("com.google.code.gson:gson:2.10.1")
}
