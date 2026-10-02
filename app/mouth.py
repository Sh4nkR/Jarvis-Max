"""Mouth: turns reply sentences into speech (Microsoft's free neural voices via edge-tts).

jaredrhod's backtalk uses Kokoro. Kokoro on Windows needs PyTorch (about 2 GB)
plus espeak-ng installed separately, so Jarvis uses edge-tts instead: small,
natural, British. If it can't reach the voice service, the window falls back
to the browser's built-in voice, so Jarvis never goes mute.
"""
import logging
import re
import time

from common import CFG

log = logging.getLogger("jarvis.mouth")

_CODE = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_URL = re.compile(r"https?://\S+")
_MARK = re.compile(r"[*_`#>|]+")
_BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.M)


def for_speech(text: str) -> str:
    """What should be SAID for a chunk of reply text (markdown stripped)."""
    t = _CODE.sub(" I've put the code on screen. ", text)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub("the link on screen", t)
    t = _BULLET.sub("", t)
    t = _MARK.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


VOICES = {   # style -> (edge-tts voice, rate, pitch)
    "british":  ("en-GB-RyanNeural", None, "+0Hz"),
    "yakuza":   ("en-HK-SamNeural", "-15%", "-30Hz"),      # backup when Gemini's acted voice isn't available
    "rikuo":    ("en-HK-SamNeural", "-6%", "-12Hz"),       # backup for the acted 'rikuo' voice
    "japanese": ("ja-JP-KeitaNeural", None, "+0Hz"),
    "indian":   ("en-IN-PrabhatNeural", None, "+0Hz"),
    "american": ("en-US-GuyNeural", None, "+0Hz"),
    "hindi":    ("hi-IN-MadhurNeural", None, "+0Hz"),     # Hindi male; reads Devanagari and Hinglish
}


# The yakuza voice is ACTED by Gemini's speech model (free key): a character, not just a pitch shift.
YAKUZA_STYLE = ("Voice an old, huge, heavy-set Japanese yakuza boss, the dragon of the dojo: an extremely "
                "deep, heavy, rough and gruff voice, gravelly like a growl from the chest, as if worn down by "
                "years of cigarettes and sake. Slow, weighty, clipped delivery with hard stops between short "
                "phrases. Full of arrogance and pride, contemptuous, calm but menacing, with a low grunting "
                "chuckle. He speaks English with a thick, heavy Japanese accent: Japanese vowel sounds, "
                "rolled and clipped R and L sounds, strong Japanese rhythm and intonation. "
                "Read the text exactly, word for word.")
RIKUO_STYLE = ("Voice a wild, cold, self-destructive young yakuza loner: a low, raspy, flat voice that is "
               "quiet and dead-calm one moment and suddenly sharp and explosive the next. Reckless, unpredictable, "
               "nothing to lose, no respect for anyone; short clipped delivery with menacing pauses. He speaks "
               "English with a strong Japanese accent. Read the text exactly, word for word.")
STYLES = {"yakuza": (YAKUZA_STYLE, "Algenib"), "rikuo": (RIKUO_STYLE, "Algenib")}
YAKUZA_VOICE = "Algenib"                          # Gemini's gravelly prebuilt voice
TTS_MODELS = ["gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-2.5-flash-preview-tts"]


def _wav(pcm: bytes, rate: int = 24000) -> bytes:
    import struct
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


async def _gemini_tts(text: str, style: str = "yakuza") -> bytes | None:
    import base64
    import re as _re
    import httpx
    from common import read_secret
    key = read_secret("gemini_key.txt")
    if not key:
        return None
    act, voice = STYLES.get(style, STYLES["yakuza"])
    gen = {"responseModalities": ["AUDIO"],
           "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=8)) as c:
        for model in TTS_MODELS:
            for body in ({"systemInstruction": {"parts": [{"text": act}]},
                          "contents": [{"role": "user", "parts": [{"text": text}]}], "generationConfig": gen},
                         {"contents": [{"role": "user", "parts": [{"text": act + "\n\n" + text}]}],
                          "generationConfig": gen}):
                r = await c.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 json=body, headers={"x-goog-api-key": key})
                if r.status_code == 429:
                    raise RuntimeError("gemini voice: free limit reached")
                if r.status_code == 404:
                    break                                  # this model name doesn't exist: next model
                if r.status_code != 200:
                    continue                               # try the plain-prompt form
                for p in ((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts", []):
                    d = p.get("inlineData") or {}
                    if d.get("data"):
                        raw = base64.b64decode(d["data"])
                        mime = d.get("mimeType", "")
                        if raw[:4] == b"RIFF" or "wav" in mime:
                            return raw
                        m = _re.search(r"rate=(\d+)", mime)
                        return _wav(raw, int(m.group(1)) if m else 24000)
    return None


class Mouth:
    def __init__(self):
        self.voice = CFG["voice"]
        self.rate = CFG["voice_rate"]
        self.pitch = "+0Hz"
        style = CFG.get("voice_style")
        self.style = style if style in VOICES else "british"
        self._gemini_off_until = 0.0
        if style in VOICES:
            self.voice, rate, self.pitch = VOICES[style]
            self.rate = rate or self.rate
        self.status = "ready"
        self.error = ""
        self._retry_at = 0.0          # after a failure, don't re-try the service on every sentence

    async def synth(self, text: str) -> bytes | None:
        """MP3 bytes for one sentence, or None (the window then uses its own voice)."""
        said = for_speech(text)
        if not said:
            return None
        if time.monotonic() < self._retry_at:
            return None
        if self.style in STYLES and time.monotonic() >= self._gemini_off_until:
            try:
                audio = await _gemini_tts(said, self.style)
                if audio:
                    return audio
            except Exception as e:
                log.info("yakuza voice: %s; using the backup voice for a minute", e)
                self._gemini_off_until = time.monotonic() + 60
        try:
            import edge_tts
            comm = edge_tts.Communicate(said, self.voice, rate=self.rate, pitch=self.pitch)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf += chunk["data"]
            if buf:
                self.status = "ready"
                return bytes(buf)
            raise RuntimeError("voice service returned no audio")
        except Exception as e:
            if self.status != "fallback":
                log.warning("voice service unavailable, using the window's own voice: %s", e)
            self.status = "fallback"
            self.error = str(e)[:200]
            self._retry_at = time.monotonic() + 300     # try the service again in 5 minutes
            return None

    def set_style(self, style: str) -> str:
        """Switch Jarvis's voice (kept in jarvis.json, so it survives restarts)."""
        style = (style or "").strip().lower()
        if style not in VOICES:
            return "Voices I have: " + ", ".join(VOICES) + "."
        self.voice, rate, self.pitch = VOICES[style]
        self.rate = rate or CFG["voice_rate"]
        self.style = style
        self._retry_at = 0.0
        self._gemini_off_until = 0.0
        try:
            import json
            from common import APP
            p = APP / "jarvis.json"
            cfg = json.loads(p.read_text(encoding="utf-8"))
            cfg["voice_style"] = style
            p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("couldn't save the voice choice: %s", e)
        return f"Voice switched to {style}."
