"""Jarvis learns new abilities.

Dr Wolf says "learn how to ..." and Jarvis hands the question to Claude Opus (the same Claude
sign-in his brain uses). Opus researches it, writes a clear step-by-step plan for learning it,
and builds the ability ON A COPY of Jarvis's code, as ONE new file in app/abilities/. Opus can
read and search the web but can't run anything, and can only write that one file.

The new file must pass Jarvis's own checks (compiles, loads, has a test, no forbidden moves).
Then it goes on screen (PC and phone) with the plan and the code: ALLOW installs it, DENY or no
answer in 15 minutes leaves Jarvis as he was. Dr Wolf restarts Jarvis (Jarvis never restarts
himself); on that start Jarvis runs the ability's own test. Pass = "ready, try saying ...";
fail = the ability is switched off (moved to abilities/_disabled, never deleted).

If Dr Wolf finds a bug when he tries it, "fix the <name> ability: <what went wrong>" sends it
back to Opus with that ability's file open for changes, through the same ALLOW and test.
"""
import asyncio
import difflib
import hashlib
import json
import logging
import py_compile
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from common import APP, IS_WIN, LOGS

log = logging.getLogger("jarvis.learner")

DIR = LOGS / "abilities"
NOTE = DIR / "pending.json"             # carries an installed ability across the restart
HISTORY = DIR / "history.md"
ABIL = APP / "abilities"
DISABLED = ABIL / "_disabled"
_NOWIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0
SLUG = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

# Moves a learned ability may never make. Checked on the code itself, before it is shown.
FORBIDDEN = [
    (r"shutil\.rmtree|os\.remove\(|os\.unlink\(|\.unlink\(|os\.rmdir\(|\.rmdir\(|send2trash", "deletes files"),
    (r"\beval\(|\bexec\(|__import__\(", "runs code built at run time"),
    (r"shell\s*=\s*True", "runs commands through a shell"),
    (r"winreg|\breg(\.exe)?\s+(add|delete)\b|diskpart|bcdedit|format\s+[a-z]:|Set-MpPreference|"
     r"DisableRealtimeMonitoring|netsh\s+advfirewall", "changes Windows settings"),
    (r"Invoke-Expression|\biex\b|\|\s*(sh|bash|powershell)\b", "downloads and runs code"),
    (r"getpass|keyring|password\s*=|passwd|\bcvv\b|card_?number|\botp\s*=", "handles passwords or card details"),
    (r"secrets[/\\\"']|SECRETS\b|\.env\b", "reads Jarvis's keys"),
    (r"^\s*(import|from)\s+(server|brain|selftest|learner|growth|behaviour|signin)\b", "reaches into Jarvis's own insides"),
    (r"FAILSAFE\s*=\s*False|STATE\.hands\s*=", "switches off a safety"),
    (r"jarvis\.json|CFG\[[^\]]*\]\s*=", "changes Jarvis's settings"),
]
SENDS = re.compile(r"\.post\(|\.put\(|smtplib|socket\.|upload|webhook|sendmail", re.I)

