// The play screen: a chat transcript, a live state panel, and the two question kinds.
//
// Two rules this screen exists to make visible:
//
//  * Legal actions are pill buttons and never a text box. You pick from a list the
//    core already built - that is invariant 1, on screen.
//  * A question is drawn differently from a move, and carries the CLI's own framing
//    sentence verbatim. Being asked what a card does is information, not a decision
//    being judged, and the interface must not blur the two.

import { api } from "../api.js";
import { connect } from "../sse.js";
import { go } from "../router.js";
import { badge, el, empty, money, notice } from "../ui.js";
import { serverConfig } from "../main.js";

let stream = null;
let transcriptNode = null;
let stateNode = null;
let sessionId = null;

export function unmount() {
  // Closing matters: browsers cap connections per origin, so leaked streams
  // eventually stop the next one opening. Note this does NOT abandon the match -
  // an SSE reconnect is routine, and only the janitor or an explicit Resign ends one.
  stream?.close();
  stream = null;
  transcriptNode = null;
  stateNode = null;
  sessionId = null;
}

export async function mount(root, params) {
  if (params.id) return mountMatch(root, params.id);
  return mountSetup(root, params);
}

// -- setup ------------------------------------------------------------------

async function mountSetup(root, params) {
  const [games, config] = await Promise.all([api.games(), serverConfig()]);
  const playable = games.filter((g) => g.conforms);
  const hasKey = Boolean(config?.credentials?.google || config?.credentials?.anthropic);

  root.append(
    el("header", {}, [
      el("h1", { text: "Play a match" }),
      el("p", {
        text:
          "You play at the table with the real components; Automa plays the other side. " +
          "The three switches below are independent - reading the table costs nothing " +
          "either way, so you can demo it without spending on a live Tactician.",
      }),
    ])
  );

  if (!playable.length) {
    root.append(empty("No conforming game module is installed."));
    return;
  }

  const state = {
    game: params.game && playable.some((g) => g.name === params.game)
      ? params.game
      : playable[0].name,
    mode: hasKey ? "live" : "offline",
    simulate_deal: !hasKey,
    use_catalog: true,
    seed: "",
    delay: 0,
  };

  const gameSelect = el(
    "select",
    { onChange: (e) => (state.game = e.target.value) },
    playable.map((g) => el("option", { value: g.name, selected: g.name === state.game, text: g.name }))
  );

  const modeSelect = el(
    "select",
    {
      onChange: (e) => {
        state.mode = e.target.value;
        renderCost();
      },
    },
    [
      el("option", { value: "live", selected: state.mode === "live", disabled: !hasKey,
        text: "Live - the Tactician decides (uses the API)" }),
      el("option", { value: "offline", selected: state.mode === "offline",
        text: "Offline - a seeded random opponent (no API calls)" }),
    ]
  );

  const dealBox = el("input", {
    type: "checkbox",
    checked: state.simulate_deal,
    onChange: (e) => {
      state.simulate_deal = e.target.checked;
      renderCost();
    },
  });

  const catalogBox = el("input", {
    type: "checkbox",
    checked: state.use_catalog,
    onChange: (e) => {
      state.use_catalog = e.target.checked;
      renderCost();
    },
  });

  const seedInput = el("input", {
    type: "text",
    placeholder: "blank for a fresh match",
    onInput: (e) => (state.seed = e.target.value.trim()),
  });

  const delayInput = el("input", {
    type: "number",
    min: "0",
    max: "30",
    step: "0.5",
    value: "0",
    onInput: (e) => (state.delay = Number(e.target.value) || 0),
  });

  const costLine = el("p", { class: "cost" });
  function renderCost() {
    const parts = [];
    parts.push(
      state.mode === "live"
        ? "Automa's moves: one model call per turn"
        : "Automa's moves: free (seeded random)"
    );
    parts.push(
      state.simulate_deal
        ? "Deal: the module deals itself (nothing is asked)"
        : "Deal: read off the table (free - no model is involved)"
    );
    if (state.use_catalog) {
      parts.push(
        state.mode === "live"
          ? "Cards: a card nobody has described costs one call, once ever"
          : "Cards: nothing is asked and nothing is saved"
      );
    } else {
      parts.push("Cards: the catalog is not consulted");
    }
    costLine.textContent = parts.join(" · ");
  }
  renderCost();

  const form = el("div", { class: "panel stack" }, [
    el("div", { class: "field" }, [el("label", { text: "Game" }), gameSelect]),
    el("div", { class: "field" }, [
      el("label", { text: "Who plays Automa's side" }),
      modeSelect,
      hasKey ? null : el("span", { class: "faint", text: "Live play needs an API key." }),
    ]),
    el("label", { class: "check" }, [
      dealBox,
      el("span", {}, [
        el("span", { text: "Deal from the module's own deck (--simulate)" }),
        el("div", {
          class: "faint",
          text:
            "Off by default: the cards are the ones physically in front of you, and " +
            "you read them out. Turn this on to try a game without the deck.",
        }),
      ]),
    ]),
    el("label", { class: "check" }, [
      catalogBox,
      el("span", {}, [
        el("span", { text: "Learn card effects at the table" }),
        el("div", {
          class: "faint",
          text: "Answer once and it is remembered, so later matches ask fewer questions.",
        }),
      ]),
    ]),
    el("div", { class: "row" }, [
      el("div", { class: "field grow" }, [el("label", { text: "Seed" }), seedInput]),
      el("div", { class: "field grow" }, [
        el("label", { text: "Pause before each Automa turn (s)" }),
        delayInput,
      ]),
    ]),
    costLine,
  ]);

  const problem = el("div");
  const startButton = el("button", {
    class: "btn primary",
    text: "Start the match",
    onClick: async (event) => {
      event.currentTarget.disabled = true;
      problem.replaceChildren();
      try {
        const session = await api.createSession({
          game: state.game,
          mode: state.mode,
          simulate_deal: state.simulate_deal,
          use_catalog: state.use_catalog,
          seed: state.seed === "" ? null : Number(state.seed),
          delay: state.delay,
        });
        go(`#/play/${session.id}`);
      } catch (error) {
        problem.replaceChildren(notice("danger", "Could not start", error.message));
        event.currentTarget.disabled = false;
      }
    },
  });

  root.append(form, el("div", { class: "row" }, [startButton]), problem);
}

