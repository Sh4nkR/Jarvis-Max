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
import math
import re
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

# ------------------------------------------------------------- emotion engine ----
# 52 emotions placed in PAD space (Mehrabian & Russell): P = pleasant(+)/unpleasant(-), A = aroused(+)/calm(-),
# D = in control(+)/overwhelmed(-), each -1..1. Every name is an expression the living face can draw.
EMOTIONS = {
    "elated": (.80, .70, .50), "joyful": (.75, .55, .40), "triumphant": (.80, .60, .80), "realising": (.45, .65, .35),
    "inspired": (.65, .55, .40), "amused": (.55, .40, .30), "playful": (.55, .45, .35), "proud": (.60, .35, .70),
    "confident": (.45, .25, .65), "determined": (.20, .50, .60), "focused": (.15, .35, .45), "curious": (.35, .40, .15),
    "interested": (.40, .25, .20), "intrigued": (.35, .45, .10), "surprised": (.15, .75, .00), "amazed": (.50, .75, .05),
    "awed": (.50, .55, -.30), "happy": (.70, .30, .35), "grateful": (.65, .10, -.10), "affectionate": (.70, .20, .10),
    "content": (.60, -.15, .30), "pleased": (.60, .15, .30), "relieved": (.50, -.30, .20), "relaxed": (.55, -.50, .30),
    "serene": (.60, -.60, .25), "calm": (.30, -.55, .20), "sleepy": (.00, -.85, -.20), "bored": (-.35, -.60, -.05),
    "exhausted": (-.40, -.65, -.40), "sad": (-.60, -.30, -.35), "disappointed": (-.50, -.05, -.25),
    "melancholy": (-.45, -.45, -.30), "lonely": (-.50, -.35, -.45), "defeated": (-.60, -.40, -.65), "low": (-.70, -.50, -.55),
    "apologetic": (-.30, .10, -.50), "sheepish": (-.20, .20, -.45), "uncertain": (-.15, .15, -.35),
    "puzzled": (-.10, .35, -.15), "confused": (-.25, .40, -.30), "sceptical": (-.15, .20, .25), "worried": (-.45, .45, -.40),
    "anxious": (-.50, .60, -.45), "nervous": (-.35, .55, -.35), "fear": (-.65, .70, -.60), "alarmed": (-.40, .80, -.20),
    "frustrated": (-.55, .60, -.10), "annoyed": (-.40, .45, .15), "angry": (-.65, .75, .40), "startled": (-.15, .85, -.30),
    "thoughtful": (.15, .00, .20), "attentive": (.20, .25, .15),
}
# what each real event does to his feelings (dP, dA, dD); they fade back to a baseline set by morale and energy
IMPULSE = {
    "start": (0, .10, 0), "step_ok": (.06, .02, .04), "failed_step": (-.18, .14, -.12), "recovered": (.35, -.10, .25),
    "done": (.18, -.05, .15), "failed": (-.30, .10, -.20), "allow": (-.05, .20, -.25), "allowed": (.10, -.10, .15),
    "denied": (-.15, 0, -.20), "error": (-.30, .45, -.30), "lessons_done": (.30, -.20, .20),
    "praise": (.40, .15, .10), "scold": (-.35, .20, -.30), "thanks": (.25, 0, 0),
    "dithered": (-.10, .10, -.30),
}
# Confidence: a reply that ends by asking his permission again ("Shall I start?", "Would you like me to...?")
# instead of doing what he asked. Each one is a slip; confidence (0-100) drops, real successes raise it.
DITHER = re.compile(r"(shall i|should i|may i|would you like me to|do you want me to|want me to|can i go ahead|"
                    r"just say the word|shall we)\b[^?]{0,160}\?\s*$", re.I)
PRAISE = re.compile(r"\b(good job|well done|perfect|great job|excellent|shabash|bahut badhiya|brilliant|bravo|"
                    r"amazing|awesome|nice work|that'?s (right|correct|perfect)|good boy)\b", re.I)
