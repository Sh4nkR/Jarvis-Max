# TANTRA 07: The phone app takes the Roktar name
Architect: Opus. Builder: Roktar (Opus brain). Tester: Dr Wolf.

## MANTRA
The PC side is already Roktar. The Android app's source on the PC still carries the previous name
(the `OLD` value in `app/rename_migration.py`) in its package, class names, labels and the phone link.
Until it is rebuilt, the PC no longer recognises it, so the phone's hands stop. Rebuild it as Roktar Hands.

## YANTRA (what changes in `Desktop\Roktar-Builds\Roktar-Hands`)
The first start of the renamed Roktar already moved `Desktop\<OLD>-Builds` to `Desktop\Roktar-Builds`.
- Project folder `<OLD>-Hands` -> `Roktar-Hands`.
- Package `com.<old>.hands` -> `com.roktar.hands`: `applicationId` and `namespace` in `app/build.gradle`,
  the `package` lines in every `.java` file, and the folder `app/src/main/java/com/<old>/hands` -> `com/roktar/hands`.
- App name and every label in `res/values/strings.xml` and `AndroidManifest.xml` -> "Roktar Hands".
- The page link: the user agent tag `<Old>Hands/2` -> `RoktarHands/2`, and the JavaScript bridge
  `Android<Old>` -> `AndroidRoktar` (the PC's `dock.js` already expects these).
- The pinned certificate `res/raw/<old>_pc.crt` -> `res/raw/roktar_pc.crt` (same file content, from
  `secrets\roktar-lan.crt`), and `@raw/<old>_pc` -> `@raw/roktar_pc` in `res/xml/network_security_config.xml`.
- Then search the whole project, case-insensitive, for the old name: zero hits left.

## TANTRA (builder: do in order)
1. Make the changes above on a copy, show the list of changed files, wait for ALLOW.
2. `android_build` folder `C:\Users\twent\Desktop\Roktar-Builds\Roktar-Hands`. On failure: send the exact error lines to Opus.
3. Copy the new APK into the repo as `Roktar Android Hands\Roktar-Android-Hands.apk` and `github_publish` (ALLOW).

## TEST (Dr Wolf)
1. Uninstall the old hands app from the phone (a new package installs as a separate app).
2. Install the new APK from `Desktop\Roktar-Builds\APKs`, open Roktar Hands, give hands (Accessibility), switch ON.
3. In Chrome on the phone: "open WhatsApp on my phone". PASS = WhatsApp opens.
4. "Call <someone>" -> ALLOW. PASS = the phone dials.
