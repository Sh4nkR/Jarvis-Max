/* Jarvis dock: our buttons, laid over jaredrhod's face (ai-visualizer, AGPL-3.0).
   His face files are served unchanged. This script loads first and does two jobs:
   1. It answers the face's "/state" question from here, with the real voice
      waveform of what Jarvis is saying, so the face moves with the actual speech.
   2. It builds the dock: TALK, LISTEN, type box, SEND, CAMERA, SCREEN, SEARCH, MEMORY,
      HANDS, VOICE, LOG, FACE, BRAIN, STOP. Every control is a single click. */
"use strict";
(() => {
  /* ------------------------------------------------ the face's signal bus -- */
  const LOCAL = { state: "idle", level: 0, samples: null, alert: false, loading: false };
  const realFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const u = typeof input === "string" ? input : (input && input.url) || "";
    const path = u.replace(location.origin, "");
    if (path === "/state" || path.startsWith("/state?"))
      return Promise.resolve(new Response(JSON.stringify(LOCAL),
        { headers: { "Content-Type": "application/json" } }));
    return realFetch(input, init);
  };

  const FACES = ["living", "lotus", "board", "radial", "rain", "neural"];
  const FACE_NAMES = { living: "Living Face", lotus: "Ink Lotus", board: "Circuit Board", radial: "Radial", rain: "Face in the Code", neural: "Neural Core" };
  const ICON = {
    mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>',
    send: '<svg viewBox="0 0 24 24"><path d="M4 12h14M13 6l6 6-6 6"/></svg>',
    cam: '<svg viewBox="0 0 24 24"><path d="M3 7h4l2-2h6l2 2h4v12H3z"/><circle cx="12" cy="13" r="4"/></svg>',
    screen: '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/></svg>',
    search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg>',
    memory: '<svg viewBox="0 0 24 24"><path d="M5 4h11l3 3v13H5z"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>',
    hand: '<svg viewBox="0 0 24 24"><path d="M8 12V5a1.5 1.5 0 0 1 3 0v6M11 11V4a1.5 1.5 0 0 1 3 0v7M14 11V6a1.5 1.5 0 0 1 3 0v8c0 4-2.5 7-6 7s-5-2-6.5-4.5L3 13a1.5 1.5 0 0 1 2.5-1.5L8 14"/></svg>',
    voice: '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/></svg>',
    mute: '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z"/><path d="M17 9l5 6M22 9l-5 6"/></svg>',
    log: '<svg viewBox="0 0 24 24"><path d="M4 5h16v11H9l-5 4z"/></svg>',
    face: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M10 10v10"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>',
    up: '<svg viewBox="0 0 24 24"><path d="M7 11v9H4v-9zM7 11l4-8a2 2 0 0 1 3 2l-1 5h6a2 2 0 0 1 2 2.3l-1.3 6.5A2 2 0 0 1 17.7 20H7"/></svg>',
    down: '<svg viewBox="0 0 24 24"><path d="M7 13V4H4v9zM7 13l4 8a2 2 0 0 0 3-2l-1-5h6a2 2 0 0 0 2-2.3l-1.3-6.5A2 2 0 0 0 17.7 4H7"/></svg>',
    brain: '<svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="1"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/></svg>',
  };

  let ws = null, status = {}, voiceOn = true, camStream = null, rec = null;
  // read-only hints for the Living Face (what he's saying, link state, camera)
  window.JV_FACE = { reply: () => replyText || "", offline: () => status.brain === "offline", camera: () => !!camStream };
  let turnOpen = false, searchMode = false, replyText = "";
  // Jarvis can be open on the PC and on the phone at once. The screen he asked from speaks;
  // the other one shows the words silently (quietTurn).
  let myId = null, quietTurn = false, phoneUrl = "";
  const PHONE = matchMedia("(pointer: coarse)").matches;
  const HINT = PHONE ? "Type to Jarvis" : "Type to Jarvis, press Enter";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  try { voiceOn = localStorage.getItem("jv_voice") !== "0"; } catch (e) {}

  /* ------------------------------------------------------------ audio out -- */
  const AC = new (window.AudioContext || window.webkitAudioContext)();
  const analyser = AC.createAnalyser();
  analyser.fftSize = 256;
  analyser.connect(AC.destination);
  const wave = new Float32Array(analyser.fftSize);
  const kick = () => { if (AC.state === "suspended") AC.resume(); };
  addEventListener("pointerdown", kick, true);
  addEventListener("keydown", kick, true);

  const queue = [];
  let playing = null, fakeSpeech = 0, gen = 0;   // gen: bumps on STOP so a clip mid-decode never plays

  function setState(s) { LOCAL.state = s; }
  function settle() {
    if (playing || queue.length) return;
    if (rec || hfRec) setState("listening");
    else setState(turnOpen ? "thinking" : "idle");
  }
  function b64ToBuf(b64) {
    const bin = atob(b64), out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function stopAudio() {
    gen++;
    queue.length = 0;
    if (playing && playing.stop) { try { playing.stop(); } catch (e) {} }
    if (window.speechSynthesis) speechSynthesis.cancel();
    playing = null; fakeSpeech = 0;
    settle();
  }
  function enqueue(item) { queue.push(item); playNext(); }
  async function playNext() {
    if (playing || !queue.length) return;
    const item = queue.shift(), mine = gen;
    playing = { stop() {} };
    speakT0 = 0;                                    // set when the sound actually starts
    setState("speaking");
    try {
      if (!voiceOn) {
        await new Promise((r) => setTimeout(r, 120));
      } else if (item.audio) {
        const buf = await AC.decodeAudioData(b64ToBuf(item.audio));
        if (mine !== gen) return;                   // STOP was pressed while it decoded
        await new Promise((resolve) => {
          const src = AC.createBufferSource();
          src.buffer = buf;
          src.connect(analyser);
          src.onended = resolve;
          playing = { stop() { try { src.stop(); } catch (e) {} resolve(); } };
          speakT0 = performance.now();
          src.start();
        });
      } else if (window.speechSynthesis && item.text) {
        // the voice service was unreachable: use the browser's own voice
        await new Promise((resolve) => {
          const u = new SpeechSynthesisUtterance(item.text.replace(/[*_`#>|]/g, ""));
          const v = speechSynthesis.getVoices().find((v) => /en-GB/i.test(v.lang) && /male|ryan|george|thomas/i.test(v.name))
            || speechSynthesis.getVoices().find((v) => /en-GB/i.test(v.lang));
          if (v) u.voice = v;
          u.onend = resolve; u.onerror = resolve;
          fakeSpeech = 1;
          playing = { stop() { speechSynthesis.cancel(); resolve(); } };
          speechSynthesis.speak(u);
        });
      }
    } catch (e) { /* a bad clip never jams the queue */ }
    if (mine !== gen) return;
    playing = null; fakeSpeech = 0;
    if (queue.length) playNext(); else settle();
  }

  // feed the face the live waveform, every frame
  (function pump() {
    if (LOCAL.state === "speaking") {
      if (fakeSpeech) {
        const t = performance.now() / 1000, s = new Array(64);
        for (let i = 0; i < 64; i++) s[i] = Math.sin(i * 0.5 + t * 11) * 6000 * (0.4 + 0.6 * Math.abs(Math.sin(t * 2.3)));
        LOCAL.samples = s; LOCAL.level = 0.4 + 0.3 * Math.abs(Math.sin(t * 2.3));
      } else {
        analyser.getFloatTimeDomainData(wave);
        const s = new Array(64);
        let sum = 0;
        for (let i = 0; i < 64; i++) {
          const v = wave[Math.floor(i * wave.length / 64)];
          s[i] = v * 32767; sum += v * v;
        }
        LOCAL.samples = s;
        LOCAL.level = Math.min(1, Math.sqrt(sum / 64) * 4);
      }
    } else { LOCAL.samples = null; LOCAL.level = 0; }
    requestAnimationFrame(pump);
  })();

  /* --------------------------------------------------------------- the DOM -- */
  function build() {
    document.title = "JARVIS";
    const root = document.createElement("div");
    root.id = "jv-root";
    root.innerHTML = `
      <div id="jv-chips">
        <div class="jv-chip" id="jv-c-brain"><i></i>BRAIN <b>…</b></div>
        <div class="jv-chip" id="jv-c-ears"><i></i>EARS <b>…</b></div>
        <div class="jv-chip" id="jv-c-voice"><i></i>VOICE <b>…</b></div>
        <div class="jv-chip" id="jv-c-hands"><i></i>HANDS <b>…</b></div>
      </div>
      <div id="jv-toast"></div>
      <div id="jv-captions"><div id="jv-you"></div><div id="jv-reply"></div><div id="jv-tool"></div></div>
      <div id="jv-cam"><span>CAMERA</span><video autoplay muted playsinline></video></div>
      <div id="jv-dock">
        <div class="jv-row">
          <button id="jv-talk" title="Talk (F2): click, speak, click again">${ICON.mic}<span>TALK</span></button>
          <button id="jv-free" title="Hands-free: Jarvis listens on its own and answers when you pause. Click again to turn it off.">${ICON.mic}<span>LISTEN</span></button>
          <button id="jv-add" title="Add pictures, PDFs or text files for Jarvis to read, remember and learn from">+</button>
          <input id="jv-file" type="file" multiple hidden accept="image/*,.pdf,.txt,.md,.csv,.json,.html,.htm,.log,.xml">
          <input id="jv-input" type="text" autocomplete="off" spellcheck="false" placeholder="${HINT}">
          <button id="jv-send">${ICON.send}SEND</button>
          <button id="jv-good" class="jv-verdict" title="That answer was right. Jarvis keeps doing it that way.">${ICON.up}</button>
          <button id="jv-bad" class="jv-verdict" title="That answer was wrong. Type what was wrong in the box first if you like; Jarvis learns from it tonight.">${ICON.down}</button>
        </div>
        <div class="jv-row jv-tools">
          <button id="jv-camera" title="Camera on/off. While on, Jarvis sees what the camera sees with each question.">${ICON.cam}CAMERA</button>
          <button id="jv-screen" title="Jarvis looks at your screen">${ICON.screen}SCREEN</button>
          <button id="jv-search" title="Search the web for what's in the box">${ICON.search}SEARCH</button>
          <button id="jv-memory" title="What Jarvis remembers">${ICON.memory}MEMORY</button>
          <button id="jv-hands" title="Let Jarvis use the mouse and keyboard">${ICON.hand}<span>HANDS</span></button>
          <button id="jv-voice" title="Spoken replies on/off">${ICON.voice}<span>VOICE</span></button>
          <button id="jv-log" title="The whole conversation">${ICON.log}LOG</button>
          <button id="jv-face" title="Switch Jarvis's face">${ICON.face}FACE</button>
          <button id="jv-brainbtn" title="Choose Jarvis's brain: Claude Sonnet, Opus or Haiku, Local Qwen, Gemini, Gemini Flash-Lite or Auto">${ICON.brain}BRAIN</button>
          <button id="jv-stop" title="Stop (Esc)">${ICON.stop}STOP</button>
        </div>
      </div>
      <div id="jv-panel"><header><span id="jv-ptitle">LOG</span><button id="jv-pclose">CLOSE</button></header><div class="jv-body" id="jv-pbody"></div></div>
      <div class="jv-card" id="jv-perm"><h2>PERMISSION NEEDED</h2><p>Jarvis wants to:</p><code id="jv-permtext"></code>
        <div class="btns"><button class="jv-yes" id="jv-allow">ALLOW</button><button class="jv-no" id="jv-deny">DENY</button></div></div>
      <div class="jv-card" id="jv-repair"><h2>FIX PROPOSED BY CLAUDE OPUS</h2>
        <p id="jv-repair-sum"></p><p class="jv-small" id="jv-repair-cause"></p>
        <pre id="jv-repair-diff"></pre>
        <p class="jv-small">Nothing changes unless you press ALLOW. Then Jarvis applies the fix; restart him and he checks everything again, keeping the fix only if every check passes.</p>
        <div class="btns"><button class="jv-yes" id="jv-repair-yes">ALLOW: APPLY THE FIX</button><button class="jv-no" id="jv-repair-no">DENY</button></div></div>
      <div class="jv-card" id="jv-brains"><h2>CHOOSE THE BRAIN</h2>
        <div class="jv-brainopts" id="jv-brainopts"></div>
        <p class="jv-small">Jarvis restarts its brain to switch (about 10 seconds), then says which brain came up.</p></div>
      <div class="jv-card" id="jv-restart"><h2>RESTARTING JARVIS</h2>
        <p id="jv-restart-msg">Switching brain…</p></div>
      <div class="jv-card" id="jv-gemkey"><h2>GEMINI API KEY</h2>
        <p>The Gemini brain needs a Gemini API key (free from Google).</p>
        <p class="jv-small">On any browser: open <b>aistudio.google.com/apikey</b>, sign in with your Google account, click <b>Create API key</b>, copy it, and paste it here.</p>
        <input id="jv-gemkey-in" type="password" autocomplete="off" spellcheck="false" placeholder="Paste the key here">
        <p class="jv-small" id="jv-gemkey-msg"></p>
        <div class="btns"><button class="jv-yes" id="jv-gemkey-save">SAVE KEY</button><button class="jv-no" id="jv-gemkey-close">LATER</button></div></div>
      <div class="jv-card" id="jv-signin"><h2>ONE-TIME SIGN-IN</h2>
        <p>Jarvis's brain is Claude Code on your Claude plan. It needs you to sign in once.</p>
        <p id="jv-signin-msg">Press SIGN IN. A Claude page opens in your browser: sign in and click <b>Authorize</b>. Then come back here.</p>
        <div class="btns"><button class="jv-yes" id="jv-signin-go">SIGN IN</button></div></div>`;
    document.body.appendChild(root);

    const input = $("jv-input");
    // Keys typed in the box belong to the box: the face uses Space, C and F as shortcuts.
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      // Samsung/Gboard keyboards are still "composing" a word (keyCode 229): sending then garbles the text
      if (e.isComposing || e.keyCode === 229) return;
      if (e.key === "Enter") { e.preventDefault(); submit(); }
      if (e.key === "Escape") { e.preventDefault(); stopAll(); }
    });
    addEventListener("keydown", (e) => {
      if (e.key === "F2") { e.preventDefault(); talk(); }
      else if (e.key === "Escape") stopAll();
    });
    // a focused dock button must not also fire the face's Space/C/F shortcuts
    $("jv-dock").addEventListener("keydown", (e) => { if (e.key !== "F2" && e.key !== "Escape") e.stopPropagation(); });
    $("jv-panel").addEventListener("keydown", (e) => e.stopPropagation());
    $("jv-brains").addEventListener("keydown", (e) => e.stopPropagation());
    $("jv-gemkey").addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") saveGeminiKey(); });
    $("jv-gemkey-save").onclick = saveGeminiKey;
    $("jv-gemkey-close").onclick = () => { gemKeyLater = true; $("jv-gemkey").classList.remove("show"); };

    $("jv-talk").onclick = talk;
    $("jv-free").onclick = toggleListen;
    // Inside the Jarvis Hands phone app, the PHONE does the mic and the typing (native Android),
    // because the app's built-in browser blocks the web mic and garbles soft-keyboard typing.
    const NATIVE = window.AndroidJarvis && /JarvisHands/.test(navigator.userAgent);
    window.jvAsk = (t) => { t = (t || "").trim(); if (t) ask(t); };
    window.jvNote = (t, kind) => toast(t, kind || "", 5000);
    window.jvMic = (on) => { $("jv-talk").classList.toggle("lit", !!on); };
    window.jvListen = (on) => { $("jv-free").classList.toggle("lit", !!on); };
    if (NATIVE) {
      $("jv-talk").onclick = () => AndroidJarvis.talk();
      $("jv-free").onclick = () => AndroidJarvis.listen();
      $("jv-input").readOnly = true;
      $("jv-input").onclick = $("jv-input").onfocus = (e) => { e.target.blur(); AndroidJarvis.typeBox(); };
      $("jv-send").onclick = () => AndroidJarvis.typeBox();
    }
    $("jv-send").onclick = submit;
    $("jv-add").onclick = () => $("jv-file").click();
    $("jv-file").onchange = () => {
      for (const f of $("jv-file").files) attached.push(f);
      $("jv-file").value = "";
      showAttached();
    };
    $("jv-good").onclick = () => verdict(true);
    $("jv-bad").onclick = () => verdict(false);
    $("jv-camera").onclick = toggleCamera;
    $("jv-screen").onclick = () => { const t = input.value.trim(); input.value = ""; ask(t || "Look at my screen. What's on it?", { screen: true }); };
    $("jv-search").onclick = searchClick;
    $("jv-memory").onclick = openMemory;
    $("jv-hands").onclick = () => send({ type: "hands", on: !status.hands });
    $("jv-voice").onclick = () => {
      voiceOn = !voiceOn;
      try { localStorage.setItem("jv_voice", voiceOn ? "1" : "0"); } catch (e) {}
      if (!voiceOn) stopAudio();
      paint();
    };
    $("jv-log").onclick = openLog;
    $("jv-face").onclick = nextFace;
    $("jv-brainbtn").onclick = () => $("jv-brains").classList.contains("show") ? closeBrains() : openBrains();
    // the brain menu closes when you click anywhere else
    addEventListener("pointerdown", (e) => {
      if ($("jv-brains").classList.contains("show") && !e.target.closest("#jv-brains, #jv-brainbtn")) closeBrains();
    }, true);
    $("jv-stop").onclick = stopAll;
    $("jv-pclose").onclick = () => $("jv-panel").classList.remove("open");
    $("jv-allow").onclick = () => answerPermission(true);
    $("jv-repair-yes").onclick = () => answerRepair(true);
    $("jv-repair-no").onclick = () => answerRepair(false);
    $("jv-repair").addEventListener("keydown", (e) => e.stopPropagation());
    $("jv-deny").onclick = () => answerPermission(false);
    $("jv-signin-go").onclick = () => {
      send({ type: "signin" });
      $("jv-signin-msg").innerHTML = "A Claude page should now be open in your browser. Sign in and click <b>Authorize</b>. This box closes by itself when it's done.";
    };
    paint();
    connect();
  }

  /* ---------------------------------------------------------- live link -- */
  let pending = [];                                    // questions waiting for the link to come back
  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";   // https page (the phone app) needs wss
    ws = new WebSocket(`${proto}//${location.host}/ws`);
    ws.onopen = () => {
      const now = performance.now(), p = pending;
      pending = [];
      p.forEach((x) => { if (now - x.t < 20000) ws.send(JSON.stringify(x.obj)); });
    };
    ws.onmessage = (ev) => { let d; try { d = JSON.parse(ev.data); } catch (e) { return; } onMessage(d); };
    ws.onclose = () => { status.brain = "offline"; paint(); setTimeout(connect, 1000); };
  }
  function send(obj) {
    if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; }
    if (obj.type === "ask") { pending.push({ obj, t: performance.now() }); return true; }   // goes when the link is back
    toast("Jarvis isn't running. Start it with Start-Jarvis-Max.bat.");
    return false;
  }

  let history = [];
  function onMessage(d) {
    switch (d.type) {
      case "hello":
        status = d.status || {}; history = d.history || []; myId = d.you || null; phoneUrl = d.phone_url || "";
        paint(); refreshLog();
        if (restarting && !status.switching) {        // the new Jarvis, not the old one on its way out
          restarting = false; clearTimeout(restartT);
          $("jv-restart").classList.remove("show");
          toast("Jarvis is back. Checking the new brain…", "ok");
        }
        break;
      case "restarting": showRestarting(d.label); break;
      case "restarted":
        restarting = false; clearTimeout(restartT);
        $("jv-restart").classList.remove("show");
        toast("Brain restarted. Checking which one came up…", "ok");
        break;
      case "announce":
        history.push({ who: "jarvis", text: d.text }); refreshLog();
        toast(d.text, "ok", 15000);
        if (!d.silent) enqueue({ text: d.text, audio: d.audio });
        break;
      case "status": status = d.status || {}; paint(); break;
      case "turn_start":
        barged = false;
        quietTurn = !!(d.speaker && myId && d.speaker !== myId);   // asked from the other screen
        turnOpen = true; replyText = ""; sawToolThisTurn = false;
        history.push({ who: "you", text: d.text });
        $("jv-you").textContent = "YOU: " + d.text + (d.pictures ? "  [+ picture]" : "");
        $("jv-reply").textContent = ""; $("jv-tool").textContent = "";
        if (!playing && !queue.length) setState("thinking");
        break;
      case "delta":
        replyText += d.text;
        // Dr Wolf's wish: Jarvis's replies are spoken, not written. Text shows only when muted.
        $("jv-reply").textContent = voiceOn && !quietTurn ? "" : replyText.slice(-420);
        refreshLog();
        break;
      case "tool": $("jv-tool").textContent = "▸ " + d.detail; sawToolThisTurn = true; break;
      case "say": if (!barged && !d.silent) enqueue({ text: d.text, audio: d.audio }); break;
      case "notice": if (!d.silent) enqueue({ text: d.text, audio: d.audio }); break;
      case "turn_end":
        turnOpen = false; $("jv-tool").textContent = ""; projectMode = sawToolThisTurn;
        history.push({ who: "jarvis", text: replyText });
        refreshLog(); settle();
        break;
      case "stopped": turnOpen = false; stopAudio(); $("jv-tool").textContent = "stopped"; break;
      case "permission":
        $("jv-permtext").textContent = d.detail; $("jv-perm").dataset.id = d.id;
        $("jv-perm").classList.add("show"); break;
      case "permission_closed":
        if ($("jv-perm").dataset.id === d.id) $("jv-perm").classList.remove("show"); break;
      case "camera_request": cameraRequest(d.id); break;
      case "behave": window.JV_BEHAVE = d; window.JV_VITALS = d.vitals; break;
      case "repair": showRepair(d); break;
      case "repair_closed": if ($("jv-repair").dataset.id === d.id) $("jv-repair").classList.remove("show"); break;
      case "cleared": history = []; replyText = ""; $("jv-you").textContent = ""; $("jv-reply").textContent = ""; refreshLog(); break;
      case "log_note": history.push({ who: "jarvis", text: d.text }); refreshLog(); break;
      case "toast": toast(d.text, d.kind || "", d.kind === "ok" ? 15000 : 6000); break;
    }
  }

  /* -------------------------------------------------------------- asking -- */
  function currentFrame() {
    const v = $("jv-cam").querySelector("video");
    if (!camStream || !v.videoWidth) return null;
    const c = document.createElement("canvas");
    const w = Math.min(1280, v.videoWidth);
    c.width = w; c.height = Math.round(v.videoHeight * w / v.videoWidth);
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.8).split(",")[1];
  }
  /* ------------------------------------------------ the + button (files) -- */
  let attached = [];
  function showAttached() {
    const b = $("jv-add"), n = attached.length;
    b.textContent = n ? "+" + n : "+";
    b.classList.toggle("lit", n > 0);
    $("jv-input").placeholder = n ? `${n} file${n > 1 ? "s" : ""} added. Say what to do, or just SEND` : HINT;
    if (n) toast("Added: " + attached.map(f => f.name).join(", "), "ok", 5000);
  }
  function shrinkImage(file) {                       // pictures also go straight to him, at a sensible size
    return new Promise(res => {
      const url = URL.createObjectURL(file), img = new Image();
      img.onload = () => {
        const s = Math.min(1, 1600 / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        res(c.toDataURL("image/jpeg", 0.85).split(",")[1]);
      };
      img.onerror = () => { URL.revokeObjectURL(url); res(null); };
      img.src = url;
    });
  }
  async function sendWithFiles(text) {
    const files = attached; attached = []; showAttached();
    toast(`Sending ${files.length} file${files.length > 1 ? "s" : ""} to Jarvis…`, "ok", 4000);
    const saved = [], pics = [], failed = [];
    for (const f of files) {
      try {
        const r = await realFetch("/api/upload?name=" + encodeURIComponent(f.name), { method: "POST", body: f });
        const j = await r.json();
        if (!j.ok) { failed.push(j.message || f.name); continue; }
        saved.push(j.path);
        if (f.type.startsWith("image/") && pics.length < 5) {
          const d = await shrinkImage(f);
          if (d) pics.push({ media_type: "image/jpeg", data: d });
        }
      } catch (e) { failed.push(f.name + " didn't upload"); }
    }
    if (failed.length) toast(failed.join(" · "), "err", 9000);
    if (!saved.length) return;
    const t = text || "Read these, and remember what matters.";
    ask(`${t}\n\n[He added ${saved.length} file${saved.length > 1 ? "s" : ""} with the + button, saved in your vault: `
        + saved.join(", ") + ". Read each one with Read.]", { images: pics });
  }

  function ask(text, opts = {}) {
    stopAudio();
    const images = (opts.images || []).slice();
    const f = currentFrame();
    if (f) images.push({ media_type: "image/jpeg", data: f });
    send({ type: "ask", text, images, screen: !!opts.screen });
  }
  function submit() {
    const input = $("jv-input"), t = input.value.trim();
    if (attached.length) { input.value = ""; searchMode = false; input.placeholder = HINT; return sendWithFiles(t); }
    if (!t) return;
    input.value = "";
    if (searchMode) { searchMode = false; input.placeholder = HINT; return doSearch(t); }
    ask(t);
  }
  function searchClick() {
    const input = $("jv-input"), t = input.value.trim();
    if (t) { input.value = ""; return doSearch(t); }
    searchMode = true;
    input.placeholder = "What should I search for? Type it, press Enter";
    input.focus();
  }
  function doSearch(t) {
    ask(`Search the web for: ${t}. Tell me briefly what you found and where it came from.`);
  }
  function stopAll() {
    if (rec) { rec.cancelled = true; rec.stop(); }
    if (listenOn) toggleListen();
    stopAudio(); send({ type: "stop" });
  }

  /* ---------------------------------------------------------------- talk -- */
  async function finishRecording(chunks, cancelled) {
    if (cancelled) return;
    const blob = new Blob(chunks, { type: "audio/webm" });
    if (blob.size < 1500) return;
    setState("thinking");
    $("jv-you").textContent = "YOU: …";
    try {
      const r = await realFetch("/api/listen", { method: "POST", body: blob, headers: { "Content-Type": "audio/webm" } });
      const j = await r.json();
      if (!j.text) { $("jv-you").textContent = ""; settle(); toast("I didn't catch that. Try again, a little closer."); return; }
      ask(j.text);
    } catch (e) { settle(); toast("Couldn't reach my ears: " + e); }
  }

  async function talk() {
    if (listenOn) toggleListen();
    if (rec) { rec.stop(); return; }                      // second click: send it
    stopAudio();
    if (status.ears !== "ready") {
      toast(status.ears === "loading" ? "My ears are still loading (first start downloads them). Type for now."
        : "My ears aren't working: " + (status.ears_error || "unknown problem"));
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { toast("I can't use the microphone. Allow it for this window (the icon in the address bar)."); return; }
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    const chunks = [];
    rec = new MediaRecorder(stream, { mimeType: mime });
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    rec.onstop = async () => {
      const cancelled = rec && rec.cancelled;
      stream.getTracks().forEach((t) => t.stop());
      rec = null; paint(); settle();
      finishRecording(chunks, cancelled);
    };
    rec.start();
    setState("listening");
    paint();
  }

  /* ---------------------------------------------------------- hands-free -- */
  // sir's wish: 2 seconds of quiet ends what he's saying, in every kind of turn.
  const HF_START = 0.028, HF_STOP = 0.018, HF_HANGOVER_GENERAL_MS = 2000, HF_HANGOVER_PROJECT_MS = 2000;
  const HF_MIN_MS = 250, HF_MAX_MS = 45000;
  // Barge-in: while Jarvis is speaking, sir talking over him (louder than Jarvis's own voice
  // coming back through the mic, in 3 of the last 6 ticks: speech has gaps between syllables)
  // cuts him off mid-sentence and starts listening.
  const HF_BARGE_MIN = 0.05, HF_BARGE_WINDOW_MS = 600, HF_BARGE_HITS = 3, HF_ECHO_X = 2.0, HF_LEARN_MS = 1200;
  let listenOn = false, hfStream = null, hfAnalyser = null, hfData = null, hfRec = null;
  let hfChunks = [], hfSpeechStart = 0, hfSilenceSince = null, hfTimer = null;
  // Echo: the mic also hears Jarvis's own voice from the speakers. The first time he speaks, the
  // first 1.2 s teach how loud that echo is compared with his voice (hfK); after that, only
  // sound clearly louder than the expected echo counts as sir talking.
  let hfK = null, hfLearn = 0, hfOut = [], speakT0 = 0, hfLoud = [], barged = false;
  const hfOutData = new Float32Array(analyser.fftSize);
  function outLevel() {
    analyser.getFloatTimeDomainData(hfOutData);
    let sum = 0;
    for (let i = 0; i < hfOutData.length; i++) sum += hfOutData[i] * hfOutData[i];
    return Math.sqrt(sum / hfOutData.length);
  }
  let sawToolThisTurn = false, projectMode = false;

  function hfLevel() {
    hfAnalyser.getFloatTimeDomainData(hfData);
    let sum = 0;
    for (let i = 0; i < hfData.length; i++) sum += hfData[i] * hfData[i];
    return Math.sqrt(sum / hfData.length);
  }
  function hfBeginUtterance() {
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
    hfChunks = [];
    hfRec = new MediaRecorder(hfStream, { mimeType: mime });
    hfRec.ondataavailable = (e) => e.data.size && hfChunks.push(e.data);
    hfRec.onstop = () => {
      const tooShort = performance.now() - hfSpeechStart < HF_MIN_MS;
      const cancelled = hfRec && hfRec.cancelled;
      hfRec = null;
      paint();
      if (!listenOn || cancelled) return;                  // toggled off mid-stop
      if (tooShort) { barged = false; settle(); return; }  // just a noise: let him carry on
      finishRecording(hfChunks, false);
    };
    hfRec.start();
    hfSpeechStart = performance.now();
    hfSilenceSince = null;
    setState("listening");
    paint();
  }
  // A rAF loop would be paused by the browser the moment this window is minimized
  // or loses focus (sir wants LISTEN to keep working then), so this ticks off a
  // timer instead — browsers throttle background timers but don't freeze them.
  function bargeIn() {
    barged = true;                 // the rest of this answer stays unspoken
    hfLoud = [];
    stopAudio();
    hfBeginUtterance();            // already listening to what he's saying
  }
  function hfLoop() {
    if (!listenOn || rec) return;
    const level = hfLevel();
    const now = performance.now();
    if (hfRec) {                   // he's talking: wait for 2 s of quiet
      if (level > HF_STOP) {
        hfSilenceSince = null;
        if (now - hfSpeechStart > HF_MAX_MS) hfRec.stop();
      } else {
        if (hfSilenceSince === null) hfSilenceSince = now;
        else if (now - hfSilenceSince > (projectMode ? HF_HANGOVER_PROJECT_MS : HF_HANGOVER_GENERAL_MS)) hfRec.stop();
      }
      return;
    }
    if (playing || queue.length) { // Jarvis is speaking: listen for sir talking over him
      if (!speakT0) return;        // still preparing the sound: nothing to compare yet
      hfOut.push([now, outLevel()]);
      hfOut = hfOut.filter((p) => now - p[0] <= 400);
      const outMax = Math.max(0, ...hfOut.map((p) => p[1]));
      if (hfK === null) {           // first time he speaks: learn the echo, don't cut in yet
        if (now - speakT0 < HF_LEARN_MS) { if (outMax > 0.01) hfLearn = Math.max(hfLearn, level / outMax); return; }
        hfK = hfLearn;
      }
      const gate = Math.max(HF_BARGE_MIN, hfK * outMax * HF_ECHO_X);
      hfLoud = hfLoud.filter((t) => now - t <= HF_BARGE_WINDOW_MS);
      if (level > gate) {
        hfLoud.push(now);
        if (hfLoud.length >= HF_BARGE_HITS) bargeIn();
      } else if (outMax > 0.01) {   // keep the echo estimate current
        const r = level / outMax;
        if (r < hfK * 1.5 + 0.05) hfK = hfK * 0.95 + r * 0.05;
      }
      return;
    }
    hfLoud = []; hfOut = [];
    if (!turnOpen && level > HF_START) hfBeginUtterance();
  }
  let wakeLock = null;
  async function keepScreenOn(on) {
    if (!PHONE || !navigator.wakeLock) return;
    try {
      if (on && !wakeLock) {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } else if (!on && wakeLock) { const w = wakeLock; wakeLock = null; await w.release(); }
    } catch (e) { /* not allowed here: the phone may dim as usual */ }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && listenOn) keepScreenOn(true);
  });
  async function toggleListen() {
    if (listenOn) {
      listenOn = false;
      keepScreenOn(false);
      if (hfTimer) clearInterval(hfTimer);
      hfTimer = null;
      if (hfRec) { hfRec.cancelled = true; try { hfRec.stop(); } catch (e) {} hfRec = null; }
      if (hfStream) hfStream.getTracks().forEach((t) => t.stop());
      hfStream = null; hfAnalyser = null;
      paint(); settle();
      return;
    }
    if (status.ears !== "ready") {
      toast(status.ears === "loading" ? "My ears are still loading (first start downloads them). Type for now."
        : "My ears aren't working: " + (status.ears_error || "unknown problem"));
      return;
    }
    if (rec) { rec.cancelled = true; rec.stop(); }
    try {
      hfStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (e) { toast("I can't use the microphone. Allow it for this window (the icon in the address bar)."); return; }
    const src = AC.createMediaStreamSource(hfStream);
    hfAnalyser = AC.createAnalyser();
    hfAnalyser.fftSize = 1024;
    src.connect(hfAnalyser);
    hfData = new Float32Array(hfAnalyser.fftSize);
    listenOn = true;
    keepScreenOn(true);
    paint();
    hfTimer = setInterval(hfLoop, 100);
  }

  /* -------------------------------------------------------------- camera -- */
  async function cameraOn() {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: 1280, height: 720 } });
    $("jv-cam").querySelector("video").srcObject = camStream;
    $("jv-cam").classList.add("on");
  }
  function cameraOff() {
    if (camStream) camStream.getTracks().forEach((t) => t.stop());
    camStream = null;
    $("jv-cam").classList.remove("on");
  }
  async function toggleCamera() {
    if (camStream) cameraOff();
    else { try { await cameraOn(); } catch (e) { toast("I can't use the camera. Allow it for this window (the icon in the address bar)."); } }
    paint();
  }
  async function cameraRequest(id) {
    const wasOn = !!camStream;
    try {
      if (!wasOn) { await cameraOn(); paint(); await new Promise((r) => setTimeout(r, 1500)); }
      const data = currentFrame();
      send({ type: "camera_frame", id, data, note: data ? "" : "The camera gave no picture." });
    } catch (e) {
      send({ type: "camera_frame", id, data: null, note: "The camera isn't allowed in the Jarvis window." });
    }
    if (!wasOn) { cameraOff(); paint(); }
  }

  /* ------------------------------------------------------------- panels -- */
  function openPanel(title, html) {
    $("jv-ptitle").textContent = title;
    $("jv-pbody").innerHTML = html;
    $("jv-panel").classList.add("open");
  }
  // Web addresses in the log become links that open in his normal browser (via Jarvis).
  const linkify = (s) => esc(s).replace(/https?:\/\/[^\s<>"]+/g,
    (u) => `<a class="jv-link" href="#" data-url="${u}">${u}</a>`);
  function logHtml() {
    const items = history.slice(-80);
    if (turnOpen) items.push({ who: "jarvis", text: replyText });
    return items.map((m) => `<div class="jv-msg ${m.who === "you" ? "you" : ""}"><div class="who">${m.who === "you" ? "YOU" : "JARVIS"}</div><div class="txt">${linkify(m.text)}</div></div>`).join("")
      || "<p>Nothing said yet.</p>";
  }
  const LOG_BUTTONS = `<button class="jv-panelbtn" id="jv-new">START A FRESH CONVERSATION</button>` +
    `<button class="jv-panelbtn" id="jv-learn" title="Jarvis does this by himself every night">LEARN NOW: TODAY'S LESSONS AND SKILLS</button>`;
  const phoneLine = () => phoneUrl && !PHONE
    ? `<p class="jv-phone">ON YOUR PHONE (same Wi-Fi), open in Chrome: <b>${esc(phoneUrl)}</b></p>` : "";
  function wireLogButtons() {
    $("jv-new").onclick = () => { send({ type: "new_session" }); $("jv-panel").classList.remove("open"); };
    $("jv-pbody").querySelectorAll("a.jv-link").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); send({ type: "open_link", url: a.dataset.url }); toast("Opening it in your browser, sir.", "ok"); };
    });
    $("jv-learn").onclick = async () => {
      try {
        const j = await (await realFetch("/api/grow", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json();
        toast(j.message, "ok");
      } catch (e) { toast("Jarvis isn't running."); }
    };
  }
  async function verdict(good) {
    const box = $("jv-input"), note = good ? "" : box.value.trim();
    if (note) box.value = "";
    try {
      const r = await realFetch("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ good, note }) });
      const j = await r.json();
      toast(j.message, j.ok ? "ok" : "");
    } catch (e) { toast("Jarvis isn't running."); }
  }
  function openLog() {
    if ($("jv-panel").classList.contains("open") && $("jv-ptitle").textContent === "LOG") { $("jv-panel").classList.remove("open"); return; }
    openPanel("LOG", logHtml() + LOG_BUTTONS + phoneLine());
    $("jv-pbody").scrollTop = 1e9;
    wireLogButtons();
  }
  function refreshLog() {
    if ($("jv-panel").classList.contains("open") && $("jv-ptitle").textContent === "LOG") {
      const b = $("jv-pbody"), atEnd = b.scrollHeight - b.scrollTop - b.clientHeight < 40;
      b.innerHTML = logHtml() + LOG_BUTTONS + phoneLine();
      wireLogButtons();
      if (atEnd) b.scrollTop = 1e9;
    }
  }
  async function openMemory() {
    let notes = [];
    try { notes = await (await realFetch("/api/memory")).json(); } catch (e) {}
    const list = notes.map((n) => `<button class="jv-note" data-p="${esc(n.path)}">${esc(n.path)}<small>${new Date(n.modified * 1000).toLocaleString()}</small></button>`).join("");
    openPanel("MEMORY", `<button class="jv-panelbtn" id="jv-recall">WHAT DO YOU REMEMBER ABOUT ME?</button>${list || "<p>No notes yet.</p>"}`);
    $("jv-recall").onclick = () => { $("jv-panel").classList.remove("open"); ask("What do you remember about me? Keep it short."); };
    document.querySelectorAll(".jv-note").forEach((b) => b.onclick = async () => {
      const j = await (await realFetch("/api/memory/note?path=" + encodeURIComponent(b.dataset.p))).json();
      openPanel("MEMORY", `<button class="jv-panelbtn" id="jv-back">BACK</button><h3 style="font-weight:normal;color:var(--jv-gold)">${esc(j.path)}</h3><div class="jv-notetext">${esc(j.text)}</div>`);
      $("jv-back").onclick = openMemory;
    });
  }
  function showRepair(d) {
    const card = $("jv-repair");
    card.dataset.id = d.id;
    $("jv-repair-sum").textContent = d.summary || "Claude changed my code.";
    $("jv-repair-cause").textContent = (d.cause ? "Cause: " + d.cause + "  " : "") + "Files: " + (d.files || []).join(", ");
    $("jv-repair-diff").innerHTML = (d.diff || "").split("\n").map((l) =>
      `<span class="${l.startsWith("@@") ? "hunk" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l)}</span>`).join("\n");
    card.classList.add("show");
  }
  function answerRepair(allow) {
    const card = $("jv-repair");
    send({ type: "repair_reply", id: card.dataset.id, allow });
    card.classList.remove("show");
  }
  function answerPermission(allow) {
    const card = $("jv-perm");
    send({ type: "permission_reply", id: card.dataset.id, allow });
    card.classList.remove("show");
  }
  async function nextFace() {
    const cur = (location.pathname.match(/faces\/([^/]+)/) || [])[1] || "board";
    const next = FACES[(FACES.indexOf(cur) + 1) % FACES.length];
    toast("Face: " + FACE_NAMES[next]);
    try { await realFetch("/api/face", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ face: next }) }); } catch (e) {}
    location.href = `/face/faces/${next}/index.html`;
  }

  /* ---------------------------------------------------------- gemini key -- */
  let gemKeyLater = false;
  async function saveGeminiKey() {
    const key = $("jv-gemkey-in").value.trim(), msg = $("jv-gemkey-msg");
    if (!key) { msg.textContent = "Paste the key first."; return; }
    msg.textContent = "Checking the key with Google…";
    try {
      const r = await realFetch("/api/gemini_key", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key }) });
      const j = await r.json();
      msg.textContent = j.message || "";
      if (j.ok) { $("jv-gemkey-in").value = ""; setTimeout(() => $("jv-gemkey").classList.remove("show"), 1200); }
    } catch (e) { msg.textContent = "Jarvis isn't running."; }
  }

  /* --------------------------------------------------------------- brain -- */
  let restarting = false, restartT = null;
  function closeBrains() { $("jv-brains").classList.remove("show"); $("jv-brainbtn").classList.remove("lit"); }
  async function openBrains() {
    const box = $("jv-brainopts"), menu = $("jv-brains"), btn = $("jv-brainbtn").getBoundingClientRect();
    box.innerHTML = "<p>Loading…</p>";
    // a menu that opens straight up from the BRAIN button
    menu.style.left = Math.max(16, Math.min(btn.right - 460, innerWidth - 476)) + "px";
    menu.style.bottom = (innerHeight - btn.top + 12) + "px";
    menu.classList.add("show");
    $("jv-brainbtn").classList.add("lit");
    let info;
    try { info = await (await realFetch("/api/brain")).json(); }
    catch (e) { box.innerHTML = "<p>Jarvis isn't running.</p>"; return; }
    box.innerHTML = info.choices.map((c) =>
      `<button class="jv-brainopt${c.id === info.current ? " now" : ""}" data-id="${esc(c.id)}">
         <b>${esc(c.label)}${c.id === info.current ? " <em>NOW</em>" : ""}</b><small>${esc(c.about)}</small></button>`).join("");
    box.querySelectorAll(".jv-brainopt").forEach((b) => b.onclick = () => pickBrain(b.dataset.id, b.querySelector("b").textContent.replace(/\s*NOW$/, "")));
  }
  async function pickBrain(id, label) {
    closeBrains();
    showRestarting(label);
    try {
      const r = await realFetch("/api/brain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice: id }) });
      const j = await r.json();
      if (!j.ok) { restarting = false; $("jv-restart").classList.remove("show"); toast(j.message || "Couldn't switch."); }
    } catch (e) { /* the server may already be restarting */ }
  }
  function showRestarting(label) {
    if (restarting) return;
    restarting = true; stopAudio();
    $("jv-restart-msg").textContent = `Switching to ${label}. Jarvis is restarting its brain; back in about 10 seconds.`;
    $("jv-restart").classList.add("show");
    clearTimeout(restartT);
    restartT = setTimeout(() => {
      $("jv-restart-msg").textContent = "Jarvis didn't come back by itself. Click Start-Jarvis-Max.bat on your Desktop.";
    }, 120000);
  }

  /* -------------------------------------------------------------- paint -- */
  let toastT = null;
  function toast(t, kind = "", ms = 6000) {
    const el = $("jv-toast"); if (!el) return;
    el.textContent = t; el.className = "show" + (kind ? " " + kind : "");
    clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove("show"), ms);
  }
  function chip(id, cls, label) {
    const el = $(id); if (!el) return;
    el.className = "jv-chip " + cls; el.querySelector("b").textContent = label;
  }
  function paint() {
    if (!$("jv-dock")) return;
    const b = status.brain;
    chip("jv-c-brain", b === "ready" || b === "local" ? "ok" : b === "starting" ? "wait" : "bad",
      (b === "ready" || b === "local") && status.brain_name ? status.brain_name
        : { starting: "STARTING", signin: "SIGN IN", needs_key: "KEY NEEDED", error: "ERROR", offline: "OFFLINE" }[b] || "…");
    const e = status.ears;
    chip("jv-c-ears", e === "ready" ? "ok" : e === "loading" ? "wait" : "bad",
      { ready: "READY", loading: "LOADING", error: "ERROR" }[e] || "…");
    chip("jv-c-voice", !voiceOn ? "" : status.mouth === "fallback" ? "wait" : "ok",
      !voiceOn ? "MUTED" : status.mouth === "fallback" ? "BACKUP" : "ON");
    chip("jv-c-hands", status.hands ? "ok" : "", status.hands ? "ON" : "OFF");
    $("jv-talk").classList.toggle("on", !!rec);
    $("jv-talk").querySelector("span").textContent = rec ? "SEND IT" : "TALK";
    $("jv-free").classList.toggle("capturing", !!hfRec);
    $("jv-free").classList.toggle("armed", listenOn && !hfRec);
    $("jv-free").querySelector("span").textContent = hfRec ? "HEARING YOU" : listenOn ? "LISTENING" : "LISTEN";
    $("jv-camera").classList.toggle("lit", !!camStream);
    $("jv-hands").classList.toggle("lit", !!status.hands);
    $("jv-hands").classList.toggle("off", !status.hands);
    $("jv-hands").querySelector("span").textContent = status.hands ? "HANDS ON" : "HANDS OFF";
    $("jv-voice").classList.toggle("off", !voiceOn);
    $("jv-voice").innerHTML = (voiceOn ? ICON.voice : ICON.mute) + `<span>${voiceOn ? "VOICE ON" : "MUTED"}</span>`;
    $("jv-signin").classList.toggle("show", b === "signin");
    if (b === "needs_key" && !gemKeyLater) $("jv-gemkey").classList.add("show");
    if (b !== "needs_key") gemKeyLater = false;
    if (b === "error" && status.brain_error) $("jv-c-brain").title = status.brain_error;
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", build);
  else build();
})();
