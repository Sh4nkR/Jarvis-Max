"""Ears: local speech-to-text with faster-whisper (the engine backtalk uses).

Tuned for Dr Wolf's Indian English: the default model is Whisper large-v3-turbo, which
was trained on far more accents than the small English-only model, and every clip gets a
short hint about who is talking and the names he uses (Jarvis, Claude, Qwen...). A few
names Whisper keeps mishearing are fixed afterwards. Jarvis's own voice doesn't change.

Runs on the CPU in int8, so it needs no graphics drivers and leaves the graphics card to
the local brain. The first start downloads the model (about 1.6 GB) into
Jarvis-Max/models; until then the small model (already there) does the listening.
"""
import logging
import os
import re
import threading
import time
import uuid

from common import CFG, MODELS, TMP

log = logging.getLogger("jarvis.ears")

FALLBACK = "small.en"               # already downloaded; used until the better model is ready

# Who's talking and the words he uses. Whisper leans towards these spellings.
HINT = ("Dr Wolf is speaking Indian English, sometimes with Hindi words, to his assistant "
        "Jarvis. Jarvis, switch to Claude Opus, Claude Sonnet, Claude Haiku, Gemini Flash-Lite or the local Qwen brain. "
        "Multi search on Google, DeepSeek, Gemini, ChatGPT, YouTube, GitHub, Ollama.")

# Mishearings seen in his logs, fixed after transcription.
FIXES = [
    (re.compile(r"^\s*(java is|javis|jervis|jarves|jarvez|javez|chavez|charvis|jowers|jarwis|travis)\b[,.]?",
                re.I), "Jarvis,"),
    (re.compile(r"\b(javis|jervis|jarves|jarvez|javez|charvis|jowers|jarwis)\b", re.I), "Jarvis"),
    (re.compile(r"\b(cloud|clawed|clod|clog|clogged|connet|conned|plot|klaud)\s+(brain|opus|sonnet|code)\b", re.I),
     lambda m: "Claude " + m.group(2)),
    (re.compile(r"\b(quen|queen|kwen|qwin)\s+(brain|model|3\.5)\b", re.I), lambda m: "Qwen " + m.group(2)),
    (re.compile(r"\bdeep\s*seats?\b", re.I), "DeepSeek"),
]


def fix_names(text: str) -> str:
    for pat, rep in FIXES:
        text = pat.sub(rep, text)
    return re.sub(r"\s{2,}", " ", text).strip(" ,") if text.strip() else text


class Ears:
    def __init__(self):
        # "ears_model" in jarvis.json picks another model (the old "stt_model": "small.en" is ignored)
        self.name = CFG.get("ears_model") or "large-v3-turbo"
        self.using = ""
        self.model = None
        self.status = "loading"
        self.error = ""
        self._lock = threading.Lock()

    def _load(self, name: str):
        from faster_whisper import WhisperModel
        t0 = time.time()
        model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODELS),
                             cpu_threads=min(8, os.cpu_count() or 4))
        log.info("ears ready (%s, %.1fs)", name, time.time() - t0)
        return model

    def _cached(self, name: str) -> bool:
        return any(name.replace(".", "-") in p.name or name in p.name for p in MODELS.glob("models--*"))

    def load(self):
        """Blocking. Called once in a background thread at startup."""
        MODELS.mkdir(parents=True, exist_ok=True)
        try:
            if self.name != FALLBACK and not self._cached(self.name):
                # first start with the better model: listen with the small one while it downloads
                try:
                    self.model, self.using, self.status = self._load(FALLBACK), FALLBACK, "ready"
                    log.info("ears: downloading %s in the background (about 1.6 GB)", self.name)
                except Exception:
                    log.exception("ears: the small model didn't load")
            model = self._load(self.name)
            with self._lock:
                self.model, self.using = model, self.name
            self.status, self.error = "ready", ""
        except Exception as e:
            log.exception("ears: %s didn't load", self.name)
            if self.model is None:
                try:
                    self.model, self.using, self.status = self._load(FALLBACK), FALLBACK, "ready"
                except Exception as e2:
                    self.status, self.error = "error", str(e2)[:300]
            else:
                log.info("ears: staying on %s (%s)", self.using, str(e)[:120])

    def transcribe(self, audio: bytes, suffix: str = ".webm") -> str:
        """Blocking. audio = whatever the browser recorded (webm/opus)."""
        if self.model is None:
            raise RuntimeError("ears are still loading" if self.status == "loading"
                               else f"ears unavailable: {self.error}")
        TMP.mkdir(parents=True, exist_ok=True)
        path = TMP / f"heard-{uuid.uuid4().hex}{suffix}"
        path.write_bytes(audio)
        try:
            with self._lock:
                kw = dict(language="en", beam_size=3, initial_prompt=HINT,
                          condition_on_previous_text=False)
                try:
                    segs, _ = self.model.transcribe(str(path), vad_filter=True, **kw)
                    text = " ".join(s.text.strip() for s in segs)
                except Exception:
                    # the silence trimmer is optional; never lose a sentence over it
                    segs, _ = self.model.transcribe(str(path), **kw)
                    text = " ".join(s.text.strip() for s in segs)
            text = text.strip()
            if len(text) > 25 and text.lower().rstrip(".") in HINT.lower():
                return ""                       # on near-silence Whisper can echo the hint back
            return fix_names(text)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