// -- the match --------------------------------------------------------------

async function mountMatch(root, id) {
  sessionId = id;
  const snapshot = await api.session(id);

  const header = el("header", {}, [
    el("div", { class: "row spread" }, [
      el("h1", { text: `${snapshot.game}` }),
      el("div", { class: "row tight" }, [
        badge(snapshot.mode === "live" ? "live tactician" : "offline opponent",
          snapshot.mode === "live" ? "ok" : "neutral"),
        el("button", {
          class: "btn danger small",
          text: "Resign",
          onClick: async (event) => {
            event.currentTarget.disabled = true;
            await api.abandon(id).catch(() => {});
          },
        }),
      ]),
    ]),
    el("p", {
      class: "faint",
      text: `You are ${snapshot.human_player}; Automa is ${snapshot.automa_player}.`,
    }),
  ]);

  transcriptNode = el("div", {
    class: "transcript",
    id: "transcript",
    role: "log",
    "aria-live": "polite",
    "aria-label": "Match transcript",
  });

  stateNode = el("pre", { class: "state-view", text: "waiting for the first state..." });

  const panel = el("aside", { class: "state-panel" }, [
    el("h3", { text: "The table, as you see it" }),
    stateNode,
    el("p", {
      class: "faint",
      text: "Rendered for your own view only. Automa's hidden information is never shown here.",
    }),
  ]);

  root.append(header, el("div", { class: "play-layout" }, [transcriptNode, panel]));

  // The SSE history replay rebuilds the whole transcript on a reconnect, so nothing
  // is re-rendered from the snapshot here - but a finished session that was evicted
  // from the stream still shows its outcome.
  if (snapshot.status !== "running" && snapshot.result) {
    say("system", "This match is over", snapshot.result.outcome);
  }

  stream = connect(`/api/sessions/${encodeURIComponent(id)}/events`, {
    match_start: onMatchStart,
    state: onState,
    thinking: onThinking,
    automa_moved: onAutomaMoved,
    your_turn: onYourTurn,
    question: onQuestion,
    learned: onLearned,
    unanswered: () => {},
    invariant_violation: (data) =>
      say("problem", "Invariant violation", data.problem ?? ""),
    match_end: onMatchEnd,
    abandoned: (data) => say("system", "Match abandoned", `reason: ${data.reason}`),
    error: onError,
  });
}

