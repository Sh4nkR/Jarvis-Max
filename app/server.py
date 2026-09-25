"""Jarvis: one local server that runs everything.

  http://127.0.0.1:8795/          the window: jaredrhod's face + the button dock
  /ws                             live link between the window and Jarvis
  /state, /config                 what the face reads (same contract as ai-visualizer)
  /api/...                        speech-to-text, status, memory notes

Only this PC can reach it (127.0.0.1), and only the Jarvis window itself may
talk to it: every live connection and every POST must come from this page.
Other websites open in your browser are refused.
"""
import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser

from aiohttp import WSMsgType, web

from common import (APP, BRAINS, CFG, IS_WIN, LOGS, MEMORY, ROOT, SWITCH_NOTE, brain_choice,
                    load_config, pretty_model, save_brain, save_secret, setup_logging)

log = setup_logging()

import tools                                    # noqa: E402

tools.dpi_aware()                               # before anything measures the screen
tools.STATE.hands = bool(CFG.get("hands_on_at_start", True))

import signin                                   # noqa: E402
from brain import Brain, describe               # noqa: E402
from ears import Ears                           # noqa: E402
from mouth import Mouth                         # noqa: E402
from local_brain import Hybrid, LocalBrain      # noqa: E402
from gemini_brain import KEY_FILE, GeminiBrain, pretty as gemini_pretty  # noqa: E402
import growth                                   # noqa: E402
import builder                                  # noqa: E402

PORT = int(CFG["port"])
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ORIGINS = {f"http://{h}" for h in HOSTS}
FACE_DIR = (APP / "face").resolve()
DOCK_DIR = (APP / "dock").resolve()
FACES = ["board", "radial", "rain", "neural"]
INJECT = ('<link rel="stylesheet" href="/dock/dock.css">\n'
          '<script src="/dock/dock.js"></script>\n')


