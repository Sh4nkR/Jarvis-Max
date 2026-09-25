"""Jarvis's eyes and hands on the PC, offered to the brain as tools.

Eyes:  look_at_screen (the picture), read_screen_text (words + where they are),
       look_through_camera (a frame from the window's camera).
Hands: click_text, click_at, type_text, press_keys, scroll, open_app,
       open_url, focus_window, list_windows.

Lessons carried over from the earlier builds:
- Seeing and aiming are different. The model is weak at pixel-precise
  positions in a picture, so clicks go by the WORDS on screen (Tesseract finds
  where they are), not by guessing coordinates off an image.
- The process declares itself DPI-aware first, or Windows reports a scaled
  screen size and every click lands off-target.
- Long or non-English text is pasted, not typed, because a paste arrives whole.
- Moving the mouse into a screen corner stops the hands (pyautogui failsafe).
"""
import asyncio
import base64
import io
import os
import re
import shutil
import subprocess
import sys
import time
import types

import builder
import webbrowser

from claude_agent_sdk import create_sdk_mcp_server, tool

from common import IS_WIN, TMP


class _State:
    hands = True                 # the HANDS button in the window flips this
    camera_provider = None       # set by the server: async () -> (b64 jpeg | None, note)
    brain_switcher = None        # set by the server: async (choice) -> str
    learner = None               # set by the server: async () -> str (starts the daily learning)


STATE = _State()
_NOWIN = subprocess.CREATE_NO_WINDOW if IS_WIN else 0


def dpi_aware():
    if not IS_WIN:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_tesseract() -> str | None:
    p = shutil.which("tesseract")
    if p:
        return p
    for c in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe")):
        if os.path.exists(c):
            return c
    return None


def _say(t: str, err: bool = False) -> dict:
    d = {"content": [{"type": "text", "text": t}]}
    if err:
        d["is_error"] = True
    return d


def errors_as_words(fn):
    """A tool that fails tells the brain exactly what went wrong, in plain words,
    so it never has to guess (and never wrongly blames the HANDS switch)."""
    import functools

    @functools.wraps(fn)
    async def run(args):
        try:
            return await fn(args)
        except Exception as e:
            return _say(f"That didn't work: {type(e).__name__}: {e}", err=True)
    return run


def _hands_off():
    return _say("My hands are switched OFF (the HANDS button in the Jarvis window). "
                "Tell Dr Wolf, and ask him to switch them on if he wants me to do it.", err=True)


# ------------------------------------------------------------------ eyes ----
def grab_screen():
    """(PIL image of the main screen, monitor dict with left/top/width/height)."""
    import mss
    from PIL import Image
    with mss.mss() as s:
        mon = dict(s.monitors[1])
        raw = s.grab(mon)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img, mon


def to_jpeg_b64(img, max_w: int = 1568, quality: int = 72) -> tuple[str, tuple[int, int]]:
    from PIL import Image
    im = img
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode(), im.size


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z\u0900-\u097f]+", " ", s.lower())).strip()


