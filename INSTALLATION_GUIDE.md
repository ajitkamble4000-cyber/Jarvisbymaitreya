# Installation guide for 1.1.0-rc.1

## Docker installation

Requirements: Git, Docker Engine/Desktop with Compose, and free ports 5173 and 8000.

```sh
git clone https://github.com/ajitkamble4000-cyber/Jarvisbymaitreya.git
cd Jarvisbymaitreya
cp .env.example .env
docker compose up --build
```

Open `http://127.0.0.1:5173`, create the owner account, configure an available AI provider, select a model, and pair the desktop agent. Keep `.env` and its encryption key private and backed up.

In a managed network, pass the trusted CA to each image as a BuildKit secret:

```sh
docker build --secret id=proxy_ca,src=/path/to/proxy-ca.pem -t jarvis-ai-api services/api
docker build --secret id=proxy_ca,src=/path/to/proxy-ca.pem -t jarvis-ai-web apps/web
docker compose up -d --no-build
```

## Windows desktop package

Use `release/JARVIS Agent-1.1.0-rc.1-x64.exe` for the single-file portable build, or extract the ZIP and start `JARVIS Agent.exe`. Pair it from the app with the web-generated code. These packages were not executed or signed on Windows; SmartScreen may warn about an unsigned package.

Build the installer on Windows with Node and npm:

```powershell
cd apps\desktop
npm ci
npm run typecheck
npm test
npm run dist
```

## Android

`release/JARVIS-Android-1.1.0-rc.1-debug.apk` is supplied for testing. It is v2 debug-signed and targets Android SDK 35 with minimum SDK 26. Allow installation from the selected file source, then install the APK. On an authorized Android development machine, rebuild it with:

```sh
cd apps/android
./gradlew testDebugUnitTest assembleDebug
```

Treat the resulting debug APK as a test artifact. A production package still needs release signing and device verification.

## Source development

Use Python 3.12+ and Node with the committed lockfiles. PostgreSQL 16 must include pgvector. Backend setup is `python -m pip install -e 'services/api[dev]'`; web and desktop use `npm ci`. Full commands and database creation are in `README.md`.

## Verification

After startup, check `http://127.0.0.1:8000/api/v1/health` and the proxied health route through port 5173. Compare downloaded artifacts with `release/SHA256SUMS.txt`. See `TEST_RESULTS.md` for what was actually tested.