# ------------------------------------------------------------------ hub ----
class Hub:
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.history: list[dict] = []
        self.perms: dict[str, asyncio.Future] = {}
        self.cams: dict[str, asyncio.Future] = {}
        self.state = "idle"

    async def send(self, obj: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(obj)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def remember(self, who: str, text: str):
        self.history.append({"who": who, "text": text, "t": time.time()})
        del self.history[:-200]


HUB = Hub()
EARS = Ears()
MOUTH = Mouth()


def local_name(cfg: dict | None = None) -> str:
    """qwen3.5:9b -> Qwen 3.5 9B"""
    m = re.match(r"([a-z]+)([\d.]*)(?::(\w+))?", ((cfg or load_config()).get("local_model") or "").lower())
    if not m:
        return "Qwen"
    return " ".join(x for x in (m.group(1).title(), m.group(2), (m.group(3) or "").upper()) if x)


def brain_name() -> str:
    """Short name for the BRAIN chip: OPUS 5.5, SONNET 5, LOCAL QWEN, AUTO · OPUS 5.5."""
    cfg = load_config()
    choice = brain_choice(cfg)
    claude = pretty_model(CLAUDE.model_id or cfg.get("model") or "").replace("Claude", "").strip().upper() or "CLAUDE"
    if choice == "local":
        return "LOCAL QWEN"
    if choice.startswith("gemini"):
        return gemini_pretty(GEMINI.model_id).upper() if GEMINI.model_id else "GEMINI"
    if choice == "auto":
        return "AUTO · " + ("LOCAL" if BRAIN.status == "local" else claude)
    return claude


def status() -> dict:
    return {"brain": BRAIN.status, "brain_error": BRAIN.error, "brain_name": brain_name(),
            "brain_choice": brain_choice(), "switching": SWITCH["choice"], "ears": EARS.status,
            "ears_error": EARS.error, "mouth": MOUTH.status, "hands": tools.STATE.hands,
            "screen_text": bool(tools.find_tesseract()), "name": CFG["name"],
            "call_me": CFG["call_me"], "face": CFG["face"]}


async def push_status():
    await HUB.send({"type": "status", "status": status()})


async def permission_gate(name: str, inp: dict) -> bool:
    """The brain wants to do something that needs Dr Wolf's yes."""
    if not HUB.clients:
        return False
    pid = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    HUB.perms[pid] = fut
    await HUB.send({"type": "permission", "id": pid, "tool": name, "detail": describe(name, inp)})
    audio = await MOUTH.synth("I need your permission for this one, sir. It's on screen.")
    await HUB.send({"type": "notice", "text": "Permission needed", "audio": _b64(audio)})
    try:
        return bool(await asyncio.wait_for(fut, 120))
    except asyncio.TimeoutError:
        await HUB.send({"type": "permission_closed", "id": pid})
        return False
    finally:
        HUB.perms.pop(pid, None)


async def camera_provider():
    """The brain asked for a camera picture; the window takes it."""
    if not HUB.clients:
        return None, "The Jarvis window isn't open."
    cid = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    HUB.cams[cid] = fut
    await HUB.send({"type": "camera_request", "id": cid})
    try:
        data, note = await asyncio.wait_for(fut, 20)
        return data, note
    except asyncio.TimeoutError:
        return None, "The camera didn't answer. Is it allowed in the Jarvis window?"
    finally:
        HUB.cams.pop(cid, None)


tools.STATE.camera_provider = camera_provider
CLAUDE = Brain(permission_gate)
CLAUDE.on_model = lambda: push_status()
LOCAL = LocalBrain(permission_gate)
GEMINI = GeminiBrain(permission_gate)
BRAIN = Hybrid(CLAUDE, LOCAL, GEMINI)           # routes each request to the brain he picked
TURNLOG = growth.TurnLog()                      # every request, for the daily learning
GROWTH = growth.Growth()
LAST_TURN = {"at": time.time()}
GOOD = re.compile(r"^\s*(good job|well done|perfect|great job|excellent|shabash|bahut badhiya|"
                  r"that'?s (right|correct|perfect)|exactly( right)?)\b", re.I)


def answered_by() -> str:
    cur = getattr(BRAIN, "current", None)
    if cur is GEMINI:
        return GEMINI.model_id or "gemini"
    if cur is LOCAL:
        return "local " + LOCAL.model
    return CLAUDE.model_id or "claude"


def _b64(b: bytes | None):
    return base64.b64encode(b).decode() if b else None


# ---------------------------------------------------------- conversation ----
class Conversation:
    def __init__(self):
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.cut = False                            # STOP pressed: drop unspoken sentences

    async def ask(self, text: str, images: list[dict]):
        if self.task and not self.task.done():
            await self.stop(quiet=True)             # a new question cuts the old answer off
        self.task = asyncio.create_task(self._turn(text, images))

    async def stop(self, quiet: bool = False):
        self.cut = True
        await BRAIN.interrupt()
        for f in list(HUB.perms.values()):
            if not f.done():
                f.set_result(False)
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 15)
            except (asyncio.TimeoutError, Exception):
                self.task.cancel()
        if not quiet:
            await HUB.send({"type": "stopped"})

    async def _turn(self, text: str, images: list[dict]):
        async with self.lock:
            self.cut = False
            HUB.remember("you", text + ("  [+ picture]" if images else ""))
            await HUB.send({"type": "turn_start", "text": text, "pictures": len(images)})
            q: asyncio.Queue = asyncio.Queue()
            speaker = asyncio.create_task(self._speaker(q))
            reply: list[str] = []
            used, errors = [], []                   # for the growth log
            if GOOD.search(text) and time.time() - TURNLOG.last_at < 600:
                TURNLOG.feedback(None, True, f"he said: {text[:200]}")
            try:
                async for ev in BRAIN.ask(text, images):
                    kind = ev[0]
                    if kind == "tool_result":
                        if used:
                            used[-1]["result"] = ev[2][:200]
                            if ev[3]:
                                used[-1]["error"] = ev[2][:150]
                        continue
                    if kind == "delta":
                        reply.append(ev[1])
                        await HUB.send({"type": "delta", "text": ev[1]})
                    elif kind == "sentence":
                        q.put_nowait((ev[1], asyncio.create_task(MOUTH.synth(ev[1]))))
                    elif kind == "tool":
                        used.append({"name": ev[1].replace(f"mcp__{tools.SERVER_NAME}__", ""),
                                     "args": json.dumps(ev[2], ensure_ascii=False)[:200]})
                        await HUB.send({"type": "tool", "name": ev[1], "detail": describe(ev[1], ev[2])})
                    elif kind == "error":
                        errors.append(ev[1][:200])
                        chunk = ("\n" if reply else "") + ev[1]
                        reply.append(chunk)
                        await HUB.send({"type": "delta", "text": chunk})
                        q.put_nowait((ev[1], asyncio.create_task(MOUTH.synth(ev[1]))))
                        await push_status()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("turn failed")
                await HUB.send({"type": "delta", "text": f"\nSomething broke: {e}"})
            finally:
                q.put_nowait(None)
                try:
                    await asyncio.wait_for(speaker, 120)
                except Exception:
                    speaker.cancel()
                HUB.remember("jarvis", "".join(reply).strip())
                LAST_TURN["at"] = time.time()
                try:
                    TURNLOG.record(answered_by(), text, "".join(reply).strip(), used, errors)
                except Exception:
                    log.exception("growth log: couldn't record the turn")
                await HUB.send({"type": "turn_end"})

    async def _speaker(self, q: asyncio.Queue):
        """Sends sentences to the window in order; each one's audio was made in parallel."""
        seq = 0
        while True:
            item = await q.get()
            if item is None:
                return
            text, task = item
            if self.cut:
                task.cancel()
                continue
            try:
                audio = await task
            except Exception:
                audio = None
            if self.cut:
                continue
            seq += 1
            await HUB.send({"type": "say", "seq": seq, "text": text, "audio": _b64(audio)})


