# TANTRA 06: The Behaviour Layer (observable, honest, logged)
Architect: Opus · Vision: Dr Wolf · Status: BUILT, tested offline (unit test + rendered HUD), waiting to be installed on the PC

## MANTRA
The face is no longer a decoration. It is one window onto a **behaviour layer**: the server reports what REALLY
happens (turn starts, each tool, its success or failure, ALLOW asked or answered, errors, lessons). From those facts
alone it derives activity, step, retries, busy time, mood, and vitals, then broadcasts and logs them.
**The face may never contradict the facts:** no smile on a failing step, no calm look while an ALLOW is pending.

## YANTRA (files)
- `app/behaviour.py` (NEW): the layer. Tool name → activity; facts → mood; vitals: Level/XP (only real successes),
  Morale (recent success rate), Energy (drains with heavy work, refills with rest, full after lessons), Streak, Today.
  Log: `logs/behaviour.jsonl` · state: `logs/behaviour-state.json`.
- `app/face/faces/living/index.html` (UPDATED): reads `behave` events first (truth beats guesses), shows the
  **HUD** (LV, XP, Morale, Energy, Streak, Today), the **step line** (BUILDING · STEP 3 · RETRY 1 · 42s), the
  **LEVEL UP** burst, and the **JOURNAL** (tap the HUD: a timeline of everything he did today, ✓ ✗ ↺ ✋ ★).
- `apply_bl.py`: wires it in (server.py: turn/tool/result/error/ALLOW/lessons hooks + `/api/behaviour`;
  dock.js: passes `behave` events to the face). Every edit checks its anchor and stops if it doesn't match.

## TANTRA (install, done by Opus or a builder)
1. Copy `behaviour.py` → `Jarvis-Max\app\`, `living/index.html` → `Jarvis-Max\app\face\faces\living\`.
2. In `Jarvis-Max\app` run: `python apply_bl.py` → prints "behaviour layer wired".
3. Dr Wolf restarts Jarvis.

## TEST (Dr Wolf)
1. Ask "open Notepad": the step line shows the steps, the face watches the hands, and at the end you get ★, XP and confetti.
2. Ask him to do something that fails: ✗ appears, the face goes puzzled, then frustrated, then low (never happy).
3. Something needing ALLOW: the face shows worried/polite and the journal shows ✋.
4. Tap the HUD (top-left): the journal opens.
5. "Go to your lessons": energy refills, +40 XP.

## HONESTY RULES
- Mood words describe the WORK STATE (e.g. "low" = 3 failures in a row), not feelings.
- XP only for steps the tool reported as successful; nothing for talking or failed steps.
