# Android release signing

The release workflow signs APKs with a persistent keystore supplied through GitHub Actions secrets. The keystore and passwords must never be committed to this repository.

## Create a persistent signing key

Run this in a trusted terminal with Java 17 or newer. Keep the generated `iskander-release.jks` and its passwords backed up securely. Losing this key prevents signing compatible updates to the installed app.

```bash
keytool -genkeypair -v \
  -keystore iskander-release.jks \
  -storetype JKS \
  -alias iskander-ai-trade \
  -keyalg RSA -keysize 4096 -validity 10000
```

Choose strong, unique passwords. Do not paste them into chat, source files, issue comments, or commit history.

## Add GitHub Actions secrets

Open the repository's **Settings → Secrets and variables → Actions** and add these repository secrets:

- `ANDROID_KEYSTORE_BASE64`: Base64 encoding of the complete JKS file. On Linux use `base64 -w 0 iskander-release.jks`; on macOS use `base64 < iskander-release.jks | tr -d '\n'`.
- `ANDROID_KEYSTORE_PASSWORD`: the keystore password.
- `ANDROID_KEY_ALIAS`: `iskander-ai-trade` (or the exact alias chosen above).
- `ANDROID_KEY_PASSWORD`: the key password.

Do not add the raw keystore file to GitHub, the repository, or the APK artifact.

## Build and verify

After all four secrets are saved, open **Actions → Iskander AI Trade Signed Android Release → Run workflow** on `deploy/infrlo-paper-v1`.

The workflow builds the Release APK, checks zip alignment, verifies the APK signature with Android's `apksigner`, and uploads only the signed APK artifact. A successful Gradle build alone is not treated as proof of a signed release.
