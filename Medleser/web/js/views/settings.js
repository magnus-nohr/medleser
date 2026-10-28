import { api } from "../api.js";
import { refreshSettings, state } from "../main.js";
import { applyUiTheme } from "../prefs.js";
import { readingForm } from "../reading-form.js";
import {
  LANGUAGES, announce, button, confirmDialog, el, errorNotice, formatBytes, formatNumber, guarded, keepFocus,
  setPageTitle, toast,
} from "../ui.js";

export async function renderSettings(main) {
  setPageTitle("Innstillinger");
  await refreshSettings();
  const app = state.settings.app;
  main.append(el("header", { class: "page-header" },
    el("h1", {}, "Innstillinger"),
    el("p", {}, "Innstillingene lagres bare på denne datamaskinen.")));

  // Reading view
  const preview = el("div", { class: "preview-box", "aria-label": "Forhåndsvisning av lesevisningen" },
    el("p", {}, "Slik ser teksten ut når du leser. ", el("span", { class: "demo-word" }, "Ordet"),
      " som leses opp, markeres med farge mens resten av avsnittet står rolig."));
  main.append(el("section", { class: "card", "aria-labelledby": "s-read" },
    el("h2", { id: "s-read" }, "Lesevisning"), preview, el("div", { class: "stack" }), readingForm({ prefix: "st" })));

  // Appearance
  const uiSel = el("select", { id: "ui-theme" },
    [["system", "Følg Windows"], ["light", "Lyst"], ["dark", "Mørkt"]].map(([v, l]) => el("option", { value: v, selected: app.ui_theme === v }, l)));
  uiSel.addEventListener("change", async () => {
    await saveApp({ ui_theme: uiSel.value });
    applyUiTheme(uiSel.value);
  });
  main.append(el("section", { class: "card", "aria-labelledby": "s-ui" },
    el("h2", { id: "s-ui" }, "Utseende på programmet"),
    el("div", { class: "field" }, el("label", { for: "ui-theme" }, "Lyst eller mørkt programvindu"), uiSel),
    el("p", { class: "small muted" }, "Du kan også forstørre alt med Ctrl + pluss og forminske med Ctrl + minus.")));

  // Language and defaults
  const langSel = el("select", { id: "def-lang", "aria-describedby": "def-lang-hint" },
    LANGUAGES.map(([c, n]) => el("option", { value: c, selected: app.default_language === c }, n)));
  langSel.addEventListener("change", () => saveApp({ default_language: langSel.value }));
  const o = app.default_options;
  const opt = (key, label) => {
    const id = `def-${key}`;
    const input = el("input", { type: "checkbox", id, checked: !!o[key] });
    input.addEventListener("change", () => { o[key] = input.checked; saveApp({ default_options: o }); });
    return el("label", { class: "check", for: id }, input, el("span", {}, label));
  };
  const radio = (key, legend, choices) => el("fieldset", {}, el("legend", {}, legend), choices.map(([v, l]) => {
    const id = `def-${key}-${v}`;
    const input = el("input", { type: "radio", name: `def-${key}`, id, value: v, checked: o[key] === v });
    input.addEventListener("change", () => { o[key] = v; saveApp({ default_options: o }); });
    return el("label", { class: "check", for: id }, input, el("span", {}, l));
  }));
  main.append(el("section", { class: "card", "aria-labelledby": "s-lang" },
    el("h2", { id: "s-lang" }, "Språk og standardvalg for nye dokumenter"),
    el("div", { class: "field" }, el("label", { for: "def-lang" }, "Språk når Medleser ikke kan avgjøre det selv"), langSel,
      el("span", { class: "hint", id: "def-lang-hint" }, "Medleser prøver å finne språket automatisk. Du kan alltid endre det for hvert dokument.")),
    el("div", { class: "settings-grid" },
      el("fieldset", {}, el("legend", {}, "Hopp over"),
        opt("skip_urls", "Nettadresser (URL-er)"), opt("skip_doi", "DOI-lenker"), opt("skip_emails", "E-postadresser"),
        opt("skip_headers_footers", "Topptekst og bunntekst"), opt("skip_page_numbers", "Sidetall"),
        opt("skip_footnote_markers", "Fotnotetall i teksten")),
      el("div", {},
        radio("references", "Referanseliste", [["skip", "Hopp over"], ["read", "Les"]]),
        radio("footnotes", "Fotnoter", [["skip", "Hopp over"], ["read", "Les"]]),
        radio("tables", "Tabeller", [["read", "Les rad for rad"], ["skip", "Hopp over"], ["show", "Vis uten opplesning"]]),
        el("fieldset", {}, el("legend", {}, "Annet"), opt("captions", "Les bildetekster"), opt("expand_abbreviations", "Les forkortelser fullt ut")))),
  ));

  // Paid services
  const limit = el("input", { type: "number", id: "cloud-limit", min: "0", step: "1000", value: String(app.max_cloud_chars_per_document || 0), "aria-describedby": "limit-hint" });
  limit.addEventListener("change", () => saveApp({ max_cloud_chars_per_document: Math.max(0, Number(limit.value) || 0) }));
  main.append(el("section", { class: "card", "aria-labelledby": "s-paid" },
    el("h2", { id: "s-paid" }, "Betalte tjenester"),
    el("div", { class: "field" }, el("label", { for: "cloud-limit" }, "Største antall tegn som kan sendes per dokument"), limit,
      el("span", { class: "hint", id: "limit-hint" },
        `0 betyr ingen grense. Standard er ${formatNumber(state.settings.defaults.app.max_cloud_chars_per_document)} tegn. `
        + "Medleser stopper før grensen overskrides. Grensen gjelder tegnene Medleser sender – hvor mange kreditter tjenesten trekker, bestemmer tjenesten selv.")),
    el("p", { class: "small" }, "Du kan også sette en egen grense for hvert dokument når du godkjenner et overslag.")));

  // Storage
  const storageBox = el("div", {}, el("p", { class: "muted" }, "Henter lagringsinformasjon …"));
  main.append(el("section", { class: "card", "aria-labelledby": "s-store" }, el("h2", { id: "s-store" }, "Lagring"), storageBox));
  async function renderStorage() {
    try {
      const [info, appInfo] = await Promise.all([api.get("/api/storage"), api.get("/api/app")]);
      storageBox.replaceChildren(
        el("dl", {},
          el("dt", { class: "field-label" }, "Dokumenter, tekst og lyd"), el("dd", {}, `${formatBytes(info.projects_bytes)} i ${info.data_dir}`),
          el("dt", { class: "field-label" }, "Nedlastede stemmer"), el("dd", {}, formatBytes(info.voices_bytes)),
          el("dt", { class: "field-label" }, "Midlertidige filer (stemmeprøver)"), el("dd", {}, formatBytes(info.cache_bytes)),
          el("dt", { class: "field-label" }, "Standardmappe for lagrede MP3-filer"), el("dd", {}, appInfo.export_dir)),
        el("div", { class: "row" },
          button("Åpne datamappen", { variant: "btn-sm", onClick: () => guarded(storageBox, () => api.post("/api/system/open-folder", { target: "data" })) }),
          button("Åpne MP3-mappen", { variant: "btn-sm", onClick: () => guarded(storageBox, () => api.post("/api/system/open-folder", { target: "export" })) }),
          appInfo.desktop ? button("Velg en annen MP3-mappe", { variant: "btn-sm", onClick: () => guarded(storageBox, async () => {
            const r = await api.post("/api/system/choose-export-dir");
            if (r.cancelled) return;
            state.settings.app = { ...state.settings.app, export_dir: r.export_dir }; // keep later saves consistent
            toast("MP3-mappen er endret.");
            await keepFocus(storageBox, renderStorage);
          }) }) : null,
          button("Tøm midlertidige filer", { variant: "btn-sm", onClick: async () => {
            const ok = await confirmDialog({ title: "Tømme midlertidige filer?", message: ["Stemmeprøver og andre midlertidige filer slettes. Dokumentene og lyden deres beholdes."], confirmLabel: "Tøm" });
            if (!ok) return;
            await guarded(storageBox, async () => {
              await api.post("/api/storage/clear-cache");
              toast("Midlertidige filer er slettet.");
              await keepFocus(storageBox, renderStorage);
            });
          } })),
        el("p", { class: "small muted" }, "Du sletter et dokument med all tekst og lyd under «Mine dokumenter»."));
    } catch (err) { storageBox.replaceChildren(errorNotice(err)); }
  }
  renderStorage();

  // Privacy and about
  main.append(el("section", { class: "card", "aria-labelledby": "s-priv" },
    el("h2", { id: "s-priv" }, "Personvern og opphavsrett"),
    el("ul", {},
      el("li", {}, "Dokumentene behandles på denne datamaskinen. Ingenting sendes noe sted så lenge du bruker gratis lokale stemmer."),
      el("li", {}, "Velger du en betalt KI-tjeneste, sendes bare tekstbitene som skal leses opp – etter at du har godkjent det."),
      el("li", {}, "Tilgangsnøkler lagres kryptert i Windows Legitimasjonsbehandling, aldri i dokumentfiler eller logger."),
      el("li", {}, "Medleser har ingen brukerkontoer, sporing eller analyse."),
      el("li", {}, "Du er selv ansvarlig for at du har lov til å behandle dokumentene. Medleser omgår ikke kopibeskyttelse og deler ingenting med andre."))));

  main.append(el("section", { class: "card", "aria-labelledby": "s-about" },
    el("h2", { id: "s-about" }, "Om Medleser"),
    el("p", {}, `Versjon ${state.app.version}. Laget for studenter med dysleksi og andre lesevansker – og alle andre som vil lytte til fagstoff.`),
    el("p", {}, "Medleser er laget av Magnus Nohr og er fri programvare under lisensen GNU AGPL-3.0. "
      + "Alle kan bruke, endre og dele den. Den som deler en endret versjon, må også dele kildekoden. Se filen LICENSE i programmappen."),
    el("p", { class: "small muted" }, "Bygger på åpen kildekode, blant annet Piper (talesyntese), PyMuPDF (PDF), python-docx, FastAPI, pywebview og LAME (MP3). "
      + "Skriftene Atkinson Hyperlegible, Lexend og OpenDyslexic er lisensiert under SIL Open Font License. Se filen THIRD_PARTY_LICENSES.md i programmappen.")));

  async function saveApp(changes) {
    try {
      // Send only what changed; the server merges it with the stored settings.
      state.settings.app = await api.put("/api/settings/app", changes);
      announce("Lagret.");
    } catch (err) {
      toast(err.message);
    }
  }
}
