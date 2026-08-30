// The catalog editor.
//
// The operation form is *generated* from `/api/vocabulary`, using each operation's own
// `required` and `optional` fields. Twelve hardcoded forms would drift the moment
// `vocabulary.py` gains a thirteenth operation - and that file is explicitly where a
// person adds one.
//
// The editing verbs map onto the store's own asymmetric semantics: `learn` validates
// and refuses atomically, `park` always succeeds and needs a reason, `forget` is the
// only thing that can dislodge a wrong permanent answer.

import { api, ApiError } from "../api.js";
import { badge, el, empty, money, notice, relativeTime, renderEffect } from "../ui.js";
import { serverConfig } from "../main.js";

let currentGame = null;
let vocabulary = null;
let termsDeclared = false;
let termsHint = "";

export function unmount() {
  currentGame = null;
}

export async function mount(root, params) {
  currentGame = params.game;
  const [catalog, vocab, config] = await Promise.all([
    api.catalog(currentGame),
    api.vocabulary(),
    serverConfig(),
  ]);
  vocabulary = vocab;
  termsDeclared = catalog.terms_declared;
  termsHint = catalog.terms_hint;
  render(root, catalog, config);
}

async function refresh(root) {
  const [catalog, config] = await Promise.all([api.catalog(currentGame), serverConfig()]);
  termsDeclared = catalog.terms_declared;
  termsHint = catalog.terms_hint;
  root.replaceChildren();
  render(root, catalog, config);
}

function render(root, catalog, config) {
  root.append(
    el("header", {}, [
      el("div", { class: "row spread" }, [
        el("h1", { text: `${catalog.game} · card catalog` }),
        el("a", { href: "#/games", text: "back to games" }),
      ]),
      el("p", {
        text:
          "What each card does, as data - never prose interpreted at runtime. Effects " +
          "are drawn from a closed vocabulary; a description that will not fit is " +
          "parked for a person rather than approximated.",
      }),
    ])
  );

  // No CLI surface for this at all: a genuine web-only win rather than a port.
  if (catalog.audit.length) {
    root.append(
      notice(
        "danger",
        `${catalog.audit.length} stored entry/entries no longer validate`,
        catalog.audit.join(" · ")
      )
    );
  }

  if (!termsDeclared) {
    root.append(
      notice(
        "warn",
        "This game declares no TERMS, so names below are unchecked",
        "count_field, flag, resource and category hold a name the catalog and the " +
          "module must agree on. A mismatch never raises - the module looks for a name " +
          "nobody wrote and its rule silently never fires. " + termsHint
      )
    );
  }

  const search = el("input", {
    type: "text",
    placeholder: "filter by card type...",
    "aria-label": "Filter cards",
    onInput: (e) => {
      const needle = e.target.value.trim().toLowerCase();
      for (const row of root.querySelectorAll("[data-card-type]")) {
        row.classList.toggle(
          "hidden",
          Boolean(needle) && !row.dataset.cardType.toLowerCase().includes(needle)
        );
      }
    },
  });

  const drawerHost = el("div");

  root.append(
    el("div", { class: "row" }, [
      el("div", { class: "grow" }, [search]),
      el("button", {
        class: "btn primary small",
        text: "Add a card",
        onClick: () => openEditor(drawerHost, root, { card_type: "", effects: [] }, config),
      }),
    ]),
    drawerHost
  );

  root.append(
    section(
      "Known",
      `${catalog.known.length} card(s) with a usable effect`,
      catalog.known,
      drawerHost,
      root,
      config
    ),
    section(
      "Parked",
      `${catalog.parked.length} described but not expressible - never asked about again`,
      catalog.parked,
      drawerHost,
      root,
      config
    )
  );
}

function section(title, subtitle, entries, drawerHost, root, config) {
  const box = el("section", { class: "stack" }, [
    el("div", { class: "row spread" }, [
      el("h2", { text: title }),
      el("span", { class: "faint", text: subtitle }),
    ]),
  ]);
  if (!entries.length) {
    box.append(
      empty(
        title === "Known"
          ? "Nothing learned yet. A catalog is earned one card at a time, at the table."
          : "Nothing parked."
      )
    );
    return box;
  }
  for (const entry of entries) box.append(cardRow(entry, drawerHost, root, config));
  return box;
}

