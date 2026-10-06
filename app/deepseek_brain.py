"""The DeepSeek brain: DeepSeek's models through the DeepSeek API (Dr Wolf's own API key).

Same reach as the local and Gemini brains: the web, his screen and camera, mouse and keyboard,
files, PowerShell (each command needs his ALLOW), memory notes and switch_brain. It borrows the
local brain's tools and rules; DeepSeek speaks the OpenAI chat format, so only the transport differs.

DeepSeek reads text only: screenshots from look_at_screen reach it as their words (read_screen_text
is the way it sees). The key lives in Jarvis-Max/secrets/deepseek_key.txt (never uploaded to GitHub).
"deepseek_model" in jarvis.json picks the model; "auto" takes deepseek-v4-flash ("deepseek-v4-pro" for the bigger one).
Online only: with no key the window asks for one (no local stand-in model).
"""
import asyncio
import json
import logging
import os

import httpx

import tools
from common import load_config, read_secret
from local_brain import (CALL, MAX_ROUNDS, OLLAMA, PC, REPLY_ROOM, LocalBrain, _SENT_END, fit,
                         system_prompt, tool_specs)

log = logging.getLogger("jarvis.deepseek")

API = os.environ.get("JARVIS_DEEPSEEK_API", "https://api.deepseek.com")
KEY_FILE = "deepseek_key.txt"
BUDGET = 48000                       # tokens of conversation sent each time (keeps the bill small)

_OLD = 'running on your local brain: a model on {c}\'s own PC instead of Claude.\nIf he asks which brain you\'re on, say "my local brain, Qwen".'
_NEW = ('running on the DeepSeek brain (DeepSeek\'s model, through {c}\'s own API key) instead of Claude.\n'
        'If he asks which brain you\'re on, say "DeepSeek". You can\'t see pictures: to see the screen, '
        'use read_screen_text or see_buttons, whose words and numbers you can read.')


_FREE = ('running on the DeepSeek brain (DeepSeek R1, a free model on {c}\'s own PC, no API key) instead of Claude.\n'
         'If he asks which brain you\'re on, say "DeepSeek". You can\'t see pictures: to see the screen, '
         'use read_screen_text or see_buttons, whose words and numbers you can read.')

# With no API key, DeepSeek runs free on this PC through Ollama (the same engine as the local brain).
FREE_MODEL = os.environ.get("JARVIS_DEEPSEEK_LOCAL", "deepseek-r1:8b")


def deepseek_prompt(free: bool = False) -> str:
    base = system_prompt()
    old, new = _OLD.format(c=CALL), (_FREE if free else _NEW).format(c=CALL)
    return base.replace(old, new) if old in base else base + "\n(Right now you are on the DeepSeek brain. You can't see pictures.)"


def pretty(model_id: str) -> str:
    """deepseek-chat -> DeepSeek Chat"""
    rest = (model_id or "").removeprefix("deepseek-").replace("-", " ").title()
    return f"DeepSeek {rest}".strip() if model_id else "DeepSeek"


