// Hash routing.
//
// Hashes rather than history paths so `StaticFiles` never needs a server-side rewrite
// rule: every URL is really `/`, and the fragment is the screen.
//
// Each screen exports `mount(root, params)` and optionally `unmount()`. `unmount()` is
// always called before the next mount - that is where a screen closes its EventSource.

const routes = [];
let current = null;

export function route(pattern, screen) {
  const names = [];
  const source = pattern.replace(/:(\w+)/g, (_, name) => {
    names.push(name);
    return "([^/]+)";
  });
  routes.push({ regex: new RegExp(`^${source}$`), names, screen });
}

function parse() {
  const raw = window.location.hash.replace(/^#/, "") || "/games";
  // A hash may carry its own query string (`#/play?game=nim`). It is not part of the
  // path being matched, but it is part of what the screen is handed.
  const [hash, search = ""] = raw.split("?");
  for (const entry of routes) {
    const match = entry.regex.exec(hash);
    if (!match) continue;
    const params = Object.fromEntries(new URLSearchParams(search));
    entry.names.forEach((name, i) => {
      params[name] = decodeURIComponent(match[i + 1]);
    });
    return { entry, params, hash };
  }
  return null;
}

function highlightNav(hash) {
  for (const link of document.querySelectorAll("#nav a")) {
    const target = link.getAttribute("href").replace(/^#/, "");
    const active = hash === target || hash.startsWith(`${target}/`);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

async function render() {
  const root = document.getElementById("root");
  const found = parse();

  if (current?.unmount) {
    try {
      current.unmount();
    } catch (error) {
      console.error("unmount failed", error);
    }
  }
  current = null;
  root.replaceChildren();

  if (!found) {
    window.location.hash = "#/games";
    return;
  }

  highlightNav(found.hash);
  current = found.entry.screen;
  try {
    await found.entry.screen.mount(root, found.params);
  } catch (error) {
    console.error(error);
    root.replaceChildren();
    const box = document.createElement("div");
    box.className = "notice danger";
    box.textContent = `Could not open this screen: ${error.message}`;
    root.append(box);
  }
}

export function start() {
  window.addEventListener("hashchange", render);
  window.addEventListener("beforeunload", () => current?.unmount?.());
  render();
}

export function go(hash) {
  window.location.hash = hash;
}
