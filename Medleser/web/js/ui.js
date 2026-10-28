// UI helpers: element builder, icons, announcements, dialogs, formatting.
// Document text is always inserted with textContent / text nodes, never as HTML.

const ICONS = {
  upload: '<path d="M12 16V4M6 10l6-6 6 6M4 20h16"/>',
  library: '<path d="M4 4h5v16H4zM10 4h5v16h-5zM16.5 5l3.5 1-3 14-3.5-1z"/>',
  voice: '<path d="M12 3a3 3 0 0 1 3 3v6a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 11a7 7 0 0 0 14 0M12 18v3"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  play: '<path d="M7 4.5v15l12.5-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<path d="M7 4h3.5v16H7zM13.5 4H17v16h-3.5z" fill="currentColor" stroke="none"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none"/>',
  back: '<path d="M4 12a8 8 0 1 0 2.3-5.7M4 4v4h4"/><text x="12" y="15.5" font-size="7.5" text-anchor="middle" fill="currentColor" stroke="none" font-family="Segoe UI, Arial">10</text>',
  forward: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4"/><text x="12" y="15.5" font-size="7.5" text-anchor="middle" fill="currentColor" stroke="none" font-family="Segoe UI, Arial">30</text>',
  doc: '<path d="M6 2h8l5 5v15H6zM14 2v5h5M9 13h7M9 17h7M9 9h2"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.5v.5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  close: '<path d="M6 6l12 12M18 6L6 18"/>',
  download: '<path d="M12 4v12M6 10l6 6 6-6M4 20h16"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
  folder: '<path d="M3 6h7l2 2h9v11H3z"/>',
  headphones: '<path d="M4 15v-3a8 8 0 0 1 16 0v3M4 15h3v5H4zM17 15h3v5h-3z"/>',
  book: '<path d="M4 5c3-1.3 6-1.3 8 .5v14c-2-1.8-5-1.8-8-.5zM20 5c-3-1.3-6-1.3-8 .5v14c2-1.8 5-1.8 8-.5z"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  undo: '<path d="M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3"/>',
  sparkle: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  focus: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  keyboard: '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
};

export function icon(name, { size } = {}) {
  const span = document.createElement("span");
  span.setAttribute("aria-hidden", "true");
  span.className = "ico";
  const s = size ? ` width="${size}" height="${size}"` : "";
  span.innerHTML = `<svg viewBox="0 0 24 24"${s} fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" focusable="false">${ICONS[name] || ""}</svg>`;
  return span;
}

// el("button", {class: "btn", onclick: fn, "aria-label": "…"}, "Tekst", child)
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function button(label, { variant = "", iconName, onClick, attrs = {} } = {}) {
  const b = el("button", { type: "button", class: `btn ${variant}`.trim(), onclick: onClick, ...attrs });
  if (iconName) b.append(icon(iconName));
  if (label) b.append(el("span", {}, label));
  return b;
}

let announceTimer = null;
export function announce(message) {
  const region = document.getElementById("status-region");
  clearTimeout(announceTimer);
  region.textContent = "";
  announceTimer = setTimeout(() => { region.textContent = message; }, 80);
}

export function announceError(message) {
  const region = document.getElementById("alert-region");
  region.textContent = "";
  setTimeout(() => { region.textContent = message; }, 80);
}

export function toast(message, { timeout = 4000 } = {}) {
  const box = document.getElementById("toasts");
  const t = el("div", { class: "toast" }, message);
  box.append(t);
  announce(message);
  setTimeout(() => t.remove(), timeout);
}

// Friendly error box with optional technical details («Vis detaljer»).
export function errorNotice(err, { title } = {}) {
  const message = err?.message || "Noe gikk galt.";
  const box = el("div", { class: "notice notice-error", role: "alert" }, icon("alert"));
  const body = el("div", {});
  if (title) body.append(el("p", { class: "nowrap" }, el("strong", {}, title)));
  body.append(el("p", {}, message));
  if (err?.details) {
    const det = el("details", { class: "tech" }, el("summary", {}, "Vis detaljer"), el("pre", {}, String(err.details)));
    body.append(det);
  }
  box.append(body);
  return box;
}

export function notice(kind, ...children) {
  const iconName = kind === "warning" || kind === "error" ? "alert" : kind === "success" ? "check" : "info";
  return el("div", { class: `notice notice-${kind}` }, icon(iconName), el("div", {}, ...children));
}

// Modal dialog built on <dialog>. Returns {dialog, body, actions, close, open}.
export function createDialog(titleText, { describedBy } = {}) {
  const id = `dlg-${Math.random().toString(36).slice(2)}`;
  const dialog = el("dialog", { "aria-labelledby": `${id}-title`, "aria-describedby": describedBy });
  const body = el("div", { class: "dialog-body" });
  const title = el("h2", { id: `${id}-title` }, titleText);
  body.append(title);
  const actions = el("div", { class: "dialog-actions" });
  dialog.append(body, actions);
  document.body.append(dialog);
  const opener = document.activeElement;
  const api = {
    dialog, body, actions, title,
    open() { dialog.showModal(); const f = dialog.querySelector("[autofocus]") || dialog.querySelector("input, select, textarea, button"); f?.focus(); },
    close(value) { dialog.close(value ?? ""); },
  };
  dialog.addEventListener("close", () => {
    setTimeout(() => {
      dialog.remove();
      if (opener && document.contains(opener)) opener.focus();
    }, 0);
  });
  return api;
}

