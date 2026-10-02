"""Jarvis: one local server that runs everything.

  http://127.0.0.1:8795/          the window: jaredrhod's face + the button dock
  /ws                             live link between the window and Jarvis
  /state, /config                 what the face reads (same contract as ai-visualizer)
  /api/...                        speech-to-text, status, memory notes

This PC and his phone on the same home Wi-Fi can open it ("phone_access" in jarvis.json).
Only Jarvis's own page may talk to it: every live connection and every POST must come from
that page. Other websites open in a browser are refused.
"""
import asyncio
import base64
import ipaddress
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path

from aiohttp import WSMsgType, web

from common import (APP, BRAINS, CFG, IS_WIN, LOGS, MEMORY, ROOT, SECRETS, SWITCH_NOTE, brain_choice,
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
import selftest                                 # noqa: E402
import phonehands                               # noqa: E402
import behaviour                                # noqa: E402

PORT = int(CFG["port"])
HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
ORIGINS = {f"http://{h}" for h in HOSTS}
PHONE = bool(CFG.get("phone_access", True))        # his phone on the same Wi-Fi may open Jarvis too
TLS_PORT = PORT + 1                                # the Jarvis Hands app connects here over HTTPS
FACE_DIR = (APP / "face").resolve()
DOCK_DIR = (APP / "dock").resolve()
FACES = ["living", "lotus", "board", "radial", "rain", "neural"]
INJECT = ('<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, '
          'viewport-fit=cover, interactive-widget=resizes-content">\n'
          '<meta name="theme-color" content="#020705">\n'
          '<link rel="manifest" href="/dock/manifest.json">\n'
          '<link rel="stylesheet" href="/dock/dock.css">\n'
          '<script src="/dock/dock.js"></script>\n')


def host_ok(host: str) -> bool:
    """Jarvis answers only to his own address: this PC, or a home-network address typed as numbers
    (the phone). A website's name made to point at Jarvis (the DNS-rebinding trick) never matches."""
    if host in HOSTS:
        return True
    if not PHONE:
        return False
    ip, _, port = (host or "").rpartition(":")
    if port != str(PORT):
        return False
    try:
        return ipaddress.ip_address(ip).version == 4 and ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def origin_ok(request) -> bool:
    """Only Jarvis's own page (on the PC or the phone) may connect or change things."""
    o = request.headers.get("Origin")
    if o is None:
        return False
    return o in ORIGINS or o == f"http://{request.host}" or o == f"https://{request.host}"


def lan_ip() -> str | None:
    """This PC's address on the home Wi-Fi: the one his phone opens."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))                  # only picks the route; nothing is sent
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    a = ipaddress.ip_address(ip)
    return ip if a.is_private and not a.is_loopback else None


def _tls_context():
    """A self-signed certificate (made once, kept in secrets/, never pushed) so the phone app can
    reach Jarvis over HTTPS. A browser's microphone only works on an https page; the app trusts
    this one certificate for the PC's own address. Nothing leaves the home Wi-Fi; no password is
    ever sent over it."""
    crt, key = SECRETS / "jarvis-lan.crt", SECRETS / "jarvis-lan.key"
    try:
        if not (crt.exists() and key.exists()):
            _make_cert(crt, key)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(crt), str(key))
        return ctx
    except Exception:
        log.exception("couldn't set up the phone's HTTPS certificate")
        return None


def _make_cert(crt, key):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    SECRETS.mkdir(parents=True, exist_ok=True)
    (SECRETS / ".gitignore").write_text("*\n", encoding="utf-8")
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis on this PC")])
    now = datetime.datetime.now(datetime.timezone.utc)
    alts = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    ip = lan_ip()
    if ip:
        alts.append(x509.IPAddress(ipaddress.ip_address(ip)))
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(k.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(alts), critical=False)
            .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def network_kind() -> str:
    """Private or Public: Windows keeps phones out of a Wi-Fi it calls Public."""
    if not IS_WIN:
        return ""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-NetConnectionProfile | Select-Object -First 1).NetworkCategory"],
                           capture_output=True, text=True, timeout=20, creationflags=0x08000000)
        return r.stdout.strip()
    except Exception:
        return ""


def keep_awake():
    """While Jarvis runs, the PC doesn't go to sleep (the screen may still switch off), so the
    phone can always reach him. Windows undoes it by itself the moment Jarvis closes."""
    if IS_WIN and CFG.get("keep_awake", True):
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        log.info("keeping the PC awake while Jarvis runs")


PHONE_URL = ""


# ------------------------------------------------------------------ hub ----
class Hub:
    def __init__(self):
        self.clients: set[web.WebSocketResponse] = set()
        self.ids: dict[web.WebSocketResponse, str] = {}
        self.where: dict[web.WebSocketResponse, str] = {}
        self.active: web.WebSocketResponse | None = None   # the screen he last asked from: it speaks
        self.history: list[dict] = []
        self.perms: dict[str, asyncio.Future] = {}
        self.cams: dict[str, asyncio.Future] = {}
        self.repairs: dict[str, asyncio.Future] = {}
        self.pending_repair: dict | None = None     # a proposed fix waiting for his ALLOW / DENY
        self.state = "idle"

    async def send(self, obj: dict, only: web.WebSocketResponse | None = None):
        dead = []
        for ws in list(self.clients):
            if only is not None and ws is not only:
                continue
            try:
                await ws.send_json(obj)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def speaker(self) -> web.WebSocketResponse | None:
        return self.active if self.active in self.clients else None

    async def send_voice(self, obj: dict):
        """Jarvis's voice plays on the screen he's using (PC or phone); the others show the
        words silently, so two speakers never talk over each other."""
        target, dead = self.speaker(), []
        for ws in list(self.clients):
            o = obj if target is None or ws is target else {**obj, "audio": None, "silent": True}
            try:
                await ws.send_json(o)
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
tools.STATE.voice_switcher = MOUTH.set_style


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


# Dr Wolf's wish: one ALLOW per skill, not one per step. A yes covers the same kind of action
# for the rest of that job: a whole build plan (per project folder), or the rest of the current
# request for anything else. STOP and a fresh conversation take every grant back.
GRANTS: dict[str, float] = {}               # key -> expires at
GRANT_TTL = {"project": 3 * 3600, "turn": 15 * 60}
TURN = {"n": 0}


def _grant_key(name: str, scope: str | None) -> tuple[str, int]:
    if scope:                               # a plan's file-writing and command steps share one yes
        kind = "steps" if name in ("project_write", "run_command") else name
        return f"{scope}|{kind}", GRANT_TTL["project"]
    return f"turn{TURN['n']}|{name}", GRANT_TTL["turn"]


def clear_grants():
    GRANTS.clear()


async def permission_gate(name: str, inp: dict, scope: str | None = None) -> bool:
    """The brain wants to do something that needs Dr Wolf's yes."""
    key, ttl = _grant_key(name, scope)
    if GRANTS.get(key, 0) > time.time():
        log.info("permission: %s already allowed for this job", name)
        return True
    if not HUB.clients:
        return False
    pid = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    HUB.perms[pid] = fut
    covers = "the whole build plan" if scope else "the rest of this request"
    detail = f"{describe(name, inp)}\n\n(ALLOW covers this kind of step for {covers}.)"
    await HUB.send({"type": "permission", "id": pid, "tool": name, "detail": detail})
    await BEHAVE.allow(True, describe(name, inp))
    audio = await MOUTH.synth("I need your permission for this one, sir. It's on screen.")
    await HUB.send_voice({"type": "notice", "text": "Permission needed", "audio": _b64(audio)})
    try:
        ok = bool(await asyncio.wait_for(fut, 120))
        await BEHAVE.allow(False, allowed=ok)
        if ok:
            GRANTS[key] = time.time() + ttl
        return ok
    except asyncio.TimeoutError:
        await HUB.send({"type": "permission_closed", "id": pid})
        await BEHAVE.allow(False, allowed=False)
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
    target = HUB.speaker()                   # the screen he's using: on the phone, its front camera
    await HUB.send({"type": "camera_request", "id": cid}, only=target)
    try:
        data, note = await asyncio.wait_for(fut, 20)
        if data:
            note = ("his phone's front camera" if HUB.where.get(target, "").startswith("phone")
                    else "the PC's camera" if target is not None else "the Jarvis window's camera")
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
TURNLOG = growth.TurnLog()
BEHAVE = behaviour.Behaviour(lambda ev: HUB.send(ev), LOGS)   # the observable behaviour layer (TANTRA-06)
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
            TURN["n"] += 1                          # per-request permission grants start afresh
            HUB.remember("you", text + ("  [+ picture]" if images else ""))
            await HUB.send({"type": "turn_start", "text": text, "pictures": len(images),
                            "speaker": HUB.ids.get(HUB.speaker())})
            await BEHAVE.turn_start(text, answered_by())
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
                        await BEHAVE.tool_result(ev[1], bool(ev[3]) if len(ev) > 3 else False, ev[2])
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
                        await BEHAVE.tool(ev[1], describe(ev[1], ev[2]))
                    elif kind == "error":
                        errors.append(ev[1][:200])
                        await BEHAVE.error(ev[1])
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
                await BEHAVE.turn_end(len(errors))
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
            await HUB.send_voice({"type": "say", "seq": seq, "text": text, "audio": _b64(audio)})


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
    await HUB.send_voice({"type": "announce", "text": text, "audio": _b64(audio)})
    await push_status()


async def log_note(text: str):
    """A silent entry in his LOG panel (links in it are clickable)."""
    HUB.remember("jarvis", text)
    await HUB.send({"type": "log_note", "text": text})


import web as websearch                        # noqa: E402  (aiohttp's `web` is taken)
websearch.LOG_HOOK = log_note


# Every switch to Claude Opus (and "check yourself") runs the self-check of eyes and hands.
async def repair_gate(proposal: dict) -> bool:
    """A fix Claude prepared goes on screen (PC and phone). Nothing changes unless he presses ALLOW;
    DENY or no answer in 15 minutes leaves the code as it is."""
    rid = uuid.uuid4().hex
    fut = asyncio.get_running_loop().create_future()
    HUB.repairs[rid] = fut
    HUB.pending_repair = {"type": "repair", "id": rid, **proposal}
    await HUB.send(HUB.pending_repair)
    try:
        return bool(await asyncio.wait_for(fut, 15 * 60))
    except asyncio.TimeoutError:
        return False
    finally:
        HUB.repairs.pop(rid, None)
        HUB.pending_repair = None
        await HUB.send({"type": "repair_closed", "id": rid})


SELFTEST = selftest.SelfTest()
SELFTEST.announce = announce
SELFTEST.approve = repair_gate
SELFTEST.windows = lambda: len(HUB.clients)
SELFTEST.claude_ok = lambda: CLAUDE.status == "ready"
tools.STATE.self_tester = lambda: SELFTEST.start("he asked for a self-check")
PHONE = phonehands.PhoneHands()                 # his hands on the phone (the Jarvis Hands app)
tools.STATE.phone = PHONE


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
    if note["choice"] == "opus" and CLAUDE.status == "ready":
        await asyncio.sleep(5)                  # let the confirmation finish playing
        SELFTEST.start("he switched Jarvis to Claude Opus")


# ------------------------------------------------------------ websocket ----
async def ws_handler(request: web.Request):
    if not origin_ok(request):
        log.warning("refused a live link from %s (page %s, address %s)", request.remote,
                    request.headers.get("Origin"), request.host)
        return web.Response(status=403, text="Only the Jarvis window may connect.")
    if RESTART["go"]:                            # on the way out: the window should wait for the new one
        return web.Response(status=503, text="Jarvis is restarting.")
    # compress=False: with compression on, aiohttp 3.14 drops the link when a browser's first
    # message after a heartbeat reply is compressed ("non-zero reserved bits"), losing that
    # message (often his first question). Everything here is local, so compression isn't needed.
    ws = web.WebSocketResponse(max_msg_size=24 * 1024 * 1024, heartbeat=25, compress=False)
    await ws.prepare(request)
    HUB.clients.add(ws)
    HUB.ids[ws] = uuid.uuid4().hex[:8]
    HUB.where[ws] = "this PC" if request.remote in ("127.0.0.1", "::1") else f"phone/other device {request.remote}"
    log.info("window connected: %s", HUB.where[ws])
    if HUB.where[ws] == "this PC":
        asyncio.create_task(_maximize_soon())
    await ws.send_json({"type": "hello", "status": status(), "history": HUB.history[-60:],
                        "you": HUB.ids[ws], "phone_url": PHONE_URL})
    if HUB.pending_repair:                         # a fix still waiting for his answer
        await ws.send_json(HUB.pending_repair)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except ValueError:
                continue
            try:
                await on_client(data, ws)
            except Exception as e:
                log.exception("window message failed")
                await ws.send_json({"type": "toast", "text": f"Error: {e}"})
    finally:
        HUB.clients.discard(ws)
        HUB.ids.pop(ws, None)
        err = ws.exception()
        log.info("window closed: %s (code %s%s)", HUB.where.pop(ws, "?"), ws.close_code,
                 f", {type(err).__name__}: {err}" if err else "")
        if HUB.active is ws:
            HUB.active = None
    return ws


async def on_client(d: dict, ws: web.WebSocketResponse | None = None):
    t = d.get("type")
    if t == "ask":
        if ws is not None:
            if HUB.active is not ws:
                log.info("he's asking from %s", HUB.where.get(ws, "?"))
            HUB.active = ws
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
        clear_grants()                          # STOP takes back every "allowed for this job"
        SELFTEST.stop()
        await CONVO.stop()
    elif t == "hands":
        tools.STATE.hands = bool(d.get("on"))
        log.info("hands %s", "ON" if tools.STATE.hands else "OFF")
        await push_status()
    elif t == "permission_reply":
        f = HUB.perms.get(d.get("id"))
        if f and not f.done():
            f.set_result(bool(d.get("allow")))
    elif t == "repair_reply":
        f = HUB.repairs.get(d.get("id"))
        if f and not f.done():
            log.info("proposed fix: he pressed %s", "ALLOW" if d.get("allow") else "DENY")
            f.set_result(bool(d.get("allow")))
    elif t == "camera_frame":
        f = HUB.cams.get(d.get("id"))
        if f and not f.done():
            f.set_result((d.get("data"), d.get("note") or ""))
    elif t == "open_link":                     # a link clicked in the LOG panel
        url = str(d.get("url") or "")
        if re.match(r"^https?://[^\s\"'<>]+$", url):
            await asyncio.to_thread(webbrowser.open_new_tab, url)
    elif t == "signin":
        asyncio.create_task(do_signin())
    elif t == "new_session":
        clear_grants()
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
    if "done" in msg:
        sc = re.search(r"(\d+) out of (\d+)", msg)
        await BEHAVE.lessons_done(f"{sc.group(1)}/{sc.group(2)}" if sc else "")
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
async def project_gate(name: str, inp: dict) -> bool:
    """Build-plan steps: one ALLOW covers every step of that project."""
    folder = str(inp.get("folder") or "")
    return await permission_gate(name, inp, scope=f"project:{folder}" if folder else None)


builder.GATE = project_gate
builder.GEMINI = GEMINI


# ------------------------------------------------------------ test bench ----
# With "test_bench": true in jarvis.json, a list of eyes/hands tool calls dropped into
# logs/tmp/bench-request.json is run on the real screen, and what happened (each tool's reply, a
# screenshot after each step, which window had the keyboard) is written to logs/tmp/bench/.
# It lets the hands be tested properly without anyone typing into Jarvis.
BENCH_TOOLS = {"run_self_test", "read_whole_page", "look_at_screen", "see_buttons", "click_button", "look_closer", "read_screen_text",
               "click_text", "click_at", "type_text", "press_keys", "scroll", "open_app", "open_url",
               "list_windows", "focus_window", "android_build", "github_build", "github_build_status", "github_publish", "samasa"}


async def bench_loop():
    req, out = LOGS / "tmp" / "bench-request.json", LOGS / "tmp" / "bench"
    while True:
        await asyncio.sleep(2)
        try:
            if not req.exists() or not load_config().get("test_bench"):
                continue
            plan = json.loads(req.read_text(encoding="utf-8"))
            req.unlink()
            out.mkdir(parents=True, exist_ok=True)
            by_name = {t.name: t for t in tools.ALL}
            results = []
            for i, st in enumerate(plan.get("steps", []), 1):
                name, args = st.get("tool", ""), st.get("args") or {}
                t0 = time.time()
                if name == "sleep":
                    await asyncio.sleep(float(args.get("seconds", 1)))
                    results.append({"step": i, "tool": name, "args": args})
                    continue
                if name not in BENCH_TOOLS:
                    results.append({"step": i, "tool": name, "text": ["not allowed on the bench"], "error": True})
                    continue
                try:
                    res = await by_name[name].handler(args)
                except Exception as e:
                    res = {"content": [{"type": "text", "text": f"EXCEPTION: {e!r}"}], "is_error": True}
                content = res.get("content", [])
                for j, c in enumerate(x for x in content if x.get("type") == "image"):
                    (out / f"step{i}-tool{j}.jpg").write_bytes(base64.b64decode(c["data"]))
                img, _ = await asyncio.to_thread(tools.grab_screen)
                shot, _ = await asyncio.to_thread(tools.to_jpeg_b64, img, 1600, 70)
                (out / f"step{i}-after.jpg").write_bytes(base64.b64decode(shot))
                results.append({"step": i, "tool": name, "args": args,
                                "text": [c.get("text", "") for c in content if c.get("type") == "text"],
                                "error": bool(res.get("is_error")), "secs": round(time.time() - t0, 1),
                                "keyboard_on": tools._foreground()[1] if IS_WIN else ""})
                log.info("bench step %d %s -> %s", i, name, (results[-1]["text"] or [""])[0][:160])
            (out / "result.json").write_text(json.dumps({"id": plan.get("id"), "finished": time.time(),
                                                          "results": results}, indent=1), encoding="utf-8")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("test bench")


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


async def api_phone_pull(request):
    """The phone app asks for the next action to do (long-poll)."""
    return web.json_response(await PHONE.pull())


async def api_phone_result(request):
    """The phone app reports what happened."""
    try:
        PHONE.result(await request.json())
    except ValueError:
        raise web.HTTPBadRequest()
    return web.json_response({"ok": True})


async def api_selftest_report(request):
    """The self-check page tells Jarvis what really happened on it (clicks, typing, scrolling)."""
    try:
        SELFTEST.page_report(await request.json())
    except ValueError:
        raise web.HTTPBadRequest()
    return web.json_response({"ok": True})


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
    if not host_ok(request.host):                       # blocks DNS-rebinding tricks
        log.warning("refused %s %s from %s: address %r isn't Jarvis's", request.method, request.path,
                    request.remote, request.host)
        return web.Response(status=403, text="forbidden")
    if request.method != "GET" and not origin_ok(request):
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
                              "thinking_sound": False, "faces": faces})


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