class DeepSeekBrain(LocalBrain):
    def __init__(self, permission_gate=None):
        super().__init__(permission_gate)
        self.key = ""
        self.model_id = ""
        self.free = False               # True: no key, so DeepSeek runs free on this PC (Ollama)

    @property
    def model(self) -> str:
        if self.free:
            return FREE_MODEL
        return self.model_id or (load_config().get("deepseek_model") or "auto")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    async def _start_free(self):
        """No API key: run DeepSeek on this PC through Ollama, downloading the model if needed."""
        self.free = True
        await LocalBrain.start(self)            # starts Ollama; checks FREE_MODEL is downloaded
        if self.status == "ready":
            self.model_id = FREE_MODEL
            return
        if await self._up() is None:
            return                              # Ollama itself is missing; its error stands
        log.info("deepseek: downloading %s (free, no key)", FREE_MODEL)
        self.status, self.error = "starting", f"still downloading {FREE_MODEL}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3600, connect=10)) as c:
                r = await c.post(f"{OLLAMA}/api/pull", json={"model": FREE_MODEL, "stream": False})
                r.raise_for_status()
        except Exception as e:
            self.status, self.error = "error", f"couldn't download {FREE_MODEL} ({str(e)[:120]})"
            return
        await LocalBrain.start(self)
        if self.status == "ready":
            self.model_id = FREE_MODEL

    async def start(self):
        self.status, self.error = "starting", ""
        self.key = read_secret(KEY_FILE)
        if not self.key:
            # online DeepSeek V4 only: no local stand-in. The window asks for the key (KEY NEEDED).
            self.free = False
            self.status, self.error = "needs_key", "DeepSeek V4 needs his DeepSeek API key (platform.deepseek.com)"
            return
        self.free = False
        want = (load_config().get("deepseek_model") or "auto").strip()
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{API}/models", headers=self._headers())
            if r.status_code in (401, 403):
                log.warning("deepseek: key refused (%s): %s", r.status_code, r.text[:200])
                self.status, self.error = "needs_key", "DeepSeek refused the saved key; paste a new one"
                return
            r.raise_for_status()
            names = [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            self.status, self.error = "error", f"couldn't reach DeepSeek ({str(e)[:120]})"
            return
        if want != "auto":
            if want not in names:
                self.status, self.error = "error", f"the DeepSeek model {want} isn't available to this key"
                return
            self.model_id = want
        else:
            # V4: deepseek-v4-flash (fast, cheap) first; the old deepseek-chat alias was retired in July 2026
            pick = [n for n in ("deepseek-v4-flash", "deepseek-v4-pro") if n in names] or \
                   [n for n in names if "v4" in n] or names
            self.model_id = pick[0] if pick else ""
            if not self.model_id:
                self.status, self.error = "error", "this key can't use any DeepSeek model"
                return
        self.status = "ready"
        log.info("deepseek brain ready (%s)", self.model_id)

    async def restart(self):
        self.messages.clear()
        await self.start()

    # ------------------------------------------------------------ one turn ----
    def _wire(self) -> list[dict]:
        """Our stored conversation in the exact shape DeepSeek accepts (no pictures, no private keys)."""
        out = []
        for m in fit(self.messages, BUDGET):
            m = {k: v for k, v in m.items() if not k.startswith("_") and k != "images"}
            out.append(m)
        while out and out[0].get("role") == "tool":     # a cut never starts on an orphaned tool answer
            out.pop(0)
        return out

    async def _chat(self, tight: bool = False):
        if self.free:                                   # same as the local brain, DeepSeek's own words
            async for d in self._chat_free(tight):
                yield d
            return
        body = {"model": self.model_id, "stream": True, "temperature": 0.7, "tools": tool_specs(),
                "messages": [{"role": "system", "content": deepseek_prompt()}] + self._wire()}
        async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as c:
            async with c.stream("POST", f"{API}/chat/completions", headers=self._headers(), json=body) as r:
                if r.status_code >= 400:
                    raise _Refused(r.status_code, (await r.aread()).decode(errors="ignore"))
                async for line in r.aiter_lines():
                    data = line[5:].strip() if line.startswith("data:") else ""
                    if data and data != "[DONE]":
                        yield json.loads(data)

    async def _chat_free(self, tight: bool):
        system, specs = deepseek_prompt(free=True), tool_specs()
        fixed = int((len(system) + len(json.dumps(specs))) / 3.2) + 200
        budget = (self.ctx - REPLY_ROOM - fixed) // (2 if tight else 1)
        body = {"model": FREE_MODEL, "stream": True, "keep_alive": "30m", "tools": specs, "think": False,
                "options": {"num_ctx": self.ctx},
                "messages": [{"role": "system", "content": system}]
                + [{k: v for k, v in m.items() if k != "images"} for m in fit(self.messages, max(budget, 800))]}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=5)) as c:
            async with c.stream("POST", f"{OLLAMA}/api/chat", json=body) as r:
                if r.status_code >= 400:
                    raise RuntimeError(f"DeepSeek said {r.status_code}: {(await r.aread()).decode(errors='ignore')[:160]}")
                async for line in r.aiter_lines():
                    if line.strip():
                        yield json.loads(line)

    async def ask(self, text: str, images: list[dict] | None = None):
        if self.status != "ready":
            await self.start()
        if self.free and self.status == "ready":        # the local brain's loop, with DeepSeek's prompt
            note = " (He also sent a picture, but you can't see pictures; say so if it matters.)" if images else ""
            async for ev in LocalBrain.ask(self, text + note, None):
                yield ev
            return
        if self.free and self.status == "starting":
            yield ("error", f"DeepSeek is still downloading to this PC, sir. Give it a few minutes.")
            return
        if self.status == "needs_key":
            yield ("error", "I need your DeepSeek API key first, sir. Paste it in the box on screen.")
            return
        if self.status != "ready":
            yield ("error", f"The DeepSeek brain isn't available: {self.error}.")
            return
        self.busy, self._interrupted, self._stop = True, False, asyncio.Event()
        note = " (He also sent a picture, but you can't see pictures; say so if it matters.)" if images else ""
        self.messages.append({"role": "user", "content": text + note, "_turn": True})
        try:
            rounds = 0
            while rounds < MAX_ROUNDS:
                calls: dict[int, dict] = {}
                buf, said = "", ""
                stream = self._chat()
                try:
                    async for d in stream:
                        if self._interrupted:
                            break
                        delta = ((d.get("choices") or [{}])[0]).get("delta") or {}
                        for tc in delta.get("tool_calls") or []:   # tool calls arrive in pieces
                            slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["arguments"] += fn.get("arguments") or ""
                        piece = delta.get("content") or ""
                        if piece:
                            said += piece
                            buf += piece
                            yield ("delta", piece)
                            while (s := _SENT_END.search(buf)):
                                out, buf = buf[:s.end()].strip(), buf[s.end():]
                                if out:
                                    yield ("sentence", out)
                except _Refused as e:
                    if e.code == 402:
                        raise RuntimeError("the DeepSeek account has run out of credit")
                    if e.code == 429:
                        raise RuntimeError("DeepSeek is rate-limiting this key right now")
                    raise RuntimeError(f"DeepSeek said {e.code}: {e.detail[:160]}")
                finally:
                    await stream.aclose()
                rounds += 1
                if buf.strip():
                    yield ("sentence", buf.strip())
                order = [calls[i] for i in sorted(calls)]
                msg = {"role": "assistant", "content": said}
                if order:
                    msg["tool_calls"] = [{"id": c["id"] or f"call_{rounds}_{i}", "type": "function",
                                          "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                                         for i, c in enumerate(order)]
                self.messages.append(msg)
                if self._interrupted or not order:
                    return
                if said:
                    yield ("delta", "\n")
                for call in msg["tool_calls"]:               # every call needs an answer, even after STOP
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    if self._interrupted:
                        out = f"Not done: {CALL} pressed STOP."
                    else:
                        yield ("tool", f"mcp__{tools.SERVER_NAME}__{name}" if name in PC else name, args)
                        out, pics = await self._run_tool_or_stop(name, args)
                        if pics:
                            out += "\n(A picture came back too, which you can't see. Use read_screen_text for its words.)"
                        yield ("tool_result", name, out[:300], getattr(self, "tool_failed", False))
                    self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": out})
                if self._interrupted:
                    return
            yield ("sentence", "I've taken a lot of steps on that, sir, so I'll stop here.")
        except Exception as e:
            log.exception("deepseek turn failed")
            yield ("error", f"My DeepSeek brain had a problem: {str(e)[:200]}")
        finally:
            del self.messages[:-80]
            while self.messages and self.messages[0].get("role") == "tool":
                self.messages.pop(0)
            self.busy = False


class _Refused(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(detail)
        self.code, self.detail = code, detail


__all__ = ["DeepSeekBrain", "pretty", "KEY_FILE"]