CONVO = Conversation()


# ------------------------------------------------------- brain switching ----
# The BRAIN button (or switch_brain, when he asks by voice) saves the choice in jarvis.json,
# leaves a note, and restarts Jarvis. The new Jarvis reads the note, checks which brain
# really came up, and says so.
SWITCH = {"choice": None}
RESTART = {"go": False}
SHUTDOWN: asyncio.Event | None = None
HISTORY_FILE = LOGS / "history.json"


async def request_switch(choice: str, from_button: bool = False) -> str:
    choice = (choice or "").strip().lower()
    if choice == "claude":                      # "Claude" alone: the last Claude model he used
        m = (load_config().get("model") or "").lower()
        choice = "opus" if "opus" in m else "haiku" if "haiku" in m else "sonnet"
    choice = {"gemini lite": "gemini-lite", "flash-lite": "gemini-lite", "flash lite": "gemini-lite",
              "gemini flash-lite": "gemini-lite", "gemini flash lite": "gemini-lite"}.get(choice, choice)
    if choice not in BRAINS:
        return f"'{choice}' isn't a brain I know. The choices are {', '.join(BRAINS)}."
    if SWITCH["choice"]:
        return f"I'm already switching to {BRAINS[SWITCH['choice']]['label']}."
    await asyncio.to_thread(save_brain, choice)
    SWITCH["choice"] = choice
    label = BRAINS[choice]["label"]
    log.info("brain switch to %s (%s)", label, "button" if from_button else "asked by voice/text")
    await HUB.send({"type": "restarting", "label": label})
    await push_status()
    if from_button:
        await CONVO.stop(quiet=True)
    asyncio.create_task(_restart_brains(choice, wait_for_turn=not from_button))
    return (f"Saved: Jarvis will use {label}. Jarvis restarts its brain in a few seconds and "
            f"confirms the new one. Tell {CFG['call_me']} that in one short sentence.")