def ocr_lines(img, psm: int = 3) -> list[dict]:
    """Every line of text on the screen: {text, words:[{t,x0,y0,x1,y1}]} in screen pixels."""
    from PIL import Image
    tess = find_tesseract()
    if not tess:
        raise RuntimeError("Tesseract (the screen-text reader) is not installed on this PC")
    scale = 2 if img.width < 2400 else 1           # small UI text reads far better enlarged
    im = img.convert("L")
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    TMP.mkdir(parents=True, exist_ok=True)
    path = TMP / "screen-ocr.png"
    im.save(path)
    r = subprocess.run([tess, str(path), "stdout", "--psm", str(psm), "tsv"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=90, creationflags=_NOWIN)
    if r.returncode != 0:
        raise RuntimeError(f"Tesseract failed: {r.stderr.strip()[:200]}")
    lines: dict[tuple, list] = {}
    for row in r.stdout.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        try:
            conf = float(f[10])
        except ValueError:
            conf = 0
        if conf < 30:
            continue
        x, y, w, h = (int(v) / scale for v in f[6:10])
        lines.setdefault((f[1], f[2], f[3], f[4]), []).append(
            {"t": f[11].strip(), "x0": x, "y0": y, "x1": x + w, "y1": y + h})
    out = []
    for words in lines.values():
        words.sort(key=lambda w: w["x0"])
        out.append({"text": " ".join(w["t"] for w in words), "words": words})
    out.sort(key=lambda l: (round(l["words"][0]["y0"] / 12), l["words"][0]["x0"]))
    return out


def find_phrase(lines: list[dict], phrase: str) -> list[tuple[float, float, str]]:
    """All places the phrase appears: (center_x, center_y, the line it was in)."""
    target = _norm(phrase)
    if not target:
        return []
    hits = []
    for line in lines:
        spans, pos = [], 0
        for w in line["words"]:
            n = _norm(w["t"])
            spans.append((pos, pos + len(n), w))
            pos += len(n) + 1
        joined = " ".join(_norm(w["t"]) for w in line["words"])
        start = joined.find(target)
        while start != -1:
            end = start + len(target)
            ws = [w for (a, b, w) in spans if a < end and b > start]
            if ws:
                x0 = min(w["x0"] for w in ws); x1 = max(w["x1"] for w in ws)
                y0 = min(w["y0"] for w in ws); y1 = max(w["y1"] for w in ws)
                hits.append(((x0 + x1) / 2, (y0 + y1) / 2, line["text"]))
            start = joined.find(target, start + 1)
    return hits


# ----------------------------------------------------------------- hands ----
def _pg():
    # pyautogui's optional MouseInfo viewer pulls in tkinter (and on Linux quits the whole
    # program when it's missing). Jarvis never opens that viewer, so give it an empty stand-in.
    sys.modules.setdefault("mouseinfo", types.ModuleType("mouseinfo"))
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


def _click(x: float, y: float, button: str = "left", double: bool = False):
    pg = _pg()
    pg.click(round(x), round(y), clicks=2 if double else 1, interval=0.08, button=button)


def _paste(text: str):
    import pyperclip
    pg = _pg()
    try:
        old = pyperclip.paste()
    except Exception:
        old = None
    pyperclip.copy(text)
    time.sleep(0.08)
    pg.hotkey("ctrl", "v")
    time.sleep(0.35)
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


_KEY_ALIAS = {"windows": "win", "window": "win", "escape": "esc", "return": "enter",
              "control": "ctrl", "del": "delete", "pgup": "pageup", "pgdn": "pagedown",
              "spacebar": "space", "cmd": "win"}


def _keys(spec: str):
    pg = _pg()
    for combo in [c.strip() for c in spec.split(",") if c.strip()]:
        parts = [_KEY_ALIAS.get(p.strip().lower(), p.strip().lower()) for p in combo.split("+")]
        if len(parts) == 1:
            pg.press(parts[0])
        else:
            pg.hotkey(*parts)
        time.sleep(0.12)


def _guard(fn):
    """Run a hands action off the event loop; turn the corner failsafe into words."""
    async def run(*a):
        try:
            return await asyncio.to_thread(fn, *a)
        except Exception as e:
            if type(e).__name__ == "FailSafeException":
                raise RuntimeError("Safety stop: the mouse is in a screen corner. "
                                   "Move it away from the corner and ask again.")
            raise
    return run


# ----------------------------------------------------------------- tools ----
@tool("look_at_screen",
      "Take a picture of Dr Wolf's main screen and look at it. Use this to see what is open "
      "or to answer questions about the screen. Do not click by guessing positions from "
      "this picture; use click_text for that.",
      {"type": "object", "properties": {}})
@errors_as_words
async def look_at_screen(args):
    img, mon = await asyncio.to_thread(grab_screen)
    b64, (w, h) = await asyncio.to_thread(to_jpeg_b64, img)
    return {"content": [
        {"type": "image", "data": b64, "mimeType": "image/jpeg"},
        {"type": "text", "text": f"Main screen, {mon['width']}x{mon['height']} pixels "
                                 f"(shown scaled to {w}x{h}). To click something, use "
                                 f"click_text with the words you can see on it."}]}


@tool("read_screen_text",
      "Read the words on the main screen with their positions (screen pixels). Optional "
      "'contains' keeps only lines containing that text.",
      {"type": "object", "properties": {"contains": {"type": "string"}}})
@errors_as_words
async def read_screen_text(args):
    img, mon = await asyncio.to_thread(grab_screen)
    try:
        lines = await asyncio.to_thread(ocr_lines, img)
    except Exception as e:
        return _say(str(e), err=True)
    want = _norm(args.get("contains") or "")
    rows = []
    for l in lines:
        if want and want not in _norm(l["text"]):
            continue
        ws = l["words"]
        cx = (ws[0]["x0"] + ws[-1]["x1"]) / 2 + mon["left"]
        cy = (min(w["y0"] for w in ws) + max(w["y1"] for w in ws)) / 2 + mon["top"]
        rows.append(f"({round(cx)},{round(cy)}) {l['text']}")
    if not rows:
        return _say("No matching text found on screen." if want else "No readable text on screen.")
    return _say(f"{len(rows)} lines (x,y = centre, screen pixels):\n" + "\n".join(rows[:200]))


@tool("click_text",
      "Click the words shown on screen (a button, link, icon label or menu item). "
      "occurrence picks the Nth match from the top. button: left, right or middle. "
      "double=true double-clicks (Dr Wolf's mouse can't, so this is handy for him).",
      {"type": "object",
       "properties": {"text": {"type": "string"},
                      "occurrence": {"type": "integer", "minimum": 1},
                      "button": {"type": "string", "enum": ["left", "right", "middle"]},
                      "double": {"type": "boolean"}},
       "required": ["text"]})
@errors_as_words
async def click_text(args):
    if not STATE.hands:
        return _hands_off()
    img, mon = await asyncio.to_thread(grab_screen)
    try:
        hits = find_phrase(await asyncio.to_thread(ocr_lines, img, 3), args["text"])
        if not hits:   # scattered labels read better in sparse mode
            hits = find_phrase(await asyncio.to_thread(ocr_lines, img, 11), args["text"])
    except Exception as e:
        return _say(str(e), err=True)
    if not hits:
        return _say(f"I can't find \"{args['text']}\" on the screen. Use read_screen_text or "
                    f"look_at_screen to see what is there.", err=True)
    n = max(1, int(args.get("occurrence") or 1))
    if n > len(hits):
        return _say(f"Only {len(hits)} match(es) for \"{args['text']}\".", err=True)
    x, y, line = hits[n - 1]
    x += mon["left"]; y += mon["top"]
    await _guard(_click)(x, y, args.get("button") or "left", bool(args.get("double")))
    more = f" ({len(hits)} matches; clicked number {n})" if len(hits) > 1 else ""
    return _say(f"Clicked \"{args['text']}\" at ({round(x)},{round(y)}) in the line "
                f"\"{line}\"{more}. Check the screen before saying it worked.")


@tool("click_at", "Click at exact screen pixel coordinates (from read_screen_text).",
      {"type": "object",
       "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                      "button": {"type": "string", "enum": ["left", "right", "middle"]},
                      "double": {"type": "boolean"}},
       "required": ["x", "y"]})
