// Reading-view settings form (used in the reader dialog and on the settings page).
import { api } from "./api.js";
import { state } from "./main.js";
import { FONTS, HIGHLIGHTS, THEMES, applyReading, contrastRatio } from "./prefs.js";
import { announce, el } from "./ui.js";

let saveTimer = null;

export function saveReading(changes) {
  const merged = { ...state.settings.reading, ...changes };
  state.settings.reading = merged;
  applyReading(merged);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    api.put("/api/settings/reading", merged).catch(() => {});
  }, 400);
}

function range(id, label, { min, max, step, value, unit = "", format, onInput, hint }) {
  const out = el("output", { for: id, class: "nowrap" }, format ? format(value) : `${value}${unit}`);
  const input = el("input", { type: "range", id, min, max, step, value: String(value) });
  input.addEventListener("input", () => {
    const v = Number(input.value);
    out.textContent = format ? format(v) : `${v}${unit}`;
    input.setAttribute("aria-valuetext", out.textContent);
    onInput(v);
  });
  input.setAttribute("aria-valuetext", out.textContent);
  return el("div", { class: "field" },
    el("div", { class: "row spread" }, el("label", { for: id }, label), out),
    input,
    hint ? el("span", { class: "hint" }, hint) : null);
}

function select(id, label, options, value, onChange, hint) {
  const s = el("select", { id }, options.map(([v, l]) => el("option", { value: v, selected: v === value }, l)));
  s.addEventListener("change", () => onChange(s.value));
  return el("div", { class: "field" }, el("label", { for: id }, label), s, hint ? el("span", { class: "hint" }, hint) : null);
}

function checkbox(id, label, checked, onChange, hint) {
  const input = el("input", { type: "checkbox", id, checked });
  input.addEventListener("change", () => onChange(input.checked));
  return el("label", { class: "check", for: id }, input, el("span", {}, label, hint ? el("span", { class: "hint" }, hint) : null));
}

const comma = (n, digits = 2) => String(Number(n).toFixed(digits)).replace(".", ",").replace(/,?0+$/, "") || "0";

export function readingForm({ prefix = "rf", compact = false } = {}) {
  const r = state.settings.reading;
  const wrap = el("div", { class: compact ? "stack" : "settings-grid" });
  const colorsBox = el("div");

  const fontField = select(`${prefix}-font`, "Skrifttype", FONTS.map(([k, name]) => [k, name]), r.font_family,
    (v) => { saveReading({ font_family: v }); fontHint.textContent = FONTS.find((f) => f[0] === v)?.[3] || ""; });
  const fontHint = el("span", { class: "hint" }, FONTS.find((f) => f[0] === r.font_family)?.[3] || "");
  fontField.append(fontHint);

  wrap.append(
    fontField,
    range(`${prefix}-size`, "Skriftstørrelse", { min: 14, max: 48, step: 1, value: r.font_size, unit: " px", onInput: (v) => saveReading({ font_size: v }) }),
    range(`${prefix}-line`, "Linjeavstand", { min: 1.2, max: 2.8, step: 0.1, value: r.line_height, format: (v) => comma(v, 1), onInput: (v) => saveReading({ line_height: v }) }),
    range(`${prefix}-width`, "Tekstbredde", { min: 35, max: 110, step: 1, value: r.text_width, format: (v) => `${v} tegn`, onInput: (v) => saveReading({ text_width: v }), hint: "Kortere linjer er ofte lettere å følge." }),
    range(`${prefix}-letter`, "Bokstavavstand", { min: 0, max: 0.2, step: 0.01, value: r.letter_spacing, format: (v) => `${comma(v)} em`, onInput: (v) => saveReading({ letter_spacing: v }) }),
    range(`${prefix}-word`, "Ordavstand", { min: 0, max: 0.6, step: 0.02, value: r.word_spacing, format: (v) => `${comma(v)} em`, onInput: (v) => saveReading({ word_spacing: v }) }),
    select(`${prefix}-theme`, "Farger på lesesiden", THEMES, r.theme, (v) => { saveReading({ theme: v }); renderColors(); }),
    select(`${prefix}-hl`, "Markeringsfarge", Object.entries(HIGHLIGHTS).map(([k, [name]]) => [k, name]), r.highlight_color,
      (v) => saveReading({ highlight_color: v }), "Fargen bak ordet som leses."),
    colorsBox,
    el("div", {},
      checkbox(`${prefix}-para`, "Marker avsnittet som leses", r.paragraph_shading, (v) => saveReading({ paragraph_shading: v })),
      checkbox(`${prefix}-scroll`, "Følg opplesingen automatisk (rull teksten)", r.auto_scroll, (v) => saveReading({ auto_scroll: v })),
      checkbox(`${prefix}-motion`, "Reduser bevegelse", r.reduce_motion, (v) => saveReading({ reduce_motion: v }), "Ingen myk rulling eller animasjoner."),
      checkbox(`${prefix}-keys`, "Hurtigtaster i medlesingen", r.shortcuts, (v) => saveReading({ shortcuts: v }),
        "Mellomrom: spill/pause, J: 10 sekunder tilbake, L: 30 sekunder frem. Slå av hvis de forstyrrer skjermleseren din.")),
  );

  function renderColors() {
    colorsBox.replaceChildren();
    const cur = state.settings.reading;
    if (cur.theme !== "custom") return;
    const c = { background: "#FBFAF7", text: "#1B1B1B", highlight: "#FFE066", ...(cur.custom_colors || {}) };
    const warn = el("p", { class: "small" });
    let lastOk = null;
    const check = () => {
      const ratio = contrastRatio(c.text, c.background);
      const ok = ratio >= 4.5;
      warn.textContent = ok
        ? `Kontrast: ${comma(ratio, 1)}:1 – god.`
        : `Kontrasten mellom tekst og bakgrunn er lav (${comma(ratio, 1)}:1). Anbefalt er minst 4,5:1.`;
      // Announce only when the verdict changes, not on every colour-picker movement.
      if (lastOk !== null && ok !== lastOk) announce(ok ? "Kontrasten er god." : "Kontrasten er for lav.");
      lastOk = ok;
    };
    const picker = (key, label) => {
      const id = `${prefix}-c-${key}`;
      const input = el("input", { type: "color", id, value: c[key] });
      input.addEventListener("input", () => {
        c[key] = input.value;
        saveReading({ custom_colors: { ...c } });
        check();
      });
      return el("label", { for: id }, label, input);
    };
    colorsBox.append(el("fieldset", {}, el("legend", {}, "Egne farger"),
      el("div", { class: "color-row" }, picker("background", "Bakgrunn"), picker("text", "Tekst"), picker("highlight", "Markering")), warn));
    check();
  }
  renderColors();
  return wrap;
}
