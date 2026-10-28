import { api, waitForJob } from "../api.js";
import {
  announce, button, confirmDialog, createDialog, el, errorNotice, formatNumber, guarded, keepFocus, languageName, notice,
  progressBlock, setPageTitle, toast,
} from "../ui.js";

const STATUS = {
  ready: ["Klar", "badge-ok"],
  not_connected: ["Ikke tilkoblet", ""],
  needs_setup: ["Må settes opp", "badge-paid"],
  error: ["Feil", "badge-error"],
  unavailable: ["Ikke tilgjengelig", "badge-error"],
};

export async function renderVoices(main) {
  setPageTitle("Stemmer og KI-tjenester");
  main.append(el("header", { class: "page-header" },
    el("h1", {}, "Stemmer og KI-tjenester"),
    el("p", {}, "Velg mellom gratis stemmer som lager lyden her på datamaskinen, og betalte KI-stemmer fra eksterne tjenester. "
      + "Medleser virker fullt ut med de gratis stemmene – en betalt tjeneste er bare et frivillig tillegg.")));
  const host = el("div");
  main.append(host,
    el("p", { class: "muted small" }, "Planlagt i senere versjoner: Google Cloud Text-to-Speech og Microsoft Azure AI Speech."));
  const audio = el("audio", { controls: true, preload: "none", hidden: true, "aria-label": "Stemmeprøve" });
  main.append(audio);
  let disposed = false;

  function play(url) {
    audio.hidden = false;
    audio.src = url;
    audio.play().catch(() => {});
    announce("Spiller stemmeprøve.");
  }

  async function load() {
    let providers;
    try { providers = await api.get("/api/providers"); } catch (err) { host.replaceChildren(errorNotice(err)); return; }
    host.replaceChildren();
    for (const p of providers) host.append(await providerCard(p));
  }

  function badges(p) {
    const caps = p.capabilities;
    let [label, cls] = STATUS[p.status.state] || [p.status.state, ""];
    if (caps.auth_method === "api_key") {
      if (!p.connected) [label, cls] = STATUS.not_connected;
      else if (p.status.state === "ready") label = "Tilkoblet";
    }
    return el("div", { class: "provider-badges" },
      el("span", { class: `badge ${caps.is_paid ? "badge-paid" : "badge-free"}` }, caps.is_paid ? "Betalt" : "Gratis"),
      el("span", { class: "badge" }, caps.is_local ? "Lokalt på PC-en" : "Sender tekst over internett"),
      el("span", { class: `badge ${cls}` }, `Status: ${label}`));
  }

  async function providerCard(p) {
    const caps = p.capabilities;
    const card = el("section", { class: "card", "aria-labelledby": `pc-${caps.id}` },
      el("div", { class: "provider-head" }, el("h2", { id: `pc-${caps.id}` }, caps.name), badges(p)),
      el("p", {}, caps.description),
      el("p", { class: "small muted" }, el("strong", {}, "Medlesing: "), caps.timestamps_note));
    if (p.status.message && !(caps.auth_method === "api_key" && !p.connected)) card.append(el("p", { class: "small" }, p.status.message));
    if (caps.id === "piper") card.append(await piperBody());
    else if (caps.id === "windows") card.append(await windowsBody());
    else if (caps.auth_method === "api_key") card.append(await cloudBody(p));
    return card;
  }

  // ----- Piper ---------------------------------------------------------------------------
  async function piperBody() {
    const box = el("div", { class: "stack" });
    let catalog = [];
    try { catalog = await api.get("/api/piper/catalog"); } catch (err) { box.append(errorNotice(err)); return box; }
    const groups = new Map();
    for (const v of catalog) {
      if (!groups.has(v.language)) groups.set(v.language, []);
      groups.get(v.language).push(v);
    }
    for (const [lang, voices] of groups) {
      const list = el("ul", { class: "voice-list", "aria-label": `Stemmer på ${languageName(lang).toLowerCase()}` });
      for (const v of voices) list.append(piperItem(v));
      box.append(el("h3", {}, languageName(lang)), list);
    }
    box.append(el("p", { class: "small muted" }, "Stemmene lastes ned fra Piper-prosjektets offisielle samling, og innholdet kontrolleres før bruk. "
      + "Medleser har ikke testet kvaliteten systematisk – hør en prøve og velg den du liker best."));
    return box;
  }

  function piperItem(v) {
    const li = el("li", { class: "voice-item" });
    const nameId = `pv-${v.key}`;
    const mainCol = el("div", { class: "vi-main" },
      el("div", { class: "vi-name", id: nameId }, v.name, v.recommended ? el("span", { class: "badge badge-info" }, " Anbefalt") : null),
      el("div", { class: "small" }, v.description),
      el("div", { class: "small muted" }, `Lisens: ${v.license} · ${v.size_mb} MB${v.speakers > 1 ? ` · ${v.speakers} stemmer` : ""}`));
    const actions = el("div", { class: "row" });
    li.append(mainCol, actions);
    const render = () => {
      actions.replaceChildren();
      if (v.installed) {
        actions.append(el("span", { class: "badge badge-ok" }, v.word_timing ? "Installert · ord-for-ord" : "Installert"),
          button("Hør prøve", { variant: "btn-sm", attrs: { "aria-describedby": nameId }, onClick: () => sample("piper", v.speakers > 1 ? null : v.key, v.language, v.key) }),
          button("Slett", { variant: "btn-sm btn-danger", attrs: { "aria-describedby": nameId }, onClick: async () => {
            const ok = await confirmDialog({ title: "Slette stemmen?", message: [`«${v.name}» slettes fra datamaskinen (${v.size_mb} MB). Du kan laste den ned igjen senere.`], confirmLabel: "Slett", danger: true });
            if (!ok) return;
            await guarded(li, async () => {
              await api.del(`/api/piper/voices/${v.key}`);
              v.installed = false;
              render();
              actions.querySelector("button")?.focus();
              announce("Stemmen er slettet.");
            });
          } }));
      } else {
        const dl = button(`Last ned (${v.size_mb} MB)`, { variant: "btn-sm btn-primary", attrs: { "aria-describedby": nameId } });
        dl.addEventListener("click", () => install(v, actions, render));
        actions.append(dl);
        if (v.job) install(v, actions, render, v.job);
      }
    };
    render();
    return li;
  }

  async function install(v, actions, render, existingJob) {
    const progress = progressBlock(`Laster ned ${v.name} …`);
    const cancel = button("Avbryt", { variant: "btn-sm" });
    actions.replaceChildren(el("div", { class: "grow" }, progress.node), cancel);
    try {
      const job = existingJob || (await api.post(`/api/piper/voices/${v.key}/install`)).job;
      cancel.onclick = () => api.post(`/api/jobs/${job.id}/cancel`).catch(() => {});
      const done = await waitForJob(job.id, (j) => progress.set(j.progress, j.message));
      if (disposed) return;
      if (done.state === "done") {
        v.installed = true;
        v.word_timing = true;
        toast(`${v.name} er klar til bruk.`);
      } else if (done.state === "failed") {
        actions.parentElement.append(errorNotice(done.error || {}));
      }
    } catch (err) {
      actions.parentElement.append(errorNotice(err));
    }
    v.job = null;
    render();
  }

  async function sample(provider, voiceId, language, key) {
    try {
      let id = voiceId;
      if (!id) {
        const res = await api.get(`/api/providers/${provider}/voices?language=${language}`);
        id = res.voices.find((x) => x.id.startsWith(`${key}#`))?.id || res.voices[0]?.id;
      }
      const r = await api.post("/api/samples", { provider, voice_id: id, language });
      play(r.url);
    } catch (err) { toast(err.message); }
  }

  // ----- Windows -------------------------------------------------------------------------
  async function windowsBody() {
    const box = el("div", { class: "stack" });
    let res = { voices: [] };
    try { res = await api.get("/api/providers/windows/voices"); } catch { /* status shows the problem */ }
    if (res.voices.length) {
      const list = el("ul", { class: "voice-list", "aria-label": "Windows-stemmer" });
      for (const v of res.voices) {
        const nameId = `wv-${v.id.replace(/\W/g, "")}`;
        list.append(el("li", { class: "voice-item" },
          el("div", { class: "vi-main" }, el("div", { class: "vi-name", id: nameId }, v.name), el("div", { class: "small muted" }, languageName(v.language))),
          button("Hør prøve", { variant: "btn-sm", attrs: { "aria-describedby": nameId }, onClick: async () => {
            try { const r = await api.post("/api/samples", { provider: "windows", voice_id: v.id, language: v.language }); play(r.url); }
            catch (err) { toast(err.message); }
          } })));
      }
      box.append(list);
    }
    box.append(el("div", { class: "row" }, button("Legg til flere stemmer i Windows", { variant: "btn-sm", onClick: () => api.post("/api/system/open-link", { target: "windows_speech_settings" }) })),
      el("p", { class: "small muted" }, "I Windows-innstillingene: Tid og språk › Tale › Legg til stemmer. Start Medleser på nytt etterpå."));
    return box;
  }

  // ----- Cloud (ElevenLabs) ------------------------------------------------------------------
  async function cloudBody(p) {
    const caps = p.capabilities;
    const box = el("div", { class: "stack" });
    box.append(el("p", { class: "small" }, caps.pricing_note));
    if (!p.connected) {
      box.append(el("div", { class: "row" },
        button(`Koble til ${caps.name}`, { variant: "btn-primary", onClick: () => connectWizard(caps) })));
      return box;
    }
    const statusBox = el("div");
    const account = p.status.account || {};
    if (p.status.state === "error") statusBox.append(errorNotice({ message: p.status.message, details: p.status.details }));
    if (account.tier || account.remaining_credits != null) {
      statusBox.append(el("p", { class: "small" },
        account.tier ? `Abonnement: ${account.tier}. ` : "",
        account.remaining_credits != null ? `Igjen denne perioden: ${formatNumber(account.remaining_credits)} kreditter (ifølge ${caps.name}).` : ""));
    }
    box.append(statusBox, el("div", { class: "row" },
      button("Test tilkobling", { variant: "btn-sm", onClick: async (e) => {
        const b = e.currentTarget;
        b.disabled = true;
        try {
          const res = await api.post(`/api/providers/${caps.id}/check`);
          toast(res.status.state === "ready" ? `Tilkoblingen virker. ${res.status.message}` : res.status.message);
          b.disabled = false;
          await keepFocus(host, load);
        } catch (err) { statusBox.append(errorNotice(err)); } finally { b.disabled = false; }
      } }),
      button("Åpne kontoen min", { variant: "btn-sm", onClick: () => api.post("/api/system/open-link", { target: "elevenlabs_usage" }) }),
      button("Koble fra", { variant: "btn-sm btn-danger", onClick: async () => {
        const ok = await confirmDialog({ title: `Koble fra ${caps.name}?`, message: ["Tilgangsnøkkelen slettes fra Windows Legitimasjonsbehandling på denne datamaskinen. Lyd du allerede har laget, beholdes."], confirmLabel: "Koble fra", danger: true });
        if (!ok) return;
        await guarded(statusBox, async () => {
          await api.del(`/api/providers/${caps.id}/key`);
          toast(`${caps.name} er koblet fra.`);
          await load();
          host.querySelector(`#pc-${CSS.escape(caps.id)}`)?.closest("section")?.querySelector("button")?.focus();
        });
      } })));
    if (p.status.state === "ready") {
      try {
        const res = await api.get(`/api/providers/${caps.id}/voices`);
        const list = el("ul", { class: "voice-list", "aria-label": `Stemmer hos ${caps.name}` });
        for (const v of res.voices.slice(0, 40)) {
          const nameId = `cv-${v.id}`;
          list.append(el("li", { class: "voice-item" },
            el("div", { class: "vi-main" }, el("div", { class: "vi-name", id: nameId }, v.name), el("div", { class: "small muted" }, v.description || "")),
            v.preview_available ? button("Hør gratis prøve", { variant: "btn-sm", attrs: { "aria-describedby": nameId },
              onClick: () => play(`/api/providers/${caps.id}/preview/${encodeURIComponent(v.id)}`) }) : null));
        }
        box.append(el("details", {}, el("summary", {}, `Vis stemmer (${res.voices.length})`), list,
          el("p", { class: "small muted" }, "Prøvene er ferdiginnspilte eksempler fra tjenesten og bruker ikke kreditter.")));
      } catch { /* ignore */ }
    }
    return box;
  }

  function connectWizard(caps) {
    const d = createDialog(`Koble til ${caps.name}`);
    d.dialog.style.width = "min(40rem, calc(100vw - 2rem))";
    const steps = el("ol", { class: "steps", "aria-hidden": "true" }, [0, 1, 2, 3].map(() => el("li")));
    const content = el("div"); // each step's heading (or field) receives focus, so no live region
    d.body.append(steps, content);
    let step = 0;
    const back = button("Tilbake", { onClick: () => { step = Math.max(0, step - 1); render(); } });
    const next = button("Neste", { variant: "btn-primary", onClick: () => { step += 1; render(); } });
    const close = button("Avbryt", { onClick: () => d.close() });
    d.actions.append(close, back, next);

    const keyInput = el("input", { type: "password", id: "api-key", autocomplete: "off", spellcheck: "false", "aria-describedby": "key-hint" });
    const show = el("input", { type: "checkbox", id: "show-key" });
    show.addEventListener("change", () => { keyInput.type = show.checked ? "text" : "password"; });
    const result = el("div");

    function render() {
      steps.querySelectorAll("li").forEach((li, i) => { li.className = i < step ? "done" : i === step ? "current" : ""; });
      back.hidden = step === 0 || step === 3;
      next.hidden = step >= 2;
      close.lastChild.textContent = step === 3 ? "Lukk" : "Avbryt";
      content.replaceChildren();
      if (step === 0) {
        content.append(el("h3", {}, "Steg 1 av 3: Om tjenesten"),
          el("p", {}, `${caps.name} lager svært naturlige KI-stemmer. For å bruke den trenger du en egen konto hos ${caps.name}. `
            + "Kontoen kan være gratis med en liten månedlig kvote, eller betalt."),
          el("ul", {},
            el("li", {}, "Medleser tar ikke betalt og ser aldri betalingsinformasjonen din."),
            el("li", {}, `Når du lager lyd med ${caps.name}, sendes teksten som skal leses over internett til ${caps.name}. Du godkjenner hver gang før noe sendes.`),
            el("li", {}, "Medleser ber aldri om passordet ditt.")),
          el("div", { class: "row" },
            button(`Åpne ${caps.name} i nettleseren`, { iconName: "link", onClick: () => api.post("/api/system/open-link", { target: "elevenlabs_signup" }) })),
          el("p", { class: "small muted" }, "Har du allerede en konto? Trykk «Neste»."));
      } else if (step === 1) {
        content.append(el("h3", {}, "Steg 2 av 3: Lag en tilgangsnøkkel"),
          el("p", {}, `En tilgangsnøkkel (på engelsk «API key») er en lang kode som gir Medleser lov til å lage lyd på kontoen din. Slik lager du den:`),
          el("ol", { class: "howto" },
            el("li", {}, `Logg inn på ${caps.name} i nettleseren.`),
            el("li", {}, "Åpne siden for tilgangsnøkler med knappen under («API Keys» under innstillingene)."),
            el("li", {}, "Trykk «Create API Key» (lag nøkkel) og gi den navnet «Medleser»."),
            el("li", {}, "Gi nøkkelen tilgang til «Text to Speech» og «Voices» (lese). Vil du se hvor mange kreditter du har igjen, gi også lesetilgang til «Models» og «User»."),
            el("li", {}, "Anbefalt: Sett en kredittgrense (credit quota) på nøkkelen. Da kan den aldri bruke mer enn du har bestemt, uansett hva som skjer."),
            el("li", {}, "Kopier nøkkelen. Den vises bare én gang.")),
          el("div", { class: "row" }, button("Åpne siden for tilgangsnøkler", { iconName: "link", onClick: () => api.post("/api/system/open-link", { target: "elevenlabs_api_keys" }) })));
      } else if (step === 2) {
        const saveBtn = button("Lagre og test", { variant: "btn-primary", onClick: save });
        content.append(el("h3", {}, "Steg 3 av 3: Lim inn nøkkelen"),
          el("div", { class: "field" }, el("label", { for: "api-key" }, "Tilgangsnøkkel"), keyInput,
            el("span", { class: "hint", id: "key-hint" }, "Lim inn med Ctrl+V. Nøkkelen lagres kryptert i Windows Legitimasjonsbehandling – ikke i Medleser sine filer.")),
          el("label", { class: "check", for: "show-key" }, show, el("span", {}, "Vis nøkkelen")),
          el("div", { class: "row" }, saveBtn), result);
        keyInput.focus();
        async function save() {
          const key = keyInput.value.trim();
          result.replaceChildren();
          if (key.length < 8) { result.append(errorNotice({ message: "Lim inn hele nøkkelen først." })); keyInput.focus(); return; }
          saveBtn.disabled = true;
          saveBtn.lastChild.textContent = "Tester …";
          try {
            const res = await api.post(`/api/providers/${caps.id}/key`, { api_key: key });
            keyInput.value = "";
            if (res.status.state === "ready") {
              step = 3;
              render();
              content.append(notice("success", el("p", {}, el("strong", {}, "Tilkoblet! "), res.status.message),
                el("p", {}, "Velg tjenesten under «Stemmetjeneste» når du lager lyd for et dokument. Du godkjenner alltid forbruket før noe sendes.")));
              announce(`${caps.name} er tilkoblet.`);
            } else {
              result.append(errorNotice({ message: res.status.message, details: res.status.details }));
            }
          } catch (err) {
            result.append(errorNotice(err));
          } finally {
            saveBtn.disabled = false;
            saveBtn.lastChild.textContent = "Lagre og test";
          }
        }
      } else {
        content.append(el("h3", {}, "Ferdig"));
      }
      const heading = content.querySelector("h3");
      heading?.setAttribute("tabindex", "-1");
      // On the paste step, go straight to the field (its label is read aloud); otherwise to the step heading.
      if (step === 2) keyInput.focus();
      else heading?.focus();
    }
    d.dialog.addEventListener("close", () => { keyInput.value = ""; load().catch(() => {}); });
    render();
    d.open();
    content.querySelector("h3")?.focus();
  }

  await load();
  return () => { disposed = true; audio.pause(); };
}