@errors_as_words
async def click_at(args):
    if not STATE.hands:
        return _hands_off()
    await _guard(_click)(args["x"], args["y"], args.get("button") or "left", bool(args.get("double")))
    return _say(f"Clicked at ({args['x']},{args['y']}). Check the screen before saying it worked.")


@tool("type_text",
      "Put text where the typing cursor is (it is pasted, so it arrives whole, including "
      "Hindi). Click the right box first. enter=true presses Enter afterwards.",
      {"type": "object",
       "properties": {"text": {"type": "string"}, "enter": {"type": "boolean"}},
       "required": ["text"]})
@errors_as_words
async def type_text(args):
    if not STATE.hands:
        return _hands_off()
    await _guard(_paste)(args["text"])
    if args.get("enter"):
        await _guard(_keys)("enter")
    return _say("Text sent to the focused box. Read the screen to confirm it landed.")


@tool("press_keys",
      "Press keys. One combo like 'ctrl+s', 'alt+tab', 'win', 'enter', or a sequence "
      "separated by commas: 'tab, tab, enter'.",
      {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]})
@errors_as_words
async def press_keys(args):
    if not STATE.hands:
        return _hands_off()
    await _guard(_keys)(args["keys"])
    return _say(f"Pressed {args['keys']}.")


@tool("scroll", "Scroll the window under the mouse up or down by some notches (default 5).",
      {"type": "object",
       "properties": {"direction": {"type": "string", "enum": ["up", "down"]},
                      "amount": {"type": "integer", "minimum": 1, "maximum": 50}},
       "required": ["direction"]})
