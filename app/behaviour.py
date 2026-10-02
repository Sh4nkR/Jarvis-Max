"""Behaviour Layer: what Jarvis is doing, made observable, honest and logged (TANTRA-06).

The server tells this module what really happens (a turn starts, a tool runs, the tool succeeds or
fails, a permission is asked, the turn ends). From those facts alone it derives:

  activity   which kind of job (build, browse, phone, code ...), from the tool's NAME, not guesswork
  step       step number in this turn, fails in a row, retries, seconds busy
  risk       is something waiting for his ALLOW
  mood       a working-state word (focused, frustrated, relieved, triumphant ...) computed from the
             facts above. It describes the work, it is not a feeling, and it may never contradict
             them: no happy face on a failing step.
  vitals     a small game layer that rewards real work: XP and level (only for steps that really
             succeeded), today's streak, morale (recent success rate) and energy (drains with long
             work, refills when idle or after lessons).

Every event is broadcast to the window ({"type": "behave"}) and appended to logs/behaviour.jsonl,
so the face, the phone and Dr Wolf all see the same truth, and the nightly lessons can read it.
"""
import json
import time
from pathlib import Path

ACTIVITY = {   # tool name -> activity (the face shows a prop for each)
    "look_at_screen": "look", "read_screen_text": "look", "see_buttons": "look", "look_closer": "look",
    "read_whole_page": "browse", "open_url": "browse", "read_webpage": "browse", "WebFetch": "browse",
    "click_text": "hands", "click_button": "hands", "click_at": "hands", "press_keys": "hands", "scroll": "hands",
    "open_app": "hands", "focus_window": "hands", "list_windows": "hands", "type_text": "typing",
    "web_search": "search", "WebSearch": "search", "Grep": "search", "Glob": "search",
    "research": "research", "multi_search": "research", "smart_search": "research",
    "project_start": "build", "project_next": "build", "project_status": "build", "android_build": "build",
    "github_build": "build", "github_build_status": "build", "github_publish": "build", "Bash": "build",
    "PowerShell": "build", "run_command": "build",
    "project_create": "code", "Write": "code", "Edit": "code", "MultiEdit": "code", "write_file": "code",
    "project_ask": "consult",
    "phone_call": "call", "phone_type": "message",
    "phone_read_screen": "phone", "phone_open_app": "phone", "phone_tap_text": "phone", "phone_scroll": "phone",
    "phone_button": "phone",
    "run_self_test": "selfcheck", "start_lessons": "lessons", "add_to_note": "notes", "read_note": "notes",
    "list_notes": "notes", "Read": "reading", "read_file": "reading", "samasa": "samasa",
    "switch_brain": "brain", "switch_voice": "voice", "look_through_camera": "camera",
}
LABEL = {"look": "looking at the screen", "browse": "browsing", "hands": "working the PC", "typing": "typing",
         "search": "searching", "research": "researching", "build": "building", "code": "writing code",
         "consult": "consulting another AI", "call": "on a phone call", "message": "messaging", "phone": "using your phone",
         "selfcheck": "checking himself", "lessons": "at his lessons", "notes": "taking notes", "reading": "reading",
         "samasa": "running a codeword", "brain": "switching brain", "voice": "changing voice", "camera": "using the camera",
         "think": "thinking", "talk": "answering", "wait": "waiting for your ALLOW", "idle": "idle"}
XP_STEP, XP_TURN, XP_LESSON = 5, 10, 40


def level_of(xp: int) -> tuple[int, int, int]:
    """level, xp into this level, xp needed for the next one (each level needs 25 % more)."""
    lvl, need, left = 1, 150, xp
    while left >= need:
        left -= need
        lvl += 1
        need = int(need * 1.25)
    return lvl, left, need


