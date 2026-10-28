import { api, waitForJob } from "../api.js";
import { navigate } from "../main.js";
import {
  LANGUAGES, announce, button, confirmDialog, createDialog, el, errorNotice, formatBytes, formatDuration,
  formatNumber, guarded, keepFocus, languageName, notice, plural, progressBlock, setPageTitle, toast,
} from "../ui.js";

const KIND_LABELS = {
  heading: "Overskrift", paragraph: "Avsnitt", list_item: "Listepunkt", quote: "Sitat", caption: "Bildetekst",
  footnote: "Fotnote", reference: "Referanse", table: "Tabell", header: "Topptekst", footer: "Bunntekst",
  page_number: "Sidetall", figure_text: "Tekst i figur",
};
const TYPE_NAMES = { pdf: "PDF", docx: "Word-dokument", epub: "E-bok (EPUB)", txt: "Tekstfil" };
const CHUNK = 150;
// Rough speaking rate, only used for an approximate audio length. Measured 17.5 chars/s
// with Piper «Norsk (Talesyntese)» on a 6 600-character document (2026-10-28).
const CHARS_PER_SECOND = 17;

function approxMinutes(characters) {
  const minutes = Math.max(1, Math.round(characters / CHARS_PER_SECOND / 60));
  return `${minutes} ${minutes === 1 ? "minutt" : "minutter"}`;
}

