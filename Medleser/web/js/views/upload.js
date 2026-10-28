import { api, waitForJob } from "../api.js";
import { navigate, state } from "../main.js";
import { announce, button, el, errorNotice, icon, notice, progressBlock, setPageTitle } from "../ui.js";

export async function renderUpload(main) {
  setPageTitle("Nytt dokument");
  const exts = (state.app?.supported_extensions || [".pdf", ".docx", ".epub", ".txt"]).join(",");
  const maxMb = state.app?.max_upload_mb || 300;

  main.append(
    el("header", { class: "page-header" },
      el("h1", {}, "Nytt dokument"),
      el("p", {}, "Last opp en PDF, et Word-dokument (.docx), en e-bok (EPUB) eller en tekstfil. "
        + "Teksten hentes ut her på datamaskinen din. Etterpå kan du velge stemme, høre teksten og følge med på skjermen."),
    ),
  );

  const voiceHint = el("div");
  main.append(voiceHint);
  checkVoices(voiceHint);

  const input = el("input", { type: "file", accept: exts, class: "visually-hidden", id: "file-input", tabindex: "-1", "aria-hidden": "true" });
  const chooseBtn = button("Velg fil fra datamaskinen", { variant: "btn-primary btn-lg", iconName: "upload", onClick: () => input.click() });
  const zone = el("section", { class: "dropzone", "aria-labelledby": "dz-title" },
    el("div", { class: "big-icon" }, icon("doc")),
    el("h2", { id: "dz-title" }, "Dra og slipp filen her"),
    el("p", { class: "muted" }, "eller"),
    chooseBtn,
    el("p", { class: "muted small" }, `PDF, Word (.docx), EPUB og tekst (.txt). Maks ${maxMb} MB.`),
    input,
  );
  const ocr = el("input", { type: "checkbox", id: "force-ocr" });
  const ocrField = el("label", { class: "check", for: "force-ocr" }, ocr,
    el("span", {}, "Dokumentet er skannet (bruk tekstgjenkjenning, OCR)",
      el("span", { class: "hint" }, "Velg dette hvis sidene er bilder av tekst. Medleser prøver også automatisk når en PDF mangler tekst. Gjenkjent tekst kan inneholde feil.")));
  const status = el("div", { "aria-live": "off" });

  main.append(zone, el("div", { class: "card" }, ocrField), status,
    el("p", { class: "muted small", id: "rights-note" },
      "Du er selv ansvarlig for at du har lov til å behandle dokumentet slik, for eksempel pensum du har tilgang til gjennom studiet. "
      + "Medleser er laget for din egen lesing. Programmet deler ingenting med andre og omgår ikke kopibeskyttelse."));

  let busy = false;
  const abort = new AbortController(); // stops polling (and redirects) when the user leaves the page
  async function handle(file) {
    if (!file || busy) return;
    busy = true;
    chooseBtn.disabled = true;
    status.replaceChildren();
    const progress = progressBlock(`Laster opp «${file.name}» …`);
    const cancelBtn = button("Avbryt", { variant: "btn-sm" });
    cancelBtn.disabled = true;
    const card = el("div", { class: "card", role: "region", "aria-label": "Fremdrift" }, progress.node, el("div", { class: "row" }, cancelBtn));
    status.append(card);
    announce(`Laster opp ${file.name}`);
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("force_ocr", ocr.checked ? "true" : "false");
      const res = await api.upload("/api/projects", form);
      cancelBtn.disabled = false;
      cancelBtn.onclick = () => api.post(`/api/jobs/${res.job.id}/cancel`).catch(() => {});
      const job = await waitForJob(res.job.id, (j) => progress.set(j.progress, j.message || "Leser dokumentet …"),
        { signal: abort.signal });
      if (!job) return; // the user has left this page
      if (job.state === "done") {
        announce("Dokumentet er lest inn.");
        navigate(`#/dokument/${res.project_id}`);
        return;
      }
      if (job.state === "cancelled") {
        await api.del(`/api/projects/${res.project_id}`).catch(() => {});
        status.replaceChildren(notice("info", el("p", {}, "Innlesingen ble avbrutt.")));
      } else {
        status.replaceChildren(failure(job.error, res.project_id));
      }
    } catch (err) {
      status.replaceChildren(errorNotice(err));
    } finally {
      busy = false;
      chooseBtn.disabled = false;
      input.value = "";
    }
  }

  function failure(error, projectId) {
    const box = el("div", { class: "stack" }, errorNotice(error || {}, { title: "Dokumentet kunne ikke leses" }));
    const actions = el("div", { class: "row" });
    if (!ocr.checked && error?.code !== "drm" && error?.code !== "encrypted") {
      actions.append(button("Prøv med tekstgjenkjenning (OCR)", { variant: "btn-primary", onClick: async () => {
        status.replaceChildren(el("p", {}, "Prøver tekstgjenkjenning …"));
        try {
          const res = await api.post(`/api/projects/${projectId}/reimport`, { force_ocr: true });
          const progress = progressBlock("Tekstgjenkjenning …");
          status.replaceChildren(el("div", { class: "card" }, progress.node));
          const job = await waitForJob(res.job.id, (j) => progress.set(j.progress, j.message), { signal: abort.signal });
          if (!job) return;
          if (job.state === "done") navigate(`#/dokument/${projectId}`);
          else status.replaceChildren(errorNotice(job.error || {}, { title: "Tekstgjenkjenningen lyktes ikke" }));
        } catch (err) { status.replaceChildren(errorNotice(err)); }
      } }));
    }
    actions.append(button("Fjern dokumentet", { onClick: async () => {
      await api.del(`/api/projects/${projectId}`).catch(() => {});
      status.replaceChildren(notice("info", el("p", {}, "Dokumentet er fjernet.")));
    } }));
    box.append(actions);
    return box;
  }

  input.addEventListener("change", () => handle(input.files?.[0]));
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragover"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file) handle(file);
  });
  return () => abort.abort();
}

async function checkVoices(container) {
  try {
    const providers = await api.get("/api/providers");
    const ready = providers.filter((p) => !p.capabilities.is_paid && p.status.state === "ready");
    let hasNorwegian = false;
    for (const p of ready) {
      const res = await api.get(`/api/providers/${p.capabilities.id}/voices?language=nb`);
      if (res.voices.length) { hasNorwegian = true; break; }
    }
    if (!hasNorwegian) {
      container.append(notice("info",
        el("p", {}, el("strong", {}, "Tips: "), "Last ned en gratis norsk stemme før du starter, så kan du høre dokumentet uten internett."),
        el("p", {}, el("a", { href: "#/stemmer" }, "Gå til Stemmer og KI-tjenester"))));
    }
  } catch { /* not critical */ }
}
