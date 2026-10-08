"""The Kimi brains: Moonshot AI's Kimi K3 and Kimi K2.8 through the Kimi API (Dr Wolf's own API key).

Same reach as the other brains: the web, his screen, mouse and keyboard, phone, files, PowerShell
(each command needs his ALLOW), memory notes, learned abilities and switch_brain. Kimi speaks the
OpenAI chat format, so it reuses the DeepSeek brain's turn loop; only the address, the model and a
few request rules differ:
- K3 always thinks; "kimi_effort" in jarvis.json (low / high / max, default low for quick voice
  replies) sets how hard. Temperature and other sampling settings are fixed by Kimi: never sent.
- Kimi needs the whole assistant message back after a tool call, including its reasoning, so the
  reasoning is kept in the conversation (DeepSeek's is not).
The key lives in Jarvis-Max/secrets/kimi_key.txt (never uploaded). "kimi_model" picks the model:
kimi-k3, or kimi-k2.8 (if the key can't use kimi-k2.8, the newest Kimi K2 model it can use is taken).
Text only for now: pictures reach it as words (read_screen_text), like DeepSeek.
"""
import json
import logging
import os
import re

import httpx

from common import load_config, read_secret
from deepseek_brain import DeepSeekBrain, _Refused
from local_brain import CALL, system_prompt, tool_specs

log = logging.getLogger("jarvis.kimi")

API = os.environ.get("JARVIS_KIMI_API", "https://api.moonshot.ai/v1")
KEY_FILE = "kimi_key.txt"

_OLD = 'running on your local brain: a model on {c}\'s own PC instead of Claude.\nIf he asks which brain you\'re on, say "my local brain, Qwen".'
_NEW = ('running on the Kimi brain ({m}, Moonshot AI\'s model, through {c}\'s own API key) instead of Claude.\n'
        'If he asks which brain you\'re on, say "{m}". You can\'t see pictures here: to see the screen, '
        'use read_screen_text or see_buttons, whose words and numbers you can read.')


def pretty(model_id: str) -> str:
    """kimi-k3 -> Kimi K3, kimi-k2.8 -> Kimi K2.8"""
    m = re.match(r"kimi-(k[\d.]+)(.*)$", model_id or "")
    if not m:
        return "Kimi"
    rest = m.group(2).replace("-", " ").strip().title()
    return f"Kimi {m.group(1).upper()}" + (f" {rest}" if rest else "")


class KimiBrain(DeepSeekBrain):
    NAME = "Kimi"
    KEEP_REASONING = True

    @property
    def model(self) -> str:
        return self.model_id or (load_config().get("kimi_model") or "kimi-k3")

    async def start(self):
        self.status, self.error, self.free = "starting", "", False
        self.key = read_secret(KEY_FILE)
        if not self.key:
            self.status, self.error = "needs_key", "Kimi needs his Kimi API key (platform.kimi.ai)"
            return
        want = (load_config().get("kimi_model") or "kimi-k3").strip()
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{API}/models", headers=self._headers())
            if r.status_code in (401, 403):
                log.warning("kimi: key refused (%s): %s", r.status_code, r.text[:200])
                self.status, self.error = "needs_key", "Kimi refused the saved key; paste a new one"
                return
            r.raise_for_status()
            names = [m["id"] for m in r.json().get("data", [])]
        except Exception as e:
            self.status, self.error = "error", f"couldn't reach Kimi ({str(e)[:120]})"
            return
        if want in names:
            self.model_id = want
        elif want.startswith("kimi-k2"):
            # K2.8 not offered to this key yet: the newest general K2 model it can use (not the -code ones)
            k2 = sorted((n for n in names if re.fullmatch(r"kimi-k2\.\d+", n)),
                        key=lambda n: float(n.split("k2.")[1]), reverse=True)
            self.model_id = k2[0] if k2 else ""
        else:
            self.model_id = ""
        if not self.model_id:
            self.status, self.error = "error", f"this Kimi key can't use {want}"
            return
        self.status = "ready"
        log.info("kimi brain ready (%s)", self.model_id)

    def _prompt(self) -> str:
        base, old = system_prompt(), _OLD.format(c=CALL)
        new = _NEW.format(c=CALL, m=pretty(self.model_id))
        return base.replace(old, new) if old in base else \
            base + f"\n(Right now you are on the Kimi brain, {pretty(self.model_id)}. You can't see pictures.)"

    async def _chat(self, tight: bool = False):
        body = {"model": self.model_id, "stream": True, "tools": tool_specs(),
                "messages": [{"role": "system", "content": self._prompt()}] + self._wire()}
        if self.model_id.startswith("kimi-k3"):
            body["reasoning_effort"] = (load_config().get("kimi_effort") or "low").lower()
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
            async with c.stream("POST", f"{API}/chat/completions", headers=self._headers(), json=body) as r:
                if r.status_code >= 400:
                    raise _Refused(r.status_code, (await r.aread()).decode(errors="ignore"))
                async for line in r.aiter_lines():
                    data = line[5:].strip() if line.startswith("data:") else ""
                    if data and data != "[DONE]":
                        yield json.loads(data)


__all__ = ["KimiBrain", "pretty", "KEY_FILE"]
