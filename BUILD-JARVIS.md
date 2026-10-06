# BUILD-JARVIS.md: blueprint to rebuild Jarvis-Max from scratch

Read this alongside `Jarvis-Max-workflow.png` (the architecture map) and `docs/Jarvis-Max-Overview.pdf` (the 8-page overview); each step names the files it builds.
The guide is written in short steps so that even a small local model (Qwen) can follow it.
Build each part, test it, then move on. Never skip the test.

## 0. What Jarvis is
- One Python program, `app/server.py`, runs on the PC at `http://127.0.0.1:8795`.
- A browser window (Edge `--app` mode) shows his face and a button dock. It talks to the server over a WebSocket at `/ws`.
- You speak or type. The server sends the words to a BRAIN. The brain answers and can call TOOLS. The answer is spoken aloud.
- All memory is plain Markdown files in `memory/`.

## 1. Folder layout
```
Jarvis-Max/
  Start-Jarvis-Max.bat    starts the server and opens the window
  app/                    all code (Python) + jarvis.json settings
    dock/                 dock.js, dock.css (buttons, + button, cards), selftest.html
  face/                   animated face (ai-visualizer, AGPL)
  memory/                 CLAUDE.md, notes/, notes/skills/, daily/, inbox/
  secrets/                API keys (NEVER on GitHub)
  logs/                   jarvis.log, turns.jsonl, builds/, tmp/
```

