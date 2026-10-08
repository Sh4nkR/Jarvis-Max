# TANTRA 04: Jarvis talks INSIDE the phone call (no speakerphone)
Architect: Opus. Builder: Gemini or Jarvis. Tester: Dr Wolf. Status: DESIGN, not built yet.

## MANTRA
Android won't let an app put a voice into a phone call. But Windows can: **Phone Link** connects the phone
to the PC as a Bluetooth headset, so the call's sound goes in and out through the PC. Then we swap the PC's
"headset" for **virtual audio cables**: Jarvis's voice goes straight into the call, and the caller's voice goes
straight into Jarvis's ears. Digital, clean, nothing on speaker. Dr Wolf can listen in on his headphones.

## YANTRA (pieces)
- Phone Link (built into Windows 11) + Link to Windows (on the phone), paired over Bluetooth, Calls enabled.
- VB-Audio **VB-CABLE** (free): gives "CABLE Input" (play into it) and "CABLE Output" (record from it).
- VB-Audio **Voicemeeter** (free): its virtual "VAIO" cable is the second wire.
- Wiring:
  - Windows default **communications MIC** = "CABLE Output"  -> the call hears whatever plays into CABLE Input
  - Jarvis's **voice** plays into "CABLE Input" (new jarvis.json setting `call_voice_device`)
  - Windows default **communications SPEAKER** = "Voicemeeter Input (VAIO)" -> the caller's voice
  - Jarvis's **ears** record from "Voicemeeter Out B1" (new setting `call_ears_device`)
  - Dr Wolf listens: Voicemeeter routes the call (and Jarvis) to his headphones too.

## TANTRA (steps)
1. Dr Wolf (once): install Phone Link calls (Phone Link > Calls > set up, pair Bluetooth). Test one normal call from the PC.
2. Dr Wolf (once): install VB-CABLE and Voicemeeter from vb-audio.com (installers need his click + a restart).
3. Opus writes the code: a CALL MODE in Jarvis:
   - mouth.py: in call mode, play voice to `call_voice_device` (and to his headphones).
   - ears: in call mode, listen continuously on `call_ears_device` with the hands-free pause detection.
   - tool `call_mode on/off`; tool `pc_call who=...` dials through Phone Link (search contact, click Call) using PC hands.
   - The conversation rules are in memory/notes/skills/Phone Call On My Behalf.md (introduce as an AI agent, stick to brief, summary after).
4. Builder: restart, set the Windows sound devices as wired above (Settings > Sound > communication devices).
5. TEST: call Dr Wolf's own second number or a friend who knows. PASS = they hear Jarvis clearly, Jarvis answers what they say, no echo.

## LIMITS (honest)
- Calls come from his real SIM via the phone, so caller ID is his number; Jarvis must say it's an AI agent.
- Phone Link + Bluetooth must stay connected; phone within Bluetooth range of the PC.
- Alternative later (paid): a cloud phone number (e.g. Exotel/Twilio) with an AI voice agent, no phone needed.