function cardRow(entry, drawerHost, root, config) {
  const details = el("div", { class: "grow stack tight" }, [
    el("div", { class: "row tight" }, [
      el("strong", { text: entry.card_type }),
      entry.unresolved ? badge("parked", "warn") : badge("playable", "ok"),
      el("span", { class: "faint", text: relativeTime(entry.learned_at) }),
    ]),
  ]);

  if (entry.effects.length) {
    details.append(
      el(
        "div",
        { class: "stack tight" },
        entry.effects.map((effect) => el("div", { class: "effect-line", text: renderEffect(effect) }))
      )
    );
  }
  if (entry.description) details.append(el("div", { class: "faint", text: entry.description }));
  if (entry.unresolved) {
    details.append(el("div", { class: "faint", text: `unresolved: ${entry.unresolved}` }));
  }

  const buttons = el("div", { class: "row tight" }, [
    el("button", {
      class: "btn small",
      text: entry.unresolved ? "Resolve" : "Edit",
      onClick: () => openEditor(drawerHost, root, entry, config),
    }),
    el("button", {
      class: "btn danger small",
      text: "Forget",
      onClick: async () => {
        // The only way to dislodge a wrong permanent answer - a parked card is never
        // asked about again, so a wrong park is permanent too. Hence the confirm.
        const ok = window.confirm(
          `Forget everything recorded for "${entry.card_type}"?\n\n` +
            "It will be asked about afresh the next time it appears."
        );
        if (!ok) return;
        try {
          await api.forgetCard(currentGame, entry.card_type);
          await refresh(root);
        } catch (error) {
          alert(error.message);
        }
      },
    }),
  ]);

  return el("div", { class: "card-row", "data-card-type": entry.card_type }, [details, buttons]);
}

// -- the editor drawer -------------------------------------------------------