THANKS = re.compile(r"\b(thank(s| you)|dhanyavaad|shukriya)\b", re.I)
SCOLD = re.compile(r"\b(stupid|useless|idiot|wrong again|you failed|bad job|pathetic|rubbish|nonsense|"
                   r"what is your problem|damn|wtf|shit|bakwas|bekaar)\b", re.I)
FADE_S = 90                                           # feelings settle back with a ~90 s time constant


def clamp(x: float) -> float:
    return max(-1.0, min(1.0, x))


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
                  "best_streak": 0, "energy": 100.0, "last_active": time.time(), "confidence": 70.0, "dithers_today": 0}
        try:
            self.v.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        self.pad = list(self.v.get("pad") or (.3, -.2, .2))   # his feelings now (P, A, D), kept across restarts
        self.pad_t = time.time()
        self.emotion = "calm"

    # ------------------------------------------------------------ helpers ----
    def _day(self):
        d = time.strftime("%Y-%m-%d")
        if self.v.get("day") != d:
            self.v.update(day=d, jobs_today=0, steps_today=0, fails_today=0, streak=0, dithers_today=0)

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

    # ------------------------------------------------------------ feelings ----
    def _baseline(self, now: float) -> tuple[float, float, float]:
        """Where his feelings settle when nothing happens: set by morale, energy, streak and how long he's been alone."""
        m, e = (self.morale() - 65) / 35, self.v["energy"] / 100
        p, a, d = .15 + .35 * m + .02 * min(self.v["streak"], 5), -.30 + .45 * e, .10 + .25 * m
        if e < .3:                                    # worn out: everything feels heavier
            p, a, d = p - (.3 - e) * 2, a - .3, d - (.3 - e)
        alone = (now - self.v.get("last_active", now)) / 60 if not self.t0 else 0
        if alone > 10:                                # left alone: drowsy; after half an hour a little lonely
            a -= .30
        if alone > 30:
            p, d = p - .25, d - .25
        return p, a, d

    def _settle(self, now: float):
        k = 1 - math.exp(-max(0.0, now - self.pad_t) / FADE_S)
        self.pad = [x + (b - x) * k for x, b in zip(self.pad, self._baseline(now))]
        self.pad_t = now

    def feel(self, kind: str, scale: float = 1.0):
        """A real event moves his feelings; tiredness caps how worked-up he can get."""
        now = time.time()
        self._settle(now)
        # soft limits: the further he already is in a direction, the less a new push moves him (no slamming to the edge)
        self.pad = [clamp(x + d * scale * (1 - x if d > 0 else 1 + x)) for x, d in zip(self.pad, IMPULSE[kind])]
        self.pad[1] = min(self.pad[1], -.8 + 1.9 * self.v["energy"] / 100)
        self.v["pad"] = [round(x, 3) for x in self.pad]

    def _emotion(self, phase: str) -> str:
        """The nearest of the 52 emotions to how he feels now. Facts fence it in: no smile on a failing step,
        no gloom on a job that really succeeded. It sticks a little so the face doesn't flicker."""
        self._settle(time.time())
        bad = self.fails_row > 0 or phase in ("failed", "failed_step", "error", "denied")
        good = not bad and phase in ("done", "recovered", "step_ok", "allowed", "lessons_done")
        pool = {n: c for n, c in EMOTIONS.items() if (not bad or c[0] <= .05) and (not good or c[0] >= 0)}
        dist = {n: math.dist(self.pad, c) for n, c in pool.items()}
        best = min(dist, key=dist.get)
        if self.emotion in dist and dist[self.emotion] <= dist[best] + .06:
            best = self.emotion
        self.emotion = best
        return best

    def mood(self, phase: str) -> str:
        """The face's expression. Errors and waits for his ALLOW are facts and always win; otherwise it's the
        emotion his real experience has built up (see feel)."""
        if phase == "error":
            return "startled"
        if self.pending_allow:
            return "worried" if self.activity in ("build", "call", "message", "code") else "polite"
        return self._emotion(phase)

    def vitals(self) -> dict:
        lvl, into, need = level_of(int(self.v["xp"]))
        return {"level": lvl, "xp": int(self.v["xp"]), "xp_in": into, "xp_need": need,
                "morale": self.morale(), "energy": int(self.v["energy"]), "streak": self.v["streak"],
                "best_streak": self.v["best_streak"], "jobs_today": self.v["jobs_today"],
                "steps_today": self.v["steps_today"], "fails_today": self.v["fails_today"],
                "confidence": int(self.v.get("confidence", 70)), "dithers_today": self.v.get("dithers_today", 0),
                "emotion": self.emotion, "pleasure": round(self.pad[0], 2), "arousal": round(self.pad[1], 2),
                "dominance": round(self.pad[2], 2)}

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
        self.feel("start")
        if SCOLD.search(text):                        # how he speaks to me moves me too
            self.feel("scold")
        elif PRAISE.search(text):
            self.feel("praise")
        elif THANKS.search(text):
            self.feel("thanks")
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
            self._conf(-3)
        else:
            self.fails_row = 0
            up = self._gain(XP_STEP)
        phase = "failed_step" if failed else ("recovered" if was_failing else "step_ok")
        self.feel(phase, 1 + .3 * min(self.fails_row, 3) if failed else 1.0)
        await self._emit(phase, tool=name.split("__")[-1], result=(text or "")[:140], level_up=up)

    async def allow(self, asked: bool, detail: str = "", allowed: bool | None = None):
        self.pending_allow = asked
        if asked:
            self.feel("allow")
            await self._emit("allow", detail=detail[:160])
        else:
            self.feel("allowed" if allowed else "denied")
            await self._emit("allowed" if allowed else "denied")

    async def error(self, msg: str):
        self.feel("error")
        await self._emit("error", error=msg[:200])

    async def drift(self):
        """Every 20 s while idle: feelings settle on their own; tell the face only when the emotion changes."""
        if self.t0 or self.pending_allow:
            return
        before = self.emotion
        if self._emotion("idle") != before:
            self.activity = "idle"
            await self._emit("mood")

    async def feedback(self, good: bool):
        """His GOOD / WRONG buttons."""
        self.feel("praise" if good else "scold")
        await self._emit("praised" if good else "scolded")

    def _conf(self, d: float):
        self.v["confidence"] = max(0.0, min(100.0, float(self.v.get("confidence", 70)) + d))

    async def turn_end(self, errors: int, said: str = "", reply: str = ""):
        ok = errors == 0 and self.fails_row == 0
        if reply and DITHER.search(reply.strip()[-240:]):
            self.v["dithers_today"] = self.v.get("dithers_today", 0) + 1
            self._conf(-8)
            self.feel("dithered")
            await self._emit("dithered", said=said[:120], asked=reply.strip()[-160:])
        elif ok:
            self._conf(+3 if self.step else +1)
        if self.step == 0:
            self.activity = "talk"
        self.v["jobs_today"] += 1
        if ok:
            self.v["streak"] += 1
            self.v["best_streak"] = max(self.v["best_streak"], self.v["streak"])
        else:
            self.v["streak"] = 0
        up = self._gain(XP_TURN + (5 * min(self.step, 6) if ok else 0)) if ok else None
        self.feel("done" if ok else "failed", 1 + .15 * min(self.step, 6) + (.5 if up else 0))
        await self._emit("done" if ok else "failed", level_up=up)
        self.t0 = 0
        self.activity = "idle"
        self.pending_allow = False
        self.v["last_active"] = time.time()

    async def lessons_done(self, score: str = ""):
        self.v["energy"] = 100.0                      # a night's study: fully rested
        up = self._gain(XP_LESSON)
        self.feel("lessons_done")
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
