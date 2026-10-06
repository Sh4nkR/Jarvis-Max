"""Shared paths, settings and logging for Jarvis."""
import json
import logging
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent          # Jarvis-Max/app
ROOT = APP.parent                              # Jarvis-Max
MEMORY = ROOT / "memory"                       # the brain's home folder (its vault)
MODELS = ROOT / "models"                       # speech model lives here, not in your user profile
LOGS = ROOT / "logs"
TMP = ROOT / "logs" / "tmp"
IS_WIN = sys.platform == "win32"

DEFAULTS = {
    "name": "JARVIS",
    "call_me": "Dr Wolf",
    "port": 8795,
    "face": "board",
    "voice": "en-GB-RyanNeural",
    "voice_rate": "+0%",
    "stt_model": "small.en",
    "model": "sonnet",
    "brain_mode": "auto",          # auto: Claude first, local brain when Claude is out | claude | local
    "local_model": "qwen3.5:9b",
    "local_ctx": 12288,            # the local brain's working memory, in tokens
    "search_region": "in-en",
    "gemini_model": "auto",
    "deepseek_model": "auto",      # auto = deepseek-v4-flash; or "deepseek-v4-pro"
    "github_user": "",             # his GitHub username, for github_publish        # auto = the newest Gemini Flash the key can use; or e.g. "gemini-3.8-flash"
    "hands_on_at_start": True,
    "open_window": True,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((APP / "jarvis.json").read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass                                   # first start: the defaults; the BRAIN menu creates the file
    except (OSError, ValueError) as e:
        print(f"  (jarvis.json unreadable, using defaults: {e})")
    return cfg


CFG = load_config()


# ------------------------------------------------------------ brain choice ----
# What the BRAIN button and the switch_brain tool offer. "model" is a Claude Code alias:
# it always means the newest Sonnet / Opus that Dr Wolf's plan allows.
BRAINS = {
    "sonnet": {"label": "Claude Sonnet", "brain_mode": "claude", "model": "sonnet",
               "about": "Fast and sharp. Uses your Claude plan."},
    "opus": {"label": "Claude Opus", "brain_mode": "claude", "model": "opus",
             "about": "The strongest. Uses more of your Claude limit."},
    "haiku": {"label": "Claude Haiku", "brain_mode": "claude", "model": "haiku",
              "about": "The quickest Claude. Uses the least of your Claude limit."},
    "local": {"label": "Local Qwen", "brain_mode": "local",
              "about": "Runs on this PC. Free and private, but weaker."},
    "gemini": {"label": "Gemini", "brain_mode": "gemini", "gemini_model": "auto",
               "about": "Google's Gemini, with your own Gemini API key."},
    "gemini-lite": {"label": "Gemini Flash-Lite", "brain_mode": "gemini", "gemini_model": "gemini-3.5-flash-lite",
                    "about": "Gemini 3.5 Flash-Lite: the quickest Gemini, same API key. Weaker at long jobs."},
    "deepseek": {"label": "DeepSeek", "brain_mode": "deepseek", "deepseek_model": "auto",
                 "about": "DeepSeek V4 online (deepseek-v4-flash), with your own DeepSeek API key. Reads text, not pictures."},
    "auto": {"label": "Auto", "brain_mode": "auto",
             "about": "Claude first; the local brain when your Claude limit runs out."},
}
SWITCH_NOTE = LOGS / "brain_switch.json"        # written before a switch-restart, read on startup
SECRETS = ROOT / "secrets"                      # API keys; this folder never goes to GitHub


def read_secret(name: str) -> str:
    try:
        return (SECRETS / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_secret(name: str, value: str):
    SECRETS.mkdir(parents=True, exist_ok=True)
    (SECRETS / ".gitignore").write_text("*\n", encoding="utf-8")   # keeps the whole folder out of git
    (SECRETS / name).write_text(value.strip() + "\n", encoding="utf-8")


def brain_choice(cfg: dict | None = None) -> str:
    """Which BRAINS entry jarvis.json is set to right now."""
    cfg = cfg or load_config()
    mode = (cfg.get("brain_mode") or "auto").lower()
    if mode == "gemini":
        return "gemini-lite" if "lite" in (cfg.get("gemini_model") or "").lower() else "gemini"
    if mode in ("local", "auto", "deepseek"):
        return mode
    model = (cfg.get("model") or "").lower()
    return "opus" if "opus" in model else "haiku" if "haiku" in model else "sonnet"


def pretty_model(model_id: str) -> str:
    """claude-opus-5-5 -> Claude Opus 5.5, sonnet -> Claude Sonnet."""
    m = re.match(r"claude-(opus|sonnet|haiku)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$", (model_id or "").strip())
    if m:
        return f"Claude {m.group(1).title()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
    alias = (model_id or "").strip().lower()
    return f"Claude {alias.title()}" if alias in ("opus", "sonnet", "haiku") else (model_id or "Claude")


def save_brain(choice: str) -> dict:
    """Write the choice into jarvis.json (keeping every other setting) and return the new config."""
    pick = BRAINS[choice]
    path = APP / "jarvis.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    cfg["brain_mode"] = pick["brain_mode"]
    for key in ("model", "gemini_model", "deepseek_model"):
        if key in pick:
            cfg[key] = pick[key]
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return load_config()


def setup_logging() -> logging.Logger:
    LOGS.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.FileHandler(LOGS / "jarvis.log", encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)
    for noisy in ("aiohttp.access", "httpx", "faster_whisper", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("jarvis")
