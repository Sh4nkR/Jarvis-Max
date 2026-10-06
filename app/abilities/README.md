# Jarvis's learned abilities

Every `.py` file in this folder is one ability Jarvis learned. You never need to touch them.

**Teach him something new.** Say *"learn how to ..."* (or *"teach yourself to ..."*).
1. Claude Opus researches it, writes a step-by-step plan, and builds the ability on a copy of Jarvis's code.
2. Jarvis checks it: it has to compile, load, include its own test, and make no forbidden moves (deleting files, passwords, settings, shells, sending things as you).
3. The plan and the code come up on screen. **ALLOW** adds the ability. **DENY**, or no answer in 15 minutes, leaves Jarvis as he was.
4. Restart Jarvis. He tests the new ability himself, then tells you what to say to use it.
5. Try it yourself. If it misbehaves, say *"fix the <name> ability: <what went wrong>"*.

**See them:** *"what abilities have you learned?"*
**Remove one:** *"forget the <name> ability"*. It moves to `_disabled` and is never deleted. Move it back here to restore it.

A learned ability that changes anything asks for your ALLOW the first time it's used, the same as his other hands. One that fails to load is skipped, and Jarvis keeps working.

The record of everything he learned or refused: `logs\abilities\history.md`. Each attempt's plan and code: `logs\abilities\learn-<date>\`.
