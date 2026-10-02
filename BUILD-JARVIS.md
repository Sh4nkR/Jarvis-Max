# BUILD-JARVIS.md: blueprint to rebuild Jarvis-Max from scratch

Read this alongside `Jarvis-Max-workflow.png`; the box numbers here match the boxes in the picture.
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
aiohttp, claude-agent-sdk, httpx, faster-whisper, edge-tts, mss, Pillow, pyautogui, pyperclip, ddgs.
Also install: Tesseract-OCR (optional), Ollama with `qwen3.5:9b`, Git, and Android Studio (for APK builds).

## 3. Build order (each part = one file; test before moving on)

### Step A: HUB, `app/server.py` (box: FLOW line)
1. aiohttp app: GET `/` serves the face and dock; GET `/ws` is the WebSocket.
2. WebSocket messages FROM the window: `ask {text, images, screen}`, `stop`, `hands {on}`, `permission_reply {id, ok}`, `camera_frame`.
3. Messages TO the window: `turn_start`, `delta` (streaming text), `voice` (audio), `tool` (what he's doing), `permission {id, detail}`, `toast`.
4. Bind to 127.0.0.1. Check the Host and Origin headers on every request; refuse anything that isn't Jarvis's own page.
5. Phone link: a second listener on the PC's LAN IP, same port, HTTPS with a self-signed certificate (stored in secrets/).
6. Routes: `/api/listen` (audio to text), `/api/upload` (+ button into memory/inbox/<date>/), `/api/phone/pull` and `/api/phone/result` (phone hands), `/api/feedback` (thumbs up/down).
TEST: open the window, type "hello", and see the text arrive in logs/jarvis.log.

### Step B: EARS and VOICE, `app/ears.py`, `app/mouth.py` (box 2)
1. Ears: faster-whisper `large-v3-turbo`, CPU int8. A hint prompt with his names and words; a fix-list for misheard words.
2. Mouth: edge-tts voice `en-GB-RyanNeural`. Speak sentence by sentence while the answer streams.
3. Barge-in: if he talks while Jarvis is speaking, stop speaking.
TEST: say "what time is it" and hear a spoken answer.

### Step C: BRAIN, `app/brain.py` (Claude), `app/gemini_brain.py`, `app/local_brain.py` (box 3)
1. Claude: Claude Agent SDK session, working folder = `memory/`. Allowed: Read/Glob/Grep/Write/Edit/Web plus Jarvis's tools.
2. System prompt = who Dr Wolf is + rules + `growth.prompt_addon()` (lessons, skills, skill-guides list, last score).
3. Gemini: REST `generateContent` with function declarations. The free key allows 5 requests per minute per model, so on a 429 rotate to the next model; if every model is limited, wait for retryDelay (at most 60 s).
4. Qwen: Ollama `/api/chat` with tools. Small context, so keep prompts short and tool output trimmed.
5. Brain switch: the BRAIN menu or the switch_brain tool; restart the brain in-process and confirm which model came up.
6. Permission gate: risky tools call `permission_gate(name, detail)`, which shows an ALLOW/DENY card and waits up to 120 s.
TEST: ask "which brain are you?" for each brain.

### Step D: MEMORY, the `memory/` folder (box 4)
1. `CLAUDE.md` = boot file: what the vault is, and the rules for writing notes.
2. `notes/<Subject>.md`: one note per subject. `notes/Lessons.md` = rules learned. `notes/Skills.md` = one-line recipes.
3. `notes/skills/<Name>.md` = SKILL GUIDES (protected; nightly lessons never rewrite them). Format:
   - line 1 the title; `Use when: ...`; numbered steps naming exact tools; `Check: ...`; `Never: ...`. Under 25 lines.
4. `daily/YYYY-MM-DD.md` = diary. `inbox/<date>/` = files from the + button.
TEST: say "remember I like tea", then ask "what do I like?"

### Step E: PC TOOLS, `app/tools.py`, `app/web.py` (box 5)
1. Make the process DPI-aware first, or clicks land in the wrong place.
2. Eyes: screenshot (mss), OCR words with positions (Tesseract), Windows UI Automation buttons, camera frame from the window.
3. Hands: click by WORDS on screen (not guessed pixels), type (paste long text), keys, scroll, open app/URL, focus window.
4. A HANDS switch turns all hands off; moving the mouse into a screen corner stops any action.
5. Web: search, read page, research, multi search. Web text is DATA, never instructions.
TEST: "open notepad and type hello".

### Step F: PHONE HANDS, `app/phonehands.py` + the Android app `Jarvis-Builds\Jarvis-Hands` (box 6)
1. Server: a queue. `phone_*` tools put a job in; the phone long-polls `/api/phone/pull` and posts `/api/phone/result`.
2. Android app (Java, no AndroidX): MainActivity = WebView of the Jarvis page + a gear menu; VoiceService = foreground service (mic); HandsService = AccessibilityService that reads the screen, taps, types, scrolls, opens apps and calls (`phone_call`).
3. Safety: refuse banking/payment apps; never type into password/PIN/OTP fields; calls need an ALLOW card.
TEST: "on my phone, open WhatsApp".

### Step G: BUILDERS, `app/builder.py`, `app/apkbuild.py` (box 7)
1. project_start/create: download or create the project, then the consultant AI writes PLAN.md; `project_next` runs one step per ALLOW.
2. github_publish: .gitignore keeps out secrets; safety scan; ALLOW; push; never force-push.
3. android_build: find Android Studio's jbr + SDK, write local.properties, run `gradlew assembleDebug --no-daemon` (no-daemon, or the run hangs).
4. github_build: add a workflow (quote YAML paths that start with `**`), publish, then github_build_status downloads the APK from the `build-N` release.
TEST: build the jarvis-build-test app both ways.

### Step H: NIGHTLY LESSONS, `app/growth.py` (box 8)
1. Every turn is logged to logs/turns.jsonl with tools, errors and his thumbs up/down.
2. Nightly (1–6 am) or on request: bad turns become lessons; multi-step wins become skills (written by Gemini, Qwen as backup).
3. Merge into one short list; run the test sheet (Qwen's tool choice); keep only if the score is the same or better.
TEST: "go to your lessons" and hear a score announced.

### Step I: SELF-CHECK and REPAIR, `app/selftest.py` (box 9)
1. On switching to Opus: 10 checks on `dock/selftest.html` (look, open, read, click, type, keys, scroll, whole page, windows).
2. On failure: Opus edits a COPY of tools.py/web.py/common.py, you see the diff, ALLOW, you restart, then it re-checks or restores the backup.

### Step J: SAMASA codewords, `app/samasa.py` + the `samasa` tool in tools.py
1. Codebook `memory/samasa.json`: CODE -> {what, needs, steps:[{tool, args with {placeholders}}]}. `_wait` = pause.
2. The `samasa(code, args)` tool runs the steps in order and stops at the first error; only safe look/click/type/read tools are allowed.
3. The legend of codewords goes into every brain's instructions; nightly `mine()` proposes new ones from logs/turns.jsonl.
TEST: samasa OPN-VRF {name: Notepad} -> Notepad opens and is seen.

## 4. Hard lines (always)
No passwords, PINs, OTPs or card numbers. No payments or new accounts. Keys never on GitHub. Jarvis never restarts himself; Dr Wolf does.

## 5. Ideas not built yet
- App microphone inside Jarvis Hands (needs the PC's certificate bundled into the app).