@errors_as_words
async def scroll(args):
    if not STATE.hands:
        return _hands_off()
    n = int(args.get("amount") or 5) * (120 if IS_WIN else 1)
    await _guard(lambda: _pg().scroll(n if args["direction"] == "up" else -n))()
    return _say(f"Scrolled {args['direction']}.")


def _open_app(name: str):
    pg = _pg()
    pg.press("win")
    time.sleep(0.8)
    _paste(name)
    time.sleep(1.2)
    pg.press("enter")


@tool("open_app", "Open a program by name through the Start menu (e.g. 'Notepad', 'Spotify').",
      {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
@errors_as_words
async def open_app(args):
    if not STATE.hands:
        return _hands_off()
    await _guard(_open_app)(args["name"])
    return _say(f"Asked Windows to open {args['name']}. Look at the screen to confirm it opened.")


@tool("open_url", "Open a web address in Dr Wolf's normal browser.",
      {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]})
@errors_as_words
async def open_url(args):
    if not STATE.hands:
        return _hands_off()
    url = args["url"].strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    await asyncio.to_thread(webbrowser.open, url)
    return _say(f"Opened {url}.")


def _windows() -> list:
    import pygetwindow as gw
    return [w for w in gw.getAllWindows() if w.title and w.title.strip() and w.visible]


@tool("list_windows", "List the titles of the open windows.", {"type": "object", "properties": {}})
@errors_as_words
async def list_windows(args):
    if not IS_WIN:
        return _say("Window listing only works on Windows.", err=True)
    titles = await asyncio.to_thread(lambda: [w.title for w in _windows()])
    return _say("Open windows:\n" + "\n".join(f"- {t}" for t in titles[:60]))


def _focus(title: str) -> str:
    wins = [w for w in _windows() if title.lower() in w.title.lower()]
    if not wins:
        raise RuntimeError(f"No open window has '{title}' in its title.")
    w = wins[0]
    if w.isMinimized:
        w.restore()
    _pg().press("alt")          # Windows only lets the active app pass focus on; this unlocks it
    try:
        w.activate()
    except Exception:
        pass                    # pygetwindow sometimes reports "error 0 = success"
    time.sleep(0.3)
    return w.title


@tool("focus_window", "Bring a window to the front by (part of) its title, before typing into it.",
      {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]})
@errors_as_words
async def focus_window(args):
    if not STATE.hands:
        return _hands_off()
    if not IS_WIN:
        return _say("Window focus only works on Windows.", err=True)
    t = await _guard(_focus)(args["title"])
    return _say(f"Brought '{t}' to the front.")


@tool("look_through_camera",
      "Take a picture with the camera in the Jarvis window (Dr Wolf, the room, or something "
      "he holds up) and look at it.",
      {"type": "object", "properties": {}})
@errors_as_words
async def look_through_camera(args):
    if STATE.camera_provider is None:
        return _say("The Jarvis window isn't connected.", err=True)
    b64, note = await STATE.camera_provider()
    if not b64:
        return _say(note or "The camera didn't give me a picture.", err=True)
    return {"content": [{"type": "image", "data": b64, "mimeType": "image/jpeg"},
                        {"type": "text", "text": "Camera picture from the Jarvis window."}]}