function openEditor(host, root, entry, config) {
  host.replaceChildren();
  const draft = {
    card_type: entry.card_type ?? "",
    effects: entry.effects?.length ? structuredClone(entry.effects) : [],
    description: entry.description ?? "",
  };

  const problems = el("div");
  const opList = el("div", { class: "stack tight" });

  function redrawOps() {
    opList.replaceChildren();
    if (!draft.effects.length) {
      opList.append(el("p", { class: "faint", text: "No operations yet." }));
    }
    draft.effects.forEach((effect, index) => opList.append(opEditor(draft, effect, index, redrawOps)));
  }
  redrawOps();

  const typeInput = el("input", {
    type: "text",
    value: draft.card_type,
    placeholder: "card type, exactly as the module names it",
    onInput: (e) => (draft.card_type = e.target.value.trim()),
  });

  const descInput = el("textarea", {
    placeholder: "what the card does, in plain words",
    onInput: (e) => (draft.description = e.target.value),
  });
  descInput.value = draft.description;

  const addSelect = el(
    "select",
    { "aria-label": "Add an operation" },
    [el("option", { value: "", text: "add an operation..." })].concat(
      Object.entries(vocabulary.operations).map(([name, op]) =>
        el("option", { value: name, text: `${name} - ${op.summary}` })
      )
    )
  );

  const hasKey = Boolean(config?.credentials?.google || config?.credentials?.anthropic);

  const drawer = el("div", { class: "drawer stack" }, [
    el("h2", { text: entry.card_type ? `Editing ${entry.card_type}` : "New card" }),
    el("div", { class: "field" }, [el("label", { text: "Card type" }), typeInput]),
    el("div", { class: "field" }, [el("label", { text: "Description" }), descInput]),

    el("div", { class: "row" }, [
      el("div", { class: "grow" }, [addSelect]),
      el("button", {
        class: "btn small",
        text: "Add",
        onClick: () => {
          const name = addSelect.value;
          if (!name) return;
          draft.effects.push({ op: name });
          addSelect.value = "";
          redrawOps();
        },
      }),
    ]),
    opList,
    problems,

    el("div", { class: "row tight" }, [
      el("button", {
        class: "btn primary small",
        text: "Save",
        onClick: async () => {
          problems.replaceChildren();
          try {
            await api.learnCard(currentGame, draft.card_type, draft.effects, draft.description);
            await refresh(root);
          } catch (error) {
            const list =
              error instanceof ApiError && error.problems.length
                ? error.problems
                : [error.message];
            // The store's own strings, verbatim - they are written to be read.
            problems.replaceChildren(
              el("div", { class: "notice danger stack tight" }, [
                el("strong", { text: "Refused - nothing was written" }),
                ...list.map((p) => el("div", { class: "mono", text: p })),
              ])
            );
          }
        },
      }),
      el("button", {
        class: "btn small",
        text: "Park it",
        onClick: async () => {
          const reason = window.prompt(
            "Record it as a known gap - it will never be asked about again.\n\n" +
              "Why can it not be expressed?"
          );
          if (!reason) return;
          try {
            await api.parkCard(currentGame, draft.card_type, draft.description, reason);
            await refresh(root);
          } catch (error) {
            problems.replaceChildren(notice("danger", "Could not park", error.message));
          }
        },
      }),
      el("button", {
        class: "btn small",
        disabled: !hasKey,
        text: "Describe in words (1 model call)",
        onClick: async (event) => {
          const button = event.currentTarget;
          button.disabled = true;
          problems.replaceChildren(notice("info", null, "asking the Cataloguer..."));
          try {
            const result = await api.describeCard(currentGame, draft.card_type, draft.description);
            if (result.ok) {
              await refresh(root);
              return;
            }
            problems.replaceChildren(
              notice(
                "warn",
                "It did not fit the vocabulary",
                `${result.unresolved || result.problems.join("; ")} · ` +
                  `${result.llm_calls} call(s), ${money(result.cost_usd)}`
              )
            );
          } catch (error) {
            problems.replaceChildren(notice("danger", "Could not describe", error.message));
          } finally {
            button.disabled = false;
          }
        },
      }),
      el("button", { class: "btn small", text: "Close", onClick: () => host.replaceChildren() }),
    ]),
  ]);

  host.append(drawer);
  typeInput.focus();
}

function opEditor(draft, effect, index, redraw) {
  const spec = vocabulary.operations[effect.op];
  const box = el("div", { class: "op-editor" });

  box.append(
    el("div", { class: "field" }, [
      el("label", { text: "operation" }),
      el("div", { class: "row tight" }, [
        el("strong", { class: "mono", text: effect.op }),
        el("button", {
          class: "btn small",
          text: "remove",
          onClick: () => {
            draft.effects.splice(index, 1);
            redraw();
          },
        }),
      ]),
    ])
  );

  if (!spec) {
    box.append(notice("danger", null, `${effect.op} is not in the vocabulary`));
    return box;
  }

  const named = new Set(vocabulary.named_fields);
  for (const name of spec.required.concat(spec.optional)) {
    const optional = spec.optional.includes(name);
    const isName = named.has(name);
    const input = el("input", {
      type: isName || name === "tiers" ? "text" : "number",
      value: effect[name] === undefined ? "" : JSON.stringify(effect[name]).replace(/^"|"$/g, ""),
      placeholder: name === "tiers" ? "e.g. 1,3,6" : optional ? "optional" : "required",
      onInput: (e) => {
        const raw = e.target.value.trim();
        if (raw === "") {
          delete effect[name];
          return;
        }
        if (name === "tiers") {
          effect[name] = raw.split(",").map((n) => Number(n.trim())).filter((n) => !Number.isNaN(n));
        } else if (isName) {
          effect[name] = raw;
        } else {
          const number = Number(raw);
          effect[name] = Number.isNaN(number) ? raw : number;
        }
      },
    });
    box.append(
      el("div", { class: "field" }, [
        el("label", {}, [
          el("span", { text: name }),
          optional ? el("span", { class: "faint", text: " (optional)" }) : null,
          isName && !termsDeclared
            ? el("span", { class: "faint", text: " · unchecked name" })
            : null,
        ]),
        input,
      ])
    );
  }

  return box;
}