async def _restart_brains(choice: str, wait_for_turn: bool):
    """Restart both brains with the new setting, inside this same Jarvis (the window stays open),
    then check which brain really came up and say so."""
    if wait_for_turn:                           # let the brain finish its goodbye, and the window say it
        t0 = time.monotonic()
        while CONVO.task and not CONVO.task.done() and time.monotonic() - t0 < 45:
            await asyncio.sleep(0.3)
        await asyncio.sleep(4)
    log.info("restarting the brains for %s", BRAINS[choice]["label"])
    try:
        BRAIN._claude_back_at = 0.0             # a fresh start forgets "Claude's limit ran out"
        await asyncio.gather(start_brain(), LOCAL.restart(), GEMINI.restart(), return_exceptions=True)
    finally:
        SWITCH["choice"] = None
    await HUB.send({"type": "restarted"})
    await confirm_switch({"choice": choice}, [])


tools.STATE.brain_switcher = request_switch


def switch_report(choice: str) -> str:
    """What really came up after a switch, in one or two spoken sentences."""
    cfg = load_config()
    label = BRAINS[choice]["label"]
    now = brain_choice(cfg)
    if now != choice:
        return f"Sir, I restarted, but my settings say {BRAINS[now]['label']}, not {label}."
    claude = pretty_model(CLAUDE.model_id) if CLAUDE.model_id else ""
    if choice in ("sonnet", "opus", "haiku"):
        if CLAUDE.status == "ready" and claude:
            if choice in CLAUDE.model_id.lower():
                return f"Brain switch confirmed, sir. I'm running on {claude}."
            return f"Sir, I asked for {label}, but Claude Code is running {claude}."
        if CLAUDE.status == "ready":
            return "I'm back on Claude, sir, but I couldn't read which model it's running."
        if CLAUDE.status == "signin":
            return "I've switched to Claude, sir, but it needs you to sign in. Press SIGN IN on screen."
        return f"I tried to switch to {label}, sir, but Claude didn't start: {CLAUDE.error[:120]}"
    if choice.startswith("gemini"):
        if GEMINI.status == "ready":
            return f"Brain switch confirmed, sir. I'm on {gemini_pretty(GEMINI.model_id)}."
        if GEMINI.status == "needs_key":
            return ("I've switched to Gemini, sir, but I need your Gemini API key. "
                    "Paste it in the box on screen.")
        return f"I tried to switch to Gemini, sir, but it isn't ready: {GEMINI.error}."
    if choice == "local":
        if LOCAL.status == "ready":
            return f"Brain switch confirmed, sir. I'm on my local brain, {local_name(cfg)}."
        return f"I tried to switch to my local brain, sir, but it isn't ready: {LOCAL.error}."
    first = (claude or "Claude") + ("" if CLAUDE.status == "ready" else ", which isn't connected right now,")
    second = "my local brain" + ("" if LOCAL.status == "ready" else ", which isn't ready,")
    return f"Brain switch confirmed, sir. I'm on Auto: {first} first, then {second} when your Claude limit runs out."


async def announce(text: str):
    HUB.remember("jarvis", text)
    audio = await MOUTH.synth(text)
    await HUB.send({"type": "announce", "text": text, "audio": _b64(audio)})
    await push_status()


def read_switch_note() -> dict | None:
    try:
        note = json.loads(SWITCH_NOTE.read_text(encoding="utf-8"))
        SWITCH_NOTE.unlink()
    except (OSError, ValueError):
        return None
    if note.get("choice") not in BRAINS or time.time() - float(note.get("at", 0)) > 600:
        return None
    return note


async def confirm_switch(note: dict, starting: list):
    """After a switch-restart: wait for the brains to come up, then say which one really did."""
    try:
        await asyncio.wait_for(asyncio.gather(*starting, return_exceptions=True), 120)
    except asyncio.TimeoutError:
        pass
    if CLAUDE.status == "ready" and CLAUDE.model_check:
        try:
            await asyncio.wait_for(asyncio.shield(CLAUDE.model_check), 70)
        except Exception:
            pass
    await BRAIN.online(fresh=True)
    text = switch_report(note["choice"])
    log.info("switch check: %s", text)
    for _ in range(120):                        # wait (up to a minute) for the window to connect
        if HUB.clients:
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(1)
    await announce(text)


