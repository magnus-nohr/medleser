// Medleser – app bootstrap and hash router.
import { api } from "./api.js";
import { applyReading, applyUiTheme } from "./prefs.js";
import { el, errorNotice, hydrateIcons, setPageTitle } from "./ui.js";
import { renderUpload } from "./views/upload.js";
import { renderLibrary } from "./views/library.js";
import { renderVoices } from "./views/voices.js";
import { renderSettings } from "./views/settings.js";
import { renderWorkspace } from "./views/workspace.js";
import { renderReader } from "./views/reader.js";

export const state = {
  app: null,
  settings: null, // { reading, app, defaults }
};

const ROUTES = [
  [/^#\/ny$/, "ny", () => renderUpload],
  [/^#\/dokumenter$/, "dokumenter", () => renderLibrary],
  [/^#\/stemmer$/, "stemmer", () => renderVoices],
  [/^#\/innstillinger$/, "innstillinger", () => renderSettings],
  [/^#\/dokument\/([0-9a-f]{32})$/, "dokumenter", () => renderWorkspace],
  [/^#\/les\/([0-9a-f]{32})$/, "dokumenter", () => renderReader],
];

let cleanup = null;
let routeSeq = 0;

export async function refreshSettings() {
  state.settings = await api.get("/api/settings");
  applyReading(state.settings.reading);
  applyUiTheme(state.settings.app.ui_theme || "system");
  return state.settings;
}

export function navigate(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function route() {
  const hash = location.hash || "#/ny";
  if (!hash.startsWith("#/")) return; // in-page anchors (e.g. the skip link) are not routes
  let match = null;
  for (const [re, nav, view] of ROUTES) {
    const m = re.exec(hash);
    if (m) { match = { nav, view: view(), params: m.slice(1) }; break; }
  }
  if (!match) { location.replace("#/ny"); return; }

  const seq = ++routeSeq;
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  document.body.classList.remove("focus-mode");
  document.documentElement.classList.remove("reader-active");
  document.querySelectorAll(".nav-list a").forEach((a) => {
    if (a.dataset.route === match.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });

  // Each view renders into its own container; a view that finishes after the user has
  // navigated on is discarded (and cleaned up) instead of being mixed into the new page.
  const main = document.getElementById("main");
  const container = document.createElement("div");
  main.replaceChildren(container);
  let result;
  try {
    result = await match.view(container, ...match.params);
  } catch (err) {
    if (seq !== routeSeq) return;
    console.error(err);
    container.replaceChildren(el("h1", { tabindex: "-1" }, "Noe gikk galt"), errorNotice(err));
    setPageTitle("Feil");
  }
  if (seq !== routeSeq) {
    if (typeof result === "function") { try { result(); } catch { /* ignore */ } }
    container.remove();
    return;
  }
  if (typeof result === "function") cleanup = result;
  // Move focus to the page heading so keyboard and screen-reader users land in the new view.
  const heading = main.querySelector("h1");
  if (heading) {
    if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
    heading.focus({ preventScroll: false });
  } else {
    main.focus();
  }
}

async function start() {
  hydrateIcons();
  try {
    state.app = await api.get("/api/app");
    await refreshSettings();
  } catch (err) {
    document.getElementById("main").replaceChildren(el("h1", {}, "Medleser"), errorNotice(err));
    return;
  }
  document.getElementById("app-version").textContent = `Versjon ${state.app.version}`;
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    applyUiTheme(state.settings?.app?.ui_theme || "system");
  });
  window.addEventListener("hashchange", route);
  // Prevent the window from navigating away when a file is dropped outside a drop zone.
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());
  // Skip link: move focus without touching the hash (which drives the router).
  document.querySelector(".skip-link")?.addEventListener("click", (e) => {
    e.preventDefault();
    const target = document.querySelector("#main h1") || document.getElementById("main");
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus();
  });
  if (!location.hash.startsWith("#/")) {
    history.replaceState(null, "", state.app.active_jobs?.length ? "#/dokumenter" : "#/ny");
  }
  route();
}

start();