TEACHER_RULES = r"""You are the teacher for Jarvis-Max, the voice assistant on Dr Wolf's Windows 11 PC (RTX 5050,
Python 3.11/3.12, a 3840x2160 screen; his phone is a Samsung Note 10+ reached through Jarvis's
phone_* tools). Dr Wolf can't code; he asked Jarvis to learn something new. Your job, in order:

1. LEARN IT. Work out how this is really done: read the code in this folder (tools.py has every
   tool Jarvis already has; pyproject.toml and uv.lock list the packages installed) and search the
   web when you need to. Prefer the simplest way that really works on his PC.
2. PLAN IT. Write a short step-by-step plan in plain words a non-coder understands: how the
   ability works and what it will do when he asks for it. 3 to 7 steps.
3. BUILD IT as ONE new file: abilities/<name>.py (<name>: lowercase letters, digits, underscores,
   3-40 long, not already taken). You can't run anything, so write carefully and simply.

The file, exactly in this shape:

    \"\"\"<one line: what this ability does>.
    Learned from: "<his request>"
    Plan:
    1. ...
    \"\"\"
    import asyncio
    from tools import tool, _say, errors_as_words, STATE

    SAFE = False      # True ONLY if it just looks or reads and changes nothing anywhere

    @tool("<tool_name>", "<for the brain: what it does, WHEN to use it (the words he might say), "
          "what each argument means>", {"type": "object", "properties": {...}, "required": [...]})
    @errors_as_words
    async def <tool_name>(args):
        ...
        return _say("<the result in plain words, for the brain to tell him>")

    TOOLS = [<tool_name>]      # one tool, two or three at most

    async def check() -> str:
        \"\"\"Jarvis's test of this ability, run once after he installs it. Quick (under a minute),
        changes nothing of his, uses a small sample. Raise an Exception with a plain reason if
        it doesn't work; return a short note if it does.\"\"\"

Rules:
- Ability tool names must not clash with existing tools (see ALL in tools.py).
- If the job is just a sequence of things Jarvis can ALREADY do with his tools, make the ability a
  "recipe": a tool that returns the exact steps (which existing tools, in what order, with what
  arguments) for the brain to follow. That is the simplest and safest kind; prefer it.
- To use his hands, call existing tools: `import tools` and `await tools.type_text.handler({...})`
  etc. If you use the mouse or keyboard, first check `STATE.hands` and say so if it is off.
- Slow or blocking work (files, programs, downloads) goes in `await asyncio.to_thread(...)`.
- Only the standard library and packages already in pyproject.toml / uv.lock. A program like
  ffmpeg: find it with shutil.which and, if it is missing, return a plain message saying so.
- Files it makes go in Desktop\Jarvis-Builds\<name>\ (Path.home() / "Desktop" / "Jarvis-Builds"),
  or where he names. It never deletes, overwrites his own files, or touches anything in Jarvis's
  folder, his keys or his settings.
- Never: passwords, PINs, OTPs, card numbers, payments, creating accounts, sending messages or
  posting anything as him, changing Windows settings, running downloaded code, a shell
  (shell=True), eval/exec, importing server/brain/selftest/learner. Jarvis rejects files that do.
- Be honest. If it can't be done safely with what is installed (it needs a new package, a paid
  service, an account, a password, or something above), build NOTHING and say why.
- If you were told to FIX an existing ability, edit only that file, keep its tool names, and fix
  the real cause of the bug he reported.

Finish with ONE line of JSON and nothing after it:
{"built": true or false, "name": "<file name without .py>", "plan": ["step", "step", ...],
 "summary": "<what he can now do, one plain sentence>", "try_saying": "<a sentence he can say to use it>",
 "reason": "<if not built: why, one plain sentence>", "needs": ["<anything missing>"]}
"""


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(p: Path, d: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=1), encoding="utf-8")


def pending() -> dict | None:
    """The note an installed ability left for the next Jarvis."""
    return _read_json(NOTE) or None


def scan(code: str) -> str:
    """'' if the code makes none of the forbidden moves; otherwise which one."""
    for pat, what in FORBIDDEN:
        if re.search(pat, code, re.I | re.M):
            return what
    return ""


def sends(code: str) -> bool:
    return bool(SENDS.search(code))


def _verdict(text: str) -> dict:
    for m in reversed(list(re.finditer(r"\{[^{}]*\"built\"[^{}]*\}", text or "", re.S))):
        try:
            d = json.loads(m.group(0))
        except ValueError:
            continue
        plan = d.get("plan") or []
        return {"built": bool(d.get("built")), "name": str(d.get("name") or "").strip()[:41],
                "plan": [str(s)[:200] for s in plan][:8] if isinstance(plan, list) else [],
                "summary": str(d.get("summary") or "")[:300], "try_saying": str(d.get("try_saying") or "")[:160],
                "reason": str(d.get("reason") or "")[:300],
                "needs": [str(s)[:80] for s in (d.get("needs") or [])][:5]}
    return {"built": False, "name": "", "plan": [], "summary": "", "try_saying": "", "needs": [],
            "reason": (text or "").strip()[-300:] or "Claude gave no answer"}


def existing() -> list[str]:
    return sorted(p.stem for p in ABIL.glob("*.py") if not p.name.startswith("_")) if ABIL.is_dir() else []


def disable(name: str) -> bool:
    """Switch an ability off: it moves to abilities/_disabled (kept, never deleted)."""
    src = ABIL / f"{name}.py"
    if not src.exists():
        return False
    DISABLED.mkdir(parents=True, exist_ok=True)
    dest = DISABLED / f"{name}.py"
    if dest.exists():
        dest = DISABLED / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.py"
    shutil.move(str(src), str(dest))
    return True