function scroll() {
  if (transcriptNode) transcriptNode.scrollTop = transcriptNode.scrollHeight;
}

function say(kind, who, text) {
  if (!transcriptNode) return null;
  const bubble = el("div", { class: `bubble ${kind}` }, [
    who ? el("div", { class: "who", text: who }) : null,
    text ? el("div", { text }) : null,
  ]);
  transcriptNode.append(bubble);
  scroll();
  return bubble;
}

function dropTransient() {
  transcriptNode?.querySelectorAll("[data-transient]").forEach((node) => node.remove());
}

function onMatchStart(data) {
  const bits = [
    `seed ${data.seed ?? "none"}`,
    data.simulate_deal ? "the module deals itself" : "cards read off the table",
    data.mode === "live" ? `model ${data.model}` : "seeded random opponent",
  ];
  if (data.catalog) {
    bits.push(
      data.catalog.known + data.catalog.parked === 0
        ? "catalog empty - every card will be asked about once"
        : `catalog: ${data.catalog.known} known, ${data.catalog.parked} parked`
    );
  }
  say("system", "Match started", bits.join(" · "));
}

function onState(data) {
  if (stateNode) stateNode.textContent = data.view ?? "";
}

function onThinking(data) {
  dropTransient();
  const bubble = say("automa", "Automa", "");
  if (!bubble) return;
  bubble.setAttribute("data-transient", "");
  bubble.lastChild.className = "thinking";
  bubble.lastChild.textContent = "thinking";
}

function onAutomaMoved(data) {
  dropTransient();
  const bubble = say("automa", `Automa · turn ${data.turn}`, data.action);
  if (data.fallback && bubble) {
    // Shown separately from an intervention, never merged: a fallback means the
    // model was unreachable, not that it got the rules wrong.
    bubble.append(
      el("div", {
        class: "faint",
        text: "the model could not be reached - this move was picked at random",
      })
    );
  }
}

function onYourTurn(data) {
  dropTransient();
  if (stateNode) stateNode.textContent = data.view ?? "";
  const bubble = say("human", `Your turn · turn ${data.turn}`, "");
  if (!bubble) return;
  bubble.setAttribute("data-transient", "");

  const actions = el("div", { class: "actions" });
  data.options.forEach((label, index) => {
    actions.append(
      el("button", {
        class: "btn small",
        text: `${index}. ${label}`,
        onClick: async (event) => {
          for (const button of actions.querySelectorAll("button")) button.disabled = true;
          try {
            await api.move(sessionId, index);
            bubble.removeAttribute("data-transient");
            actions.replaceWith(el("div", { text: label }));
          } catch (error) {
            for (const button of actions.querySelectorAll("button")) button.disabled = false;
            say("problem", "That move was refused", error.message);
          }
        },
      })
    );
  });
  bubble.append(actions);
  scroll();
  actions.querySelector("button")?.focus();
}

