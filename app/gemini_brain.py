"""The Gemini brain: Google's Gemini through the Gemini API (Dr Wolf's own API key).

Same reach as the local brain: the web (research, search, reading pages, multi search),
his screen and camera, mouse and keyboard, files, PowerShell (each command needs his
ALLOW), memory notes and switch_brain. Only the thinking happens at Google instead of on
this PC. It borrows the local brain's tools and rules and talks Gemini's own format.

The key lives in Jarvis-Max/secrets/gemini_key.txt (never uploaded to GitHub).
"gemini_model" in jarvis.json picks the model; "auto" takes the newest Gemini Flash the key
can use. Note: a Google AI Pro membership doesn't pay for API use; the key runs on the
Gemini API's free tier (or on Cloud billing if he adds it).
"""
import asyncio
import json
import logging
import os
import re
import time

import httpx

import tools
from common import load_config, read_secret
from local_brain import (CALL, LOCAL_PROMPT, LOCAL_TOOLS, MAX_ROUNDS, PC, LocalBrain,
                         _SENT_END, _small_picture, system_prompt, tool_specs)

log = logging.getLogger("jarvis.gemini")

API = os.environ.get("JARVIS_GEMINI_API", "https://generativelanguage.googleapis.com/v1beta")
KEY_FILE = "gemini_key.txt"
KEEP_TURNS = 12                     # earlier requests are forgotten after this many

_OLD = 'running on your local brain: a model on {c}\'s own PC instead of Claude.\nIf he asks which brain you\'re on, say "my local brain, Qwen".'
_NEW = 'running on the Gemini brain (Google\'s Gemini, through {c}\'s own API key) instead of Claude.\nIf he asks which brain you\'re on, say "Gemini".'


def gemini_prompt() -> str:
    base = system_prompt()
    old, new = _OLD.format(c=CALL), _NEW.format(c=CALL)
    return base.replace(old, new) if old in base else base + "\n(Right now you are on the Gemini brain.)"


# JSON Schema (what our tools use) -> the schema form Gemini's function declarations take
_KEEP = {"description", "enum", "properties", "required", "items", "minimum", "maximum",
         "format", "nullable", "minItems", "maxItems"}


def _schema(s: dict) -> dict:
    out = {}
    for k, v in s.items():
        if k == "type":
            out["type"] = str(v).upper()
        elif k == "properties":
            out["properties"] = {n: _schema(p) for n, p in v.items()}
        elif k == "items":
            out["items"] = _schema(v)
        elif k == "required":
            if v:
                out["required"] = list(v)
        elif k in _KEEP:
            out[k] = v
    return out


def declarations() -> list[dict]:
    decls = []
    for spec in tool_specs():
        f = spec["function"]
        d = {"name": f["name"], "description": f["description"]}
        params = f.get("parameters") or {}
        if params.get("properties"):
            d["parameters"] = _schema(params)
        decls.append(d)
    return decls


def pretty(model_id: str) -> str:
    """gemini-3.8-flash -> Gemini 3.8 Flash"""
    m = re.match(r"(?:models/)?gemini-([\d.]+)-(.+)", model_id or "")
    return f"Gemini {m.group(1)} {m.group(2).replace('-', ' ').title()}" if m else (model_id or "Gemini")


