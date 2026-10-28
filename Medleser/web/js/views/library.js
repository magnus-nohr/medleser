import { api } from "../api.js";
import { navigate } from "../main.js";
import {
  announce, button, confirmDialog, el, errorNotice, formatBytes, formatDate, formatDuration, icon,
  keepFocus, languageName, plural, setPageTitle,
} from "../ui.js";

const TYPE_NAMES = { pdf: "PDF", docx: "Word", epub: "EPUB", txt: "Tekst" };

function audioLabel(p) {
  if (p.active_jobs?.some((j) => j.kind === "generate")) return ["Lager lyd …", "badge-info"];
  if (p.active_jobs?.some((j) => j.kind === "import")) return ["Leses inn …", "badge-info"];
  if (p.status === "error") return ["Kunne ikke leses", "badge-error"];
  switch (p.audio_state) {
    case "ready": return [`Lyd klar · ${formatDuration(p.audio_duration)}`, "badge-ok"];
    case "partial": return ["Lyd delvis laget", "badge-paid"];
    case "generating": return ["Lyd ikke ferdig", "badge-paid"];
    default: return ["Ingen lyd ennå", ""];
  }
}

export async function renderLibrary(main) {
  setPageTitle("Mine dokumenter");
  main.append(el("header", { class: "page-header" },
    el("h1", {}, "Mine dokumenter"),
    el("p", {}, "Dokumentene og lydfilene ligger lagret her på datamaskinen. Du kan åpne dem igjen uten å lage lyden på nytt.")));
  const listHost = el("div");
  main.append(listHost);

  async function load() {
    let projects;
    try {
      projects = await api.get("/api/projects");
    } catch (err) {
      listHost.replaceChildren(errorNotice(err));
      return;
    }
    if (!projects.length) {
      listHost.replaceChildren(el("div", { class: "card stack" },
        el("h2", {}, "Ingen dokumenter ennå"),
        el("p", {}, "Når du laster opp et dokument, dukker det opp her."),
        button("Last opp et dokument", { variant: "btn-primary", iconName: "upload", onClick: () => navigate("#/ny") })));
      return;
    }
    const list = el("ul", { class: "doc-list", "aria-label": `${plural(projects.length, "dokument", "dokumenter")}, sist åpnet først` });
    for (const p of projects) list.append(item(p));
    listHost.replaceChildren(list);
  }

  function item(p) {
    const [label, cls] = audioLabel(p);
    const headingId = `doc-${p.id}`;
    const meta = el("div", { class: "doc-meta" },
      el("span", {}, TYPE_NAMES[p.source_type] || (p.source_type || "").toUpperCase()),
      p.page_count ? el("span", {}, plural(p.page_count, "side", "sider")) : null,
      el("span", {}, formatBytes(p.size_bytes)),
      p.language ? el("span", {}, languageName(p.language)) : null,
      el("span", {}, `Åpnet ${formatDate(p.opened_at || p.updated_at)}`),
      el("span", { class: `badge ${cls}` }, label));
    const actions = el("div", { class: "doc-actions" },
      button("Åpne", { variant: "btn-primary btn-sm", onClick: () => navigate(`#/dokument/${p.id}`),
        attrs: { "aria-describedby": headingId } }));
    if (p.audio_state === "ready") {
      actions.append(button("Medlesing", { variant: "btn-sm", iconName: "headphones", onClick: () => navigate(`#/les/${p.id}`),
        attrs: { "aria-describedby": headingId } }));
    }
    actions.append(button("Slett", { variant: "btn-danger btn-sm", iconName: "trash", attrs: { "aria-describedby": headingId },
      onClick: async () => {
        const ok = await confirmDialog({
          title: "Slette dokumentet?",
          message: [`«${p.title}» slettes fra Medleser, sammen med kopien av originalfilen, uttrukket tekst, rettelser og lydfiler som er laget.`,
            "MP3-filer du har lagret andre steder, blir ikke slettet. Dette kan ikke angres."],
          confirmLabel: "Slett dokumentet",
          danger: true,
        });
        if (!ok) return;
        try {
          await api.del(`/api/projects/${p.id}`);
          announce(`«${p.title}» er slettet.`);
          await load();
          main.querySelector("h1")?.focus();
        } catch (err) {
          listHost.prepend(errorNotice(err));
        }
      } }));
    return el("li", { class: "card doc-item" },
      el("span", { class: "doc-icon" }, icon("doc")),
      el("div", {}, el("h2", { id: headingId }, p.title), meta),
      actions);
  }

  await load();
  // While something is being imported or generated, refresh the list without losing keyboard focus.
  const timer = setInterval(() => {
    if (listHost.querySelector(".badge-info")) keepFocus(listHost, load);
  }, 3000);
  return () => clearInterval(timer);
}
