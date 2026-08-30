// The games screen: one card per installed module.

import { api, ApiError } from "../api.js";
import { go } from "../router.js";
import { badge, el, empty, notice } from "../ui.js";
import { serverConfig } from "../main.js";

export async function mount(root) {
  const [games, config] = await Promise.all([api.games(), serverConfig()]);

  root.append(
    el("header", {}, [
      el("div", { class: "row spread" }, [
        el("h1", { text: "Installed games" }),
        el("button", {
          class: "btn primary",
          onClick: () => go("#/compile"),
          text: "Add a game",
        }),
      ]),
      el("p", {
        text:
          "Each game is a Python module the core calls to enumerate legal actions. " +
          "A module is either compiled from rule material you submit, or hand-written.",
      }),
    ])
  );

  if (config && !config.credentials.google && !config.credentials.anthropic) {
    root.append(
      notice(
        "warn",
        "No API key is set",
        "Offline play works and costs nothing. Live play against the Tactician, " +
          "compiling a new game, and describing a card in words all need a key - " +
          "set GEMINI_API_KEY in .env and restart the server."
      )
    );
  }

  if (!games.length) {
    root.append(empty("No game modules are installed yet. Add one to get started."));
    return;
  }

  const grid = el("div", { class: "grid" });
  for (const game of games) grid.append(gameCard(game));
  root.append(grid);

  if (config) {
    root.append(
      el("p", { class: "faint" }, [
        `Tactician model: ${config.models.tactician} · trajectories: ${config.trajectory_dir}`,
      ])
    );
  }
}

function gameCard(game) {
  const card = el("div", { class: "card stack" });

  card.append(
    el("div", { class: "row spread" }, [
      el("h2", { text: game.name }),
      game.conforms ? badge("conforms", "ok") : badge("does not conform", "danger"),
    ])
  );

  card.append(
    el("div", { class: "faint" }, [
      game.players ? game.players.join(" vs ") : "players unknown",
    ])
  );

  // Worth showing only when they differ: `dnup.py` and `dnup_compiled.py` both call
  // themselves "dnup", so the module name is what addresses one and the declared
  // NAME is what its catalog follows.
  if (game.declared_name && game.declared_name !== game.name) {
    card.append(
      el("div", { class: "faint", text: `declares NAME "${game.declared_name}" - shares that catalog` })
    );
  }

  card.append(
    el("div", { class: "row tight" }, [
      badge(`${game.catalog.known} known`, game.catalog.known ? "neutral" : "neutral"),
      badge(`${game.catalog.parked} parked`, game.catalog.parked ? "warn" : "neutral"),
      el("span", {
        class: "faint",
        text: game.catalog.known + game.catalog.parked === 0 ? "catalog empty" : "cards learned",
      }),
    ])
  );

  if (game.problems?.length) {
    card.append(notice("danger", "This module cannot be loaded", game.problems.join("; ")));
  }

  const actions = el("div", { class: "row tight" });
  actions.append(
    el("button", {
      class: "btn primary small",
      disabled: !game.conforms,
      onClick: () => go(`#/play?game=${encodeURIComponent(game.name)}`),
      text: "Play",
    }),
    el("button", {
      class: "btn small",
      disabled: !game.conforms,
      onClick: () => go(`#/games/${encodeURIComponent(game.name)}/catalog`),
      text: "Catalog",
    })
  );

  const status = el("span", { class: "faint" });
  actions.append(
    el("button", {
      class: "btn small",
      disabled: !game.conforms,
      onClick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        status.textContent = "validating (self-play, no model)...";
        try {
          const report = await api.validateGame(game.name, 100);
          status.textContent = report.ok
            ? `100 matches, no violations (plies ${report.ply_min}-${report.ply_max})`
            : `${report.violations.length} violation(s) found`;
        } catch (error) {
          status.textContent = error.message;
        } finally {
          button.disabled = false;
        }
      },
      text: "Validate",
    }),
    el("button", {
      class: "btn small",
      onClick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        status.textContent = "reloading...";
        try {
          await api.reloadGame(game.name);
          status.textContent = "reloaded";
        } catch (error) {
          status.textContent =
            error instanceof ApiError && error.status === 409
              ? "a match is using this game right now"
              : error.message;
        } finally {
          button.disabled = false;
        }
      },
      text: "Reload",
    })
  );

  card.append(actions, status);
  return card;
}

export function unmount() {
  // Nothing to release: this screen holds no stream.
}