def _version(name: str) -> tuple:
    m = re.match(r"gemini-(\d+)(?:\.(\d+))?-flash$", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (-1, -1)


class GeminiBrain(LocalBrain):
    def __init__(self, permission_gate=None):
        super().__init__(permission_gate)
        self.contents: list[dict] = []      # Gemini's own conversation format
        self.turn_starts: list[int] = []
        self.key = ""
        self.model_id = ""
        self.flashes: list[str] = []        # every Flash model the key can use, newest first
        self._busy_until: dict[str, float] = {}
        self._thinking = True

    @property
    def model(self) -> str:
        return self.model_id or (load_config().get("gemini_model") or "auto")

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.key, "Content-Type": "application/json"}

    async def start(self):
        self.status, self.error = "starting", ""
        self.key = read_secret(KEY_FILE)
        if not self.key:
            self.status, self.error = "needs_key", "no Gemini API key yet"
            return
        want = (load_config().get("gemini_model") or "auto").strip()
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{API}/models", headers=self._headers(), params={"pageSize": 1000})
            if r.status_code in (400, 401, 403):
                self.status, self.error = "needs_key", "Google refused this API key"
                log.warning("gemini: key refused (%s): %s", r.status_code, r.text[:200])
                return
            r.raise_for_status()
            names = [m["name"].split("/", 1)[1] for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
        except Exception as e:
            self.status, self.error = "error", f"couldn't reach Gemini ({str(e)[:120]})"
            return
        self.flashes = sorted((n for n in names if _version(n) >= (0, 0)), key=_version, reverse=True)
        if want != "auto":
            if want not in names:
                self.status, self.error = "error", f"the Gemini model {want} isn't available to this key"
                return
            self.model_id = want
        else:
            self.model_id = self.flashes[0] if self.flashes else next((n for n in names if "flash" in n), "")
            if not self.model_id:
                self.status, self.error = "error", "this key can't use any Gemini Flash model"
                return
        self.status = "ready"
        log.info("gemini brain ready (%s)", self.model_id)

    async def restart(self):
        self.contents.clear()
        self.turn_starts.clear()
        await self.start()

    # ------------------------------------------------------------ one turn ----
    def _view(self) -> list[dict]:
        """The last few requests, with only the newest picture still attached."""
        start = self.turn_starts[-KEEP_TURNS] if len(self.turn_starts) > KEEP_TURNS else 0
        view = self.contents[start:]
        last_pic = max((i for i, c in enumerate(view)
                        if any("inlineData" in p for p in c["parts"])), default=-1)
        out = []
        for i, c in enumerate(view):
            if i != last_pic and any("inlineData" in p for p in c["parts"]):
                parts = [p for p in c["parts"] if "inlineData" not in p] or [{"text": "(picture no longer shown)"}]
                c = {"role": c["role"], "parts": parts}
            out.append(c)
        return out

    async def _stream_any(self):
        """Gemini's newest models are sometimes 'experiencing high demand' (503). Then the next
        newest Flash answers instead, and the busy one is skipped for five minutes."""
        order = [self.model_id] + [f for f in self.flashes if f != self.model_id]
        last = None
        for lap in range(2):
            now = time.monotonic()
            live = [m for m in order if self._busy_until.get(m, 0) <= now]
            if not live and lap == 0:
                live = order
            for m in live[:4]:
                for attempt in range(2):
                    try:
                        async for chunk in self._stream(m):
                            yield chunk
                        return
                    except _Refused as e:            # refusals come before the first chunk
                        if e.code == 429:            # the FREE key allows ~5 requests a minute per model
                            last = e
                            daily = "PerDay" in e.detail
                            self._busy_until[m] = time.monotonic() + (3600 if daily else 60)
                            log.info("gemini: %s hit its free %s limit, trying the next model", m,
                                     "daily" if daily else "per-minute")
                            break
                        if e.code not in (500, 502, 503, 504):
                            raise
                        last = e
                        if attempt == 0:
                            await asyncio.sleep(1.2)
                            continue
                        self._busy_until[m] = time.monotonic() + 300
                        log.info("gemini: %s is busy (%s), trying the next model", m, e.code)
            if lap == 0 and isinstance(last, _Refused) and last.code == 429 and "PerDay" not in last.detail:
                m_ = re.search(r'"retryDelay":\s*"(\d+)', last.detail)
                wait = min(60, int(m_.group(1)) + 1 if m_ else 60)
                log.info("gemini: every model is at its per-minute limit; waiting %s s", wait)
                yield {"candidates": [{"content": {"parts": [
                    {"text": "One moment, sir: Gemini's free allowance needs a short breather. "}]}}]}
                await asyncio.sleep(wait)
                self._busy_until.clear()
                continue
            break
        if isinstance(last, _Refused) and last.code == 429:
            raise _Refused(429, last.detail)
        raise RuntimeError("Gemini's servers are overloaded right now; try again in a minute") from last

    def _skip_signatures(self):
        for c in self.contents:
            for part in c["parts"]:
                if "thoughtSignature" in part:
                    part["thoughtSignature"] = "skip_thought_signature_validator"

    async def _stream(self, model: str):
        body = {"systemInstruction": {"parts": [{"text": gemini_prompt()}]},
                "contents": self._view(),
                "tools": [{"functionDeclarations": declarations()}],
                "generationConfig": {"temperature": 0.7}}
        if self._thinking:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}   # quick voice replies
        url = f"{API}/models/{model}:streamGenerateContent"
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10)) as c:
            async with c.stream("POST", url, params={"alt": "sse"}, headers=self._headers(), json=body) as r:
                if r.status_code >= 400:
                    raise _Refused(r.status_code, (await r.aread()).decode(errors="ignore"))
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[5:].strip() or "{}")

    async def ask(self, text: str, images: list[dict] | None = None):
        if self.status != "ready":
            await self.start()
        if self.status == "needs_key":
            yield ("error", "I need your Gemini API key first, sir. Paste it in the box on screen.")
            return
        if self.status != "ready":
            yield ("error", f"The Gemini brain isn't available: {self.error}.")
            return
        self.busy, self._interrupted, self._stop = True, False, asyncio.Event()
        parts = [{"text": text}]
        for im in images or []:
            parts.append({"inlineData": {"mimeType": im.get("media_type", "image/jpeg"),
                                         "data": _small_picture(im["data"])[0]}})
        self.turn_starts.append(len(self.contents))
        self.contents.append({"role": "user", "parts": parts})
        try:
            rounds, signatures_skipped = 0, False
            while rounds < MAX_ROUNDS:
                model_parts, calls, buf = [], [], ""
                stream = self._stream_any()
                try:
                    async for chunk in stream:
                        if self._interrupted:
                            break
                        cand = (chunk.get("candidates") or [{}])[0]
                        for p in (cand.get("content") or {}).get("parts", []):
                            model_parts.append(p)
                            if p.get("functionCall"):
                                calls.append(p["functionCall"])
                            elif p.get("text") and not p.get("thought"):
                                buf += p["text"]
                                yield ("delta", p["text"])
                                while (s := _SENT_END.search(buf)):
                                    out, buf = buf[:s.end()].strip(), buf[s.end():]
                                    if out:
                                        yield ("sentence", out)
                except _Refused as e:
                    if "thinking" in e.detail.lower() and self._thinking:
                        self._thinking = False          # this model has no thinking setting
                        continue
                    if "signature" in e.detail.lower() and not signatures_skipped:
                        signatures_skipped = True       # history came from another Gemini model
                        self._skip_signatures()
                        continue
                    if e.code == 429:
                        raise RuntimeError("Gemini's usage limit for this key is used up for now")
                    raise RuntimeError(f"Gemini said {e.code}: {e.detail[:160]}")
                finally:
                    await stream.aclose()
                rounds += 1
                if buf.strip():
                    yield ("sentence", buf.strip())
                if model_parts:
                    self.contents.append({"role": "model", "parts": model_parts})
                if self._interrupted or not calls:
                    return
                yield ("delta", "\n")
                answers, pics = [], []
                for call in calls:                  # every call needs an answer, even after STOP
                    name, args = call.get("name", ""), call.get("args") or {}
                    if self._interrupted:
                        out, got = f"Not done: {CALL} pressed STOP.", []
                    else:
                        yield ("tool", f"mcp__{tools.SERVER_NAME}__{name}" if name in PC else name, args)
                        out, got = await self._run_tool_or_stop(name, args)
                        yield ("tool_result", name, out[:300], getattr(self, "tool_failed", False))
                    answer = {"name": name, "response": {"result": out}}
                    if call.get("id"):
                        answer["id"] = call["id"]          # newer models number their calls
                    answers.append({"functionResponse": answer})
                    pics += got
                self.contents.append({"role": "user", "parts": answers})
                if pics:
                    self.contents.append({"role": "user", "parts": [{"text": "(The picture from that tool.)"}] +
                                          [{"inlineData": {"mimeType": "image/jpeg", "data": b}} for b in pics]})
                if self._interrupted:
                    return
            yield ("sentence", "I've taken a lot of steps on that, sir, so I'll stop here.")
        except Exception as e:
            log.exception("gemini turn failed")
            yield ("error", f"My Gemini brain had a problem: {str(e)[:200]}")
        finally:
            if len(self.turn_starts) > 40:
                cut = self.turn_starts[-KEEP_TURNS]
                self.contents = self.contents[cut:]
                self.turn_starts = [i - cut for i in self.turn_starts[-KEEP_TURNS:]]
            self.busy = False


class _Refused(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


__all__ = ["GeminiBrain", "pretty", "KEY_FILE", "LOCAL_TOOLS", "LOCAL_PROMPT"]
