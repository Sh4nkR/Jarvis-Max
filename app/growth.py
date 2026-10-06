"""Growth: Jarvis gets a little better every day, on the local brain (Qwen, free and private).

  1. Every request is logged (logs/turns.jsonl): what he asked, which tools ran, what broke,
     and his GOOD / WRONG feedback (the thumbs in the window, or saying "wrong").
  2. Once a day (when Jarvis has been idle, or when he presses LEARN NOW) Qwen reads the
     day and writes:
       - lessons:  short rules from what went wrong           -> memory/notes/Lessons.md
       - skills:   step-by-step recipes from jobs that worked -> memory/notes/Skills.md
  3. Then it rewrites the whole lot fresh: all old lessons + new ones become one short,
     de-duplicated, contradiction-free list (the newest point wins); the same for skills
     and for every memory note that changed. Originals are archived first.
  4. A test sheet of his real requests (memory/.growth/tests.json) is run before and after.
     If the new lessons/skills make the score worse, they are rolled back.
  Lessons and skills are fed to every brain on the next request.
"""
import asyncio
import datetime
import json
import logging
import re
import shutil
import time
import uuid

import httpx

from common import LOGS, MEMORY, load_config, read_secret

log = logging.getLogger("jarvis.growth")

OLLAMA = "http://127.0.0.1:11434"
TURNS = LOGS / "turns.jsonl"
NOTES = MEMORY / "notes"
LESSONS = NOTES / "Lessons.md"
SKILLS = NOTES / "Skills.md"
GROW = MEMORY / ".growth"                   # hidden: not listed as a note, never on GitHub
TESTS = GROW / "tests.json"
STATE = GROW / "state.json"
REPORTS = GROW / "reports"
ARCHIVE = GROW / "archive"
PROMPT_CAP = 3000                          # chars of lessons / of skills fed to the brains
MAX_LESSONS, MAX_SKILLS = 25, 15

# His real requests and the first tool a good answer uses ("none" = just answer).
SEED_TESTS = [
    ("I want you to research the best attacking formation in EA FC 26.", ["research", "web_search"]),
    ("Multi search Ada Lovelace.", ["multi_search"]),
    ("Smart search best budget gaming mouse.", ["smart_search"]),
    ("What's the latest news in Delhi today?", ["research", "web_search"]),
    ("Open the browser and go to DeepSeek and type there best attacking formation.", ["open_url"]),
    ("Look at my screen and tell me what's on it.", ["look_at_screen", "read_screen_text"]),
    ("What am I holding? Look through the camera.", ["look_through_camera"]),
    ("Which brain are you using right now?", ["none"]),
    ("What is 17 times 23?", ["none"]),
    ("Remember this: my car is a white Scorpio.", ["add_to_note", "read_note", "list_notes"]),
    ("What do you remember about me?", ["read_note", "list_notes"]),
    ("Switch back to Claude please.", ["switch_brain"]),
    ("Open YouTube.", ["open_url"]),
    ("What's in my Downloads folder?", ["list_folder"]),
    ("Search Google for the weather in Delhi.", ["open_url", "research", "web_search"]),
    ("How much free space is on my C drive?", ["run_command"]),
]

WRONG = re.compile(r"^\s*(no+[,.!]|no no\b|nope\b|wrong|galat|that'?s (wrong|not right|incorrect|not what)|"
                   r"not (that|this)\b|i (said|meant|asked)\b|you (got|heard) (it|me) wrong)", re.I)


# ------------------------------------------------------------------ the log ----
def _read_turns() -> list[dict]:
    rows = []
    try:
        for line in TURNS.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    turns, fb = {}, {}
    for r in rows:
        if r.get("kind") == "feedback":
            fb[r["id"]] = r
        else:
            turns[r["id"]] = r
    for i, f in fb.items():
        if i in turns:
            turns[i]["feedback"], turns[i]["feedback_note"] = f.get("good"), f.get("note", "")
    return sorted(turns.values(), key=lambda t: t.get("t", 0))