class Behaviour:
    def __init__(self, send, logs: Path):
        self.send = send                              # async (dict) -> None, broadcast to every window
        self.log_path = logs / "behaviour.jsonl"
        self.state_path = logs / "behaviour-state.json"
        self.turn = 0
        self.t0 = 0.0
        self.step = 0
        self.fails_row = 0
        self.retries = 0
        self.activity = "idle"
        self.last_tool = ""
        self.recent: list[int] = []                   # 1 = step ok, 0 = failed (last 30 steps)
        self.pending_allow = False
        self.v = {"xp": 0, "day": "", "jobs_today": 0, "steps_today": 0, "fails_today": 0, "streak": 0,
                  "best_streak": 0, "energy": 100.0, "last_active": time.time()}
        try:
            self.v.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ helpers ----
    def _day(self):
        d = time.strftime("%Y-%m-%d")
        if self.v.get("day") != d:
            self.v.update(day=d, jobs_today=0, steps_today=0, fails_today=0, streak=0)

    def _energy(self, now: float):
        idle = now - self.v.get("last_active", now)
        if idle > 30:                                 # rest refills energy: about 1 point a minute
            self.v["energy"] = min(100.0, self.v["energy"] + idle / 60.0)
        self.v["last_active"] = now

    def morale(self) -> int:
        r = self.recent[-12:]
        if not r:
            return 70
        return int(30 + 70 * sum(r) / len(r))

    def mood(self, phase: str) -> str:
        """A working-state word from facts only. Errors and waits always win."""
        busy = time.time() - self.t0 if self.t0 else 0
        if phase == "error" or self.fails_row >= 3:
            return "low" if self.fails_row >= 3 else "startled"
        if self.pending_allow:
            return "worried" if self.activity in ("build", "call", "message", "code") else "polite"
        if self.fails_row == 2:
            return "frustrated"
        if self.fails_row == 1:
            return "puzzled"
        if phase == "recovered":
            return "relieved"
        if phase == "done":
            if self.retries:
                return "phew"
            return "triumphant" if self.step >= 3 else ("proud" if self.step else "content")
        if phase == "failed":
            return "disappointed"
        if self.v["energy"] < 20:
            return "exhausted"
        if busy > 180:
            return "straining"
        if busy > 60:
            return "determined"
        if phase == "start":
            return "confident" if self.v["streak"] >= 5 else "attentive"
        return {"build": "determined", "code": "ultra", "research": "contemplative", "search": "scrutinising",
                "browse": "curious", "hands": "focused", "typing": "concentrating", "consult": "confident",
                "call": "happy", "message": "playful", "phone": "focused", "selfcheck": "serene",
                "lessons": "interested", "notes": "content", "reading": "interested", "samasa": "confident",
                "brain": "surprised", "voice": "regal", "look": "attentive"}.get(self.activity, "focused")

    def vitals(self) -> dict:
        lvl, into, need = level_of(int(self.v["xp"]))
        return {"level": lvl, "xp": int(self.v["xp"]), "xp_in": into, "xp_need": need,
                "morale": self.morale(), "energy": int(self.v["energy"]), "streak": self.v["streak"],
                "best_streak": self.v["best_streak"], "jobs_today": self.v["jobs_today"],
                "steps_today": self.v["steps_today"], "fails_today": self.v["fails_today"]}

    async def _emit(self, phase: str, **extra):
        now = time.time()
        ev = {"type": "behave", "t": round(now, 2), "turn": self.turn, "phase": phase, "activity": self.activity,
              "label": LABEL.get(self.activity, self.activity), "step": self.step, "fails_row": self.fails_row,
              "retries": self.retries, "busy_s": int(now - self.t0) if self.t0 else 0,
              "allow": self.pending_allow, "mood": self.mood(phase), "vitals": self.vitals()}
        ev.update(extra)
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({k: v for k, v in ev.items() if k != "type"}, ensure_ascii=False) + "\n")
            self.state_path.write_text(json.dumps(self.v), encoding="utf-8")
        except OSError:
            pass
        try:
            await self.send(ev)
        except Exception:
            pass

    def _gain(self, xp: int) -> int | None:
        before = level_of(int(self.v["xp"]))[0]
        self.v["xp"] = int(self.v["xp"]) + xp
        after = level_of(int(self.v["xp"]))[0]
        return after if after > before else None

    # ------------------------------------------------------- events (facts) ----
    async def turn_start(self, text: str, brain: str):
        now = time.time()
        self._day()
        self._energy(now)
        self.turn += 1
        self.t0, self.step, self.fails_row, self.retries = now, 0, 0, 0
        self.activity = "think"
        await self._emit("start", brain=brain, said=text[:120])

    async def tool(self, name: str, detail: str):
        name = name.split("__")[-1]
        act = ACTIVITY.get(name, "hands")
        if name == self.last_tool and self.fails_row:
            self.retries += 1
        self.last_tool = name
        self.activity = act
        self.step += 1
        self.v["energy"] = max(0.0, self.v["energy"] - (1.5 if act in ("build", "code", "research") else .6))
        await self._emit("step", tool=name, detail=detail[:140])

    async def tool_result(self, name: str, failed: bool, text: str = ""):
        was_failing = self.fails_row > 0
        self.recent = (self.recent + [0 if failed else 1])[-30:]
        self.v["steps_today"] += 1
        up = None
        if failed:
            self.fails_row += 1
            self.v["fails_today"] += 1
        else:
            self.fails_row = 0
            up = self._gain(XP_STEP)
        phase = "failed_step" if failed else ("recovered" if was_failing else "step_ok")
        await self._emit(phase, tool=name.split("__")[-1], result=(text or "")[:140], level_up=up)

    async def allow(self, asked: bool, detail: str = "", allowed: bool | None = None):
        self.pending_allow = asked
        if asked:
            await self._emit("allow", detail=detail[:160])
        else:
            await self._emit("allowed" if allowed else "denied")

    async def error(self, msg: str):
        await self._emit("error", error=msg[:200])

    async def turn_end(self, errors: int):
        ok = errors == 0 and self.fails_row == 0
        if self.step == 0:
            self.activity = "talk"
        self.v["jobs_today"] += 1
        if ok:
            self.v["streak"] += 1
            self.v["best_streak"] = max(self.v["best_streak"], self.v["streak"])
        else:
            self.v["streak"] = 0
        up = self._gain(XP_TURN + (5 * min(self.step, 6) if ok else 0)) if ok else None
        await self._emit("done" if ok else "failed", level_up=up)
        self.t0 = 0
        self.activity = "idle"
        self.pending_allow = False
        self.v["last_active"] = time.time()

    async def lessons_done(self, score: str = ""):
        self.v["energy"] = 100.0                      # a night's study: fully rested
        up = self._gain(XP_LESSON)
        self.activity = "lessons"
        await self._emit("lessons_done", score=score, level_up=up)
        self.activity = "idle"

    def snapshot(self, last: int = 60) -> dict:
        """For /api/behaviour: the vitals and the latest events (the journal)."""
        rows = []
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()[-last:]
            rows = [json.loads(l) for l in lines if l.strip()]
        except (OSError, ValueError):
            pass
        return {"vitals": self.vitals(), "activity": self.activity, "mood": self.mood("idle"), "events": rows}