@tool("switch_brain",
      "Switch Jarvis to another brain. Jarvis restarts (about 15 seconds) and confirms the "
      "new brain when it's back. brain: 'sonnet' (Claude Sonnet), 'opus' (Claude Opus), 'haiku' (Claude Haiku), "
      "'claude' (Claude with the last Claude model used), 'local' (Qwen on this PC), "
      "'gemini' (Google's Gemini), 'gemini-lite' (Gemini Flash-Lite) or 'auto' (Claude first, local when the Claude limit runs "
      "out). Use it whenever Dr Wolf "
      "asks to change brain or model. Never edit jarvis.json or restart Jarvis any other way.",
      {"type": "object",
       "properties": {"brain": {"type": "string", "enum": ["sonnet", "opus", "haiku", "claude", "local", "gemini", "gemini-lite", "auto"]}},
       "required": ["brain"]})
@errors_as_words
async def switch_brain(args):
    if STATE.brain_switcher is None:
        return _say("Brain switching isn't available right now.", err=True)
    return _say(await STATE.brain_switcher((args.get("brain") or "").strip().lower()))


@tool("start_lessons",
      "Start your daily learning right now: lessons from mistakes, skills from jobs that worked, "
      "a tidy rewrite of your notes, and your test sheet. It runs on the local brain in the "
      "background for a few minutes, and you announce the result out loud when it's done. Use it "
      "whenever Dr Wolf tells you to go to your lessons, classes or daily learning. Never pretend "
      "to learn without calling it.",
      {"type": "object", "properties": {}})
@errors_as_words
async def start_lessons(args):
    if STATE.learner is None:
        return _say("Learning isn't available right now.", err=True)
    return _say(await STATE.learner())


_EMPTY = {"type": "object", "properties": {}}


@tool("project_start",
      "Download a GitHub project into Desktop\\Jarvis-Builds and get a step-by-step install plan "
      "(a checklist, PLAN.md) written by the consultant AI. Use it when Dr Wolf asks to download, "
      "build or install something from GitHub. Afterwards read him the plan in short lines and wait "
      "for his go. source: the GitHub link or owner/name.",
      {"type": "object", "properties": {"source": {"type": "string"}}, "required": ["source"]})
@errors_as_words
async def project_start(args):
    return _say(await builder.project_start(args.get("source", "")))


@tool("project_next",
      "Run the next unticked step of the current build (he clicks ALLOW), tick it off and report. "
      "Call it once per step. Never run build commands any other way.", _EMPTY)
@errors_as_words
async def project_next(args):
    return _say(await builder.project_next())


@tool("project_ask",
      "Ask the consultant AI (Claude, or Gemini) when a build step failed or you're unsure. It "
      "returns the fix and updates the checklist. question: what you think went wrong, or what "
      "you want to know.",
      {"type": "object", "properties": {"question": {"type": "string"}}})
@errors_as_words
async def project_ask(args):
    return _say(await builder.project_ask(args.get("question", "")))


@tool("project_status", "Show the current build's checklist: what's done and what's left.", _EMPTY)
@errors_as_words
async def project_status(args):
    return _say(await builder.project_status())


@tool("github_publish",
      "Publish a folder to Dr Wolf's GitHub: safety scan for keys and personal files, a README if "
      "there isn't a proper one, his ALLOW, then push (never a force-push). Use it whenever he asks "
      "to push or upload code to GitHub; never use git commands for this yourself. folder: the "
      "folder's path; repo: the repo name (or owner/name); private: true for a private repo.",
      {"type": "object", "properties": {"folder": {"type": "string"}, "repo": {"type": "string"},
                                        "private": {"type": "boolean"}, "message": {"type": "string"}},
       "required": ["folder", "repo"]})
@errors_as_words
async def github_publish(args):
    return _say(await builder.github_publish(args.get("folder", ""), args.get("repo", ""),
                                             bool(args.get("private")), args.get("message", "")))


ALL = [look_at_screen, read_screen_text, look_through_camera, click_text, click_at, type_text,
       press_keys, scroll, open_app, open_url, list_windows, focus_window, switch_brain,
       start_lessons, project_start, project_next, project_ask, project_status,
       github_publish]
SERVER_NAME = "pc"
TOOL_NAMES = [f"mcp__{SERVER_NAME}__{t.name}" for t in ALL]


def make_server():
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=ALL)