export function confirmDialog({ title, message, confirmLabel = "OK", cancelLabel = "Avbryt", danger = false }) {
  return new Promise((resolve) => {
    const d = createDialog(title);
    for (const line of [].concat(message)) d.body.append(el("p", {}, line));
    const cancel = button(cancelLabel, { onClick: () => d.close("cancel") });
    const ok = button(confirmLabel, { variant: danger ? "btn-danger-solid" : "btn-primary", onClick: () => d.close("ok") });
    d.actions.append(cancel, ok);
    d.dialog.addEventListener("close", () => resolve(d.dialog.returnValue === "ok"));
    d.open();
    cancel.focus();
  });
}

export function progressBlock(labelText) {
  const id = `p-${Math.random().toString(36).slice(2)}`;
  const label = el("span", { id: `${id}-l` }, labelText || "");
  const pct = el("span", { class: "nowrap" }, "");
  const bar = el("progress", { max: "100", value: "0", "aria-labelledby": `${id}-l` });
  const wrap = el("div", { class: "progress-wrap" }, el("div", { class: "progress-text" }, label, pct), bar);
  let lastAnnounced = -1;
  return {
    node: wrap,
    set(fraction, message) {
      const value = Math.round((fraction || 0) * 100);
      bar.value = value;
      pct.textContent = `${value} %`;
      if (message) label.textContent = message;
      // Announce sparingly so screen readers are not flooded.
      const bucket = Math.floor(value / 25);
      if (bucket !== lastAnnounced && value > 0) {
        lastAnnounced = bucket;
        announce(`${message || labelText || "Fremdrift"}: ${value} prosent`);
      }
    },
  };
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const s = Math.floor(seconds % 60);
  const m = Math.floor((seconds / 60) % 60);
  const h = Math.floor(seconds / 3600);
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function spokenDuration(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  const parts = [];
  if (h) parts.push(`${h} ${h === 1 ? "time" : "timer"}`);
  if (m) parts.push(`${m} ${m === 1 ? "minutt" : "minutter"}`);
  if (s || !parts.length) parts.push(`${s} ${s === 1 ? "sekund" : "sekunder"}`);
  return parts.join(" ");
}

export function formatBytes(bytes) {
  if (!bytes) return "0 kB";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} kB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2).replace(".", ",")} GB`;
}

// "1 side" / "3 sider"
export function plural(n, one, many) {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

// Re-render a region without losing keyboard focus: the focused control is found again by
// id, data-fk or (label + description) after rendering.
function focusKey(node) {
  if (!node || node === document.body) return null;
  if (node.id) return `#${node.id}`;
  if (node.dataset?.fk) return `fk:${node.dataset.fk}`;
  return `t:${node.tagName}|${node.getAttribute("aria-describedby") || ""}|${node.textContent.trim()}`;
}

export async function keepFocus(region, render) {
  const active = document.activeElement;
  const key = region.contains(active) ? focusKey(active) : null;
  await render();
  if (!key || region.contains(document.activeElement)) return;
  const candidates = region.querySelectorAll("button, a, input, select, textarea, [tabindex]");
  for (const node of candidates) {
    if (focusKey(node) === key) { node.focus(); return; }
  }
}

// Run an async UI action and show a friendly error in `host` instead of failing silently.
export async function guarded(host, action) {
  try {
    return await action();
  } catch (err) {
    host?.append(errorNotice(err));
    announceError(err?.message || "Noe gikk galt.");
    return undefined;
  }
}

export function formatNumber(n) {
  return new Intl.NumberFormat("nb-NO").format(n || 0);
}

export function formatDate(epochSeconds) {
  if (!epochSeconds) return "";
  return new Intl.DateTimeFormat("nb-NO", { dateStyle: "medium", timeStyle: "short" }).format(new Date(epochSeconds * 1000));
}

export const LANGUAGES = [
  ["nb", "Norsk bokmål"],
  ["nn", "Norsk nynorsk"],
  ["en", "Engelsk"],
  ["sv", "Svensk"],
  ["da", "Dansk"],
  ["de", "Tysk"],
  ["fr", "Fransk"],
  ["es", "Spansk"],
];

export function languageName(code) {
  const hit = LANGUAGES.find(([c]) => c === code);
  return hit ? hit[1] : (code === "und" || !code ? "Ukjent" : code);
}

export function setPageTitle(text) {
  document.title = text ? `${text} – Medleser` : "Medleser";
}

// Fill the static nav icons.
export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((node) => {
    if (!node.firstChild) node.append(icon(node.dataset.icon));
  });
}
