# JARVIS Android App

This folder contains a native Android project for JARVIS. It is a real Android application project intended to be built into an APK and installed on compatible Android devices.

## Requirements

- JDK 17+
- Android SDK with API 35 installed
- Android Studio or Gradle CLI

## Local setup

1. Install Android SDK and set `sdk.dir` in `local.properties`.
2. Run:

```bash
./gradlew assembleDebug
```

The debug APK will be created under:

```text
app/build/outputs/apk/debug/app-debug.apk
```

## Notes

- This Android app is intentionally lightweight and native first.
- It connects to the same JARVIS backend and stores user preferences locally with Jetpack DataStore.
- The app does not hardcode API keys or secrets.
- Full production signing is not included here; use your own signing keystore for release builds.

## Build with release signing

```bash
./gradlew assembleRelease
```

Then sign using the Android keystore process configured for your distribution channel.
