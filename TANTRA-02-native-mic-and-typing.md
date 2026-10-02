# TANTRA 02 (CANCELLED 2 Oct: replaced by TANTRA-03, Chrome does eyes/ears/voice)
Architect: Opus. Builder: Gemini or Jarvis (follow exactly). Tester: Dr Wolf.

## MANTRA
TANTRA-01 (trusting the PC's certificate) was built but the mic is STILL blocked, and typing arrives
reversed ("olleH"). Both are bugs inside the app's built-in browser. So we stop using the browser for
them: the app's own Android code now records the mic and shows a native text box, and hands the words
to the Jarvis page. The Jarvis face, voice and everything else stay exactly the same.

## YANTRA (already written by Opus, nothing to code)
- NEW  `Jarvis-Builds\Jarvis-Hands\app\src\main\java\com\jarvis\hands\NativeVoice.java`
- EDIT `Link.java`: new `postBytes(...)` (sends the recording to the PC's /api/listen)
- EDIT `MainActivity.java`: app tells the page "JarvisHands/2" and connects `AndroidJarvis`
- EDIT (PC, already live) `Jarvis-Max\app\dock\dock.js`: inside the app, TALK / LISTEN / typing box call the phone

## TANTRA (builder: do in order)
1. Build: `android_build` folder `C:\Users\twent\Desktop\Jarvis-Builds\Jarvis-Hands`.
   - If it FAILS: copy the error lines exactly to Opus. Do not edit the code yourself.
2. Dr Wolf restarts Jarvis on the PC (loads the new dock.js).
3. Dr Wolf installs the new APK from `Desktop\Jarvis-Builds\APKs` on the phone, opens Jarvis Hands.

## TEST (Dr Wolf)
- Tap the TYPE box: a phone text box pops up -> type "hello" -> SEND. PASS = Jarvis gets "hello" (not "olleh").
- Tap TALK (button lights up), speak, tap TALK again. PASS = Jarvis answers what you said.
- Tap LISTEN, speak, pause. PASS = he answers by himself; tap LISTEN again to stop.
- Known limit: in LISTEN mode the phone may hear Jarvis's own voice from its speaker; use earphones or TALK if so.