UPLOAD_OK = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".pdf", ".txt", ".md", ".csv", ".json",
             ".html", ".htm", ".log", ".xml"}


async def api_upload(request):
    """The + button: one file from the window, kept in the memory vault's inbox for the brain to read."""
    name = Path(request.query.get("name") or "file").name
    safe = re.sub(r"[^\w.\- ]+", "_", name).strip(" .")[:80] or "file"
    ext = Path(safe).suffix.lower()
    if ext not in UPLOAD_OK:
        return web.json_response({"ok": False, "message": f"I can't read {ext or 'that kind of'} files yet, sir. "
                                  "Pictures, PDFs and text files work."})
    data = await request.read()
    if not data:
        return web.json_response({"ok": False, "message": f"{safe} was empty."})
    day = MEMORY / "inbox" / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    p, n = day / safe, 1
    while p.exists():
        n += 1
        p = day / f"{Path(safe).stem}-{n}{ext}"
    p.write_bytes(data)
    log.info("he added a file: %s (%d KB)", p.name, len(data) // 1024)
    return web.json_response({"ok": True, "path": p.relative_to(MEMORY).as_posix(), "name": p.name})


async def api_behaviour(request):
    """The behaviour layer: vitals + the latest events (the journal)."""
    return web.json_response(BEHAVE.snapshot())


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
    app.router.add_post("/api/upload", api_upload)
    app.router.add_get("/api/behaviour", api_behaviour)
    app.router.add_post("/api/grow", api_grow)
    app.router.add_post("/api/selftest/report", api_selftest_report)
    app.router.add_get("/api/phone/pull", api_phone_pull)
    app.router.add_post("/api/phone/result", api_phone_result)
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


def maximize_window() -> bool:
    """Edge ignores --start-maximized when it is already running (the new process hands the
    window to the old one, which reuses its last size), so the window can come up small in a
    corner. Find the Jarvis window ourselves and maximize it. True once it's done."""
    if not IS_WIN:
        return True
    import ctypes
    from ctypes import wintypes
    u32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        if u32.IsWindowVisible(h):
            cls = ctypes.create_unicode_buffer(120)
            u32.GetClassNameW(h, cls, 120)
            t = tools._title(h)
            if cls.value.startswith("Chrome_WidgetWin") and (t.strip().upper() == "JARVIS" or tools._is_jarvis(t)):
                found.append(h)
        return True

    u32.EnumWindows(each, 0)
    for h in found:
        if not u32.IsZoomed(h):
            u32.ShowWindow(h, 3)                 # SW_MAXIMIZE
    return bool(found)


async def _maximize_soon():
    for _ in range(12):                          # the page may still be naming itself
        try:
            if await asyncio.to_thread(maximize_window):
                return
        except Exception as e:
            log.warning("couldn't maximize the window: %s", e)
            return
        await asyncio.sleep(0.5)


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
    global SHUTDOWN, PHONE_URL
    SHUTDOWN = asyncio.Event()
    runner = web.AppRunner(build_app(), access_log=None, shutdown_timeout=3)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()        # the PC window: loopback, no firewall
    keep_awake()
    ip = lan_ip() if PHONE else None
    PHONE_URL = ""
    if PHONE and ip:                             # the phone: HTTPS on the SAME already-open port
        ctx = await asyncio.to_thread(_tls_context)
        if ctx is not None:
            try:
                await web.TCPSite(runner, ip, PORT, ssl_context=ctx).start()
                PHONE_URL = f"https://{ip}:{PORT}/"
                log.info("phone link (HTTPS on the open port): %s", PHONE_URL)
            except OSError as e:
                log.warning("couldn't open the phone HTTPS listener on %s:%s: %s", ip, PORT, e)
    print()
    print(f"  {CFG['name']} is running at {URL}")
    if PHONE_URL:
        print(f"  On your phone (Chrome or the app):  {PHONE_URL}")
    print("  Keep this window open. Closing it switches Jarvis off.")
    print()
    if PHONE_URL:
        kind = await asyncio.to_thread(network_kind)
        log.info("phone link: %s (Windows calls this network: %s)", PHONE_URL, kind or "unknown")
        if kind.lower() == "public":
            print("  NOTE: Windows calls this Wi-Fi 'Public', which keeps phones out. To let the phone in:")
            print("  Settings > Network & internet > Wi-Fi > your network > choose 'Private network'.")
            print()
    bg = [asyncio.create_task(start_ears()), asyncio.create_task(start_brain()),
          asyncio.create_task(start_local()), asyncio.create_task(start_gemini()),
          asyncio.create_task(growth_loop()), asyncio.create_task(bench_loop())]
    fix = selftest.pending()                     # a fix he applied: check it now it's loaded
    if fix:
        bg.append(asyncio.create_task(SELFTEST.resume(fix)))
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