# ------------------------------------------------------------ websocket ----
async def ws_handler(request: web.Request):
    if request.headers.get("Origin") not in ORIGINS:
        return web.Response(status=403, text="Only the Jarvis window may connect.")
    if RESTART["go"]:                            # on the way out: the window should wait for the new one
        return web.Response(status=503, text="Jarvis is restarting.")
    ws = web.WebSocketResponse(max_msg_size=24 * 1024 * 1024, heartbeat=25)
    await ws.prepare(request)
    HUB.clients.add(ws)
    await ws.send_json({"type": "hello", "status": status(), "history": HUB.history[-60:]})
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            try:
                await on_client(data)
            except Exception as e:
                log.exception("window message failed")
                await ws.send_json({"type": "toast", "text": f"Error: {e}"})
    finally:
        HUB.clients.discard(ws)
    return ws


async def on_client(d: dict):
    t = d.get("type")
    if t == "ask":
        text = (d.get("text") or "").strip()
        images = [im for im in (d.get("images") or []) if isinstance(im, dict) and im.get("data")]
        if d.get("screen"):
            img, _ = await asyncio.to_thread(tools.grab_screen)
            b64, _ = await asyncio.to_thread(tools.to_jpeg_b64, img)
            images.append({"media_type": "image/jpeg", "data": b64})
            text = text or "Look at my screen. What's on it?"
        if text or images:
            await CONVO.ask(text or "What do you see in this picture?", images)
    elif t == "stop":
        await CONVO.stop()
    elif t == "hands":
        tools.STATE.hands = bool(d.get("on"))
        log.info("hands %s", "ON" if tools.STATE.hands else "OFF")
        await push_status()
    elif t == "permission_reply":
        f = HUB.perms.get(d.get("id"))
        if f and not f.done():
            f.set_result(bool(d.get("allow")))
    elif t == "camera_frame":
        f = HUB.cams.get(d.get("id"))
        if f and not f.done():
            f.set_result((d.get("data"), d.get("note") or ""))
    elif t == "signin":
        asyncio.create_task(do_signin())
    elif t == "new_session":
        await CONVO.stop(quiet=True)
        await BRAIN.restart()
        HUB.history.clear()
        await HUB.send({"type": "cleared"})
        await push_status()


async def do_signin():
    """Opens Claude's sign-in page, then waits (up to 5 minutes) for it to finish."""
    try:
        await asyncio.to_thread(signin.login, True)
    except Exception as e:
        await HUB.send({"type": "toast", "text": f"Couldn't open the sign-in: {e}"})
        return
    for _ in range(100):
        await asyncio.sleep(3)
        s = await asyncio.to_thread(signin.status)
        if s.get("loggedIn"):
            await start_brain()
            return
    await HUB.send({"type": "toast", "text": "Sign-in didn't finish. Press SIGN IN to try again."})


async def start_brain():
    CLAUDE.status = "starting"
    await push_status()
    s = await asyncio.to_thread(signin.status)
    if not s.get("loggedIn"):
        CLAUDE.status, CLAUDE.error = "signin", s.get("error", "")
        log.info("brain needs sign-in")
        await push_status()
        return
    try:
        await CLAUDE.stop()
        await CLAUDE.start()
    except Exception as e:
        CLAUDE.status, CLAUDE.error = "error", str(e)[:300]
        log.exception("brain failed to start")
    await BRAIN.online(fresh=True)
    await push_status()


async def start_local():
    try:
        await LOCAL.start()
    except Exception as e:
        LOCAL.status, LOCAL.error = "error", str(e)[:300]
        log.exception("local brain failed to start")
    await push_status()


async def start_gemini():
    try:
        await GEMINI.start()
    except Exception as e:
        GEMINI.status, GEMINI.error = "error", str(e)[:300]
        log.exception("gemini brain failed to start")
    await push_status()


async def api_feedback(request):
    """The GOOD / WRONG buttons: a verdict on Jarvis's last answer (and what was wrong, if typed)."""
    d = await request.json()
    good = bool(d.get("good"))
    ok = TURNLOG.feedback(None, good, str(d.get("note") or "")[:300])
    if not ok:
        return web.json_response({"ok": False, "message": "There's no answer to mark yet."})
    return web.json_response({"ok": True, "message": "Noted, sir. " + (
        "I'll keep doing it that way." if good else "I'll learn from that in tonight's lessons.")})


