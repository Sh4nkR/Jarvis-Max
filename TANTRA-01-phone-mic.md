# TANTRA 01: make the Jarvis Hands app's MIC and LISTEN buttons work  (DONE 2 Oct: built, did NOT fix the mic; see TANTRA-02)
Written by Opus (architect). Executed by Gemini or Jarvis (builder), following these steps exactly. Dr Wolf tests and reports bugs.

## MANTRA (the whole idea, in one line)
The app's built-in browser blocks the mic because it does not TRUST the PC's home-made certificate.
Fix: bake the PC's certificate into the app, so the app trusts the PC fully, and the mic unlocks.

## YANTRA (the pieces)
- PC certificate (public part only, safe to share): `Desktop\Jarvis-Max\secrets\jarvis-lan.crt`
  (valid to 2036, covers localhost, 127.0.0.1, 192.168.1.4)
- NEVER copy `jarvis-lan.key`. That is the private key. It stays in secrets/.
- App project: `Desktop\Jarvis-Builds\Jarvis-Hands`

## TANTRA (the steps, do them in order, check each one)
1. Copy `Desktop\Jarvis-Max\secrets\jarvis-lan.crt` to
   `Desktop\Jarvis-Builds\Jarvis-Hands\app\src\main\res\raw\jarvis_pc.crt` (make the `raw` folder).
   CHECK: the file exists and starts with `-----BEGIN CERTIFICATE-----`.
2. Create `app\src\main\res\xml\network_security_config.xml` with exactly:
   ```xml
   <?xml version="1.0" encoding="utf-8"?>
   <network-security-config>
       <base-config cleartextTrafficPermitted="true">
           <trust-anchors>
               <certificates src="system" />
               <certificates src="@raw/jarvis_pc" />
           </trust-anchors>
       </base-config>
   </network-security-config>
   ```
3. In `app\src\main\AndroidManifest.xml`, inside the `<application ...>` tag, add the attribute
   `android:networkSecurityConfig="@xml/network_security_config"`.
   CHECK: the attribute appears exactly once.
4. Build on the PC: `android_build` with folder `C:\Users\twent\Desktop\Jarvis-Builds\Jarvis-Hands`.
   CHECK: "Built ... APK is ...APKs\Jarvis-Hands-<date>.apk".
5. Dr Wolf installs that APK on the phone (over the old one), opens the app, taps the MIC button.
   PASS = Jarvis hears him. Also test LISTEN (hands-free).

## IF STEP 5 FAILS (plan B, ask Opus first)
- The phone's IP or PC's IP changed: the certificate only covers 192.168.1.4. The PC must stay on 192.168.1.4
  (set a fixed IP in the router), or Jarvis regenerates the cert and steps 1-5 are repeated.
- If the mic is still blocked even with a trusted cert: Opus writes plan B = the app records audio itself
  (Android AudioRecord in VoiceService) and sends it to the PC's `/api/listen`. Do not improvise this.

## RULES
- Change only the files named above. Never touch secrets/ except to READ the .crt.
- Never commit `secrets/` or `.key` files to GitHub.
- Report the exact error text if any step fails; don't guess.

## ALREADY FIXED BY OPUS (no rebuild needed, restart Jarvis)
- Gibberish when typing in the app: the Samsung keyboard was still "composing" a word when Enter fired.
  `app/dock/dock.js` now ignores Enter while the keyboard is composing.
