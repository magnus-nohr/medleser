// Applies reading and app-appearance settings to CSS custom properties.

export const FONTS = [
  ["atkinson", "Atkinson Hyperlegible", '"Atkinson Hyperlegible Next", "Segoe UI", Arial, sans-serif', "Laget for god lesbarhet, tydelige bokstavformer."],
  ["lexend", "Lexend", '"Lexend", "Segoe UI", Arial, sans-serif', "Luftig skrift utviklet for leseflyt."],
  ["opendyslexic", "OpenDyslexic", '"OpenDyslexic", "Comic Sans MS", sans-serif', "Tunge bokstavbunner. Noen med dysleksi foretrekker den."],
  ["verdana", "Verdana", "Verdana, Geneva, sans-serif", "Bred og tydelig skjermskrift."],
  ["arial", "Arial", "Arial, Helvetica, sans-serif", "Vanlig og nøytral."],
  ["segoe", "Segoe UI", '"Segoe UI", system-ui, sans-serif', "Windows sin standardskrift."],
  ["calibri", "Calibri", "Calibri, Carlito, sans-serif", "Kjent fra Word."],
  ["georgia", "Georgia", "Georgia, serif", "Skrift med seriffer."],
  ["comic", "Comic Sans MS", '"Comic Sans MS", "Comic Neue", sans-serif', "Uformell skrift som noen lesere liker."],
];

export const THEMES = [
  ["light", "Lys"],
  ["cream", "Krem"],
  ["blue", "Lyseblå"],
  ["dark", "Mørk"],
  ["contrast", "Høy kontrast"],
  ["custom", "Egne farger"],
];

export const HIGHLIGHTS = {
  yellow: ["Gul", "#FFE066"],
  green: ["Grønn", "#B8F2B0"],
  blue: ["Blå", "#BBD9FF"],
  pink: ["Rosa", "#FFC9E3"],
  orange: ["Oransje", "#FFD1A6"],
};

let current = null;

export function fontStack(key) {
  return (FONTS.find((f) => f[0] === key) || FONTS[0])[2];
}

export function applyReading(settings) {
  current = settings;
  const root = document.documentElement;
  const s = root.style;
  s.setProperty("--read-font", fontStack(settings.font_family));
  s.setProperty("--read-size", `${settings.font_size}px`);
  s.setProperty("--read-line", String(settings.line_height));
  s.setProperty("--read-letter", `${settings.letter_spacing}em`);
  s.setProperty("--read-word", `${settings.word_spacing}em`);
  s.setProperty("--read-width", `${settings.text_width}ch`);
  const theme = settings.theme || "light";
  root.dataset.readTheme = theme;
  for (const prop of ["--read-bg", "--read-text", "--read-para", "--read-muted"]) s.removeProperty(prop);
  if (theme === "custom") {
    const c = settings.custom_colors || {};
    if (c.background) s.setProperty("--read-bg", c.background);
    if (c.text) {
      s.setProperty("--read-text", c.text);
      s.setProperty("--read-muted", c.text);
    }
    s.setProperty("--read-para", mix(c.background || "#FBFAF7", c.highlight || "#FFE066", 0.25));
  }
  if (theme === "contrast") {
    s.removeProperty("--read-hl");
  } else {
    const hl = theme === "custom" && settings.custom_colors?.highlight
      ? settings.custom_colors.highlight
      : (HIGHLIGHTS[settings.highlight_color] || HIGHLIGHTS.yellow)[1];
    s.setProperty("--read-hl", hl);
    s.setProperty("--read-hl-text", "#000000");
  }
  document.body.classList.toggle("reduce-motion", !!settings.reduce_motion);
}

export function readingSettings() {
  return current;
}

export function prefersReducedMotion() {
  return !!current?.reduce_motion || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function applyUiTheme(mode) {
  const root = document.documentElement;
  const dark = mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  root.dataset.uiTheme = dark ? "dark" : "light";
}

function mix(a, b, t) {
  const pa = parse(a), pb = parse(b);
  if (!pa || !pb) return a;
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

function parse(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// WCAG relative-luminance contrast ratio between two hex colours.
export function contrastRatio(a, b) {
  const lum = (hex) => {
    const rgb = parse(hex);
    if (!rgb) return 0;
    const [r, g, bl] = rgb.map((v) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const l1 = lum(a), l2 = lum(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
