# TANTRA 05: "The Living Face": Jarvis's new face (DESIGN SPEC v1)
Architect: Opus · Vision: Dr Wolf · Status: design for review, then production
Replaces: Ink Lotus (kept as a fallback face)

---

## 1. MANTRA: the idea in one breath
A large, faint, holographic **human-like face** floats in the middle of a dark screen.
Everything is drawn in **coloured smoke and neon light**: dreamy, translucent, slow-glowing, with 3D depth.
The face **is never still and never boring**:
- Around it, **holographic props** appear for whatever Jarvis is doing (ear, hands, laptop, globe, mill, phone, dancers...).
- The face **watches its own props**: the eyes follow them, it reacts, smiles, frowns, raises a brow, scratches its head.
- Its **mood** changes with the job, with your tone of voice, and with success or failure.

So anyone in the room can tell at a glance **what Jarvis is doing, how it's going, and how he "feels" about it.**

---

## 2. Art direction
| Rule | Detail |
|---|---|
| Medium | Neon line art + volumetric smoke (soft particles), additive glow on near-black (#03060c). |
| Opacity | Face 35–55 % (faint), props 25–60 %, nothing fully solid except the ERROR skull. |
| Depth | Slow parallax: face in the middle plane, props in front/behind; gentle 3D tilt (±8°) following the action. |
| Motion | Ease-in/ease-out, nothing faster than 300 ms except blinks; smoke always drifting upward. |
| Colour language | Teal = listening · Violet = thinking · Warm gold = speaking · Green = hands/work · Cyan = web · Amber = waiting for ALLOW · Red = error · Pink = social · Orange = calls · Blue = idle |
| Feel | "A dream you can read": beautiful first, informative always. |

---

## 3. The FACE rig (what can move)
Built as a 2.5D neon line face (front + slight turns), driven by ~20 controls:

| Part | Controls |
|---|---|
| Eyes | gaze x/y (tracks props), lid open 0–1, squint, blink, wink L/R, roll |
| Pupils | size (focus = small, surprise = big) |
| Brows | L/R height, inner raise (worry), knit (tension), one-brow arch |
| Forehead | wrinkles (surprise / concentration lines) |
| Nose | flare, scrunch |
| Mouth | open, smile/frown, pucker (kiss/blow), side-smirk, tongue out, lip-bite, lip-lick, speaking visemes (synced to voice level) |
| Cheeks | puff (blow out after a big task) |
| Chin / jaw | drop, tilt, twist left/right ("hmm…") |
| Head / neck | turn, tilt, nod, slight forward flexion (leaning into work), drop (low morale) |
| Hand | one ghost hand for scratching the head / stroking the chin |

### Expression library (each = a preset of the controls above)
Grouped by family. **96 expressions.** Each has a code name the mood engine uses.

**1. Calm & neutral (8):** neutral · calm · serene · attentive · thoughtful · pensive · contemplative · deadpan
**2. Focus & effort (8):** focused · ultra-focused (squint, small pupils, lean in) · determined · concentrating (tongue tip out) · straining · calculating · scrutinising · vigilant
**3. Joy & warmth (12):** happy · joyful (crinkled eyes) · delighted · elated · amused · laughing · giggling · grinning · content · pleased · grateful · affectionate
**4. Pride & power (8):** proud · confident · triumphant · smug · cocky · regal/arrogant (yakuza) · victorious (fist-pump hand) · self-satisfied
**5. Pleasure & ease (8):** pleasure (eyes closed, soft smile) · relieved · relaxed (drooping lids) · blissful · dreamy · cosy · savouring · satisfied sigh
**6. Love & charm (7):** loving · tender · admiring · coy (look away, small smile) · shy · flirtatious · air kiss
**7. Play & mischief (10):** playful · cheeky · naughty (side-smirk + brow) · mischievous · teasing · wink · tongue out · lick lips · bite lip · silly (cross-eyed)
**8. Surprise family (8):** surprised · astonished (wide eyes, O-mouth) · amazed · awed · startled · shocked · stunned · impressed
**9. Curiosity & doubt (9):** curious · interested · intrigued · puzzled (head scratch, chin twist) · confused · uncertain · sceptical (one brow, narrowed eyes) · suspicious (side glance) · hesitant
**10. Ideas (4):** inspired · eureka (bulb, brows shoot up) · scheming (fingers tented) · realising ("ahh" nod)
**11. Sadness & low (9):** sad · disappointed · gloomy · melancholy · hurt · lonely · tearful (single tear of light) · defeated · low morale (chin down, slump)
**12. Fear & worry (8):** fear · anxious · worried (inner brows up) · nervous (darting eyes) · alarmed · panicked · uneasy · dread
**13. Disgust & dislike (6):** disgust (nose scrunch, upper lip raise) · revolted · distaste · contempt (one-sided lip curl) · disdain (looks down nose) · bored
**14. Anger family (8):** annoyed · irritated · frustrated · angry (knit brows, flared nose) · furious · indignant · grumpy · stern
**15. Sass & attitude (7):** sarcastic (one brow up, half-lid) · eye-roll · unimpressed · "really?" (head tilt, flat mouth) · facepalm (ghost hand) · shrug · side-eye
**16. Effort aftermath (5):** exasperated (blow out with pursed lips, wide eyes) · exhausted · phew (wipes brow) · sheepish (after an error) · apologetic
**17. Sleep & idle (5):** sleepy · yawning · dozing (Zzz smoke) · daydreaming · waking up (stretch, blink)

**Total: 96 named expressions** + live blends between any two (e.g. 60 % happy + 40 % sleepy).

**Idle life (always running underneath):** blink every 3–6 s · micro eye saccades · breathing pulse · tiny head drift · occasional glance at the corners of the screen.

---

## 4. The PROP library (what appears around the face)
Each prop = a short looping neon/smoke animation, placed in one of 8 "stations" around the face (left, right, top, top-left…). The face's eyes always track the active prop.

| # | Jarvis moment | Prop animation | Face reaction | Colour |
|---|---|---|---|---|
| 1 | **Listening to you** | Faint holographic **ear** beside the face, sound ripples entering it | attentive, slight head tilt toward the ear, small nods | Teal |
| 2 | **Thinking** | Face fades to **X-ray/MRI**: 3D brain inside the head, **circuits racing over both hemispheres** | eyes up-left, thoughtful | Violet |
| 3 | **Had an idea** | **Bulb lights up** over the head with a soft "ting" | surprised → happy, brows up | Yellow |
| 4 | **Speaking** | Lips move with his words (visemes); faint sound waves from the mouth | expression matches the reply's mood | Gold |
| 5 | **Writing code** | **Code symbols `{ } < /> ; ( ) =>`** stream out of his lips like smoke | ultra-focused, quick blinks | Green |
| 6 | **Searching** | **Magnifying glass** in front of one eye (eye enlarged behind it) | squint other eye, serious | Cyan |
| 7 | **Deep research** | Face wears a **monocle**, smokes a **pipe**, scans the floor "for a lost coin" (eyes sweep) | serious, pensive, brow knit | Amber-brown |
| 8 | **Browsing the web** | Bright neon **globe spinning** | curious, eyes follow the spin | Cyan |
| 9 | **Using PC hands (mouse/keys)** | Two holographic **hands** on either side of the face | focused, eyes follow the hands | Green |
| 10 | **Typing on the PC** | A neon **laptop** appears, two hands **typing vigorously** | ultra-focused, tongue tip out | Green |
| 11 | **Building / doing a task** (project, APK) | **Wheel & axle turning + a mill chimney puffing smoke** beside the face | serious → proud when done | Orange |
| 12 | **Asking another AI / consultant** | Face puts on a **fedora** and **smokes a cigar**, smoke rings drift up | relaxed, sly half-smile | Smoke grey |
| 13 | **Phone call** | A **mobile phone dances** around the face, ringing waves | cheerful, eyebrows up | Orange |
| 14 | **SMS / WhatsApp / social** | A tiny **tuxedo & bow-tie figure ballroom-dancing with a lady in a red gown**, **letters flying like birds** | playful smile, eyes follow the dancers | Pink/red |
| 15 | **YouTube / video** | Neon **cinema screen** with flickering film strip | relaxed, eyes wide | Purple |
| 16 | **Music** | **Disco ball** + **music notes** flying around | joyful, slight head bob | Multicolour |
| 17 | **Waiting for your ALLOW** | Face looks straight at you, **raised open palm**, amber halo | polite, expectant, brows up | Amber |
| 18 | **ERROR** | A **red laughing skull** pops up holding a **STOP sign** | face shocked → disappointed / low morale | Red |
| 19 | **Big task completed** | Confetti of light sparks; face **blows out with pursed lips**, then smiles | exasperated-relief → proud | Gold |
| 20 | **Task failed** | Props crumble into smoke | chin down, low morale; recovers after 4 s | Dim red |

### My additions (fit your doctor + Jarvis world)
| # | Moment | Prop | Face |
|---|---|---|---|
| 21 | **Self-check** | Face wears a **stethoscope**, checks its own pulse; ECG line runs | serious "doctor" look |
| 22 | **Repair (Opus mechanic)** | **Spanner & screwdriver** working on a floating gear | focused, tongue out |
| 23 | **Nightly lessons** | An **open book** with pages flipping + graduation cap; score floats up (e.g. 19/28) | studious, then proud/sad by score |
| 24 | **Remembering a note** | A **quill writing on a scroll**, which rolls up into his temple | content |
| 25 | **Reading your file (+)** | A **document/photo floats in**, a scan line passes over it | reading eyes left→right |
| 26 | **Samasa codeword** | A **Sanskrit-style seal** stamps, then 3 mini props play in fast-forward | confident wink |
| 27 | **Brain switch** | Face **swaps a mask**: Opus / Gemini / Qwen symbol on the forehead for 2 s | surprised then settles |
| 28 | **Voice switch (yakuza/rikuo)** | **Ink-brush stroke** across the face; dragon smoke for yakuza | proud/arrogant smirk |
| 29 | **Phone hands active** | Tiny **phone satellite** orbiting | (background) |
| 30 | **Link lost / offline** | Face dissolves into static, **plug icon** | sleepy, sad |
| 31 | **Camera on** | A faint **eye-lens iris** in the corner | curious |
| 32 | **Screen look** | A **picture frame** floats in, eyes scan it | focused |
| 33 | **Idle long** | Clouds of smoke drift, he daydreams, yawns, dozes ("Zzz" smoke) | sleepy, easy |

---

## 5. The MOOD engine (how the face decides its expression)
Mood = blend of 4 inputs, re-calculated ~5×/second, eased over 600 ms:

1. **Activity** (from the props above): each prop has a default expression.
2. **Outcome**: success → happy/proud; error → shocked → low morale; long task → focused → exasperated-relief.
3. **Your tone**: the PC already measures your voice loudness/pace; loud + fast = he looks alert/serious; soft = calm; laughing (high variance) = playful. Later: the brain tags each reply with a mood word.
4. **Reply mood tag** (Phase 3): the brain adds a hidden tag like `[mood: playful]` that the face reads and hides from the text.

Default reactions (examples): error → startled → sheepish/apologetic · big success → triumphant → relieved · long wait → bored/sleepy · you shout → alarmed/attentive · you laugh → amused/laughing · risky ALLOW → worried · scam/spam text seen → disgust/suspicious · research found it → eureka · lessons score up → proud; down → disappointed.

Personality rule: **variety without chaos**. The same expression never repeats more than twice in a row, and sassy expressions (wink, tongue, kiss) appear only in light moments, never during errors, ALLOW cards, or serious tasks.

---

## 6. Priority (when several things happen at once)
`ERROR > ALLOW > Speaking > Calls/Social > Build > Code > Research > Browse > PC hands > Thinking > Listening > Idle`
- Max **2 props on screen** at once (main + one small), so it stays readable.
- Each prop stays at least **1.5 s** (no flicker), fades out over 0.8 s.

---

## 7. Sound (easy on the ears)
No hums, no screech. Short soft tones only, all under 0.4 s, quiet:
bell (listening) · soft "ting" (idea) · wooden tok (each action) · two-note chime (done) · low "dum" (error) · mute switch.
Optional per-prop whisper sounds (page flip, typing clicks at 10 % volume), off by default.

---

## 8. How it gets the information (wiring)
| Signal | Source | Exists? |
|---|---|---|
| idle/listening/thinking/speaking + voice level | face bus (`/state`, dock.js) | ✅ yes |
| current tool name | dock `tool` event (`▸ …` text) | ✅ yes; needs a clean tool **name** added to the event |
| ALLOW waiting | dock permission card | ✅ yes |
| error | dock toast (red) | ✅ yes |
| turn finished (success/fail) | `turn_end` event | ✅ yes; add `ok: true/false` |
| your voice loudness/pace | ears (hands-free level) | ✅ partly |
| reply mood tag | brain prompt + server strips the tag | ❌ Phase 3 |

**Tool → prop map** (examples): `listen`→ear · `look_at_screen`→frame · `click_*`/`press_keys`→hands · `type_text`→laptop · `open_url`/`read_whole_page`→globe · `research`/`multi_search`→monocle+pipe · `web_search`→magnifier · `project_*`/`android_build`/`github_*`→mill · `project_ask`/consultant→hat+cigar · `project_create`/writes code→code-from-lips · `phone_call`→dancing phone · `phone_type` in WhatsApp→ballroom dancers · `open_url youtube`→cinema · music→disco · `run_self_test`→stethoscope · `start_lessons`→book · notes→quill · `samasa`→seal · `switch_brain`→mask · `switch_voice`→ink stroke.

---

## 9. Technology (honest)
- **Engine:** one HTML page (a new face in `app/face/faces/living/`), **Canvas2D + a small WebGL smoke layer** (no internet needed; libraries copied locally).
- **Face:** neon vector rig (paths + controls), not a photo; light enough for the phone in Chrome.
- **Props:** each prop is a small self-contained drawing function, so it's easy to add more later.
- **3D brain (Thinking):** a pre-made low-poly brain outline in neon wire, rotating, with "electric" dots running along paths. Real MRI-grade 3D would be too heavy for the phone.
- **Performance target:** 60 fps on the PC, 30 fps on the Note 10+, auto-reduces smoke on the phone.
- **Honest limit:** hand-drawn neon at this level is a big art job. Phase 1 will look good, not Pixar; it improves with each phase.

---

## 10. Build phases (YANTRA → TANTRA → test)
| Phase | Delivers | Size |
|---|---|---|
| **1. Face + core** | Neon face rig, blink/gaze/breathing, 12 core expressions, lip-sync, priority system, soft sounds; props: ear, MRI brain, bulb, hands, laptop, globe, magnifier, ALLOW palm, red skull | 1 session |
| **2. Work & world props** | Mill & axle, code-from-lips, hat+cigar, monocle+pipe, dancing phone, ballroom dancers + letter-birds, cinema, disco, stethoscope, book, quill, seal, mask | 1–2 sessions |
| **3. Mood brain** | Reply mood tags, your-tone reading, sassy set (wink, kiss, tongue, bite/lick lip), idle daydream/doze, eye-roll/neck reactions | 1 session |
| **4. Polish** | 3D parallax tilt, smoke shader, phone performance tuning | 1 session |

**Server/dock changes (small):** add clean `tool` name + `turn_end ok` to events; FACE button lists "Living Face".

---

## 11. Acceptance tests (Dr Wolf)
1. Speak → ear appears; the face tilts toward it.
2. Ask something hard → the face turns X-ray and the brain circuits race.
3. "Search X" → magnifier at one eye; "research X" → monocle + pipe.
4. "Open Notepad and type hello" → hands + laptop, and the eyes follow them.
5. "Build the app on PC" → mill and axle; at the end, blow-out + proud.
6. Trigger an error → red laughing skull with STOP, then the face looks low.
7. ALLOW card → amber, open palm, looking at you.
8. Leave him idle for 10 min → he daydreams and dozes.
9. No sound ever lasts longer than 0.4 s; no buzzing.
10. Runs smoothly in Chrome on the phone.

---
_Edit anything above (add, remove, rename props or expressions), then say "send to production" and Phase 1 gets built._