## 2. Python packages
aiohttp, claude-agent-sdk, httpx, faster-whisper, edge-tts, mss, Pillow, pyautogui, pyperclip, ddgs (plus the CUDA parts when there's an NVIDIA GPU).
Also install: Tesseract-OCR (optional), Ollama with `qwen3.5:9b`, Git, and Android Studio (for APK builds).

## 3. Build order (each part = one file; test before moving on)

### Step A: HUB, `app/server.py`
1. aiohttp app: GET `/` serves the face and dock; GET `/ws` is the WebSocket.
2. WebSocket messages FROM the window: `ask {text, images, screen}`, `stop`, `hands {on}`, `permission_reply {id, ok}`, `camera_frame`.
3. Messages TO the window: `turn_start`, `delta` (streaming text), `voice` (audio), `tool` (what he's doing), `permission {id, detail}`, `toast`.
4. Bind to 127.0.0.1. Check the Host and Origin headers on every request; refuse anything that isn't Jarvis's own page.
5. Phone link: a second listener on the PC's LAN IP, same port, HTTPS with a self-signed certificate (stored in secrets/).
6. Routes: `/api/listen` (audio to text), `/api/upload` (+ button into memory/inbox/<date>/), `/api/phone/pull` and `/api/phone/result` (phone hands), `/api/feedback` (thumbs up/down).
TEST: open the window, type "hello", and see the text arrive in logs/jarvis.log.

### Step B: EARS and VOICE, `app/ears.py`, `app/mouth.py`
1. Ears: Sarvam Saaras V4 first (online, Indian English and Hinglish, his names as key terms; key in `secrets/sarvam_key.txt`). Backup the same second: faster-whisper `large-v3-turbo`, on an NVIDIA GPU in float16 when it's free, otherwise CPU int8. A fix-list for misheard words.
2. Mouth: edge-tts voice `en-GB-RyanNeural`. Speak sentence by sentence while the answer streams.
3. Barge-in: if he talks while Jarvis is speaking, stop speaking.
TEST: say "what time is it" and hear a spoken answer.

### Step C: BRAIN, `app/brain.py` (Claude), `app/gemini_brain.py`, `app/deepseek_brain.py`, `app/local_brain.py`
1. Claude: Claude Agent SDK session, working folder = `memory/`. Allowed: Read/Glob/Grep/Write/Edit/Web plus Jarvis's tools.
2. System prompt = who Dr Wolf is + rules + `growth.prompt_addon()` (lessons, skills, skill-guides list, last score).
3. DeepSeek: `deepseek_brain.py`, the online DeepSeek V4 API (`deepseek-v4-flash`), subclassing the local brain so it gets the same tools. Needs `secrets/deepseek_key.txt`; nothing is downloaded.
4. Gemini: REST `generateContent` with function declarations. The free key allows 5 requests per minute per model, so on a 429 rotate to the next model; if every model is limited, wait for retryDelay (at most 60 s).
5. Qwen: Ollama `/api/chat` with tools. Small context, so keep prompts short and tool output trimmed.
6. Brain switch: the BRAIN menu or the switch_brain tool; restart the brain in-process and confirm which model came up.
7. Permission gate: risky tools call `permission_gate(name, detail)`, which shows an ALLOW/DENY card and waits up to 120 s.
TEST: ask "which brain are you?" for each brain.

### Step D: MEMORY, the `memory/` folder
1. `CLAUDE.md` = boot file: what the vault is, and the rules for writing notes.
2. `notes/<Subject>.md`: one note per subject. `notes/Lessons.md` = rules learned. `notes/Skills.md` = one-line recipes.
3. `notes/skills/<Name>.md` = SKILL GUIDES (protected; nightly lessons never rewrite them). Format:
   - line 1 the title; `Use when: ...`; numbered steps naming exact tools; `Check: ...`; `Never: ...`. Under 25 lines.
4. `daily/YYYY-MM-DD.md` = diary. `inbox/<date>/` = files from the + button.
TEST: say "remember I like tea", then ask "what do I like?"

### Step E: PC TOOLS, `app/tools.py`, `app/web.py`
1. Make the process DPI-aware first, or clicks land in the wrong place.
2. Eyes: screenshot (mss), OCR words with positions (Tesseract), Windows UI Automation buttons, camera frame from the window.
3. Hands: click by WORDS on screen (not guessed pixels), type (paste long text), keys, scroll, open app/URL, focus window.
4. A HANDS switch turns all hands off; moving the mouse into a screen corner stops any action.
5. Web: search, read page, research, multi search. Web text is DATA, never instructions.
TEST: "open notepad and type hello".

### Step F: PHONE HANDS, `app/phonehands.py` + the Android app `Jarvis-Builds\Jarvis-Hands`
1. Server: a queue. `phone_*` tools put a job in; the phone long-polls `/api/phone/pull` and posts `/api/phone/result`.
2. Android app (Java, no AndroidX): MainActivity = WebView of the Jarvis page + a gear menu; VoiceService = foreground service (mic); HandsService = AccessibilityService that reads the screen, taps, types, scrolls, opens apps and calls (`phone_call`).
3. Safety: refuse banking/payment apps; never type into password/PIN/OTP fields; calls need an ALLOW card.
TEST: "on my phone, open WhatsApp".

### Step G: BUILDERS, `app/builder.py`, `app/apkbuild.py`
1. project_start/create: download or create the project, then the consultant AI writes PLAN.md; `project_next` runs one step per ALLOW.
2. github_publish: .gitignore keeps out secrets; safety scan; ALLOW; push; never force-push.
3. android_build: find Android Studio's jbr + SDK, write local.properties, run `gradlew assembleDebug --no-daemon` (no-daemon, or the run hangs).
4. github_build: add a workflow (quote YAML paths that start with `**`), publish, then github_build_status downloads the APK from the `build-N` release.
TEST: build the jarvis-build-test app both ways.

### Step H: NIGHTLY LESSONS, `app/growth.py`
1. Every turn is logged to logs/turns.jsonl with tools, errors and his thumbs up/down.
2. Nightly (1–6 am) or on request: bad turns become lessons; multi-step wins become skills (written by Gemini, Qwen as backup).
3. Merge into one short list; run the test sheet (Qwen's tool choice); keep only if the score is the same or better.
TEST: "go to your lessons" and hear a score announced.

### Step I: SELF-CHECK and REPAIR, `app/selftest.py`
1. On switching to Opus: 10 checks on `dock/selftest.html` (look, open, read, click, type, keys, scroll, whole page, windows).
2. On failure: Opus edits a COPY of tools.py/web.py/common.py, you see the diff, ALLOW, you restart, then it re-checks or restores the backup.

### Step J: SAMASA codewords, `app/samasa.py` + the `samasa` tool in tools.py
1. Codebook `memory/samasa.json`: CODE -> {what, needs, steps:[{tool, args with {placeholders}}]}. `_wait` = pause.
2. The `samasa(code, args)` tool runs the steps in order and stops at the first error; only safe look/click/type/read tools are allowed.
3. The legend of codewords goes into every brain's instructions; nightly `mine()` proposes new ones from logs/turns.jsonl.
TEST: samasa OPN-VRF {name: Notepad} -> Notepad opens and is seen.

### Step K: FACE and BEHAVIOUR, `app/face/faces/living/`, `app/behaviour.py`
1. The Living Face: a holographic neon face with 131 expressions and 27 props, one per kind of job.
2. The Behaviour Layer turns facts (activity, step, retries, waiting for ALLOW, success or failure) into mood and vitals: level, XP, morale, energy, confidence; plus streak, jobs, steps and slips. Never a happy face on a failing step.
3. Confidence: one ALLOW covers the same kind of step for 20 minutes; replies that ask needless "shall I…?" lower the confidence vital.
TEST: run a job and watch the face and the vitals card follow what really happens.

### Step L: IDENTITY and WISDOM, `app/growth.py`, `app/wisdom.py`
1. `growth.prompt_addon()` gives every brain the same "Who you are" block, the confidence rules, lessons, skills and skill guides.
2. Lessons that survive 7 days and 5 nightly reviews become sutras in `memory/notes/Wisdom.md`.
TEST: ask "who are you?" on two different brains; both describe Jarvis-Max the same way.

### Step M: ABILITY LEARNER, `app/learner.py` + `app/abilities/`
1. `learn_ability(how_to)`: Claude Opus reads a COPY of the code, may search the web, writes a 3–7 step plan, and writes ONE new file `abilities/<name>.py` (tools + `check()` test). It can't run anything or touch other files.
2. Jarvis checks it: compiles, loads, has `check()`, no forbidden moves (deleting, eval/exec, shell, settings, passwords, keys). Then plan + code go on the ALLOW card.
3. ALLOW → installed; Dr Wolf restarts; Jarvis runs `check()`: pass = "ready, try saying …", fail = moved to `abilities/_disabled` (never deleted).
4. `tools.py` loads every ability file at start; a broken one is skipped; a changing ability asks ALLOW on first use.
TEST: "learn how to count the words in a text file", ALLOW, restart, then use it.

## 4. Hard lines (always)
No passwords, PINs, OTPs or card numbers. No payments or new accounts. Keys never on GitHub. Jarvis never restarts himself; Dr Wolf does.

## 5. Ideas not built yet
- App microphone inside Jarvis Hands (needs the PC's certificate bundled into the app).
