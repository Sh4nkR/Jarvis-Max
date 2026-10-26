# TANTRA 03: Roktar Hands = HANDS ONLY. Chrome = Roktar's face, ears and voice.
Architect: Opus (idea: Dr Wolf). Builder: Gemini or Roktar. Tester: Dr Wolf.

## MANTRA
Chrome on the phone already does the mic, typing, voice and face perfectly. So the app stops trying.
The app does ONE job: give Roktar hands on the phone (accessibility), plus calls. A button opens Roktar in Chrome.

## YANTRA (already written by Opus)
- `Roktar-Builds\Roktar-Hands\app\src\main\java\com\roktar\hands\MainActivity.java` rewritten:
  a simple screen with 4 buttons:
  1 GIVE ROKTAR HANDS (Accessibility settings) · 2 SWITCH HANDS ON/OFF · 3 OPEN ROKTAR IN CHROME · PC address
  and a status line (hands given? hands on? PC address).
- HandsService, VoiceService (keeps hands alive in the background), Link, calls: unchanged.

## TANTRA (builder)
1. `android_build` folder `C:\Users\twent\Desktop\Roktar-Builds\Roktar-Hands`. On failure: send the exact error lines to Opus.
2. Dr Wolf installs the newest APK from `Desktop\Roktar-Builds\APKs`.

## TEST (Dr Wolf)
1. Open Roktar Hands: status says hands GIVEN ✔ (if not, tap 1 and turn it on).
2. Tap 2 so it says Hands ON ✔.
3. Tap 3: Roktar opens in Chrome. If Chrome warns "not private": Advanced -> Proceed. Allow the mic.
4. In Chrome, tap TALK: "open WhatsApp on my phone". PASS = WhatsApp opens.
5. Type in Chrome's box: "hello". PASS = arrives as "hello".
6. "Call <someone>" -> ALLOW on screen. PASS = the phone dials.
