# TANTRA 03: Jarvis Hands = HANDS ONLY. Chrome = Jarvis's face, ears and voice.
Architect: Opus (idea: Dr Wolf). Builder: Gemini or Jarvis. Tester: Dr Wolf.

## MANTRA
Chrome on the phone already does the mic, typing, voice and face perfectly. So the app stops trying.
The app does ONE job: give Jarvis hands on the phone (accessibility), plus calls. A button opens Jarvis in Chrome.

## YANTRA (already written by Opus)
- `Jarvis-Builds\Jarvis-Hands\app\src\main\java\com\jarvis\hands\MainActivity.java` rewritten:
  a simple screen with 4 buttons:
  1 GIVE JARVIS HANDS (Accessibility settings) · 2 SWITCH HANDS ON/OFF · 3 OPEN JARVIS IN CHROME · PC address
  and a status line (hands given? hands on? PC address).
- HandsService, VoiceService (keeps hands alive in the background), Link, calls: unchanged.

## TANTRA (builder)
1. `android_build` folder `C:\Users\twent\Desktop\Jarvis-Builds\Jarvis-Hands`. On failure: send the exact error lines to Opus.
2. Dr Wolf installs the newest APK from `Desktop\Jarvis-Builds\APKs`.

## TEST (Dr Wolf)
1. Open Jarvis Hands: status says hands GIVEN ✔ (if not, tap 1 and turn it on).
2. Tap 2 so it says Hands ON ✔.
3. Tap 3: Jarvis opens in Chrome. If Chrome warns "not private": Advanced -> Proceed. Allow the mic.
4. In Chrome, tap TALK: "open WhatsApp on my phone". PASS = WhatsApp opens.
5. Type in Chrome's box: "hello". PASS = arrives as "hello".
6. "Call <someone>" -> ALLOW on screen. PASS = the phone dials.
