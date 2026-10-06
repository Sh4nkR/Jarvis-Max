"""Ears: local speech-to-text with faster-whisper (the engine backtalk uses).

Tuned for Dr Wolf's Indian English: the default model is Whisper large-v3-turbo, which
was trained on far more accents than the small English-only model, and every clip gets a
short hint about who is talking and the names he uses (Jarvis, Claude, Qwen...). A few
names Whisper keeps mishearing are fixed afterwards. Jarvis's own voice doesn't change.

Runs on an NVIDIA GPU (float16) when it has room next to the local brain, otherwise on the
CPU in int8 ("ears_device" in jarvis.json: auto, cuda or cpu). The first start downloads the model (about 1.6 GB) into
Jarvis-Max/models; until then the small model (already there) does the listening.
"""
import logging
import os
import re
import threading
import time
import uuid

from common import CFG, MODELS, TMP, load_config

log = logging.getLogger("jarvis.ears")

FALLBACK = "small.en"               # already downloaded; used until the better model is ready

# Who's talking and the words he uses. Whisper leans towards these spellings.
HINT = ("Dr Wolf is speaking Indian English, sometimes with Hindi words, to his assistant "
        "Jarvis. Jarvis, switch to Claude Opus, Claude Sonnet, Claude Haiku, Gemini Flash-Lite, DeepSeek or the local Qwen brain. "
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


# ------------------------------------------------------------- Sarvam ----
# Main ears: Sarvam Saaras V4 (online, trained on Indian speech; Hinglish-aware). The key lives in
# secrets/sarvam_key.txt. With no key, no internet, no credits or any error, local Whisper answers instead.
SARVAM_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_KEY = "sarvam_key.txt"
KEYTERMS = ["Jarvis", "Dr Wolf", "Claude", "Opus", "Sonnet", "Haiku", "Gemini", "Flash-Lite", "Qwen",
            "Ollama", "DeepSeek", "GitHub", "YouTube", "WhatsApp", "Samasa", "Jarvis-Max", "Jarvis Hands",
            "multi search", "Android", "APK", "Chrome"]


def _sarvam_key() -> str:
    try:
        from common import read_secret
        return (read_secret(SARVAM_KEY) or "").strip()
    except Exception:
        return ""


def sarvam_transcribe(audio: bytes, suffix: str) -> str:
    """Blocking. Raises on any problem so the caller can fall back to Whisper."""
    import json
    import httpx
    key = _sarvam_key()
    if not key:
        raise RuntimeError("no Sarvam key")
    cfg = load_config()
    mime = {".webm": "audio/webm", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg"}.get(suffix, "application/octet-stream")
    data = {"model": cfg.get("sarvam_model") or "saaras:v4",
            "language_code": cfg.get("sarvam_language") or "en-IN",
            "keyterms": json.dumps(KEYTERMS)}
    r = httpx.post(SARVAM_URL, headers={"api-subscription-key": key}, data=data,
                   files={"file": ("speech" + suffix, audio, mime)}, timeout=httpx.Timeout(12, connect=4))
    if r.status_code != 200:
        raise RuntimeError(f"Sarvam {r.status_code}: {r.text[:160]}")
    return (r.json().get("transcript") or "").strip()


GPU_NEED_MB = 2000     # whisper large-v3-turbo in float16 needs about 1.6 GB; keep a margin


def _cuda_dlls():
    """pip's CUDA parts (cuBLAS, cuDNN) live in site-packages/nvidia/*/bin; Windows must be told."""
    import site
    for sp in site.getsitepackages():
        root = os.path.join(sp, "nvidia")
        if os.path.isdir(root):
            for sub in os.listdir(root):
                b = os.path.join(root, sub, "bin")
                if os.path.isdir(b):
                    try:
                        os.add_dll_directory(b)
                    except (OSError, AttributeError):
                        pass
                    os.environ["PATH"] = b + os.pathsep + os.environ.get("PATH", "")


def _ollama(path: str, body: dict | None = None):
    import json
    import urllib.request
    req = urllib.request.Request("http://127.0.0.1:11434" + path, method="POST" if body else "GET",
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _free_qwen(cfg: dict):
    """The brain isn't Qwen: take Qwen out of the GPU so the ears can use it (it comes back when needed)."""
    try:
        for m in _ollama("/api/ps").get("models") or []:
            _ollama("/api/generate", {"model": m.get("name") or cfg.get("local_model"), "keep_alive": 0})
            log.info("ears: moved %s out of the GPU (the brain is %s)", m.get("name"), cfg.get("brain_mode"))
        time.sleep(1.5)
    except Exception:
        pass                                       # no Ollama running


def _qwen_reserve(cfg: dict) -> int:
    """Auto brain (Claude first, Qwen as backup): if Qwen isn't in the GPU yet, keep room for it (MB)."""
    try:
        return 0 if _ollama("/api/ps").get("models") else int(cfg.get("qwen_vram_mb") or 6500)
    except Exception:
        return 0


def _gpu_ok() -> bool:
    """Who gets the graphics card. ears_device in jarvis.json: "auto" (default), "cuda" or "cpu".
    Auto: brain = local Qwen -> Qwen keeps the GPU, ears on the CPU.
          brain = Gemini or Claude -> Qwen is moved out of the GPU and the ears use it.
          brain = auto -> the ears use it only if there's still room next to Qwen."""
    cfg = load_config()
    want = (cfg.get("ears_device") or "auto").lower()
    mode = (cfg.get("brain_mode") or "auto").lower()
    if want == "cpu" or (want == "auto" and mode == "local"):
        return False
    try:
        import ctranslate2
        _cuda_dlls()
        if ctranslate2.get_cuda_device_count() < 1:
            log.info("ears: no CUDA GPU found; using the CPU")
            return False
    except Exception as e:
        log.info("ears: CUDA check failed (%s); using the CPU", str(e)[:120])
        return False
    if want == "cuda":
        return True
    reserve = _qwen_reserve(cfg) if mode == "auto" else 0
    if mode != "auto":
        _free_qwen(cfg)
    try:
        import subprocess
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total,name", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip().splitlines()[0]
        free, total, gpu = [x.strip() for x in out.split(",", 2)]
        need = GPU_NEED_MB + reserve
        log.info("ears: %s has %s of %s MB free (ears need %d MB)", gpu, free, total, need)
        if int(free) < need:
            log.info("ears: not enough room on the GPU; using the CPU")
            return False
        return True
    except Exception as e:
        log.info("ears: couldn't read the GPU's free memory (%s); using the CPU", str(e)[:120])
        return False


class Ears:
    def __init__(self):
        # "ears_model" in jarvis.json picks another model (the old "stt_model": "small.en" is ignored)
        self.name = CFG.get("ears_model") or "large-v3-turbo"
        self.using = ""
        self.model = None
        self.status = "loading"
        self.error = ""
        self.device = "cpu"
        self.engine = "sarvam" if _sarvam_key() else "whisper"
        self._lock = threading.Lock()

    def _load(self, name: str):
        from faster_whisper import WhisperModel
        t0 = time.time()
        if name != FALLBACK and _gpu_ok():
            try:
                # RTX 50-series (Blackwell) cards fail with int8 on the GPU; float16 works
                model = WhisperModel(name, device="cuda", compute_type="float16", download_root=str(MODELS))
                self._warm(model)
                self.device = "gpu"
                log.info("ears ready on the GPU (%s, %.1fs)", name, time.time() - t0)
                return model
            except Exception as e:
                log.info("ears: the GPU didn't work (%s); using the CPU", str(e)[:160])
        model = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODELS),
                             cpu_threads=min(8, os.cpu_count() or 4))
        self.device = "cpu"
        log.info("ears ready (%s, %.1fs)", name, time.time() - t0)
        return model

    @staticmethod
    def _warm(model):
        """Half a second of silence: CUDA/cuDNN errors show up here, not at load time."""
        import numpy as np
        segs, _ = model.transcribe(np.zeros(8000, dtype=np.float32), language="en", beam_size=1)
        list(segs)

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
        """Blocking. audio = whatever the browser recorded (webm/opus).
        Sarvam first ("ears_engine": "sarvam", the default when a key is saved); Whisper if that fails."""
        if (load_config().get("ears_engine") or "sarvam").lower() == "sarvam" and _sarvam_key():
            t0 = time.time()
            try:
                text = sarvam_transcribe(audio, suffix)
                self.engine = "sarvam"
                log.info("ears: Sarvam %.1fs: %s", time.time() - t0, text[:80])
                return fix_names(text)
            except Exception as e:
                self.engine = "whisper"
                log.info("ears: Sarvam failed (%s); Whisper takes this one", str(e)[:160])
        return self._whisper(audio, suffix)

    def _whisper(self, audio: bytes, suffix: str = ".webm") -> str:
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

    def retarget(self):
        """Blocking. After a brain switch: move the ears to the GPU or the CPU, whichever is right now.
        Holds the lock while moving, so a sentence arriving meanwhile waits a few seconds instead of failing."""
        if self.model is None or self.using == FALLBACK:
            return
        want = "gpu" if _gpu_ok() else "cpu"
        if want == self.device:
            return
        with self._lock:
            old = self.model
            if want == "cpu":                      # free the GPU first, so Qwen gets all of it
                self.model = old = None
            try:
                self.model = self._load(self.name)
            except Exception as e:
                log.info("ears: couldn't move (%s)", str(e)[:120])
                self.model = old or self._load(FALLBACK)