export async function renderWorkspace(main, projectId) {
  let data;
  let providers = [];
  let disposed = false;
  const timers = new Set();
  const abortPolling = new AbortController();

  async function load() {
    data = await api.get(`/api/projects/${projectId}`);
    return data;
  }
  await load();
  api.post(`/api/projects/${projectId}/open`).catch(() => {});
  setPageTitle(data.settings.title);

  // ----- importing / failed import -------------------------------------------
  if (!data.document) {
    main.append(el("h1", {}, data.settings.title || "Dokument"));
    const importJob = data.jobs.find((j) => j.kind === "import");
    if (importJob) {
      const progress = progressBlock("Leser dokumentet …");
      const cancel = button("Avbryt", { variant: "btn-sm", onClick: () => api.post(`/api/jobs/${importJob.id}/cancel`).catch(() => {}) });
      main.append(el("div", { class: "card stack" }, progress.node, cancel));
      const abort = new AbortController();
      // Poll in the background so the view (and focus handling) is ready immediately.
      waitForJob(importJob.id, (j) => progress.set(j.progress, j.message), { signal: abort.signal })
        .then((job) => { if (!disposed && job) navigate(`#/dokument/${projectId}`); })
        .catch((err) => { if (!disposed) main.append(errorNotice(err)); });
      return () => { disposed = true; abort.abort(); };
    }
    const err = data.settings.import_error || { message: "Dokumentet er ikke lest inn." };
    main.append(errorNotice(err, { title: "Dokumentet kunne ikke leses" }),
      el("div", { class: "row" },
        button("Prøv med tekstgjenkjenning (OCR)", { variant: "btn-primary", onClick: () => reimport(true) }),
        button("Prøv på nytt", { onClick: () => reimport(false) }),
        button("Slett dokumentet", { variant: "btn-danger", onClick: deleteProject })));
    return () => { disposed = true; };
  }

  async function reimport(forceOcr) {
    if (data.document) {
      const ok = await confirmDialog({
        title: forceOcr ? "Les dokumentet med tekstgjenkjenning?" : "Les dokumentet på nytt?",
        message: ["Teksten hentes ut på nytt fra originalfilen. Rettelser du har gjort i teksten, går tapt.",
          forceOcr ? "Tekstgjenkjenning (OCR) kan ta litt tid og kan gi feil i teksten." : ""].filter(Boolean),
        confirmLabel: "Les på nytt",
      });
      if (!ok) return;
    }
    await guarded(main, async () => {
      await api.post(`/api/projects/${projectId}/reimport`, { force_ocr: forceOcr });
      navigate(`#/dokument/${projectId}`);
    });
  }

  async function deleteProject() {
    const ok = await confirmDialog({
      title: "Slette dokumentet?",
      message: ["Dokumentet, uttrukket tekst og lydfiler som er laget, slettes fra Medleser. Dette kan ikke angres."],
      confirmLabel: "Slett", danger: true,
    });
    if (!ok) return;
    await guarded(main, async () => {
      await api.del(`/api/projects/${projectId}`);
      announce("Dokumentet er slettet.");
      navigate("#/dokumenter");
    });
  }

  try { providers = await api.get("/api/providers"); } catch { providers = []; }

  // ----- header ------------------------------------------------------------------
  const doc = data.document;
  const titleH = el("h1", {}, data.settings.title);
  const meta = el("p", { class: "ws-meta" },
    el("span", {}, TYPE_NAMES[doc.source_type] || doc.source_type),
    doc.page_count ? el("span", {}, plural(doc.page_count, "side", "sider")) : null,
    el("span", {}, formatBytes(doc.size_bytes)),
    el("span", {}, `Oppdaget språk: ${languageName(doc.language)}${doc.language_confidence && doc.language !== "und" && doc.language_confidence < 0.6 ? " (usikkert)" : ""}`));
  const header = el("header", { class: "page-header" },
    el("div", { class: "row spread" }, titleH,
      el("div", { class: "row" },
        button("Endre tittel", { variant: "btn-ghost btn-sm", iconName: "edit", onClick: renameTitle }),
        button("Slett", { variant: "btn-ghost btn-sm", iconName: "trash", onClick: deleteProject }))),
    meta);
  main.append(header);
  for (const w of doc.warnings || []) main.append(notice("warning", el("p", {}, w.message)));

  async function renameTitle() {
    const d = createDialog("Endre tittel");
    const input = el("input", { type: "text", id: "title-input", value: data.settings.title, autofocus: true, maxlength: "300" });
    d.body.append(el("div", { class: "field" }, el("label", { for: "title-input" }, "Tittel"), input));
    d.actions.append(button("Avbryt", { onClick: () => d.close() }),
      button("Lagre", { variant: "btn-primary", onClick: () => guarded(d.body, async () => {
        if (!input.value.trim()) return;
        await patch({ title: input.value.trim() });
        titleH.textContent = data.settings.title;
        setPageTitle(data.settings.title);
        d.close();
        toast("Tittelen er endret.");
      }) }));
    d.open();
  }

  // ----- layout ----------------------------------------------------------------------
  const controls = el("div", { class: "controls" });
  const previewSection = el("section", { "aria-labelledby": "preview-title" });
  main.append(el("div", { class: "workspace" }, controls, previewSection));

  const voiceCard = el("section", { class: "card", "aria-labelledby": "voice-title" });
  const filterCard = el("section", { class: "card", "aria-labelledby": "filter-title" });
  const audioCard = el("section", { class: "card", "aria-labelledby": "audio-title" });
  controls.append(audioCard, voiceCard, filterCard);

  async function patch(changes) {
    data = await api.patch(`/api/projects/${projectId}`, changes);
    return data;
  }

  // ----- voice -----------------------------------------------------------------------
  const sampleAudio = el("audio", { controls: true, preload: "none", hidden: true, "aria-label": "Stemmeprøve" });
  async function renderVoice() {
    const settings = data.settings;
    const lang = settings.language || doc.language || "nb";
    voiceCard.replaceChildren(el("h2", { id: "voice-title" }, "Språk og stemme"));

    const langSelect = el("select", { id: "lang-select" },
      LANGUAGES.map(([code, name]) => el("option", { value: code, selected: code === lang }, name)));
    if (!LANGUAGES.some(([c]) => c === lang)) langSelect.prepend(el("option", { value: lang, selected: true }, languageName(lang)));
    langSelect.addEventListener("change", () => guarded(voiceCard, async () => {
      await patch({ language: langSelect.value });
      await ensureVoice();
      await keepFocus(voiceCard, renderVoice);
      renderPreview();
      renderAudio();
      announce(`Språket er endret til ${languageName(langSelect.value).toLowerCase()}.`);
    }));
    voiceCard.append(el("div", { class: "field" },
      el("label", { for: "lang-select" }, "Språk"), langSelect,
      el("span", { class: "hint", id: "lang-hint" }, "Språket styrer uttalen og hvilke stemmer du kan velge. Teksten blir ikke oversatt.")));

    // Providers
    const fs = el("fieldset", {}, el("legend", {}, "Stemmetjeneste"));
    const cards = el("div", { class: "radio-cards" });
    for (const p of providers) {
      const caps = p.capabilities;
      const usable = p.status.state === "ready" && (caps.auth_method !== "api_key" || p.connected);
      const id = `prov-${caps.id}`;
      const badges = el("span", { class: "provider-badges" },
        el("span", { class: `badge ${caps.is_paid ? "badge-paid" : "badge-free"}` }, caps.is_paid ? "Betalt" : "Gratis"),
        el("span", { class: "badge" }, caps.is_local ? "Lokalt" : "Sender tekst over internett"));
      if (usable) {
        const radio = el("input", { type: "radio", name: "provider", id, value: caps.id, checked: settings.provider === caps.id });
        radio.addEventListener("change", () => guarded(voiceCard, async () => {
          await patch({ provider: caps.id });
          await ensureVoice();
          await keepFocus(voiceCard, renderVoice);
          renderAudio();
        }));
        cards.append(el("label", { class: "radio-card", for: id }, radio,
          el("span", {}, el("span", { class: "title" }, caps.name), " ", badges,
            el("span", { class: "desc" }, caps.timestamps_note))));
      } else {
        const why = caps.auth_method === "api_key" && !p.connected ? "Ikke tilkoblet." : p.status.message;
        cards.append(el("div", { class: "radio-card", "aria-disabled": "true" },
          el("span", {}, el("span", { class: "title" }, caps.name), " ", badges,
            el("span", { class: "desc" }, why, " ", el("a", { href: "#/stemmer" }, caps.auth_method === "api_key" ? "Koble til" : "Sett opp")))));
      }
    }
    fs.append(cards);
    voiceCard.append(fs);

    // Voices
    if (settings.provider) {
      let res = { voices: [], models: [] };
      try { res = await api.get(`/api/providers/${settings.provider}/voices?language=${encodeURIComponent(lang)}`); } catch { /* shown below */ }
      if (!res.voices.length) {
        voiceCard.append(notice("warning", el("p", {}, `Denne tjenesten har ingen stemme for ${languageName(lang).toLowerCase()}. `,
          el("a", { href: "#/stemmer" }, "Finn flere stemmer"))));
      } else {
        const voiceSelect = el("select", { id: "voice-select", "aria-describedby": "voice-note" },
          res.voices.map((v) => el("option", { value: v.id, selected: v.id === settings.voice_id }, v.name)));
        const current = res.voices.find((v) => v.id === settings.voice_id) || res.voices[0];
        const note = el("p", { class: "voice-note", id: "voice-note" }, current?.quality_note || current?.description || "");
        voiceSelect.addEventListener("change", () => guarded(voiceCard, async () => {
          await patch({ voice_id: voiceSelect.value });
          const v = res.voices.find((x) => x.id === voiceSelect.value);
          note.textContent = v?.quality_note || v?.description || "";
          renderAudio();
        }));
        voiceCard.append(el("div", { class: "field" }, el("label", { for: "voice-select" }, "Stemme"), voiceSelect, note));
        if (res.models?.length) {
          const modelSelect = el("select", { id: "model-select", "aria-describedby": "model-note" },
            res.models.map((m) => el("option", { value: m.id, selected: m.id === settings.model_id }, m.name)));
          const m0 = res.models.find((m) => m.id === settings.model_id) || res.models[0];
          const mnote = el("p", { class: "voice-note", id: "model-note" }, m0?.description || "");
          modelSelect.addEventListener("change", () => guarded(voiceCard, async () => {
            await patch({ model_id: modelSelect.value });
            mnote.textContent = res.models.find((m) => m.id === modelSelect.value)?.description || "";
            renderAudio();
          }));
          voiceCard.append(el("div", { class: "field" }, el("label", { for: "model-select" }, "Modell"), modelSelect, mnote));
        }
        const providerInfo = providers.find((p) => p.capabilities.id === settings.provider);
        const isPaid = providerInfo?.capabilities.is_paid;
        const sampleRow = el("div", { class: "row" });
        if (isPaid) {
          if (current?.preview_available) {
            sampleRow.append(button("Hør stemmeprøve (gratis)", { iconName: "play", onClick: () => {
              playSample(`/api/providers/${settings.provider}/preview/${encodeURIComponent(voiceSelect.value)}`);
            } }));
          }
          sampleRow.append(button("Hør din tekst (bruker kreditter)", { iconName: "play", onClick: () => paidSample(voiceSelect.value, lang) }));
        } else {
          sampleRow.append(button("Hør stemmeprøve", { iconName: "play", onClick: async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            btn.lastChild.textContent = "Lager prøve …";
            try {
              const r = await api.post("/api/samples", { provider: settings.provider, voice_id: voiceSelect.value, language: lang,
                model_id: settings.model_id, project_id: projectId, use_document_text: true });
              playSample(r.url);
            } catch (err) {
              voiceCard.append(errorNotice(err));
            } finally {
              btn.disabled = false;
              btn.lastChild.textContent = "Hør stemmeprøve";
            }
          } }));
        }
        voiceCard.append(sampleRow, sampleAudio);
      }
    }
    const speed = Number(settings.speed || 1);
    const speedSel = el("select", { id: "speed-select", "aria-describedby": "speed-hint" },
      [0.8, 0.9, 1, 1.1, 1.2, 1.3].map((v) => el("option", { value: String(v), selected: Math.abs(v - speed) < 0.01 },
        v === 1 ? "Normal" : `${String(v).replace(".", ",")}×`)));
    speedSel.addEventListener("change", () => guarded(voiceCard, async () => { await patch({ speed: Number(speedSel.value) }); renderAudio(); }));
    voiceCard.append(el("details", {}, el("summary", {}, "Flere valg"),
      el("div", { class: "field" }, el("label", { for: "speed-select" }, "Taletempo i lydfilen"), speedSel,
        el("span", { class: "hint", id: "speed-hint" }, "Endrer selve lydfilen. Under avspilling kan du også endre hastigheten fritt."))));
  }

  function playSample(url) {
    sampleAudio.hidden = false;
    sampleAudio.src = url;
    sampleAudio.play().catch(() => {});
    announce("Spiller stemmeprøve.");
  }

  async function paidSample(voiceId, lang) {
    const body = { provider: data.settings.provider, voice_id: voiceId, language: lang, model_id: data.settings.model_id,
      project_id: projectId, use_document_text: true };
    try {
      const r = await api.post("/api/samples", body);
      playSample(r.url);
    } catch (err) {
      if (err.code !== "confirmation_required") { voiceCard.append(errorNotice(err)); return; }
      const chars = err.extra?.characters || 0;
      const usage = err.extra?.usage;
      const ok = await confirmDialog({
        title: "Stemmeprøve med din tekst",
        message: [`Medleser sender de første ${formatNumber(chars)} tegnene av dokumentet til tjenesten.`,
          usage?.credits != null ? `Det bruker omtrent ${formatNumber(Math.ceil(usage.credits))} kreditter. ${usage.note || ""}` : (usage?.note || "Det kan bruke kreditter."),
          "Vil du fortsette?"],
        confirmLabel: "Send og hør prøven",
      });
      if (!ok) return;
      try {
        const r = await api.post("/api/samples", { ...body, confirm_paid: true });
        playSample(r.url);
      } catch (e2) { voiceCard.append(errorNotice(e2)); }
    }
  }

  async function ensureVoice() {
    const s = data.settings;
    if (!s.provider) return;
    const lang = s.language || doc.language || "nb";
    try {
      const res = await api.get(`/api/providers/${s.provider}/voices?language=${encodeURIComponent(lang)}`);
      if (res.voices.length && !res.voices.some((v) => v.id === s.voice_id)) {
        const changes = { voice_id: res.voices[0].id };
        if (res.models?.length && !res.models.some((m) => m.id === s.model_id)) changes.model_id = res.models[0].id;
        await patch(changes);
      } else if (res.models?.length && !res.models.some((m) => m.id === s.model_id)) {
        await patch({ model_id: res.models[0].id });
      }
    } catch { /* ignore */ }
  }

  // ----- text options ------------------------------------------------------------------
  function countKinds() {
    const counts = {};
    let urls = 0, dois = 0, emails = 0, refs = 0;
    for (const b of doc.blocks) {
      counts[b.kind] = (counts[b.kind] || 0) + 1;
      for (const s of b.spans || []) {
        if (s.kind === "url") urls++;
        if (s.kind === "doi") dois++;
        if (s.kind === "email") emails++;
        if (s.kind === "footnote_ref") refs++;
      }
    }
    return { counts, urls, dois, emails, refs };
  }

  function renderFilters() {
    const o = data.settings.options || {};
    const { counts, urls, dois, emails, refs } = countKinds();
    const n = (k) => counts[k] || 0;
    filterCard.replaceChildren(el("h2", { id: "filter-title" }, "Hva skal leses opp?"),
      el("p", { class: "small muted" }, "Valgene endrer bare opplesningen. Originalteksten beholdes, og du ser i forhåndsvisningen hva som hoppes over."));

    const check = (key, label, hint) => {
      const id = `opt-${key}`;
      const input = el("input", { type: "checkbox", id, checked: !!o[key] });
      input.addEventListener("change", () => setOption({ [key]: input.checked }));
      return el("label", { class: "check", for: id }, input, el("span", {}, label, hint ? el("span", { class: "hint" }, hint) : null));
    };
    const radios = (key, legend, choices) => {
      const fs = el("fieldset", {}, el("legend", {}, legend));
      for (const [value, label] of choices) {
        const id = `opt-${key}-${value}`;
        const input = el("input", { type: "radio", name: `opt-${key}`, id, value, checked: o[key] === value });
        input.addEventListener("change", () => setOption({ [key]: value }));
        fs.append(el("label", { class: "check", for: id }, input, el("span", {}, label)));
      }
      return fs;
    };

    filterCard.append(el("fieldset", {}, el("legend", {}, "Hopp over"),
      check("skip_urls", "Nettadresser (URL-er)", urls ? `${urls} i dokumentet` : null),
      check("skip_doi", "DOI-lenker", dois ? `${dois} i dokumentet` : null),
      check("skip_emails", "E-postadresser", emails ? `${emails} i dokumentet` : null),
      check("skip_headers_footers", "Topptekst og bunntekst", n("header") + n("footer") ? `${n("header") + n("footer")} funnet` : null),
      check("skip_page_numbers", "Sidetall", n("page_number") ? `${n("page_number")} funnet` : null),
      check("skip_footnote_markers", "Fotnotetall inne i teksten", refs ? `${refs} funnet` : null)));
    filterCard.append(radios("references", `Referanseliste${n("reference") ? ` (${n("reference")} referanser)` : ""}`,
      [["skip", "Hopp over referanselisten"], ["read", "Les hele referanselisten"]]));
    filterCard.append(radios("footnotes", `Fotnoter${n("footnote") ? ` (${n("footnote")})` : ""}`,
      [["skip", "Hopp over fotnoter"], ["read", "Les fotnoter"]]));
    filterCard.append(radios("tables", `Tabeller${n("table") ? ` (${n("table")})` : ""}`,
      [["read", "Les tabellen rad for rad"], ["skip", "Hopp over tabellen"], ["show", "Vis tabellen på skjermen uten opplesning"]]));
    filterCard.append(el("fieldset", {}, el("legend", {}, "Annet"),
      check("captions", "Les bildetekster", n("caption") ? `${n("caption")} funnet` : null),
      check("figure_text", "Les tekst inne i figurer", "Ofte bruddstykker fra diagrammer"),
      check("expand_abbreviations", "Les forkortelser fullt ut", "For eksempel «f.eks.» som «for eksempel»")));
    const summary = data.reading_summary;
    if (summary) {
      filterCard.append(el("p", { class: "small", id: "reading-summary" }, summaryText(summary)));
    }
  }

  function summaryText(summary) {
    return `Opplesningen blir ${formatNumber(summary.characters)} tegn, omtrent ${approxMinutes(summary.characters)} lyd. `
      + `${plural(summary.skipped_blocks, "tekstbit", "tekstbiter")} leses ikke.`;
  }

  function setOption(changes) {
    return guarded(filterCard, async () => {
      await patch({ options: changes });
      renderPreview();
      renderAudio();
      const target = filterCard.querySelector("#reading-summary");
      if (target && data.reading_summary) target.textContent = summaryText(data.reading_summary);
      announce("Valget er lagret.");
    });
  }

  // ----- audio --------------------------------------------------------------------------
  let generateJob = data.jobs.find((j) => j.kind === "generate" || j.kind === "export");
  async function renderAudio() {
    audioCard.replaceChildren(el("h2", { id: "audio-title" }, "Lyd og medlesing"));
    if (generateJob) { attachJob(generateJob); return; }
    const st = data.audio_state || {};
    const settings = data.settings;
    if (!settings.provider || !settings.voice_id) {
      audioCard.append(el("p", {}, "Velg en stemme nedenfor for å lage lyd."));
      return;
    }
    if (st.ready && !st.stale) {
      const a = st.audio;
      audioCard.append(
        notice("success", el("p", {}, el("strong", {}, "Lyden er klar. "),
          `${formatDuration(a.duration)} · ${a.voice_name} · ${a.precision === "word" ? "ord-for-ord-markering" : a.precision === "mixed" ? "ord- og avsnittsmarkering" : "avsnittsmarkering"}`)),
        el("div", { class: "row" },
          button("Åpne medlesing", { variant: "btn-primary btn-lg", iconName: "headphones", onClick: () => navigate(`#/les/${projectId}`) })),
        el("div", { class: "row" },
          button("Lagre som MP3", { iconName: "download", onClick: () => exportAudio("single") }),
          (data.chapters || []).length ? button("Lagre ett kapittel per fil", { iconName: "folder", onClick: () => exportAudio("chapters") }) : null));
      if (a.provider !== settings.provider || a.voice_id !== settings.voice_id || (a.model_id || null) !== (settings.model_id || null)) {
        audioCard.append(el("p", { class: "small muted" }, "Du har valgt en annen stemme enn den lyden ble laget med."),
          button("Lag lyd med valgt stemme", { onClick: startGenerate }));
      }
      return;
    }
    if (st.ready && st.stale) {
      audioCard.append(notice("warning", el("p", {}, "Teksten, valgene eller stemmen er endret etter at lyden ble laget. Lag lyden på nytt for å oppdatere den.")));
    } else if (st.cached_segments > 0) {
      audioCard.append(el("p", {}, `${st.cached_segments} av ${st.total_segments} deler er allerede laget og brukes på nytt.`));
    }
    const est = data.reading_summary;
    if (est) audioCard.append(el("p", { class: "small muted" }, `Lyden blir omtrent ${approxMinutes(est.characters)} lang.`));
    const label = st.ready ? "Lag lyden på nytt" : st.cached_segments > 0 ? "Fortsett å lage lyd" : "Lag lyd";
    audioCard.append(el("div", { class: "row" }, button(label, { variant: "btn-primary btn-lg", iconName: "sparkle", onClick: startGenerate }),
      st.ready ? button("Åpne medlesing likevel", { onClick: () => navigate(`#/les/${projectId}`) }) : null));
  }

  async function startGenerate() {
    let estimate;
    try { estimate = await api.get(`/api/projects/${projectId}/estimate`); } catch (err) { audioCard.append(errorNotice(err)); return; }
    let token = null;
    if (estimate.provider.is_paid && estimate.segments_pending > 0) {
      token = await costDialog(estimate);
      if (!token) return;
    }
    try {
      const res = await api.post(`/api/projects/${projectId}/generate`, { confirmation_token: token });
      generateJob = res.job;
      renderAudio();
    } catch (err) {
      audioCard.append(errorNotice(err));
    }
  }

  function costDialog(estimate) {
    return new Promise((resolve) => {
      const d = createDialog(`Godkjenn bruk av ${estimate.provider.name}`);
      const usage = estimate.usage || {};
      const rows = [
        ["Tjeneste", estimate.provider.name],
        ["Stemme", estimate.voice.name],
        estimate.model ? ["Modell", estimate.model.name] : null,
        ["Tekst som sendes", `${formatNumber(estimate.characters_pending)} tegn (${plural(estimate.segments_pending, "del", "deler")})`],
        estimate.segments_cached ? ["Allerede laget", `${plural(estimate.segments_cached, "del", "deler")} brukes på nytt uten ny kostnad`] : null,
        ["Estimert forbruk", usage.credits != null ? `Omtrent ${formatNumber(Math.ceil(usage.credits))} kreditter` : "Kan ikke beregnes sikkert"],
        usage.remaining_credits != null ? ["Igjen på kontoen din", `${formatNumber(Math.floor(usage.remaining_credits))} kreditter (ifølge tjenesten)`] : null,
        estimate.limit != null ? ["Din grense for dette dokumentet", `${formatNumber(estimate.limit)} tegn (brukt hittil: ${formatNumber(estimate.used_on_document)})`] : ["Din grense for dette dokumentet", "Ingen grense"],
      ].filter(Boolean);
      const table = el("table", { class: "cost-table" }, el("tbody", {}, rows.map(([k, v]) => el("tr", {}, el("th", { scope: "row" }, k), el("td", {}, v)))));
      d.body.append(
        el("p", {}, "Før noe sendes, må du godkjenne dette:"),
        table,
        el("p", { class: "small muted" }, usage.note || ""),
        notice("warning", el("p", {}, `Teksten sendes over internett til ${estimate.provider.name} for å lage lyd. `
          + "Bare tekstbitene som skal leses opp sendes – ikke selve filen. Ikke send tekst du ikke har lov til å dele med en ekstern tjeneste.")),
      );
      let limitBox = null;
      if (estimate.exceeds_limit) {
        const needed = estimate.used_on_document + estimate.characters_pending;
        const input = el("input", { type: "number", id: "limit-input", min: "1000", step: "1000",
          value: String(Math.ceil(needed / 1000) * 1000), "aria-describedby": "limit-help" });
        const limitMsg = el("p", { class: "small", id: "limit-help" },
          `Jobben trenger minst ${formatNumber(needed)} tegn, inkludert det som allerede er brukt på dokumentet.`);
        limitBox = el("div", { class: "stack" },
          notice("error", el("p", {}, "Jobben er større enn forbruksgrensen du har satt for dette dokumentet. Øk grensen eller velg bort tekst før du fortsetter.")),
          el("div", { class: "field" }, el("label", { for: "limit-input" }, "Ny grense for dette dokumentet (tegn)"), input, limitMsg),
          button("Lagre ny grense", { onClick: () => guarded(limitBox, async () => {
            const value = Math.floor(Number(input.value));
            if (!Number.isFinite(value) || value < needed) {
              limitMsg.textContent = `Skriv et tall på minst ${formatNumber(needed)}. En tom grense lagres ikke her.`;
              input.focus();
              return;
            }
            await patch({ max_cloud_chars: value });
            d.close("cancel");
            startGenerate();
          }) }));
        d.body.append(limitBox);
      }
      const consent = el("input", { type: "checkbox", id: "consent", disabled: estimate.exceeds_limit });
      d.body.append(el("label", { class: "check", for: "consent" }, consent,
        el("span", {}, `Jeg forstår at teksten sendes til ${estimate.provider.name}, og at dette kan bruke kreditter eller koste penger på min konto der.`)));
      const go = button("Send og lag lyd", { variant: "btn-primary" });
      go.disabled = true;
      consent.addEventListener("change", () => { go.disabled = !consent.checked; });
      go.addEventListener("click", () => d.close("ok"));
      d.actions.append(button("Avbryt", { onClick: () => d.close("cancel") }), go);
      d.dialog.addEventListener("close", () => resolve(d.dialog.returnValue === "ok" ? estimate.confirmation_token : null));
      d.open();
    });
  }

  let jobView = null; // one progress view and one poller per running job
  function attachJob(job) {
    if (jobView?.id === job.id) {
      audioCard.append(...jobView.nodes);
      return;
    }
    const label = job.kind === "export" ? "Lagrer lydfiler …" : "Lager lyd …";
    const progress = progressBlock(label);
    progress.set(job.progress, job.message || label);
    const cancel = button("Avbryt", { variant: "btn-sm", onClick: () => api.post(`/api/jobs/${job.id}/cancel`).catch(() => {}) });
    const nodes = [progress.node,
      el("p", { class: "small muted" }, "Du kan bruke resten av Medleser mens lyden lages. Avbryter du, beholdes det som er ferdig."),
      cancel];
    jobView = { id: job.id, nodes };
    audioCard.append(...nodes);
    waitForJob(job.id, (j) => progress.set(j.progress, j.message || label), { signal: abortPolling.signal }).then(async (done) => {
      if (disposed || !done) return;
      jobView = null;
      generateJob = null;
      await load();
      renderAudio();
      if (done?.state === "done") {
        if (job.kind === "export") exported(done.result);
        else { announce("Lyden er klar."); toast("Lyden er klar. Du kan åpne medlesing."); }
      } else if (done?.state === "cancelled") {
        announce("Avbrutt. Det som var ferdig, er lagret.");
      } else if (done?.error) {
        audioCard.append(errorNotice(done.error, { title: "Lyden ble ikke ferdig" }));
      }
    }).catch((err) => { if (!disposed) audioCard.append(errorNotice(err)); });
  }

  async function exportAudio(mode) {
    try {
      const res = await api.post(`/api/projects/${projectId}/export`, { mode });
      if (res.cancelled) return;
      if (res.job) { generateJob = res.job; renderAudio(); return; }
      exported(res);
    } catch (err) { audioCard.append(errorNotice(err)); }
  }

  function exported(result) {
    const files = result?.saved || [];
    if (!files.length) return;
    const where = result.folder || files[0];
    audioCard.append(notice("success",
      el("p", {}, files.length === 1 ? "Lydfilen er lagret:" : `${files.length} lydfiler er lagret i:`),
      el("p", { class: "small" }, where),
      el("div", { class: "row" }, button("Vis i mappe", { variant: "btn-sm", iconName: "folder", onClick: () => {
        api.post("/api/system/open-folder", { target: "exported", path: files[0] }).catch(() => {});
      } }))));
    announce(files.length === 1 ? "Lydfilen er lagret." : `${files.length} lydfiler er lagret.`);
  }

  // ----- preview ------------------------------------------------------------------------
  let shown = CHUNK;
  let onlyRead = false;
  function renderPreview() {
    const decisions = data.decisions || {};
    const blocks = onlyRead ? doc.blocks.filter((b) => decisions[b.id]?.include) : doc.blocks;
    const filterToggle = el("input", { type: "checkbox", id: "only-read", checked: onlyRead });
    filterToggle.addEventListener("change", () => {
      onlyRead = filterToggle.checked;
      shown = CHUNK;
      renderPreview();
      previewSection.querySelector("#only-read")?.focus(); // the old checkbox was replaced
    });
    const toolbar = el("div", { class: "preview-toolbar" },
      el("h2", { id: "preview-title" }, "Tekst"),
      el("label", { class: "check", for: "only-read" }, filterToggle, el("span", {}, "Vis bare tekst som leses opp")));
    const list = el("div", { class: "preview", lang: data.settings.language || doc.language || "nb" });
    for (const block of blocks.slice(0, shown)) list.append(previewBlock(block, decisions[block.id] || { include: true }));
    const nodes = [toolbar,
      el("p", { class: "small muted" }, "Les gjennom teksten og rett feil før du lager lyd. Overstreket tekst leses ikke opp."), list];
    if (blocks.length > shown) {
      nodes.push(el("div", { class: "row" }, button(`Vis flere (${blocks.length - shown} igjen)`, { onClick: () => {
        const firstNew = shown;
        shown += CHUNK * 2;
        renderPreview();
        // Continue where the user was: focus the first newly shown paragraph's first control.
        previewSection.querySelectorAll(".pv-block")[firstNew]?.querySelector("button, select")?.focus();
      } })));
    }
    previewSection.replaceChildren(...nodes);
  }

  function textWithSpans(text, skipped) {
    const frag = document.createDocumentFragment();
    const spans = [...(skipped || [])].sort((a, b) => a.start - b.start);
    let pos = 0;
    for (const s of spans) {
      if (s.start < pos) continue;
      frag.append(document.createTextNode(text.slice(pos, s.start)));
      frag.append(el("span", { class: "skip-span", title: "Leses ikke opp" }, text.slice(s.start, s.end),
        el("span", { class: "visually-hidden" }, " (leses ikke)")));
      pos = s.end;
    }
    frag.append(document.createTextNode(text.slice(pos)));
    return frag;
  }

  function previewBlock(block, decision) {
    const node = el("div", { class: `pv-block kind-${block.kind}${!decision.include ? " excluded" : ""}`, id: `pv-${block.id}` });
    const head = el("div", { class: "pv-head" },
      el("span", { class: "badge" }, block.kind === "heading" ? `Overskrift ${block.level || ""}`.trim() : KIND_LABELS[block.kind] || block.kind),
      block.page ? el("span", { class: "small muted" }, `s. ${block.page}`) : null,
      !decision.include && decision.reason && block.kind !== "table" ? el("span", { class: "badge badge-paid" }, `Leses ikke: ${decision.reason}`) : null,
      block.kind === "table" && decision.reason ? el("span", { class: "badge badge-info" }, decision.reason) : null,
      block.ocr ? el("span", { class: "badge badge-info", title: "Hentet med tekstgjenkjenning" }, "OCR") : null,
      block.original_text != null ? el("span", { class: "edited-mark" }, "Rettet av deg") : null,
      block.table && block.table.reliable === false ? el("span", { class: "badge badge-error" }, "Usikker tabell") : null);
    node.append(head);
    if (block.kind === "table" && block.table) {
      node.append(tableNode(block.table));
      if (block.table.reliable === false && block.table.note) node.append(el("p", { class: "small" }, block.table.note));
    } else {
      node.append(el("div", { class: "pv-text" }, textWithSpans(block.text, decision.skipped_spans)));
    }
    const tools = el("div", { class: "pv-tools" });
    if (block.kind === "table") {
      const sel = el("select", { "aria-label": "Hva skal skje med denne tabellen?" },
        [["read", "Les rad for rad"], ["skip", "Hopp over"], ["show", "Vis uten opplesning"]].map(([v, l]) => el("option", { value: v, selected: decision.table_mode === v }, l)));
      sel.dataset.fk = `table-${block.id}`;
      sel.addEventListener("change", () => guarded(node, async () => {
        // Always start from the latest overrides (other tables may have changed meanwhile).
        const overrides = { ...(data.settings.options?.table_overrides || {}), [block.id]: sel.value };
        await patch({ options: { table_overrides: overrides } });
        replaceBlock(block, `table-${block.id}`);
        renderAudio();
      }));
      tools.append(sel);
    } else {
      tools.append(button("Rediger", { variant: "btn-ghost btn-sm", iconName: "edit", onClick: () => editBlock(block, node),
        attrs: { "data-fk": `edit-${block.id}` } }));
      const toggleLabel = decision.include ? "Ikke les dette" : "Les dette likevel";
      tools.append(button(toggleLabel, { variant: "btn-ghost btn-sm", iconName: decision.include ? "close" : "check",
        attrs: { "data-fk": `toggle-${block.id}` },
        onClick: () => guarded(node, async () => {
          // Read the overrides at click time; a copy from render time would undo earlier choices.
          const next = { ...(data.settings.options?.block_overrides || {}), [block.id]: !decision.include };
          await patch({ options: { block_overrides: next } });
          replaceBlock(block, `toggle-${block.id}`);
          renderAudio();
          announce(decision.include ? "Teksten leses ikke opp." : "Teksten leses opp.");
        }) }));
    }
    node.append(tools);
    return node;
  }

  function replaceBlock(block, focusFk) {
    const fresh = data.document.blocks.find((b) => b.id === block.id) || block;
    const old = previewSection.querySelector(`#pv-${CSS.escape(block.id)}`);
    if (!old) return;
    const node = previewBlock(fresh, data.decisions[block.id] || { include: true });
    old.replaceWith(node);
    const target = (focusFk && node.querySelector(`[data-fk="${CSS.escape(focusFk)}"]`)) || node.querySelector("button, select");
    target?.focus();
  }

  function tableNode(table) {
    const t = el("table", { class: "doc-table" });
    if (table.header) t.append(el("thead", {}, el("tr", {}, table.header.map((h) => el("th", { scope: "col" }, h)))));
    t.append(el("tbody", {}, table.rows.map((row) => el("tr", {}, row.map((c) => el("td", {}, c))))));
    return el("div", { class: "pv-table" }, t);
  }

  function editBlock(block, node) {
    const fresh = data.document.blocks.find((b) => b.id === block.id) || block;
    const id = `edit-${block.id}`;
    const area = el("textarea", { id, rows: String(Math.min(14, Math.max(3, Math.ceil(fresh.text.length / 80)))) });
    area.value = fresh.text;
    const save = button("Lagre", { variant: "btn-primary btn-sm", onClick: () => guarded(node, async () => {
      await api.put(`/api/projects/${projectId}/blocks/${block.id}`, { text: area.value });
      await load();
      replaceBlock(fresh, `edit-${block.id}`);
      renderAudio();
      announce("Teksten er lagret.");
    }) });
    const cancel = button("Avbryt", { variant: "btn-sm", onClick: () => replaceBlock(fresh, `edit-${block.id}`) });
    const restore = fresh.original_text != null ? button("Gjenopprett original", { variant: "btn-sm", iconName: "undo", onClick: () => guarded(node, async () => {
      await api.post(`/api/projects/${projectId}/blocks/${block.id}/restore`);
      await load();
      replaceBlock(fresh, `edit-${block.id}`);
      renderAudio();
      announce("Originalteksten er gjenopprettet.");
    }) }) : null;
    node.replaceChildren(el("div", { class: "field" }, el("label", { for: id }, `Rediger ${(KIND_LABELS[block.kind] || "tekst").toLowerCase()}`), area),
      el("div", { class: "row" }, save, cancel, restore));
    area.focus();
  }

  renderAudio();
  await renderVoice();
  renderFilters();
  renderPreview();

  return () => {
    disposed = true;
    abortPolling.abort();
    for (const t of timers) clearInterval(t);
    sampleAudio.pause();
  };
}
