"""Jarvis's hands on Dr Wolf's phone.

The Jarvis Hands app on the phone (same Wi-Fi) long-polls this server for one phone action at a
time and posts back what happened. The brain drives it through the phone_* tools in tools.py,
which call PHONE.do(...). Everything runs only while the phone app is connected and switched on;
if it isn't, the tools say so plainly instead of pretending.
"""
import asyncio
import time
import uuid


class PhoneHands:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.waiting: dict[str, asyncio.Future] = {}
        self.last_pull = 0.0                        # when the phone last asked for work

    def connected(self) -> bool:
        return time.time() - self.last_pull < 90    # it long-polls every ~30 s while on

    async def do(self, tool: str, args: dict, timeout: float = 40) -> str:
        """Queue one action for the phone and wait for its result. Returns spoken text."""
        if not self.connected():
            return ("Your phone isn't connected, sir. Open Jarvis Hands on the phone, switch me on, "
                    "and make sure it's on the same Wi-Fi.")
        aid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self.waiting[aid] = fut
        await self.queue.put({"id": aid, "tool": tool, "args": args or {}})
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return "The phone didn't answer in time. Is Jarvis Hands still on and the screen awake?"
        finally:
            self.waiting.pop(aid, None)

    async def pull(self, wait: float = 25) -> dict:
        """The phone asks for the next action (long-poll). {} means nothing to do yet."""
        self.last_pull = time.time()
        try:
            return await asyncio.wait_for(self.queue.get(), wait)
        except asyncio.TimeoutError:
            return {}

    def result(self, data: dict):
        """The phone reports what happened."""
        fut = self.waiting.get(data.get("id"))
        if fut and not fut.done():
            text = data.get("text") or ("something went wrong" if data.get("error") else "done")
            if data.get("error"):
                text = "The phone couldn't do it: " + text
            fut.set_result(text)