class Learner:
    def __init__(self):
        self.running = False
        self.task: asyncio.Task | None = None
        # set by the server
        self.announce = None                    # async (text)
        self.approve = None                     # async (card) -> bool: his ALLOW / DENY on screen
        self.windows = lambda: 0
        self.other_busy = lambda: False         # the self-check uses the same card

    async def _say(self, text: str):
        log.info("learner: %s", text)
        if self.announce:
            try:
                await self.announce(text)
            except Exception:
                log.exception("learner: couldn't announce")

    def _history(self, line: str):
        DIR.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} {line}\n")

    # --------------------------------------------------------------------- starting ----
    def start(self, how_to: str, fix: str = "") -> str:
        how_to = (how_to or "").strip()
        fix = (fix or "").strip().removesuffix(".py")
        if not how_to:
            return "Ask him what he wants you to learn, in a sentence."
        if self.running:
            return "I'm already learning something; the result will be announced when it's done."
        if self.other_busy():
            return "The self-check is using my repair card right now; ask again in a few minutes."
        if fix and fix not in existing():
            return (f"There's no learned ability called '{fix}'. The learned ones are: "
                    + (", ".join(existing()) or "none yet") + ".")
        self.running = True
        self.task = asyncio.create_task(self._run(how_to, fix))
        what = f"fixing the {fix} ability" if fix else "learning it"
        return (f"Started {what} in the background with Claude Opus: research, a step-by-step plan, then "
                "the build on a copy of your code. It takes a few minutes. Tell him briefly; the plan and "
                "the code will come up on screen for his ALLOW, and nothing changes before that.")

    # ---------------------------------------------------------------------- the job ----
    async def _run(self, how_to: str, fix: str):
        run = time.strftime("%Y%m%d-%H%M%S")
        work = DIR / f"learn-{run}"
        try:
            stage = self._stage(work, fix)
            try:
                v = await asyncio.wait_for(self._teacher(how_to, fix, stage), 30 * 60)
            except asyncio.TimeoutError:
                v = _verdict("") | {"reason": "learning it took over 30 minutes"}
            except Exception as e:
                log.exception("learner: teacher")
                v = _verdict("") | {"reason": f"Claude Opus couldn't be reached: {str(e)[:150]}"}
            (work / "verdict.json").write_text(json.dumps(v, indent=1), encoding="utf-8")
            await self._finish(how_to, fix, stage, work, run, v)
        except Exception:
            log.exception("learner: run")
            await self._say("Something went wrong while I was learning that, sir. The details are in my log.")
        finally:
            self.running = False

    async def _finish(self, how_to, fix, stage, work, run, v):
        before = set(existing())
        made = sorted(p.stem for p in (stage / "abilities").glob("*.py")
                      if not p.name.startswith("_") and (p.stem not in before or p.stem == fix)
                      and _sha(p) != _sha(ABIL / p.name))
        if not v["built"] or not made:
            why = v["reason"] or "Claude didn't build anything"
            if v["needs"]:
                why += " It needs: " + ", ".join(v["needs"]) + "."
            self._history(f'NOT learned "{how_to[:80]}": {why}')
            await self._say(f"I couldn't learn that one safely, sir. {why[:260]}")
            return
        if len(made) > 1:
            self._history(f'rejected "{how_to[:80]}": more than one file ({", ".join(made)})')
            await self._say("Claude wrote more than one ability file, which isn't allowed, so I've thrown it away, sir.")
            return
        name = made[0]
        code = (stage / "abilities" / f"{name}.py").read_text(encoding="utf-8", errors="replace")
        problem = "" if SLUG.match(name) else "the file name isn't a plain lowercase name"
        problem = problem or scan(code)
        problem = problem or await asyncio.to_thread(self._validate, stage, name)
        if problem:
            self._history(f'rejected "{how_to[:80]}" ({name}): {problem}')
            await self._say(f"Claude's ability failed my safety checks ({problem}), so I've thrown it away, sir.")
            return
        old = ABIL / f"{name}.py"
        diff = "".join(difflib.unified_diff(
            old.read_text(encoding="utf-8").splitlines(keepends=True) if old.exists() else [],
            code.splitlines(keepends=True), fromfile=f"abilities/{name}.py (now)",
            tofile=f"abilities/{name}.py ({'fixed' if fix else 'new'})", n=2))
        (work / "proposal.diff").write_text(diff, encoding="utf-8")
        plan = "\n".join(f"{i}. {s}" for i, s in enumerate(v["plan"], 1))
        (work / "plan.md").write_text(f"# {name}\n\nAsked: {how_to}\n\n{plan}\n\n{v['summary']}\n", encoding="utf-8")
        asks = "asks your ALLOW the first time it's used" if not (_safe(code)) else "only looks and reads, no ALLOW needed"
        await self._say(f"I've learned it, sir: {v['summary'][:200]} The plan and the code are on screen. "
                        "Press ALLOW to add it to me, or DENY to leave me as I am.")
        allowed = await self.approve({
            "title": f"{'FIX FOR' if fix else 'NEW'} ABILITY: {name.upper()}",
            "button": "ALLOW: ADD THIS ABILITY",
            "note": "Nothing changes unless you press ALLOW. Then restart Jarvis: he tests the ability "
                    "and tells you what to say to use it. A failed test switches it off again.",
            "summary": f"{v['summary']}\n\nHow I learned it:\n{plan}",
            "cause": f"Try saying: \"{v['try_saying']}\". It {asks}.",
            "files": [f"abilities/{name}.py"], "diff": diff[:16000]})
        if not allowed:
            self._history(f'not added (denied or no answer): {name}')
            await self._say("I've left myself as I am, sir. The plan and code are saved for your consultant.")
            return
        ABIL.mkdir(parents=True, exist_ok=True)
        backup = ""
        if old.exists():
            bdir = DIR / f"backup-{run}"
            bdir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, bdir / old.name)
            backup = str(bdir / old.name)
        shutil.copy2(stage / "abilities" / f"{name}.py", old)
        problem = await asyncio.to_thread(self._validate, APP, name)
        if problem:
            if backup:
                shutil.copy2(backup, old)
            else:
                disable(name)
            self._history(f"{name}: added but wouldn't load ({problem}); undone")
            await self._say("It wouldn't load in my real code, sir, so I've undone it.")
            return
        _write_json(NOTE, {"stage": "verify", "name": name, "how_to": how_to, "summary": v["summary"],
                           "try_saying": v["try_saying"], "backup": backup, "fix": bool(fix), "at": time.time()})
        self._history(f'{name}: ADDED with his permission ({"fix" if fix else "new"}): {v["summary"]}')
        await self._say("Added, sir. It switches on when you restart me: close my black window and click "
                        "Start-Jarvis-Max. When I'm back I'll test it and tell you what to say.")

    # ---------------------------------------------------------------------- helpers ----
    def _stage(self, work: Path, fix: str) -> Path:
        """A copy of the code for Opus to read; abilities/ is where the new file goes."""
        stage = work / "app"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        (stage / "abilities").mkdir(parents=True, exist_ok=True)
        for p in list(APP.glob("*.py")) + [APP / "pyproject.toml", APP / "uv.lock"]:
            if p.exists():
                shutil.copy2(p, stage / p.name)
        for p in ABIL.glob("*") if ABIL.is_dir() else []:
            if p.is_file() and p.suffix in (".py", ".md") and not p.name.startswith("_"):
                shutil.copy2(p, stage / "abilities" / p.name)
        return stage

    def _validate(self, folder: Path, name: str) -> str:
        """'' if the ability compiles, loads with the rest of Jarvis, and has its test."""
        f = folder / "abilities" / f"{name}.py"
        try:
            py_compile.compile(str(f), cfile=str(DIR / "compiled.pyc"), doraise=True)
        except py_compile.PyCompileError as e:
            x = e.exc_value
            return f"it doesn't compile (line {getattr(x, 'lineno', '?')}: {getattr(x, 'msg', x)})"[:200]
        probe = ("import json, sys, tools; m = sys.modules.get('ability_%s'); "
                 "print(json.dumps({'tools': [n for n, a in tools.LEARNED.items() if a == '%s'], "
                 "'check': bool(m and callable(getattr(m, 'check', None))), 'bad': tools.BROKEN.get('%s', '')}))"
                 % (name, name, name))
        try:
            r = subprocess.run([sys.executable, "-c", probe], cwd=str(folder), capture_output=True, text=True,
                               timeout=120, creationflags=_NOWIN)
        except Exception as e:
            return f"couldn't test loading it: {e}"
        if r.returncode != 0:
            return "Jarvis doesn't load with it: " + (r.stderr or r.stdout).strip()[-300:]
        try:
            d = json.loads(r.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return "couldn't read the load test"
        if d.get("bad"):
            return f"it doesn't load: {d['bad'][:200]}"
        if not d.get("tools"):
            return "it has no usable tool (or its name clashes with one I already have)"
        if not d.get("check"):
            return "it has no check() test"
        return ""

    async def _teacher(self, how_to: str, fix: str, stage: Path) -> dict:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                                      PermissionResultAllow, PermissionResultDeny, ResultMessage, TextBlock)
        stage = stage.resolve()
        abil = (stage / "abilities").resolve()
        before = set(existing())

        def where(inp) -> Path | None:
            try:
                p = Path(inp.get("file_path") or inp.get("path") or ".")
                return (p if p.is_absolute() else stage / p).resolve()
            except (OSError, ValueError):
                return None

        def writable(p: Path | None) -> bool:
            if not p or p.parent != abil or p.suffix != ".py" or p.name.startswith("_"):
                return False
            return p.stem == fix if fix else p.stem not in before

        async def can_use(name, inp, ctx):
            p = where(inp)
            if name in ("Read", "Glob", "Grep", "LS"):
                if p and (p == stage or stage in p.parents):
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Only this folder can be read.")
            if name in ("Edit", "MultiEdit", "Write"):
                if writable(p):
                    return PermissionResultAllow()
                return PermissionResultDeny(message=(f"Only abilities/{fix}.py may be edited." if fix else
                                                     "Only ONE new file abilities/<name>.py may be written."))
            if name in ("WebSearch", "WebFetch", "TodoWrite"):
                return PermissionResultAllow()
            return PermissionResultDeny(message="Not available here: read, search the web, and write the ability file.")

        brief = [f'Dr Wolf asked Jarvis: "{how_to}"']
        if fix:
            brief.append(f"This is a FIX: he tried the learned ability abilities/{fix}.py and reported the "
                         "problem above. Read that file, find the real cause, and fix it there.")
        have = existing()
        brief.append("Abilities Jarvis has already learned (in abilities/): " + (", ".join(have) or "none yet"))
        brief.append("Learn it, plan it, build it, following your rules. End with the one-line JSON.")
        opts = ClaudeAgentOptions(model="opus", cwd=str(stage), system_prompt=TEACHER_RULES,
                                  permission_mode="default", can_use_tool=can_use, max_turns=120,
                                  setting_sources=[])
        said = []
        async with ClaudeSDKClient(options=opts) as client:
            await client.query("\n".join(brief))
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    said += [b.text for b in msg.content if isinstance(b, TextBlock)]
                elif isinstance(msg, ResultMessage):
                    break
        return _verdict("\n".join(said))

    # ----------------------------------------------------------- after the restart ----
    async def resume(self, note: dict):
        """The new Jarvis after an ability was added: test it, keep or switch it off, and say so."""
        try:
            NOTE.unlink()
        except OSError:
            pass
        name = note.get("name") or ""
        for _ in range(120):
            if self.windows():
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(6)
        import tools
        mod = sys.modules.get(f"ability_{name}")
        if not mod or name not in tools.LEARNED.values():
            why = tools.BROKEN.get(name, "it didn't load")
            self._undo(note)
            self._history(f"{name}: didn't load after the restart ({why}); switched off")
            await self._say(f"The new {name.replace('_', ' ')} ability didn't load, sir, so I've switched it off. "
                            "Restart me once more to clear it.")
            return
        self.running = True
        try:
            await self._say(f"Testing my new {name.replace('_', ' ')} ability, sir.")
            try:
                got = await asyncio.wait_for(mod.check(), 120)
            except Exception as e:
                why = "the test took over two minutes" if isinstance(e, asyncio.TimeoutError) else f"{type(e).__name__}: {e}"
                self._undo(note)
                self._history(f"{name}: test FAILED ({why[:200]}); switched off")
                undone = ("I've put its old version back" if note.get("backup") else "I've switched the ability off")
                await self._say(f"The test failed, sir: {str(why)[:200]}. {undone}; restart me once more to "
                                "load that. Tell me what you want changed and I'll try again.")
                return
            self._history(f"{name}: test passed ({str(got)[:120]}): READY")
            tip = f' Try saying: "{note["try_saying"]}".' if note.get("try_saying") else ""
            await self._say(f"New ability ready, sir: {note.get('summary', '')[:200]}{tip} "
                            f"If anything goes wrong, say 'fix the {name.replace('_', ' ')} ability' and what happened.")
        finally:
            self.running = False

    def _undo(self, note: dict):
        name = note.get("name") or ""
        if note.get("backup") and Path(note["backup"]).exists():
            shutil.copy2(note["backup"], ABIL / f"{name}.py")     # a fix that didn't work: the old version back
        else:
            disable(name)


def _safe(code: str) -> bool:
    return bool(re.search(r"^SAFE\s*=\s*True\b", code, re.M)) and not sends(code)