function onQuestion(data) {
  dropTransient();
  if (!transcriptNode) return;

  const bubble = el("div", { class: "bubble question" });
  bubble.append(
    el("div", { class: "who", text: data.kind === "components" ? "What is on the table" : "About a card" }),
    // The CLI's own sentence, verbatim. Load-bearing product copy.
    el("div", { class: "framing", text: data.framing ?? "" })
  );
  if (data.note) {
    // `table_informant` re-asks up to three times on a count mismatch. Each call is a
    // fresh Question carrying `note`, and this is a correction on the same question,
    // not a new one - so it must be shown or the person cannot know why.
    bubble.append(notice("warn", null, `${data.note}.`));
  }
  bubble.append(el("div", { class: "prompt", text: data.prompt ?? "" }));

  const input = el("textarea", {
    "aria-label": "Your answer",
    placeholder:
      data.kind === "components"
        ? "Separate them with spaces or commas"
        : "Describe it in your own words",
  });

  if (data.kind === "components") {
    const counter = el("span", { class: "token-count" });
    const update = () => {
      // Mirrors `split_components`' comma-wins rule as a *preview* only; the server's
      // own split stays authoritative.
      const text = input.value;
      const parts = (text.includes(",") ? text.split(",") : text.trim().split(/\s+/))
        .map((s) => s.trim())
        .filter(Boolean);
      const wanted = data.count;
      counter.textContent =
        wanted === null || wanted === undefined
          ? `${parts.length} entered`
          : `${parts.length} of ${wanted}`;
      counter.className = `token-count ${wanted && parts.length === wanted ? "ok" : "off"}`;
    };
    input.addEventListener("input", update);
    update();
    bubble.append(input, el("div", { class: "row tight" }, [counter]));

    if (data.options?.length) {
      const chips = el("div", { class: "row tight" });
      chips.append(el("span", { class: "faint", text: "names this game knows:" }));
      for (const name of data.options.slice(0, 24)) {
        chips.append(
          el("button", {
            class: "chip",
            type: "button",
            text: name,
            // Appends rather than filters. An unfamiliar name is a card nobody has
            // described yet, not an error - never filter against these.
            onClick: () => {
              input.value = input.value ? `${input.value.trim()}, ${name}` : name;
              input.dispatchEvent(new Event("input"));
              input.focus();
            },
          })
        );
      }
      if (data.options.length > 24) {
        chips.append(el("span", { class: "faint", text: `... (${data.options.length} in all)` }));
      }
      bubble.append(chips);
    }
  } else {
    bubble.append(input);
  }

  const buttons = el("div", { class: "row tight" });
  const send = async (text) => {
    for (const button of buttons.querySelectorAll("button")) button.disabled = true;
    input.disabled = true;
    try {
      await api.answer(sessionId, text);
      bubble.querySelector(".row:last-of-type")?.remove();
    } catch (error) {
      for (const button of buttons.querySelectorAll("button")) button.disabled = false;
      input.disabled = false;
      say("problem", "That answer was refused", error.message);
    }
  };
  buttons.append(
    el("button", { class: "btn primary small", text: "Answer", onClick: () => send(input.value) }),
    el("button", {
      class: "btn small",
      text: "I can't say",
      onClick: () => send(""),
    })
  );
  bubble.append(buttons);

  transcriptNode.append(bubble);
  scroll();
  input.focus();
}

function onLearned(data) {
  say(
    "system",
    null,
    data.stored
      ? `recorded: ${data.card_type}`
      : `parked: ${data.card_type} (kept as a known gap, never asked again)`
  );
}

function onMatchEnd(data) {
  dropTransient();
  const lines = [
    data.summary,
    `turns ${data.turns} · plies ${data.plies} · facts supplied ${data.informed}`,
  ];
  if (data.first_intervention_turn !== null && data.first_intervention_turn !== undefined) {
    lines.push(`first corrective intervention: turn ${data.first_intervention_turn}`);
  }
  if (data.learned?.length) lines.push(`asked about ${data.learned.length} new card(s)`);
  const unanswered = Object.entries(data.unanswered ?? {});
  if (unanswered.length) {
    lines.push(
      `unanswered: ${unanswered.map(([k, n]) => `${k} x${n}`).join(", ")} - ` +
        "the game played on without those facts"
    );
  }
  lines.push(`tactician calls ${data.llm_calls} · cost ${money(data.cost_usd)}`);

  const bubble = say("system", "Match over", lines.join("\n"));
  bubble?.lastChild.style.setProperty("white-space", "pre-wrap");
  if (data.invariant_violations?.length) {
    say("problem", "Invariant violations", data.invariant_violations.join("\n"));
  }
  if (data.trajectory) {
    say(
      "system",
      "Trajectory",
      `${data.trajectory}\n\nRead it with:\n  python -m automa.trajectory.show "${data.trajectory}"`
    ).lastChild.style.setProperty("white-space", "pre-wrap");
  }
  transcriptNode?.append(
    el("div", { class: "row tight" }, [
      el("button", { class: "btn small", text: "Play another", onClick: () => go("#/play") }),
    ])
  );
  scroll();
}

function onError(data) {
  say("problem", data.kind ?? "Error", data.message ?? "");
}