async def run_growth(spoken: bool):
    msg = await GROWTH.run()
    log.info("growth: %s", msg)
    if "done" in msg and CLAUDE.status == "ready" and not BRAIN.busy:
        await CLAUDE.restart()                  # Claude reads its lessons when it starts
    if spoken:
        await announce(msg)
    else:
        HUB.remember("jarvis", msg)
        await HUB.send({"type": "toast", "text": msg, "kind": "ok"})


async def api_grow(request):
    """LEARN NOW: run the daily learning straight away."""
    if GROWTH.running:
        return web.json_response({"ok": True, "message": "I'm already learning, sir."})
    asyncio.create_task(run_growth(spoken=True))
    return web.json_response({"ok": True, "message": "Learning now, sir. It takes a few minutes; I'll tell you when I'm done."})


async def learn_now() -> str:
    """start_lessons (asked by voice): same as the LEARN NOW button."""
    if GROWTH.running:
        return "Already learning; the result will be announced when it's done."
    asyncio.create_task(run_growth(spoken=True))
    return ("Learning started. Tell him you've gone to your lessons and will announce what you "
            "learned when you're back, in a few minutes.")


tools.STATE.learner = learn_now
builder.GATE = permission_gate
builder.GEMINI = GEMINI


async def growth_loop():
    """Once a day, when he's not using Jarvis: at night (1-6 am), or any idle time if it's overdue."""
    while True:
        await asyncio.sleep(30 * 60)
        try:
            idle = time.time() - LAST_TURN["at"] > 10 * 60 and not BRAIN.busy
            hour = time.localtime().tm_hour
            overdue = time.time() - float(growth._state().get("last_run", 0)) > 36 * 3600
            if idle and growth.due() and (1 <= hour < 6 or overdue) and not GROWTH.running:
                await run_growth(spoken=False)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("growth loop")