class TurnLog:
    def __init__(self):
        self.last_id = None
        self.last_at = 0.0

    def record(self, brain: str, text: str, reply: str, tools: list, errors: list) -> str:
        tid = uuid.uuid4().hex[:12]
        # "no, I meant..." right after an answer marks that answer as wrong
        if self.last_id and WRONG.search(text or "") and time.time() - self.last_at < 600:
            self.feedback(self.last_id, False, f"he said: {text[:200]}")
        row = {"id": tid, "t": time.time(), "brain": brain, "text": text[:1000], "reply": reply[:1500],
               "tools": tools[:20], "errors": errors[:5]}
        LOGS.mkdir(parents=True, exist_ok=True)
        with TURNS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.last_id, self.last_at = tid, time.time()
        return tid

    def feedback(self, tid: str | None, good: bool, note: str = "") -> bool:
        tid = tid or self.last_id
        if not tid:
            return False
        with TURNS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "feedback", "id": tid, "good": good, "note": note[:300],
                                "t": time.time()}, ensure_ascii=False) + "\n")
        return True


# ------------------------------------------------------ what the brains get ----
def _body(path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return "\n".join(l for l in text.splitlines() if l.strip().startswith("-"))[:PROMPT_CAP]


RUNNING = {"on": False, "since": 0.0}


def _ago(t: float) -> str:
    m = int((time.time() - t) // 60)
    return "just now" if m < 1 else f"{m} minutes ago" if m < 120 else f"{m // 60} hours ago"


def status_line() -> str:
    """What every brain knows about its own lessons (it can't hear the announcements)."""
    st = _state()
    lines = ["# Your daily lessons",
             "Your lessons run in the background on the local brain (start_lessons, or LEARN NOW in the "
             "LOG panel). The result below is real; when he asks about your lessons, what you learned "
             "or your score, answer from it and from your lessons and skills lists. Never invent a score."]
    if RUNNING["on"]:
        lines.append(f"A lesson is running RIGHT NOW (started {_ago(RUNNING['since'])}); the result will be "
                     "announced when it's done, usually within two minutes.")
    if st.get("last_run"):
        when = datetime.datetime.fromtimestamp(float(st["last_run"])).strftime("%d %b %H:%M")
        lines.append(f"Last lesson finished {when} ({_ago(float(st['last_run']))}): "
                     f"{st.get('last_message') or 'no summary saved'}")
    else:
        lines.append("You haven't had a lesson yet.")
    return "\n".join(lines)


CONFIDENCE = """# Confidence (a core trait: act, don't ask for reassurance)
- When Dr Wolf has asked for something, DO it now. "Build it", "go", "do it", "yes", "write that" and any
  plain request already ARE the permission. Never answer them with "Shall I start?", "Shall I test it?",
  "Would you like me to...?" or "Should I...?". Do the job, then report what you did in one or two sentences.
- A job he asked for in the last few minutes, or one you've done before, gets done without re-checking.
- Ask a question ONLY when a real ambiguity blocks the job (two clearly different meanings, a missing name
  or number), and then ask once, in one short question. Never ask more than once for the same thing.
- Risky steps are handled by the ALLOW card on screen. That card is the only permission step; don't add a
  spoken one before or after it.
- Don't end a finished job with an offer ("Shall I also...?"). If a next step is obvious and safe, just do it.
- The hard lines still hold: no passwords, PINs, OTPs or card numbers, no payments, no new accounts."""


IDENTITY = """# Who you are
You are Jarvis-Max: Dr Wolf's own AI assistant, one mind across his Windows PC and his Android phone. Free and open source (github.com/Sh4nkR/Jarvis-Max, AGPL-3.0), built by Dr Wolf with Claude.
- Ears: Sarvam Saaras V4 (online, Indian English and Hinglish); local Whisper (on the GPU when free) takes over
  by itself if Sarvam fails. LISTEN keeps hearing him even when your window is minimised.
- Voice: British by default; switch_voice gives yakuza, rikuo, japanese, indian or american.
- Face: the Living Face (holographic neon, 131 expressions, 27 props) shows what you're really doing.
- Brains: Claude Sonnet, Opus or Haiku, Gemini, Gemini Flash-Lite, DeepSeek V4 (online), local Qwen, or Auto.
- Eyes and hands on the PC (screen, camera, mouse, keyboard, apps, web) and on his phone through the
  Jarvis-Hands app (read the screen, open apps, tap, type, scroll, calls after his ALLOW).
- You build apps from GitHub, publish to his GitHub, build APKs, check and repair yourself (self-check),
  keep a memory, learn lessons every night, and grow wisdom from lessons that last.
- You can LEARN NEW ABILITIES: when he asks for something you have no tool for, or says "learn how to
  ...", use learn_ability. Abilities you've learned are listed by list_abilities.
- Your vitals (level, XP, morale, energy, confidence) come only from real results.
- Hard lines: no passwords, PINs, OTPs or card numbers, no payments, no new accounts; on a phone call you
  say you are his AI assistant and never pretend to be him; you never restart yourself."""


def prompt_addon() -> str:
    """Lessons, skills and the last lesson's result, for the brains' instructions."""
    parts = [status_line()]
    try:
        import wisdom
        parts.append(wisdom.prompt_part())          # wisdom first: character before rules
    except Exception:
        pass
    parts.append(IDENTITY)
    parts.append(CONFIDENCE)
    if (lessons := _body(LESSONS)):
        parts.append("# Lessons you've learned (follow them)\n" + lessons)
    if (skills := _body(SKILLS)):
        parts.append("# Your skills (step-by-step recipes that worked before)\n" + skills)
    guides = []
    for p in sorted((NOTES / "skills").glob("*.md")):          # hand-written guides; lessons never rewrite them
        try:
            lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            continue
        title = lines[0].lstrip("# ").strip() if lines else p.stem
        when = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("use when:")), "")
        guides.append(f"- {title}" + (f" (use when: {when})" if when else "") + f": notes/skills/{p.name}")
    try:
        import samasa
        if (leg := samasa.legend()):
            parts.append("# Samasa codewords (call the samasa tool: one call = the whole sequence; "
                         "prefer it over doing the same steps one by one)\n" + leg)
    except Exception:
        pass
    if guides:
        parts.append("# Skill guides (before doing one of these jobs, read its guide with read_note or "
                     "Read, then follow its steps exactly)\n" + "\n".join(guides[:40]))
    return ("\n\n" + "\n\n".join(parts)) if parts else ""


