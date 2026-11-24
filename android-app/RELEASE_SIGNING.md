# Android release signing

Use the existing persistent Android signing identity. Do not generate a replacement key: changing the signing identity can prevent compatible future updates.

The private Library file `Mumin-Android-GitHub-Secrets.txt` contains the existing PKCS12 keystore and its signing values. The keystore was validated locally with Java `keytool`; it opens successfully as a `PrivateKeyEntry` with alias `mumin-release`. Keep this file private and never commit it.

## GitHub Actions secrets

Open the repository's **Settings → Secrets and variables → Actions** and verify these repository secrets:

- `ANDROID_KEYSTORE_BASE64`: the complete Base64 value from the existing private signing file, copied as one uninterrupted string.
- `ANDROID_KEYSTORE_PASSWORD`: the existing keystore password from that file.
- `ANDROID_KEY_ALIAS`: `mumin-release`.
- `ANDROID_KEY_PASSWORD`: the existing private-key password from that file.

The previous Base64 secret was reported as malformed. Replace that value from the validated private file rather than creating a new keystore. Do not paste passwords or Base64 into chat, source files, issue comments, or commit history.

## Build and verify

After all four secrets are saved, open **Actions → Iskander AI Trade Signed Android Release → Run workflow** on `deploy/infrlo-paper-v1`.

The workflow builds the Release APK, checks zip alignment, verifies the APK signature with Android's `apksigner`, and uploads only the signed APK artifact. A successful Gradle build alone is not treated as proof of a signed release.
