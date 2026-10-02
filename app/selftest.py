"""Jarvis checks his own eyes and hands.

Whenever Dr Wolf switches Jarvis to Claude Opus (or asks him to check himself), Jarvis runs a
fixed check on the real screen, on a test page he serves himself (dock/selftest.html): open a
web page, read the screen, click by words, click a button from Windows' list, type into a
box, press keys, scroll, read a whole page, switch windows. The page reports back what really
happened, so nothing is taken on trust.

He says the result out loud, and anything that failed is written up in
logs/selftest/last-report.md with a screenshot of each failure.

When something fails, Claude Opus works out a repair ON A COPY of Jarvis's code (only tools.py,
web.py and common.py may change). The live code is untouched until Dr Wolf has seen the change
on screen and pressed ALLOW. Then Jarvis backs up the old code and applies the fix, and Dr Wolf
restarts him (Jarvis never restarts himself). On that start he runs the whole check again: he
keeps the fix only if every check passes, otherwise he puts the old code back and asks for one
more restart. DENY, or no answer within 15 minutes, leaves everything as it was.
"""
import asyncio
import base64
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

import tools
from common import APP, CFG, IS_WIN, LOGS

log = logging.getLogger("jarvis.selftest")

DIR = LOGS / "selftest"
REPORT = DIR / "last-report.md"
HISTORY = DIR / "history.md"
NOTE = DIR / "pending.json"              # carries an applied fix across the restart
STATE_FILE = DIR / "state.json"
TYPED = "jarvis self test 42"
EDITABLE = ("tools.py", "web.py", "common.py")    # the only files a repair may change
RETRY_AFTER = 6 * 3600                   # after a fix that didn't hold, don't propose another for a while
_NOWIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


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
    """The note an applied fix left for the next Jarvis."""
    return _read_json(NOTE) or None


MECHANIC_RULES = """You are the mechanic for Jarvis-Max, the voice assistant on Dr Wolf's Windows 11 PC
(a 3840x2160 screen). Jarvis just ran his self-check: fixed actions with his real mouse and
keyboard on a test page he serves himself, and the page reported back what really happened.
Some checks failed. Your job: find the real cause in Jarvis's code and prepare a repair.

The current folder is a WORKING COPY of Jarvis's code. Nothing you change here touches the
live Jarvis: Dr Wolf sees your change on screen and decides whether to apply it.
- tools.py: Jarvis's eyes and hands (pyautogui with DPI awareness, Tesseract OCR, Windows UI
  Automation through comtypes, win32 calls through ctypes). Every tool the check uses is here.
- web.py: web search. common.py: settings and paths.
- selftest.py and dock/selftest.html are the self-check itself: read them to see exactly what
  each check does, but never change them.

Rules:
1. Read before you change anything. Find the root cause of each failure from the results, the
   screenshots and the code. Fix the cause, not the symptom.
2. Smallest change that fixes it. Keep every tool's name, arguments and meaning.
3. You may edit ONLY tools.py, web.py and common.py in this folder. Other edits are refused.
4. Never weaken a safety: the HANDS on/off switch, the corner emergency stop (pyautogui
   failsafe), Jarvis never clicking or typing into his own window, the refusals to type
   passwords, card numbers or OTPs.
5. The fix must work for real apps and websites, not just the test page. Never special-case
   the test page, its words or its title.
6. You can't run anything. If Dr Wolf applies your fix and restarts Jarvis, Jarvis runs the
   whole check again and keeps the fix only if everything passes.
7. If the cause is outside the code (a program not installed, a Windows setting, the browser,
   the mouse left in a screen corner, someone using the PC during the check), change nothing
   and say what it is.
8. Finish with ONE line of JSON and nothing after it:
   {"fixed": true or false, "cause": "<the cause, one plain sentence>", "summary": "<what you changed, one plain sentence Dr Wolf can hear>"}
"""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


class _Stopped(Exception):
    pass