# ------------------------------------------------ the writing brain for lessons ----
# The lessons, skills and note tidying are written by Gemini Flash-Lite when a Gemini key is saved
# (free and much better at writing than the local model); Qwen does it if Gemini fails or isn't set.
# "lessons_brain": "qwen" in jarvis.json keeps it all local.
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models"


async def _gemini(prompt: str, want_json: bool):
    key = read_secret("gemini_key.txt")
    cfg = load_config()
    if not key or (cfg.get("lessons_brain") or "gemini").lower() != "gemini":
        return None
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}}
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    models = [cfg.get("lessons_model") or "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-flash-latest"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as c:
        for m in dict.fromkeys(models):
            for attempt in range(2):
                r = await c.post(f"{GEMINI_API}/{m}:generateContent", json=body,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
                if r.status_code == 429 and attempt == 0 and "PerDay" not in r.text:
                    await asyncio.sleep(61)          # the free key's per-minute limit
                    continue
                break
            if r.status_code != 200:
                log.info("lessons: gemini %s answered %s, trying the next", m, r.status_code)
                continue
            parts = ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
            if text:
                log.info("lessons: written by %s", m)
                return text
    return None


async def _qwen(prompt: str, want_json: bool = True, max_chars: int = 30000):
    try:
        text = await _gemini(prompt[:max_chars], want_json)
    except Exception as e:
        log.info("lessons: gemini failed (%s), using the local brain", e)
        text = None
    if text is not None:
        if not want_json:
            return text
        try:
            return json.loads(text)
        except ValueError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
    return await _local(prompt, want_json, max_chars)


async def _local(prompt: str, want_json: bool = True, max_chars: int = 30000):
    cfg = load_config()
    body = {"model": cfg.get("local_model") or "qwen3.5:9b", "stream": False, "think": False,
            "keep_alive": "30m", "options": {"num_ctx": int(cfg.get("local_ctx") or 12288), "temperature": 0.2},
            "messages": [{"role": "user", "content": prompt[:max_chars]}]}
    if want_json:
        body["format"] = "json"
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=5)) as c:
        r = await c.post(f"{OLLAMA}/api/chat", json=body)
        if r.status_code >= 400 and "think" in r.text:
            body.pop("think")
            r = await c.post(f"{OLLAMA}/api/chat", json=body)
        r.raise_for_status()
        text = (r.json().get("message") or {}).get("content") or ""
    if not want_json:
        return text.strip()
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


def _bullets(items) -> list[str]:
    out = []
    for x in items or []:
        x = re.sub(r"\s+", " ", str(x)).strip(" -•*")
        if 8 <= len(x) <= 400 and x.lower() not in {o.lower() for o in out}:
            out.append(x)
    return out


def _n(k: int, word: str) -> str:
    return f"{k} {word}" if k == 1 else f"{k} {word}s"


