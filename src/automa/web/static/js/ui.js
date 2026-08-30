// Small DOM helpers, shared by every screen.
//
// `el()` sets text through `textContent`, never `innerHTML`, so nothing a game module,
// a card description or a person at the table types can inject markup. That matters
// here more than in most apps: half the strings on screen come from generated code.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") throw new Error("use text; markup is never injected");
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function badge(text, tone = "neutral") {
  return el("span", { class: `badge ${tone}`, text });
}

export function notice(tone, title, body) {
  return el("div", { class: `notice ${tone}` }, [
    title ? el("strong", { text: title }) : null,
    body ? el("span", { text: body }) : null,
  ]);
}

export function empty(message) {
  return el("div", { class: "empty", text: message });
}

export function field(labelText, control, hint) {
  return el("div", { class: "field" }, [
    el("label", { text: labelText }),
    control,
    hint ? el("span", { class: "faint", text: hint }) : null,
  ]);
}

export function checkbox(labelText, checked, onChange, hint) {
  const input = el("input", { type: "checkbox", checked, onChange: (e) => onChange(e.target.checked) });
  return el("label", { class: "check" }, [
    input,
    el("span", {}, [el("span", { text: labelText }), hint ? el("div", { class: "faint", text: hint }) : null]),
  ]);
}

/** An effect operation as one readable line: `score_flat(points=3)`. */
export function renderEffect(effect) {
  const args = Object.entries(effect)
    .filter(([key]) => key !== "op")
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(", ");
  return `${effect.op}(${args})`;
}

export function relativeTime(iso) {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function money(value) {
  if (!value) return "$0.00";
  return value < 0.01 ? `$${value.toFixed(6)}` : `$${value.toFixed(4)}`;
}
