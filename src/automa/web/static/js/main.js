// Wiring: the theme toggle, the routes, and one shared copy of `/api/config`.

import { api } from "./api.js";
import { route, start } from "./router.js";
import * as gamesScreen from "./screens/games.js";
import * as catalogScreen from "./screens/catalog.js";
import * as compileScreen from "./screens/compile.js";
import * as playScreen from "./screens/play.js";

const THEME_KEY = "automa-theme";

function applyTheme(choice) {
  if (choice === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", choice);
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === choice));
  }
  try {
    if (choice === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, choice);
  } catch {
    // Private mode. The choice still applies for this page load.
  }
}

function setUpTheme() {
  let saved = "system";
  try {
    saved = localStorage.getItem(THEME_KEY) ?? "system";
  } catch {
    saved = "system";
  }
  applyTheme(saved);
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
  }
}

/** `/api/config`, fetched once and shared. Drives the honest empty state: with no
 *  key, live play and compiling are disabled up front rather than failing minutes in. */
let configPromise = null;
export function serverConfig() {
  if (!configPromise) configPromise = api.config().catch(() => null);
  return configPromise;
}

route("/games", gamesScreen);
route("/games/:game/catalog", catalogScreen);
route("/play", playScreen);
route("/play/:id", playScreen);
route("/compile", compileScreen);
route("/compile/:id", compileScreen);

setUpTheme();
start();