class SelfTest:
    def __init__(self):
        self.running = False
        self.cancel = False
        self.page: dict[str, dict] = {}         # run id -> what the test page last reported
        self.task: asyncio.Task | None = None
        # set by the server
        self.announce = None                    # async (text)
        self.approve = None                     # async (proposal) -> bool: his ALLOW / DENY on screen
        self.windows = lambda: 0                # Jarvis windows connected
        self.claude_ok = lambda: False          # Claude is signed in and running

    # ---------------------------------------------------------------- page reports ----
    def page_report(self, d: dict):
        run = str(d.get("run") or "")
        if run:
            self.page[run] = d
            for k in list(self.page)[:-5]:      # keep the last few runs only
                self.page.pop(k, None)

    def stop(self):
        if self.running:
            self.cancel = True

    async def _say(self, text: str):
        log.info("selftest: %s", text)
        if self.announce:
            try:
                await self.announce(text)
            except Exception:
                log.exception("selftest: couldn't announce")

    # --------------------------------------------------------------------- starting ----
    def start(self, reason: str) -> str:
        """Begin a check in the background (a switch to Opus, or he asked)."""
        if self.running:
            return "A self-check is already running."
        if not tools.STATE.hands:
            asyncio.create_task(self._say("I'd like to check my hands, sir, but they're switched off. "
                                          "Press HANDS and ask me to check myself."))
            return "The hands are switched off, so the self-check can't run."
        self.running, self.cancel = True, False
        self.task = asyncio.create_task(self._run(reason))
        return ("Self-check started. It takes about a minute on the screen; tell him to leave the "
                "mouse and keyboard alone. The result will be announced when it's done.")

    async def _run(self, reason: str):
        try:
            await self._say("Self-check, sir: I'm testing my eyes, mouse, keyboard, browsing and "
                            "scrolling. Please leave the mouse and keyboard alone for about a minute.")
            await asyncio.sleep(7)
            results, run = await self.check()
            if results is None:
                return
            failed = [r for r in results if not r["ok"]]
            self._write_report(results, run, reason)
            if not failed:
                self._history(f"passed all {len(results)} checks ({reason})")
                await self._say(f"Self-check passed, sir: all {len(results)} checks work. "
                                "Eyes, mouse, keyboard, browsing and scrolling are fine.")
                return
            names = ", ".join(r["label"] for r in failed)
            self._history(f"FAILED {len(failed)} of {len(results)}: {names} ({reason})")
            if self.approve is None or not self.claude_ok():
                await self._say(f"Self-check found {len(failed)} problem{'s' if len(failed) > 1 else ''}, sir: "
                                f"{names}. I need Claude to work out a fix and it isn't running, so the "
                                "report with screenshots is ready for your consultant.")
                return
            await self._propose(results, run, reason)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("selftest failed")
            await self._say(f"My self-check itself broke, sir: {str(e)[:120]}.")
        finally:
            self.running = False

    # ----------------------------------------------------------------------- checks ----
    async def _tool(self, name: str, args: dict) -> tuple[bool, str, bool]:
        """(error?, text, has_image) from one real tool call."""
        t = {x.name: x for x in tools.ALL}[name]
        try:
            res = await asyncio.wait_for(t.handler(args), 120)
        except asyncio.TimeoutError:
            return True, "the tool took over two minutes and was stopped", False
        except Exception as e:
            return True, f"EXCEPTION {type(e).__name__}: {e}", False
        content = res.get("content", [])
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        return bool(res.get("is_error")), text, any(c.get("type") == "image" for c in content)

    async def _page_until(self, run: str, cond, secs: float) -> dict:
        end = time.monotonic() + secs
        while True:
            st = self.page.get(run) or {}
            if st and cond(st):
                return st
            if time.monotonic() > end:
                return st
            await asyncio.sleep(0.25)

    async def _shot(self, run: str, key: str) -> str:
        try:
            img, _ = await asyncio.to_thread(tools.grab_screen)
            b64, _ = await asyncio.to_thread(tools.to_jpeg_b64, img, 1600, 70)
            p = DIR / f"{run}-{key}.jpg"
            p.write_bytes(base64.b64decode(b64))
            return str(p)
        except Exception:
            return ""

    async def check(self) -> tuple[list | None, str]:
        """Run every check once. Returns (results, run id), or (None, run) if stopped."""
        DIR.mkdir(parents=True, exist_ok=True)
        run = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        url = f"http://127.0.0.1:{int(CFG['port'])}/dock/selftest.html?run={run}"
        results: list[dict] = []

        async def step(key, label, tool, args, judge, wait=None, secs=5.0):
            if self.cancel:
                raise _Stopped("STOP was pressed")
            t0 = time.monotonic()
            err, text, img = await self._tool(tool, args)
            if "Safety stop" in text:
                raise _Stopped("the mouse was in a screen corner (the emergency stop)")
            st = await self._page_until(run, wait, secs) if wait else (self.page.get(run) or {})
            ok, why = judge(err, text, img, st)
            r = {"key": key, "label": label, "tool": tool, "args": args, "ok": bool(ok), "why": why,
                 "tool_said": text[:900], "page": {k: v for k, v in st.items() if k != "run"},
                 "secs": round(time.monotonic() - t0, 1)}
            if not ok:
                r["screenshot"] = await self._shot(run, key)
            results.append(r)
            log.info("selftest %s %s: %s", key, "ok" if ok else "FAILED", why)
            return r

        def said(t: str, n: int = 300) -> str:
            return f" Tool said: {t[:n]}" if t else ""

        try:
            await step("eyes", "seeing the screen", "look_at_screen", {},
                       lambda e, t, i, s: (not e and i, "got a picture" if not e and i else f"no picture.{said(t)}"))
            r = await step("browser", "opening a web page", "open_url", {"url": url},
                           lambda e, t, i, s: (not e and bool(s.get("loaded")),
                                               "the test page opened and reported in" if s.get("loaded")
                                               else f"the page never reported in.{said(t)}"),
                           wait=lambda s: s.get("loaded"), secs=25)
            if not r["ok"]:
                return results, run                  # nothing else can be checked without the page
            await asyncio.sleep(2.5)                 # let it finish drawing

            def reading(e, t, i, s):
                ok = not e and "top marker" in _norm(t)
                return ok, "read the page's heading off the screen" if ok else f"didn't read the heading.{said(t)}"
            await step("read", "reading the screen", "read_screen_text", {}, reading)
            await step("click", "clicking by words", "click_text", {"text": "PRESS ME"},
                       lambda e, t, i, s: (s.get("clicks", 0) > 0,
                                           "the page felt the click" if s.get("clicks", 0) > 0
                                           else f"the page felt no click.{said(t)}"),
                       wait=lambda s: s.get("clicks", 0) > 0)
            await step("type", "typing", "type_text", {"text": TYPED, "into": "Type here for Jarvis", "enter": True},
                       lambda e, t, i, s: (s.get("typed") == TYPED and bool(s.get("enter")),
                                           "the page got the exact text and the Enter key"
                                           if s.get("typed") == TYPED and s.get("enter")
                                           else f"the page got {s.get('typed')!r}, Enter={bool(s.get('enter'))}.{said(t)}"),
                       wait=lambda s: s.get("typed") == TYPED and s.get("enter"), secs=6)
            await step("keys", "pressing keys", "press_keys", {"keys": "ctrl+a, backspace"},
                       lambda e, t, i, s: (s.get("typed") == TYPED and s.get("value") == "",
                                           "Ctrl+A then Backspace emptied the box"
                                           if s.get("typed") == TYPED and s.get("value") == ""
                                           else f"the box still holds {s.get('value')!r}.{said(t)}"),
                       wait=lambda s: s.get("value") == "", secs=5)
            await step("button", "clicking a button from Windows' list", "click_button", {"name": "SECOND BUTTON"},
                       lambda e, t, i, s: (s.get("second", 0) > 0,
                                           "the page felt the button click" if s.get("second", 0) > 0
                                           else f"the page felt no click.{said(t)}"),
                       wait=lambda s: s.get("second", 0) > 0)
            await step("scroll", "scrolling", "scroll", {"direction": "down", "amount": 10},
                       lambda e, t, i, s: (s.get("maxScroll", 0) > 0,
                                           f"the page scrolled down {s.get('maxScroll', 0)} pixels"
                                           if s.get("maxScroll", 0) > 0 else f"the page didn't move.{said(t)}"),
                       wait=lambda s: s.get("maxScroll", 0) > 0)

            def whole(e, t, i, s):
                n = _norm(t)
                top, bottom = "top marker" in n, "bottom marker" in n
                ok = not e and top and bottom
                return ok, ("read the whole page, top to bottom" if ok else
                            f"top of the page read: {top}, bottom read: {bottom}.{said(t, 400)}")
            await step("page", "reading a whole page", "read_whole_page",
                       {"start_after": "SELFTEST TOP MARKER ALPHA", "wait": False}, whole)

            def switched(e, t, i, s):
                front = tools._foreground()[1]
                ok = not e and "hands self-test" in front.lower()
                return ok, "the test page's window came to the front" if ok else f"the window in front is {front!r}.{said(t, 200)}"
            await step("switch", "switching windows", "focus_window", {"title": "Hands Self-Test"}, switched)
        except _Stopped as s:
            await self._tidy()
            await self._say(f"I stopped my self-check, sir: {s}.")
            return None, run
        await self._tidy()
        return results, run

    async def _tidy(self):
        """Close the test page's tab and bring Jarvis back to the front."""
        try:
            if "hands self-test" in tools._foreground()[1].lower():
                await self._tool("press_keys", {"keys": "ctrl+w"})
                await asyncio.sleep(1)
            await self._tool("focus_window", {"title": "Neural Link"})
        except Exception:
            log.exception("selftest: tidy-up")

    # ----------------------------------------------------------------------- report ----
    def _write_report(self, results: list, run: str, reason: str, extra: str = ""):
        lines = [f"# Jarvis self-check {run}", f"Why: {reason}", ""]
        for r in results:
            lines.append(f"- {'PASS' if r['ok'] else 'FAIL'} **{r['label']}** "
                         f"({r['tool']} {json.dumps(r['args'])}, {r['secs']}s): {r['why']}")
            if not r["ok"]:
                lines.append(f"  - tool said: {r['tool_said'][:600]!r}")
                lines.append(f"  - page reported: {json.dumps(r['page'])}")
                if r.get("screenshot"):
                    lines.append(f"  - screenshot: {r['screenshot']}")
        if extra:
            lines += ["", extra]
        DIR.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _history(self, line: str):
        DIR.mkdir(parents=True, exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M')} {line}\n")

    # ----------------------------------------------------------------- the repair ----
    async def _propose(self, results: list, run: str, reason: str):
        """Claude Opus prepares a fix on a copy; it's applied only if he presses ALLOW."""
        failed = [r for r in results if not r["ok"]]
        names = ", ".join(r["label"] for r in failed)
        plural = "s" if len(failed) > 1 else ""
        state = _read_json(STATE_FILE)
        if time.time() - float(state.get("last_failed_fix", 0)) < RETRY_AFTER:
            await self._say(f"Self-check found {len(failed)} problem{plural}, sir: {names}. A fix I applied in the "
                            "last few hours didn't hold, so I won't propose another yet. The report is ready "
                            "for your consultant.")
            return
        await self._say(f"Self-check found {len(failed)} problem{plural}, sir: {names}. I'm asking Claude Opus "
                        "to work out a fix. It takes a few minutes, and nothing changes until you've seen it.")
        work = DIR / f"repair-{run}"
        stage = self._stage(work)
        try:
            verdict = await asyncio.wait_for(self._mechanic(results, stage), 25 * 60)
        except asyncio.TimeoutError:
            verdict = {"fixed": False, "cause": "", "summary": "working out a fix took over 25 minutes"}
        except Exception as e:
            log.exception("selftest: mechanic")
            verdict = {"fixed": False, "cause": "", "summary": f"Claude couldn't work on it: {str(e)[:150]}"}
        changed = [n for n in EDITABLE if _sha(stage / n) != _sha(APP / n)]
        diff = self._diff(stage, changed)
        (work / "proposal.diff").write_text(diff, encoding="utf-8")
        self._write_report(results, run, reason,
                           f"## Fix proposed by Claude Opus\n- cause: {verdict['cause']}\n- summary: {verdict['summary']}\n"
                           f"- files: {', '.join(changed) or 'none'}\n- proposal: {work / 'proposal.diff'}")
        if not changed:
            self._history(f"no fix proposed: {verdict['cause'] or verdict['summary']}")
            await self._say("Claude looked, sir, but found nothing in my code to change. "
                            + (verdict["cause"] or verdict["summary"])[:220])
            return
        problem = await asyncio.to_thread(self._validate, stage, changed)
        if problem:
            self._history(f"fix rejected before showing it ({problem})")
            await self._say("Claude's fix didn't pass my safety checks, so I've thrown it away, sir. "
                            "The report is ready for your consultant.")
            return
        await self._say(f"Claude has a fix ready, sir: {verdict['summary'][:200]} It's on screen. Press ALLOW to "
                        "apply it, or DENY to leave my code as it is.")
        allowed = await self.approve({"cause": verdict["cause"], "summary": verdict["summary"],
                                      "files": changed, "diff": diff[:16000]})
        if not allowed:
            self._history("fix not applied (denied or no answer)")
            await self._say("I've left my code as it is, sir. The proposed fix is saved for your consultant.")
            return
        backup = self._backup(run)
        for n in changed:
            shutil.copy2(stage / n, APP / n)
        problem = await asyncio.to_thread(self._validate, APP, changed)
        if problem:
            self._restore(backup)
            self._history(f"fix applied but failed to load ({problem}); old code restored")
            await self._say("The fix wouldn't load, sir, so I put my old code straight back.")
            return
        _write_json(NOTE, {"stage": "verify", "run": run, "backup": str(backup), "changed": changed,
                           "failed": [r["label"] for r in failed], "summary": verdict["summary"],
                           "cause": verdict["cause"], "at": time.time()})
        self._history(f"fix APPLIED with his permission ({', '.join(changed)}): {verdict['summary']}")
        await self._say("Fix applied, sir. It takes effect when you restart me: close my black window and "
                        "click Start-Jarvis-Max. When I'm back I'll check everything again, and keep the fix "
                        "only if it all passes.")

    def _stage(self, work: Path) -> Path:
        """A working copy of the code for Claude to repair (the live code stays untouched)."""
        stage = work / "app"
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        (stage / "dock").mkdir(parents=True, exist_ok=True)
        for p in APP.glob("*.py"):
            shutil.copy2(p, stage / p.name)
        if (APP / "dock" / "selftest.html").exists():
            shutil.copy2(APP / "dock" / "selftest.html", stage / "dock" / "selftest.html")
        return stage

    def _diff(self, stage: Path, changed: list[str]) -> str:
        out = []
        for n in changed:
            a = (APP / n).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            b = (stage / n).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            out += difflib.unified_diff(a, b, fromfile=f"{n} (now)", tofile=f"{n} (with the fix)", n=2)
        return "".join(out)

    def _backup(self, run: str) -> Path:
        dest = DIR / f"backup-{run}"
        dest.mkdir(parents=True, exist_ok=True)
        for n in EDITABLE:
            if (APP / n).exists():
                shutil.copy2(APP / n, dest / n)
        return dest

    def _restore(self, backup) -> None:
        for n in EDITABLE:
            src = Path(backup) / n
            if src.exists():
                shutil.copy2(src, APP / n)
        log.info("selftest: old code restored from %s", backup)

    def _validate(self, folder: Path, changed: list[str]) -> str:
        """'' if the code in folder compiles and imports; otherwise what's wrong."""
        for n in changed:
            try:
                py_compile.compile(str(folder / n), cfile=str(DIR / "compiled.pyc"), doraise=True)
            except py_compile.PyCompileError as e:
                return f"{n} doesn't compile: {str(e)[:200]}"
        try:
            r = subprocess.run([sys.executable, "-c", "import common, web, tools; print(len(tools.ALL))"],
                               cwd=str(folder), capture_output=True, text=True, timeout=120, creationflags=_NOWIN)
        except Exception as e:
            return f"couldn't test the import: {e}"
        if r.returncode != 0:
            return "the code doesn't import: " + (r.stderr or r.stdout).strip()[-300:]
        return ""

    def _log_tail(self, n: int = 50) -> str:
        try:
            lines = (LOGS / "jarvis.log").read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except OSError:
            return ""
        keep = [l for l in lines if any(k in l for k in ("ERROR", "Traceback", "Error", "selftest", '  File "'))]
        return "\n".join(keep[-n:])

    async def _mechanic(self, results: list, stage: Path) -> dict:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                                      PermissionResultAllow, PermissionResultDeny, ResultMessage, TextBlock)
        stage = stage.resolve()
        editable = {(stage / n).resolve() for n in EDITABLE}
        readable = (stage, DIR.resolve())

        def where(inp) -> Path | None:
            try:
                p = Path(inp.get("file_path") or inp.get("path") or ".")
                return (p if p.is_absolute() else stage / p).resolve()
            except (OSError, ValueError):
                return None

        async def can_use(name, inp, ctx):
            p = where(inp)
            if name in ("Read", "Glob", "Grep", "LS"):
                if p and any(p == r or r in p.parents for r in readable):
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Only the working copy and the self-check folder can be read.")
            if name in ("Edit", "MultiEdit", "Write"):
                if p in editable:
                    return PermissionResultAllow()
                return PermissionResultDeny(message="Only tools.py, web.py and common.py in this folder may be edited.")
            if name == "TodoWrite":
                return PermissionResultAllow()
            return PermissionResultDeny(message="Not available here: read the code and edit tools.py, web.py or common.py.")

        brief = ["Self-check results (every check, in order):"]
        for r in results:
            brief.append(f"- {'PASS' if r['ok'] else 'FAIL'} [{r['key']}] {r['label']}: {r['tool']}"
                         f"({json.dumps(r['args'])}) -> {r['why']}")
            if not r["ok"]:
                brief.append(f"    tool's full reply: {r['tool_said']!r}")
                brief.append(f"    test page reported: {json.dumps(r['page'])}")
        shots = [r["screenshot"] for r in results if r.get("screenshot")]
        if shots:
            brief.append("\nScreenshots taken right after each failed check (open them with Read):")
            brief += [f"- {s}" for s in shots]
        tail = self._log_tail()
        if tail:
            brief.append("\nRecent errors from Jarvis's log:\n" + tail)
        brief.append("\nFind the cause and prepare the repair, following your rules. End with the one-line JSON.")
        opts = ClaudeAgentOptions(model="opus", cwd=str(stage), system_prompt=MECHANIC_RULES,
                                  permission_mode="default", can_use_tool=can_use, max_turns=80,
                                  setting_sources=[], add_dirs=[str(DIR.resolve())])
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
        """The new Jarvis after an applied fix: check again, keep or undo, and say so."""
        try:
            NOTE.unlink()
        except OSError:
            pass
        for _ in range(120):                    # the window reconnects by itself
            if self.windows():
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(4)
        if note.get("stage") == "restored":
            await self._say(note.get("message") or "I'm back on my old code, sir.")
            return
        if self.running:
            return
        self.running, self.cancel = True, False
        try:
            await self._say("I'm back with the fix, sir. Checking everything again; please leave the "
                            "mouse and keyboard alone for a minute.")
            await asyncio.sleep(7)
            results, run = await self.check()
            if results is None:
                self._history("check after the fix was stopped; the fix stays in place, untested")
                return
            failed = [r for r in results if not r["ok"]]
            self._write_report(results, run, "checking an applied fix",
                               f"## Fix being checked\n- cause: {note.get('cause')}\n- summary: {note.get('summary')}\n"
                               f"- files: {', '.join(note.get('changed') or [])}\n- backup: {note.get('backup')}")
            if not failed:
                self._history(f"fix CONFIRMED: all {len(results)} checks pass")
                st = _read_json(STATE_FILE)
                st.pop("last_failed_fix", None)
                _write_json(STATE_FILE, st)
                await self._say(f"Fix confirmed, sir: all {len(results)} checks pass now.")
                return
            names = ", ".join(r["label"] for r in failed)
            self._restore(note["backup"])
            _write_json(STATE_FILE, {**_read_json(STATE_FILE), "last_failed_fix": time.time()})
            self._history(f"fix did NOT hold ({names} still failing); old code restored")
            _write_json(NOTE, {"stage": "restored", "at": time.time(), "message":
                               "I'm back on my old code, sir. The report on the fix that didn't hold is "
                               "ready for your consultant."})
            await self._say(f"The fix didn't hold, sir: {names} still failed. I've put my old code back; "
                            "please restart me once more to load it.")
        finally:
            self.running = False


def _verdict(text: str) -> dict:
    for m in reversed(list(re.finditer(r"\{[^{}]*\"fixed\"[^{}]*\}", text or ""))):
        try:
            d = json.loads(m.group(0))
            return {"fixed": bool(d.get("fixed")), "cause": str(d.get("cause") or "")[:300],
                    "summary": str(d.get("summary") or "")[:300]}
        except ValueError:
            continue
    return {"fixed": False, "cause": "", "summary": (text or "").strip()[-300:]}