async def api_gemini_key(request):
    """The window's key box: save the key, check it with Google, and say how it went."""
    d = await request.json()
    key = (d.get("key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.\-]{20,200}", key):
        return web.json_response({"ok": False, "message": "That doesn't look like a Gemini API key."}, status=400)
    await asyncio.to_thread(save_secret, KEY_FILE, key)
    await start_gemini()
    ok = GEMINI.status == "ready"
    if ok and brain_choice().startswith("gemini"):
        asyncio.create_task(announce(f"Key accepted, sir. I'm on {gemini_pretty(GEMINI.model_id)}."))
    return web.json_response({"ok": ok, "message": "Key saved and working." if ok else
                              f"Google didn't accept it: {GEMINI.error}."}, status=200 if ok else 400)


async def start_ears():
    await asyncio.to_thread(EARS.load)
    await push_status()


# ----------------------------------------------------------------- http ----
@web.middleware
async def guard(request: web.Request, handler):
    if request.host not in HOSTS:                       # blocks DNS-rebinding tricks
        return web.Response(status=403, text="forbidden")
    if request.method != "GET" and request.headers.get("Origin") not in ORIGINS:
        return web.Response(status=403, text="Only the Jarvis window may do that.")
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _safe(base, tail: str):
    p = (base / tail).resolve()
    if p != base and base not in p.parents:
        raise web.HTTPNotFound()
    if p.is_dir():
        p = p / "index.html"
    if not p.is_file():
        raise web.HTTPNotFound()
    return p


async def root(request):
    face = CFG["face"] if CFG["face"] in FACES else "board"
    raise web.HTTPFound(f"/face/faces/{face}/index.html")


async def face_file(request):
    p = _safe(FACE_DIR, request.match_info["tail"])
    if p.name == "index.html" and p.parent.parent.name == "faces":
        html = p.read_text(encoding="utf-8").replace("<head>", "<head>\n" + INJECT, 1)
        return web.Response(text=html, content_type="text/html")
    return web.FileResponse(p)


async def dock_file(request):
    return web.FileResponse(_safe(DOCK_DIR, request.match_info["tail"]))


async def face_state(request):
    return web.json_response({"state": HUB.state, "level": 0, "samples": None,
                              "alert": False, "loading": False})


async def face_config(request):
    faces = []
    for f in FACES:
        meta = {"id": f, "title": f.title(), "tagline": ""}
        try:
            meta.update(json.loads((FACE_DIR / "faces" / f / "face.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        faces.append(meta)
    return web.json_response({"name": CFG["name"], "badge": "", "face": CFG["face"],
                              "thinking_sound": True, "faces": faces})


async def api_ping(request):
    return web.json_response({"jarvis": True, "name": CFG["name"]})


async def api_status(request):
    return web.json_response(status())


async def api_listen(request):
    audio = await request.read()
    if len(audio) < 1500:
        return web.json_response({"text": "", "note": "too short"})
    try:
        text = await asyncio.to_thread(EARS.transcribe, audio)
    except Exception as e:
        return web.json_response({"text": "", "note": str(e)}, status=503)
    log.info("heard: %s", text)
    return web.json_response({"text": text})


async def api_face(request):
    d = await request.json()
    face = d.get("face")
    if face not in FACES:
        raise web.HTTPBadRequest()
    CFG["face"] = face
    try:
        path = APP / "jarvis.json"
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["face"] = face
        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError):
        pass
    return web.json_response({"face": face})


async def api_brain(request):
    """GET: the choices and the current one. POST {"choice": ...}: switch and restart."""
    if request.method == "POST":
        d = await request.json()
        msg = await request_switch(d.get("choice", ""), from_button=True)
        ok = SWITCH["choice"] is not None
        return web.json_response({"ok": ok, "message": msg}, status=200 if ok else 400)
    return web.json_response({"current": brain_choice(), "name": brain_name(),
                              "choices": [{"id": k, "label": v["label"], "about": v["about"]}
                                          for k, v in BRAINS.items()]})


async def api_memory(request):
    out = []
    for p in sorted(MEMORY.rglob("*.md")):
        rel = p.relative_to(MEMORY).as_posix()
        if rel.startswith("."):
            continue
        st = p.stat()
        out.append({"path": rel, "size": st.st_size, "modified": st.st_mtime})
    out.sort(key=lambda n: -n["modified"])
    return web.json_response(out)


async def api_note(request):
    p = _safe(MEMORY.resolve(), request.query.get("path", ""))
    if p.suffix.lower() != ".md":
        raise web.HTTPNotFound()
    return web.json_response({"path": p.relative_to(MEMORY.resolve()).as_posix(),
                              "text": p.read_text(encoding="utf-8", errors="replace")})


def build_app() -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=32 * 1024 * 1024)
    app.router.add_get("/", root)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/state", face_state)
    app.router.add_get("/config", face_config)
    app.router.add_get("/face/{tail:.*}", face_file)
    app.router.add_get("/dock/{tail:.*}", dock_file)
    app.router.add_get("/api/ping", api_ping)
    app.router.add_get("/api/status", api_status)
    app.router.add_post("/api/listen", api_listen)
    app.router.add_post("/api/face", api_face)
    app.router.add_get("/api/brain", api_brain)
    app.router.add_post("/api/brain", api_brain)
    app.router.add_post("/api/gemini_key", api_gemini_key)
    app.router.add_post("/api/feedback", api_feedback)
    app.router.add_post("/api/grow", api_grow)
    app.router.add_get("/api/memory", api_memory)
    app.router.add_get("/api/memory/note", api_note)
    return app


# ----------------------------------------------------------------- main ----
URL = f"http://127.0.0.1:{PORT}/"


def open_window():
    if not CFG.get("open_window", True):
        return
    if IS_WIN:
        for exe in (shutil.which("msedge"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")):
            if exe and os.path.exists(exe):
                subprocess.Popen([exe, f"--app={URL}", "--start-maximized"])
                return
    webbrowser.open(URL)


def already_running() -> bool:
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=2) as r:
            return json.loads(r.read()).get("jarvis") is True
    except Exception:
        return False


async def _window_unless_back():
    """After a restart the old window reconnects by itself; open one only if it doesn't."""
    for _ in range(20):
        await asyncio.sleep(0.5)
        if HUB.clients:
            return
    open_window()


async def serve(note: dict | None):
    global SHUTDOWN
    SHUTDOWN = asyncio.Event()
    runner = web.AppRunner(build_app(), access_log=None, shutdown_timeout=3)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    print()
    print(f"  {CFG['name']} is running at {URL}")
    print("  Keep this window open. Closing it switches Jarvis off.")
    print()
    bg = [asyncio.create_task(start_ears()), asyncio.create_task(start_brain()),
          asyncio.create_task(start_local()), asyncio.create_task(start_gemini()),
          asyncio.create_task(growth_loop())]
    if note:
        try:
            HUB.history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))[-200:]
        except (OSError, ValueError):
            pass
        bg.append(asyncio.create_task(_window_unless_back()))
        bg.append(asyncio.create_task(confirm_switch(note, bg[1:3])))
    else:
        open_window()
    try:
        await SHUTDOWN.wait()
    finally:
        for t in bg:
            t.cancel()
        if RESTART["go"]:
            log.info("restarting: closing down")
            try:
                HISTORY_FILE.write_text(json.dumps(HUB.history[-200:]), encoding="utf-8")
            except OSError:
                pass
        for ws in list(HUB.clients):             # the window reconnects to the new Jarvis by itself
            try:
                await asyncio.wait_for(ws.close(code=1012, message=b"restarting"), 2)
            except Exception:
                pass
        await BRAIN.stop()
        await runner.cleanup()
        if RESTART["go"]:
            log.info("restarting: closed")


def relaunch():
    """Start a fresh Jarvis exactly the way he does (Explorer opens the .bat in its own window),
    so the new Jarvis doesn't belong to this one's window or process tree. Then this one ends."""
    bat = next((ROOT / n for n in ("Start-Jarvis-Max.bat", "Start-Jarvis-Assistant.bat")
                if (ROOT / n).exists()), ROOT / "Start-Jarvis-Max.bat")
    if IS_WIN and bat.exists():
        try:
            subprocess.Popen(["explorer.exe", str(bat)])
            log.info("restart: asked Explorer to start %s", bat.name)
            return
        except OSError as e:
            log.warning("restart via Explorer failed (%s); starting it directly", e)
        flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(ROOT), creationflags=flags)
        return
    subprocess.Popen([sys.executable, str(APP / "server.py")], cwd=str(APP), start_new_session=True)


