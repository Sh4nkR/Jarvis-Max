"""Wire the Behaviour Layer into Jarvis (run from Jarvis-Max/app). Every edit checks its anchor first."""
from pathlib import Path


def patch(path, pairs):
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    for old, new in pairs:
        if new in s:
            continue                       # already applied
        assert s.count(old) == 1, f"{path}: anchor not found once: {old[:70]!r} ({s.count(old)})"
        s = s.replace(old, new, 1)
    p.write_text(s, encoding="utf-8")


patch("server.py", [
    ("import phonehands                               # noqa: E402\n",
     "import phonehands                               # noqa: E402\nimport behaviour                                # noqa: E402\n"),
    ("TURNLOG = growth.TurnLog()",
     "TURNLOG = growth.TurnLog()\nBEHAVE = behaviour.Behaviour(lambda ev: HUB.send(ev), LOGS)   # the observable behaviour layer"),
    ('''                            "speaker": HUB.ids.get(HUB.speaker())})\n''',
     '''                            "speaker": HUB.ids.get(HUB.speaker())})\n            await BEHAVE.turn_start(text, answered_by())\n'''),
    ('''                            if ev[3]:\n                                used[-1]["error"] = ev[2][:150]\n                        continue''',
     '''                            if ev[3]:\n                                used[-1]["error"] = ev[2][:150]\n                        await BEHAVE.tool_result(ev[1], bool(ev[3]) if len(ev) > 3 else False, ev[2])\n                        continue'''),
    ('''                        await HUB.send({"type": "tool", "name": ev[1], "detail": describe(ev[1], ev[2])})\n''',
     '''                        await HUB.send({"type": "tool", "name": ev[1], "detail": describe(ev[1], ev[2])})\n                        await BEHAVE.tool(ev[1], describe(ev[1], ev[2]))\n'''),
    ('''                        errors.append(ev[1][:200])\n''',
     '''                        errors.append(ev[1][:200])\n                        await BEHAVE.error(ev[1])\n'''),
    ('''                await HUB.send({"type": "turn_end"})''',
     '''                await BEHAVE.turn_end(len(errors))\n                await HUB.send({"type": "turn_end"})'''),
    ('''    await HUB.send({"type": "permission", "id": pid, "tool": name, "detail": detail})\n''',
     '''    await HUB.send({"type": "permission", "id": pid, "tool": name, "detail": detail})\n    await BEHAVE.allow(True, describe(name, inp))\n'''),
    ('''        ok = bool(await asyncio.wait_for(fut, 120))\n''',
     '''        ok = bool(await asyncio.wait_for(fut, 120))\n        await BEHAVE.allow(False, allowed=ok)\n'''),
    ('''    except asyncio.TimeoutError:\n        await HUB.send({"type": "permission_closed", "id": pid})\n''',
     '''    except asyncio.TimeoutError:\n        await HUB.send({"type": "permission_closed", "id": pid})\n        await BEHAVE.allow(False, allowed=False)\n'''),
    ('''    msg = await GROWTH.run()\n''',
     '''    msg = await GROWTH.run()\n    if "done" in msg:\n        sc = re.search(r"(\\d+) out of (\\d+)", msg)\n        await BEHAVE.lessons_done(f"{sc.group(1)}/{sc.group(2)}" if sc else "")\n'''),
    ('''def build_app() -> web.Application:''',
     '''async def api_behaviour(request):\n    """The behaviour layer: vitals + the latest events (the journal)."""\n    return web.json_response(BEHAVE.snapshot())\n\n\ndef build_app() -> web.Application:'''),
    ('''    app.router.add_post("/api/upload", api_upload)''',
     '''    app.router.add_post("/api/upload", api_upload)\n    app.router.add_get("/api/behaviour", api_behaviour)'''),
])

patch("dock/dock.js", [
    ('''      case "camera_request": cameraRequest(d.id); break;''',
     '''      case "camera_request": cameraRequest(d.id); break;\n      case "behave": window.JV_BEHAVE = d; window.JV_VITALS = d.vitals; break;'''),
])
print("behaviour layer wired")
