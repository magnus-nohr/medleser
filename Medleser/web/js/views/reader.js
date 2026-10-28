import { api } from "../api.js";
import { navigate, state } from "../main.js";
import { prefersReducedMotion } from "../prefs.js";
import { readingForm, saveReading } from "../reading-form.js";
import {
  announce, button, createDialog, el, errorNotice, formatDuration, icon, notice, setPageTitle, spokenDuration,
} from "../ui.js";

const HIDDEN_KINDS = new Set(["header", "footer", "page_number"]);
const RATES = [0.75, 0.85, 1, 1.15, 1.25, 1.5, 1.75, 2];

// ---------------------------------------------------------------------------
// Sync index: time -> word / segment (binary search, no per-frame allocations)

class SyncIndex {
  constructor(sync) {
    this.words = sync.words || [];
    this.segments = sync.segments || [];
    this.blocks = sync.blocks || {};
    this.chapters = sync.chapters || [];
    this.offset = Number(sync.audio_offset || 0);
    this.wordStarts = Float64Array.from(this.words, (w) => w[0]);
    this.segStarts = Float64Array.from(this.segments, (s) => s[0]);
  }

  static lastAtOrBefore(starts, t) {
    let lo = 0, hi = starts.length - 1, ans = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (starts[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return ans;
  }

  segmentAt(t) {
    const i = SyncIndex.lastAtOrBefore(this.segStarts, t);
    return i >= 0 ? i : null;
  }

  wordAt(t) {
    const i = SyncIndex.lastAtOrBefore(this.wordStarts, t);
    if (i < 0) return null;
    const w = this.words[i];
    // Keep the word lit through short pauses; clear it in longer ones (between paragraphs).
    return t - w[1] < 0.45 ? i : null;
  }

  startOfBlock(blockId) {
    const b = this.blocks[blockId];
    return b ? b[0] : null;
  }

  startOfPart(blockId, part) {
    for (const w of this.words) if (w[3] === blockId && w[4] === part) return w[0];
    return null;
  }
}

// Find (textNode, offset) for a character offset inside an element.
function rangeFor(element, start, end) {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let pos = 0, startNode = null, startOff = 0, node;
  while ((node = walker.nextNode())) {
    const len = node.nodeValue.length;
    if (!startNode && start < pos + len) { startNode = node; startOff = start - pos; }
    if (startNode && end <= pos + len) {
      const range = new Range();
      range.setStart(startNode, Math.max(0, startOff));
      range.setEnd(node, Math.max(0, Math.min(len, end - pos)));
      return range;
    }
    pos += len;
  }
  return null;
}

export async function renderReader(main, projectId) {
  let data, syncData;
  try {
    data = await api.get(`/api/projects/${projectId}`);
  } catch (err) {
    main.append(el("h1", {}, "Medlesing"), errorNotice(err));
    return;
  }
  setPageTitle(`Medlesing: ${data.settings.title}`);
  if (!data.audio_state?.ready) {
    main.append(el("h1", {}, data.settings.title),
      notice("info", el("p", {}, "Lyden for dette dokumentet er ikke laget ennå.")),
      button("Gå til dokumentet", { variant: "btn-primary", onClick: () => navigate(`#/dokument/${projectId}`) }));
    return;
  }
  try {
    syncData = await api.get(`/api/projects/${projectId}/sync`);
  } catch (err) {
    main.append(el("h1", {}, data.settings.title), errorNotice(err));
    return;
  }
  const sync = new SyncIndex(syncData);
  const doc = data.document;
  const decisions = data.decisions || {};
  const reading = () => state.settings.reading;

  // ----- layout -------------------------------------------------------------------
  document.documentElement.classList.add("reader-active");
  const focusBtn = button("Distraksjonsfri", { variant: "btn-ghost btn-sm", iconName: "focus", attrs: { "aria-pressed": "false" } });
  const top = el("div", { class: "reader-top" },
    button("Tilbake", { variant: "btn-ghost btn-sm", onClick: () => navigate(`#/dokument/${projectId}`) }),
    el("h1", {}, data.settings.title),
    button("Gå til avspilleren", { variant: "btn-ghost btn-sm", iconName: "play", onClick: () => playBtn.focus() }),
    button("Lesevisning", { variant: "btn-ghost btn-sm", iconName: "eye", onClick: openViewSettings }),
    focusBtn,
    button("Hurtigtaster", { variant: "btn-ghost btn-sm", iconName: "keyboard", onClick: openShortcuts }));
  // The text is one tab stop; arrow keys move between paragraphs (roving tabindex).
  const textHost = el("div", { class: "reader-text", lang: data.settings.language || doc.language || "nb",
    role: "group", "aria-label": "Dokumenttekst. Bruk pil opp og pil ned for å flytte mellom avsnitt, og Enter for å lese herfra." });
  const info = el("div", { class: "reader-info" },
    el("p", {}, el("strong", {}, "Markering: "), syncData.precision_note),
    data.audio_state.stale ? el("p", {}, "Merk: Teksten eller valgene er endret etter at lyden ble laget, så markeringen kan avvike enkelte steder.") : null,
    el("p", { class: "small" }, "Klikk på et avsnitt for å starte opplesingen der. Med tastaturet: gå til teksten, flytt med piltastene og trykk Enter."));
  const page = el("article", { class: "reader-page", "aria-label": "Dokumenttekst" }, info, textHost);

  // ----- player -----------------------------------------------------------------------
  const audio = el("audio", { preload: "auto", src: `/api/projects/${projectId}/audio` });
  audio.preservesPitch = true;
  const playBtn = button("Spill av", { variant: "btn-primary play-btn", iconName: "play" });
  const stopBtn = button("Stopp", { variant: "btn-sm", iconName: "stop" });
  const backBtn = button("10 s tilbake", { variant: "btn-sm", iconName: "back" });
  const fwdBtn = button("30 s frem", { variant: "btn-sm", iconName: "forward" });
  const time = el("span", { class: "time", "aria-hidden": "true" }, "0:00 / 0:00");
  const seek = el("input", { type: "range", min: "0", max: String(Math.ceil(syncData.duration || 0)), step: "1", value: "0", "aria-label": "Posisjon i lydfilen" });
  const rateSel = el("select", { id: "rate", "aria-label": "Avspillingshastighet" },
    RATES.map((r) => el("option", { value: String(r), selected: Math.abs(r - (reading().playback_rate || 1)) < 0.01 }, `${String(r).replace(".", ",")}×`)));
  const chapterSel = el("select", { class: "chapter-select", "aria-label": "Velg kapittel" },
    el("option", { value: "" }, sync.chapters.length ? "Velg kapittel …" : "Ingen kapitler"),
    sync.chapters.map((c, i) => el("option", { value: String(i) }, c.title)));
  chapterSel.disabled = !sync.chapters.length;
  const follow = el("input", { type: "checkbox", id: "follow", checked: !!reading().auto_scroll });
  const player = el("section", { class: "player", "aria-label": "Lydavspiller" },
    el("div", { class: "player-row" }, playBtn, backBtn, fwdBtn, stopBtn, time, seek),
    el("div", { class: "player-row" },
      el("label", { for: "rate" }, "Hastighet"), rateSel, chapterSel,
      el("label", { class: "check", for: "follow" }, follow, el("span", {}, "Følg teksten"))),
    audio);
  const followBtn = button("Gå til opplesingen", { variant: "btn-primary follow-btn", onClick: () => resumeFollow(true) });
  followBtn.hidden = true;

  const shell = el("div", { class: "reader-shell" }, top, page, player);
  main.append(shell, followBtn);

  // ----- render text -------------------------------------------------------------------------
  const blockEls = new Map();
  const timedEls = []; // paragraphs with audio, in reading order (roving tabindex)
  let listHost = null;
  for (const block of doc.blocks) {
    const decision = decisions[block.id] || { include: true };
    if (HIDDEN_KINDS.has(block.kind) && !decision.include) continue;
    if (block.kind === "table" && decision.table_mode === "skip") {
      textHost.append(el("p", { class: "small muted" }, "[Tabell hoppet over]"));
      listHost = null;
      continue;
    }
    let node;
    if (block.kind === "heading") {
      const level = Math.min(4, Math.max(2, (block.level || 1) + 1));
      node = el(`h${level}`, {}, block.text);
    } else if (block.kind === "list_item") {
      if (!listHost) { listHost = el("ul"); textHost.append(listHost); }
      node = el("li", {}, block.text);
      listHost.append(node);
    } else if (block.kind === "quote") {
      node = el("blockquote", {}, block.text);
    } else if (block.kind === "table" && block.table) {
      node = el("div", {}, tableEl(block.table));
    } else {
      node = el("p", {}, block.text);
    }
    if (block.kind !== "list_item") listHost = null;
    node.classList.add("rb");
    node.dataset.block = block.id;
    node.style.whiteSpace = "pre-line";
    const timed = sync.startOfBlock(block.id) != null;
    if (timed) {
      node.tabIndex = timedEls.length === 0 ? 0 : -1;
      node.setAttribute("aria-describedby", "rb-hint");
      timedEls.push(node);
    } else {
      node.classList.add("not-read");
      node.dataset.skipLabel = block.kind === "table" && decision.reason
        ? decision.reason
        : (decision.reason ? `Leses ikke: ${decision.reason}` : "Leses ikke");
    }
    if (block.kind !== "list_item") textHost.append(node);
    blockEls.set(block.id, node);
  }
  textHost.append(el("p", { id: "rb-hint", class: "visually-hidden" }, "Trykk Enter for å starte opplesingen her."));

  function tableEl(table) {
    const t = el("table", { class: "doc-table" });
    if (table.header) t.append(el("thead", {}, el("tr", {}, table.header.map((h, c) => el("th", { scope: "col", dataset: { part: `h${c}` } }, h.trim())))));
    t.append(el("tbody", {}, table.rows.map((row, r) => el("tr", { dataset: { part: `r${r}` } },
      row.map((cell, c) => el("td", { dataset: { part: `r${r}c${c}` } }, cell.trim()))))));
    return t;
  }

  // ----- highlighting --------------------------------------------------------------------
  const supportsHighlight = typeof Highlight !== "undefined" && CSS.highlights;
  const highlight = supportsHighlight ? new Highlight() : null;
  if (highlight) CSS.highlights.set("ml-word", highlight);
  let currentWord = -1, currentBlock = null, currentPartEl = null, lastScrollAt = 0;
  let followPausedUntil = 0;

  function partElement(blockEl, part) {
    if (!part || part === "t") return blockEl;
    return blockEl.querySelector(`[data-part="${CSS.escape(part)}"]`) || blockEl;
  }

  function setBlock(blockId) {
    if (blockId === currentBlock) return false;
    const prev = currentBlock && blockEls.get(currentBlock);
    prev?.classList.remove("is-current");
    currentBlock = blockId;
    const next = blockId && blockEls.get(blockId);
    if (next && reading().paragraph_shading) next.classList.add("is-current");
    return true;
  }

  function update(force = false) {
    const t = audio.currentTime - sync.offset;
    const segIdx = sync.segmentAt(t);
    const seg = segIdx != null ? sync.segments[segIdx] : null;
    const blockChanged = setBlock(seg && t <= seg[1] + 1.2 ? seg[2] : (seg ? seg[2] : null));
    const wi = sync.wordAt(t);
    if (wi !== currentWord || force) {
      currentWord = wi ?? -1;
      highlight?.clear();
      if (currentPartEl) currentPartEl.classList.remove("is-current");
      currentPartEl = null;
      if (wi != null) {
        const w = sync.words[wi];
        const blockEl = blockEls.get(w[3]);
        if (blockEl) {
          const target = partElement(blockEl, w[4]);
          if (target !== blockEl && reading().paragraph_shading) {
            const row = w[4].startsWith("r") ? blockEl.querySelector(`[data-part="${CSS.escape(w[4].split("c")[0])}"]`) : null;
            currentPartEl = row || target;
            currentPartEl.classList.add("is-current");
          }
          const range = w[6] > w[5] ? rangeFor(target, w[5], w[6]) : null;
          if (highlight && range) highlight.add(range);
          maybeScroll(target, range, false);
        }
      }
    }
    if (blockChanged && currentBlock) maybeScroll(blockEls.get(currentBlock), null, true);
    updateTime();
  }

  function maybeScroll(elm, range, blockChange) {
    if (!elm || !follow.checked || Date.now() < followPausedUntil) return;
    const rect = (range && range.getBoundingClientRect()) || elm.getBoundingClientRect();
    const vh = window.innerHeight;
    const playerH = player.getBoundingClientRect().height || 120;
    const outside = rect.top < vh * 0.12 || rect.bottom > vh - playerH - 40;
    if (!outside && !(blockChange && rect.top > vh * 0.6)) return;
    if (Date.now() - lastScrollAt < 250 && !blockChange) return;
    lastScrollAt = Date.now();
    programmaticScroll = Date.now();
    const top = window.scrollY + rect.top - vh * 0.3;
    // Smooth scrolling only for short moves; jumps (seeking, chapter change) are instant.
    const smooth = !prefersReducedMotion() && Math.abs(top - window.scrollY) < vh * 1.2;
    window.scrollTo({ top: Math.max(0, top), behavior: smooth ? "smooth" : "auto" });
  }

  // Pause auto-follow when the user scrolls by themselves.
  let programmaticScroll = 0;
  function userScrolled() {
    if (Date.now() - programmaticScroll < 900) return;
    if (!follow.checked || audio.paused) return;
    followPausedUntil = Date.now() + 8000;
    followBtn.hidden = false;
  }
  function resumeFollow(jump) {
    followPausedUntil = 0;
    followBtn.hidden = true;
    if (jump) update(true);
    if (jump && currentBlock) maybeScroll(blockEls.get(currentBlock), null, true);
  }
  window.addEventListener("wheel", userScrolled, { passive: true });
  window.addEventListener("touchmove", userScrolled, { passive: true });
  const scrollKeys = new Set(["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End"]);
  function keyScroll(e) { if (scrollKeys.has(e.key) && !isFormField(e.target)) userScrolled(); }
  window.addEventListener("keydown", keyScroll);

  // ----- playback --------------------------------------------------------------------------
  // A 40 ms timer (plus timeupdate events) drives the highlighting. requestAnimationFrame
  // is not used because it stops when the window is not being painted.
  let ticker = 0;
  function tick() {
    update();
    if (followPausedUntil && Date.now() > followPausedUntil) { followPausedUntil = 0; followBtn.hidden = true; }
  }
  function startTicker() { stopTicker(); ticker = setInterval(tick, 40); }
  function stopTicker() { if (ticker) clearInterval(ticker); ticker = 0; }
  function setPlayingUi(playing) {
    playBtn.replaceChildren(icon(playing ? "pause" : "play"), el("span", {}, playing ? "Pause" : "Spill av"));
  }
  function updateTime() {
    const cur = audio.currentTime || 0;
    const dur = Number.isFinite(audio.duration) ? audio.duration : (syncData.duration || 0);
    time.textContent = `${formatDuration(cur)} / ${formatDuration(dur)}`;
    // While the slider has focus during playback, leave it alone: screen readers would
    // otherwise announce the position every second.
    if (document.activeElement === seek && !audio.paused) return;
    seek.value = String(Math.floor(cur));
    seek.setAttribute("aria-valuetext", `${spokenDuration(cur)} av ${spokenDuration(dur)}`);
  }
  function togglePlay() {
    if (audio.paused) audio.play().catch((err) => announce(`Kunne ikke spille av: ${err.message}`));
    else audio.pause();
  }
  function seekTo(seconds, { play = false } = {}) {
    const dur = Number.isFinite(audio.duration) ? audio.duration : syncData.duration;
    audio.currentTime = Math.max(0, Math.min(dur || 0, seconds + sync.offset));
    resumeFollow(false);
    update(true);
    if (play && audio.paused) audio.play().catch(() => {});
  }

  audio.addEventListener("play", () => { setPlayingUi(true); startTicker(); });
  audio.addEventListener("pause", () => { setPlayingUi(false); stopTicker(); update(true); savePosition(); });
  audio.addEventListener("timeupdate", () => { if (!ticker) update(); });
  audio.addEventListener("ended", () => { announce("Slutt på dokumentet."); savePosition(0); });
  audio.addEventListener("seeked", () => update(true));
  audio.addEventListener("loadedmetadata", () => {
    seek.max = String(Math.ceil(audio.duration || syncData.duration || 0));
    audio.playbackRate = Number(rateSel.value);
    const pos = Number(data.settings.playback?.position || 0);
    if (pos > 2 && pos < (audio.duration || 0) - 2) {
      audio.currentTime = pos;
      info.append(el("p", { class: "small" }, `Fortsetter der du slapp (${formatDuration(pos)}). `,
        button("Start fra begynnelsen", { variant: "btn-sm", onClick: (e) => { seekTo(0); e.currentTarget.parentElement.remove(); } })));
    }
    updateTime();
    update(true);
  });
  audio.addEventListener("error", () => {
    page.prepend(errorNotice({ message: "Lydfilen kunne ikke spilles av. Prøv å lage lyden på nytt.", details: `MediaError ${audio.error?.code}` }));
  });

  playBtn.addEventListener("click", togglePlay);
  stopBtn.addEventListener("click", () => { audio.pause(); seekTo(0); announce("Stoppet."); });
  backBtn.addEventListener("click", () => seekTo(audio.currentTime - sync.offset - 10));
  fwdBtn.addEventListener("click", () => seekTo(audio.currentTime - sync.offset + 30));
  seek.addEventListener("input", () => seekTo(Number(seek.value)));
  rateSel.addEventListener("change", () => {
    audio.playbackRate = Number(rateSel.value);
    audio.preservesPitch = true;
    saveReading({ playback_rate: Number(rateSel.value) });
    announce(`Hastighet ${rateSel.value.replace(".", ",")} ganger.`);
  });
  chapterSel.addEventListener("change", () => {
    if (chapterSel.value === "") return;
    const ch = sync.chapters[Number(chapterSel.value)];
    seekTo(ch.t0, { play: !audio.paused });
    announce(`Kapittel: ${ch.title}`);
  });
  follow.addEventListener("change", () => { saveReading({ auto_scroll: follow.checked }); if (follow.checked) resumeFollow(true); });

  textHost.addEventListener("click", (e) => {
    if (window.getSelection()?.toString()) return; // let people select text
    const target = e.target.closest(".rb");
    if (!target || target.classList.contains("not-read")) return;
    const cell = e.target.closest("[data-part]");
    const t = (cell && sync.startOfPart(target.dataset.block, cell.dataset.part)) ?? sync.startOfBlock(target.dataset.block);
    if (t != null) seekTo(t, { play: true });
  });
  function moveParagraphFocus(target) {
    if (!target) return;
    for (const node of timedEls) node.tabIndex = node === target ? 0 : -1;
    target.focus();
  }
  textHost.addEventListener("keydown", (e) => {
    const target = e.target.closest?.(".rb");
    if (!target || target !== e.target || target.classList.contains("not-read")) return;
    const index = timedEls.indexOf(target);
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      const t = sync.startOfBlock(target.dataset.block);
      if (t != null) { seekTo(t, { play: true }); announce("Leser herfra."); }
    } else if (e.key === "ArrowDown" && index < timedEls.length - 1) {
      e.preventDefault();
      moveParagraphFocus(timedEls[index + 1]);
    } else if (e.key === "ArrowUp" && index > 0) {
      e.preventDefault();
      moveParagraphFocus(timedEls[index - 1]);
    } else if (e.key === "Home") {
      e.preventDefault();
      moveParagraphFocus(timedEls[0]);
    } else if (e.key === "End") {
      e.preventDefault();
      moveParagraphFocus(timedEls[timedEls.length - 1]);
    }
  });
  textHost.addEventListener("focusin", (e) => {
    // Clicking or tabbing into a paragraph makes it the group's tab stop.
    if (timedEls.includes(e.target)) for (const node of timedEls) node.tabIndex = node === e.target ? 0 : -1;
  });

  function isFormField(node) {
    return node && (node.closest?.("input, select, textarea, button, [contenteditable], dialog, a") != null);
  }
  function shortcuts(e) {
    // Escape is not a character-key shortcut, so it always works, also with shortcuts turned off.
    if (e.key === "Escape" && document.body.classList.contains("focus-mode") && !e.target.closest?.("dialog")) {
      toggleFocus(false);
      return;
    }
    if (!reading().shortcuts || e.ctrlKey || e.metaKey || e.altKey) return;
    if (isFormField(e.target) || e.target.closest?.(".rb")) {
      if (!(e.target.closest?.(".rb") && (e.key === "k" || e.key === "K" || e.key === "j" || e.key === "J" || e.key === "l" || e.key === "L"))) return;
    }
    const k = e.key.toLowerCase();
    if (e.key === " " || k === "k") { e.preventDefault(); togglePlay(); }
    else if (k === "j") { e.preventDefault(); seekTo(audio.currentTime - sync.offset - 10); }
    else if (k === "l") { e.preventDefault(); seekTo(audio.currentTime - sync.offset + 30); }
  }
  document.addEventListener("keydown", shortcuts);

  if ("mediaSession" in navigator) {
    navigator.mediaSession.metadata = new MediaMetadata({ title: data.settings.title, artist: "Medleser" });
    navigator.mediaSession.setActionHandler("play", () => audio.play());
    navigator.mediaSession.setActionHandler("pause", () => audio.pause());
    navigator.mediaSession.setActionHandler("seekbackward", () => seekTo(audio.currentTime - sync.offset - 10));
    navigator.mediaSession.setActionHandler("seekforward", () => seekTo(audio.currentTime - sync.offset + 30));
  }

  // ----- position saving ---------------------------------------------------------------------
  let lastSaved = 0;
  function savePosition(force) {
    const pos = force === 0 ? 0 : audio.currentTime || 0;
    if (force === undefined && Math.abs(pos - lastSaved) < 1) return;
    lastSaved = pos;
    api.put(`/api/projects/${projectId}/playback`, { position: pos }).catch(() => {});
  }
  const saveTimer = setInterval(() => { if (!audio.paused) savePosition(); }, 5000);

  // ----- focus mode & dialogs --------------------------------------------------------------
  function toggleFocus(on) {
    const enable = on ?? !document.body.classList.contains("focus-mode");
    document.body.classList.toggle("focus-mode", enable);
    focusBtn.setAttribute("aria-pressed", String(enable));
    announce(enable ? "Distraksjonsfri modus er på. Trykk Escape for å avslutte." : "Distraksjonsfri modus er av.");
  }
  focusBtn.addEventListener("click", () => toggleFocus());

  function openViewSettings() {
    const d = createDialog("Lesevisning");
    d.dialog.style.width = "min(44rem, calc(100vw - 2rem))";
    d.body.append(el("p", { class: "small muted" }, "Endringene vises med en gang og lagres på denne datamaskinen."), readingForm({ prefix: "rd", compact: true }));
    d.actions.append(button("Lukk", { variant: "btn-primary", onClick: () => d.close() }));
    d.dialog.addEventListener("close", () => { follow.checked = !!reading().auto_scroll; update(true); });
    d.open();
  }

  function openShortcuts() {
    const d = createDialog("Hurtigtaster");
    const rows = [["Mellomrom eller K", "Spill av / pause"], ["J", "10 sekunder tilbake"], ["L", "30 sekunder frem"],
      ["Pil opp / pil ned i teksten", "Gå til forrige / neste avsnitt"],
      ["Enter på et avsnitt", "Start opplesingen der"], ["Escape", "Avslutt distraksjonsfri modus (virker alltid)"]];
    d.body.append(el("table", { class: "cost-table" }, el("tbody", {}, rows.map(([k, v]) => el("tr", {}, el("th", { scope: "row" }, k), el("td", {}, v))))),
      el("p", { class: "small" }, reading().shortcuts ? "Hurtigtastene er slått på. Du kan slå dem av under Lesevisning." : "Hurtigtastene er slått av. Du kan slå dem på under Lesevisning."));
    d.actions.append(button("Lukk", { variant: "btn-primary", onClick: () => d.close() }));
    d.open();
  }

  setPlayingUi(false);
  updateTime();

  return () => {
    savePosition();
    clearInterval(saveTimer);
    stopTicker();
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    highlight?.clear();
    if (supportsHighlight) CSS.highlights.delete("ml-word");
    document.removeEventListener("keydown", shortcuts);
    window.removeEventListener("wheel", userScrolled);
    window.removeEventListener("touchmove", userScrolled);
    window.removeEventListener("keydown", keyScroll);
    document.body.classList.remove("focus-mode");
    document.documentElement.classList.remove("reader-active");
    if ("mediaSession" in navigator) {
      for (const a of ["play", "pause", "seekbackward", "seekforward"]) navigator.mediaSession.setActionHandler(a, null);
    }
  };
}