def watch_console_close():
    """Windows: note in the log if this window is being closed (helps explain a sudden stop)."""
    if not IS_WIN:
        return
    import ctypes
    names = {0: "Ctrl+C", 1: "Ctrl+Break", 2: "window closed", 5: "log-off", 6: "shut-down"}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    def handler(event):
        try:
            log.warning("console event: %s (pid %s)", names.get(event, event), os.getpid())
        except Exception:
            pass
        return False                             # let Windows carry on as normal

    watch_console_close.keep = handler           # keep it alive
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def main():
    watch_console_close()
    log.info("starting (pid %s, parent %s)", os.getpid(), os.getppid())
    seed = ROOT / "memory-template"
    if not (MEMORY / "CLAUDE.md").exists() and seed.is_dir():
        shutil.copytree(seed, MEMORY, dirs_exist_ok=True)      # first start: a fresh memory vault
        log.info("first start: memory vault created from memory-template")
    note = read_switch_note()
    if note:                                     # a brain switch: the old Jarvis is on its way out
        for _ in range(40):
            if not already_running():
                break
            time.sleep(0.5)
    if already_running():
        print("  Jarvis is already running. Opening its window.")
        open_window()
        return
    try:
        asyncio.run(serve(note))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        print(f"\n  Jarvis couldn't start: port {PORT} is in use by another program ({e}).")
        print("  Change \"port\" in app\\jarvis.json, or restart the PC.")
        sys.exit(1)
    if RESTART["go"]:
        print(f"  Restarting {CFG['name']} with the new brain...")
        relaunch()


if __name__ == "__main__":
    main()