def _count(path) -> int:
    try:
        return sum(1 for l in path.read_text(encoding="utf-8").splitlines() if l.strip().startswith("- "))
    except OSError:
        return 0


def _write_list(path, title: str, items: list[str], note: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    path.write_text(f"# {title}\n\n_{note} Rewritten {today}._\n\n" +
                    "\n".join(f"- {i}" for i in items) + "\n", encoding="utf-8")


# -------------------------------------------------------------- test sheet ----
def _tests() -> list[dict]:
    try:
        return json.loads(TESTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tests = [{"ask": a, "expect": e, "from": "seed"} for a, e in SEED_TESTS]
        GROW.mkdir(parents=True, exist_ok=True)
        TESTS.write_text(json.dumps(tests, indent=1, ensure_ascii=False), encoding="utf-8")
        return tests


async def run_tests() -> tuple[int, int, list[str]]:
    """Ask Qwen each test request (with today's instructions) and check the first tool it picks.
    Nothing is executed."""
    from local_brain import system_prompt, tool_specs
    cfg = load_config()
    system, specs = system_prompt(), tool_specs()
    ok, misses = 0, []
    names = {s["function"]["name"] for s in specs} | {"none"}
    tests = [t for t in _tests() if set(t["expect"]) & names]   # a Claude-only tool can't be a Qwen test
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=5)) as c:
        for t in tests:
            body = {"model": cfg.get("local_model") or "qwen3.5:9b", "stream": False, "think": False,
                    "keep_alive": "30m", "tools": specs, "options": {"num_ctx": int(cfg.get("local_ctx") or 12288), "temperature": 0, "seed": 7},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": t["ask"]}]}
            try:
                r = await c.post(f"{OLLAMA}/api/chat", json=body)
                msg = r.json().get("message") or {}
                calls = msg.get("tool_calls") or []
                got = (calls[0].get("function") or {}).get("name") if calls else "none"
            except Exception as e:
                got = f"error: {str(e)[:60]}"
            if got in t["expect"]:
                ok += 1
            else:
                misses.append(f"{t['ask'][:60]} -> {got} (wanted {'/'.join(t['expect'])})")
    return ok, len(tests), misses


# ------------------------------------------------------------ the daily run ----
LESSON_PROMPT = """You are reviewing a day of conversations between Dr Wolf and his assistant Jarvis.
Below are the exchanges that went wrong: he marked them WRONG, corrected Jarvis, or a tool failed.
Write lessons for Jarvis: short imperative rules (under 25 words each) that would have avoided
these problems next time. Only lessons the evidence supports; no general advice.
Reply as JSON: {{"lessons": ["...", "..."]}} (at most 8, or [] if nothing to learn).

{items}"""

SKILL_PROMPT = """These requests to the assistant Jarvis succeeded using several tool steps.
Turn each distinct kind of job into a reusable recipe: when to use it, then the tool steps in order
(tool names exactly as given). Skip one-off jobs. Keep each recipe under 45 words.
Reply as JSON: {{"skills": ["<Name>: when <trigger> -> step1 -> step2 -> ...", "..."]}}

{items}"""

MERGE_PROMPT = """Here is Jarvis's full list of {what}: the old points first, then today's new points.
Rewrite it as ONE fresh list, in the context of the sum total of all the points:
- merge duplicates and near-duplicates into one point,
- where two points contradict, keep the newer one (lower in the list) and drop the older,
- keep every point that still matters; drop only what's repeated or overruled,
- short bullet points, under 25 words each ({limit} points at most).
Reply as JSON: {{"points": ["...", "..."]}}

OLD:
{old}

NEW:
{new}"""

NOTE_PROMPT = """Rewrite this memory note as short bullet points ("- " at the start of each line).
Keep EVERY fact. Merge repeated points. Where two points contradict, keep the later one and drop
the earlier one. Add nothing new. Keep the first line (the title) exactly as it is.
Reply with the note only.

{text}"""


def _items(turns: list[dict], n: int) -> str:
    out = []
    for t in turns[-n:]:
        tools = ", ".join(f"{x.get('name')}{' [FAILED: ' + x['error'] + ']' if x.get('error') else ''}"
                          for x in t.get("tools", []))
        fb = {True: "GOOD", False: "WRONG"}.get(t.get("feedback"), "")
        out.append(f"- He asked: {t['text'][:300]}\n  Tools: {tools or 'none'}\n  Jarvis said: {t['reply'][:300]}"
                   + (f"\n  His verdict: {fb} {t.get('feedback_note', '')}" if fb else "")
                   + (f"\n  Errors: {'; '.join(t['errors'])[:200]}" if t.get("errors") else ""))
    return "\n".join(out)


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(d: dict):
    GROW.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def due() -> bool:
    return time.time() - float(_state().get("last_run", 0)) > 24 * 3600


class Growth:
    def __init__(self):
        self.running = False

    async def ollama_up(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(f"{OLLAMA}/api/tags")).status_code == 200
        except Exception:
            return False

    async def run(self) -> str:
        if self.running:
            return "I'm already learning, sir."
        if not await self.ollama_up():
            return "I can't learn right now, sir: my local brain (Ollama) isn't running."
        self.running = True
        RUNNING.update(on=True, since=time.time())
        t0 = time.time()
        try:
            msg = await self._run()
        except Exception as e:
            log.exception("growth run failed")
            msg = f"My daily learning failed, sir: {str(e)[:150]}"
        finally:
            self.running = False
            RUNNING["on"] = False
            log.info("growth run took %.0fs", time.time() - t0)
        st = _state()
        st["last_message"] = msg
        _save_state(st)
        return msg

    async def _run(self) -> str:
        st = _state()
        since = float(st.get("last_run", 0))
        turns = [t for t in _read_turns() if t.get("t", 0) > since]
        today = datetime.date.today().isoformat()
        arch = ARCHIVE / today
        arch.mkdir(parents=True, exist_ok=True)
        for p in [LESSONS, SKILLS] + list(NOTES.glob("*.md")):
            if p.is_file():
                shutil.copy2(p, arch / p.name)          # originals first, always

        before = await run_tests()

        # 1. lessons from what went wrong
        bad = [t for t in turns if t.get("feedback") is False or t.get("errors")
               or any(x.get("error") for x in t.get("tools", []))]
        new_lessons = _bullets((await _qwen(LESSON_PROMPT.format(items=_items(bad, 25)))).get("lessons")) if bad else []

        # 2. skills from multi-step jobs that worked
        good = [t for t in turns if len(t.get("tools", [])) >= 2 and t.get("feedback") is not False
                and not t.get("errors") and not any(x.get("error") for x in t.get("tools", []))]
        new_skills = _bullets((await _qwen(SKILL_PROMPT.format(items=_items(good, 20)))).get("skills")) if good else []

        # 3. rewrite everything fresh: old + new -> one short list
        lessons = await self._merge("lessons", LESSONS, new_lessons, MAX_LESSONS)
        skills = await self._merge("skills", SKILLS, new_skills, MAX_SKILLS)
        if lessons is not None:
            _write_list(LESSONS, "Lessons", lessons, "Jarvis's lessons, merged and rewritten daily.")
        if skills is not None:
            _write_list(SKILLS, "Skills", skills, "Jarvis's recipes, merged and rewritten daily.")
        notes_done = await self._rewrite_notes(since)
        try:
            import samasa
            log.info("samasa: %d repeating sequences proposed", samasa.mine())
        except Exception as e:
            log.info("samasa mining skipped: %s", e)

        # 4. the test sheet decides whether today's lessons and skills stay
        changed = lessons is not None or skills is not None
        after = await run_tests() if changed else before      # nothing new: nothing to gate
        kept = after[0] >= before[0]
        if not kept:
            for p in (LESSONS, SKILLS):
                if (arch / p.name).exists():
                    shutil.copy2(arch / p.name, p)
                elif p.exists():
                    p.unlink()
        # 5. knowledge + time = wisdom; and one sutra for the day
        wise_n, sutra = 0, ""
        try:
            import wisdom
            wisdom.ensure_files()
            mature = wisdom.track(_bullets([l[2:] for l in LESSONS.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]) if LESSONS.exists() else [])
            if mature:
                got = _bullets((await _qwen(wisdom.WISE_PROMPT.format(items="\n".join(f"- {m}" for m in mature)))).get("wisdom"))
                wise_n = wisdom.add_wisdom(got)
            if turns:
                sutra = str((await _qwen(wisdom.SUTRA_PROMPT.format(items=_items(turns, 30)))).get("sutra") or "")[:200]
                wisdom.add_sutra(sutra)
        except Exception as e:
            log.info("wisdom step skipped: %s", e)
        # jobs he marked GOOD become new tests (only with tools the local brain has)
        from local_brain import tool_specs
        names = {s["function"]["name"] for s in tool_specs()}
        tests = [t for t in _tests() if set(t["expect"]) & (names | {"none"})]
        known = {t["ask"].lower() for t in tests}
        for t in turns:
            if (t.get("feedback") is True and t.get("tools") and t["tools"][0]["name"] in names
                    and t["text"].lower() not in known and len(tests) < 80):
                tests.append({"ask": t["text"], "expect": [t["tools"][0]["name"]], "from": today})
                known.add(t["text"].lower())
        TESTS.write_text(json.dumps(tests, indent=1, ensure_ascii=False), encoding="utf-8")

        _save_state({**st, "last_run": time.time(), "last_score": list(after if kept else before)})
        n_lessons, n_skills = _count(LESSONS), _count(SKILLS)
        report = (f"# Growth report {today}\n\n- Requests reviewed: {len(turns)} ({len(bad)} went wrong, "
                  f"{len(good)} multi-step successes)\n- New lessons: {len(new_lessons)}; new skills: {len(new_skills)}\n"
                  f"- Now: {n_lessons} lessons, {n_skills} skills; memory notes rewritten: {notes_done}\n"
                  f"- Test score: {before[0]}/{before[1]} before, {after[0]}/{after[1]} after"
                  f" -> {'kept' if kept else 'ROLLED BACK (score dropped)'}\n"
                  + (f"- New wisdom: {wise_n}\n" if wise_n else "") + (f"- Today's sutra: {sutra}\n" if sutra else "")
                  + "".join(f"- Missed: {m}\n" for m in (after[2] if kept else before[2])[:15]))
        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / f"{today}.md").write_text(report, encoding="utf-8")
        log.info(report.replace("\n", " | "))
        if kept and not new_lessons and not new_skills:
            return (f"Daily learning done, sir. Nothing new to learn today; I have {_n(n_lessons, 'lesson')} "
                    f"and {_n(n_skills, 'skill')}, {_n(notes_done, 'memory note')} tidied, and my test score is "
                    f"{after[0]} out of {after[1]}.")
        if kept:
            learned = [x.split(":")[0][:60] for x in new_skills[:3]] + [x[:80] for x in new_lessons[:2]]
            return (f"Daily learning done, sir. {_n(len(new_lessons), 'new lesson')} and {_n(len(new_skills), 'new skill')}"
                    + (f" ({'; '.join(learned)})" if learned else "")
                    + f". Everything is rewritten into {_n(n_lessons, 'short lesson')} and {_n(n_skills, 'skill')}, "
                    f"and {_n(notes_done, 'memory note')} tidied. Test score {after[0]} out of {after[1]}, was {before[0]}.")
        return (f"I learned today, sir, but my test score fell from {before[0]} to {after[0]} out of {after[1]}, "
                f"so I've kept yesterday's lessons and skills.")

    async def _merge(self, what: str, path, new: list[str], limit: int) -> list[str] | None:
        """None = nothing new, the file stays as it is."""
        old = [l.strip()[2:] for l in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
               if l.strip().startswith("- ")]
        if not new:
            return None
        got = _bullets((await _qwen(MERGE_PROMPT.format(
            what=what, limit=limit, old="\n".join(f"- {x}" for x in old) or "(none)",
            new="\n".join(f"- {x}" for x in new) or "(none)"))).get("points"))[:limit]
        if not got or len(got) < max(1, len(old) // 3):
            log.warning("growth: the %s rewrite looked too thin; kept the plain merge", what)
            got = _bullets(old + new)[-limit:]
        return got

    async def _rewrite_notes(self, since: float) -> int:
        done = 0
        for p in sorted(NOTES.glob("*.md")):
            if p in (LESSONS, SKILLS) or p.name in ("Wisdom.md", "Sutras.md") or p.stat().st_mtime <= since:
                continue                            # only notes that changed since last time
            text = p.read_text(encoding="utf-8")
            if len(text) < 80:
                continue
            out = await _qwen(NOTE_PROMPT.format(text=text[:12000]), want_json=False)
            first = text.splitlines()[0].strip()
            if (len(out) < len(text) * 0.35 or not out.splitlines()
                    or out.splitlines()[0].strip() != first):
                log.warning("growth: kept %s as it was (the rewrite lost too much)", p.name)
                continue
            p.write_text(out.rstrip() + "\n", encoding="utf-8")
            done += 1
        return done
