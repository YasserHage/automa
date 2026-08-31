// Add a game: upload rule material, watch the compile, and approve the result.
//
// The approval panel is given real visual weight rather than being a spinner with a
// button at the end. It is the one gate in the pipeline that is not automated, and
// the CLI's own line for it applies here: "Every gate below this point is automated;
// this one is not."
//
// There is deliberately no web equivalent of `--yes`, and no in-browser editing of
// the generated source: editing generated code in a textarea and installing it would
// bypass the static scan, the sandbox and self-play validation in one click.

import { api } from "../api.js";
import { connect } from "../sse.js";
import { go } from "../router.js";
import { badge, el, money, notice } from "../ui.js";
import { serverConfig } from "../main.js";

let stream = null;

export function unmount() {
  stream?.close();
  stream = null;
}

export async function mount(root, params) {
  if (params.id) return mountJob(root, params.id);
  return mountSetup(root);
}

// -- setup -------------------------------------------------------------------

async function mountSetup(root) {
  const config = await serverConfig();
  const hasKey = Boolean(config?.credentials?.google || config?.credentials?.anthropic);

  root.append(
    el("header", {}, [
      el("h1", { text: "Add a game" }),
      el("p", {
        text:
          "Submit the rule material once. A Compiler agent reads it and writes a Python " +
          "module that the core will call every turn to enumerate what is legal. It is " +
          "validated by static scan, a subprocess sandbox and random self-play before " +
          "you ever see it - and nothing is installed until you approve it.",
      }),
    ])
  );

  if (!hasKey) {
    root.append(
      notice(
        "warn",
        "Compiling needs an API key",
        "Set GEMINI_API_KEY in .env and restart the server."
      )
    );
  }

  const state = { game: "", material: null, cards: null, use_catalog: false, attempts: 4 };
  const status = el("div", { class: "stack tight" });

  function uploader(labelText, hint, key) {
    const input = el("input", {
      type: "file",
      accept: ".pdf,.txt,.md,.markdown,.text",
      onChange: async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        const line = el("div", { class: "faint", text: `reading ${file.name}...` });
        status.append(line);
        try {
          const result = await api.upload(file);
          state[key] = result.job_id;
          // What was *actually* read, before anyone pays for a compile. A scan sent
          // down the text path would deliver no rules at all, and the compiler would
          // produce a confidently wrong module rather than an error.
          line.replaceWith(
            notice("ok", `${result.filename}`, `read as ${result.kind} - ${result.detail}`)
          );
        } catch (error) {
          line.replaceWith(notice("danger", `${file.name} could not be read`, error.message));
        }
      },
    });
    return el("div", { class: "field" }, [
      el("label", { text: labelText }),
      input,
      el("span", { class: "faint", text: hint }),
    ]);
  }

  const startButton = el("button", {
    class: "btn primary",
    disabled: !hasKey,
    text: "Compile",
    onClick: async (event) => {
      if (!state.game || !state.material) {
        status.append(notice("warn", null, "A name and a rulebook are both needed."));
        return;
      }
      event.currentTarget.disabled = true;
      try {
        const job = await api.startCompile({
          game: state.game,
          material_id: state.material,
          cards_id: state.cards,
          use_catalog: state.use_catalog,
          attempts: state.attempts,
        });
        go(`#/compile/${job.id}`);
      } catch (error) {
        status.append(notice("danger", "Could not start", error.message));
        event.currentTarget.disabled = false;
      }
    },
  });

  root.append(
    el("div", { class: "panel stack" }, [
      el("div", { class: "field" }, [
        el("label", { text: "Name for the module" }),
        el("input", {
          type: "text",
          placeholder: "lowercase, a valid Python identifier",
          onInput: (e) => (state.game = e.target.value.trim()),
        }),
        el("span", {
          class: "faint",
          text:
            "If the compiler gives the module a different NAME, that one wins - the " +
            "loader name, the module's NAME and the catalog filename must agree.",
        }),
      ]),
      uploader("Rulebook", "PDF (text or scanned), or a text file.", "material"),
      uploader(
        "Card list (optional)",
        "Most rulebooks do not list the cards. Supply them separately if you have them.",
        "cards"
      ),
      el("label", { class: "check" }, [
        el("input", {
          type: "checkbox",
          onChange: (e) => (state.use_catalog = e.target.checked),
        }),
        el("span", {}, [
          el("span", { text: "Learn card effects at runtime" }),
          el("div", {
            class: "faint",
            text:
              "For games with effect cards. The module implements the rules; what each " +
              "card is worth is looked up from a catalog you fill in as cards appear.",
          }),
        ]),
      ]),
      el("div", { class: "field" }, [
        el("label", { text: "Attempts" }),
        el("input", {
          type: "number",
          min: "1",
          max: "10",
          value: "4",
          onInput: (e) => (state.attempts = Number(e.target.value) || 4),
        }),
        el("span", {
          class: "faint",
          text: "Each failed attempt is fed back with the seed and ply that reproduce it.",
        }),
      ]),
      notice(
        "info",
        "This takes minutes, not seconds, and costs real money",
        "A running total is shown while it works."
      ),
      el("div", { class: "row" }, [startButton]),
      status,
    ])
  );
}

// -- the job ------------------------------------------------------------------

async function mountJob(root, id) {
  const job = await api.compileJob(id);

  const heading = el("h1", { text: `Compiling ${job.game}` });
  const statusBadge = badge(job.status, "neutral");
  const costLine = el("div", { class: "cost", text: "no calls yet" });
  const attemptsBox = el("div", { class: "steps" });
  const log = el("div", { class: "log", role: "log", "aria-live": "polite" });
  const approvalHost = el("div");

  root.append(
    el("header", {}, [
      el("div", { class: "row spread" }, [heading, statusBadge]),
      el("p", { class: "faint", text: `job ${id}` }),
    ]),
    el("div", { class: "panel stack" }, [costLine, attemptsBox, log]),
    approvalHost
  );

  let calls = 0;
  let cost = 0;

  function logLine(text) {
    log.append(el("div", { text }));
    log.scrollTop = log.scrollHeight;
  }

  // Reconnecting to a job that already finished: the approval panel is rebuilt from
  // the snapshot, and the SSE history replay fills the log back in behind it.
  if (job.status === "awaiting_approval") showApproval(approvalHost, root, id, job);

  stream = connect(`/api/compile/${encodeURIComponent(id)}/events`, {
    compile_start: (data) => {
      logLine(`compiling with ${data.model}, up to ${data.attempts} attempt(s)`);
      logLine(data.note);
    },
    trajectory: (data) => {
      // The compile's own trajectory, republished as it is written. Only the two
      // event kinds worth watching are surfaced; the file has everything.
      if (data.type === "llm_call") {
        calls += 1;
        cost += data.cost_usd ?? 0;
        costLine.textContent = `${calls} model call(s) · ${money(cost)} so far`;
        if (data.outcome === "api_error") {
          logLine(`  ! call failed: ${data.error ?? "unknown error"} - retrying`);
        } else if (data.outcome === "validation_failed") {
          logLine(`  ! response rejected: ${data.rejection ?? "invalid"} - retrying`);
        }
      } else if (data.event === "rule_material_ingested") {
        logLine(`material read: ${data.detail}`);
      } else if (data.event === "compile_attempt") {
        const mark = data.ok ? "PASS" : `failed at ${data.stage}`;
        attemptsBox.append(
          el("div", { class: "attempt" }, [
            el("strong", { text: `attempt ${data.attempt}` }),
            el("span", { text: mark }),
            el("span", { class: "faint", text: `${(data.source_chars ?? 0).toLocaleString()} chars` }),
          ])
        );
        for (const problem of (data.problems ?? []).slice(0, 6)) {
          logLine(`  - ${String(problem).slice(0, 200)}`);
        }
      }
    },
    catalog_start: (data) => {
      logLine(`drafting the card catalog with ${data.model}, from ${data.source}`);
    },
    catalog_drafted: (data) => {
      logLine(
        `catalog: ${data.known.length} drafted, ${data.parked.length} parked, ` +
          `${data.not_described.length} left for the table`
      );
    },
    catalog_failed: (data) => {
      logLine(`catalog draft skipped: ${data.message}`);
    },
    catalog_skipped: (data) => {
      logLine(`catalog draft skipped: ${data.reason}`);
    },
    compile_ready: async (data) => {
      logLine(data.note);
      const refreshed = await api.compileJob(id);
      showApproval(approvalHost, root, id, refreshed);
    },
    compile_failed: (data) => {
      approvalHost.replaceChildren(notice("danger", "Compilation failed", data.message ?? ""));
    },
    install_done: (data) => {
      approvalHost.replaceChildren(
        notice("ok", "Installed", `${data.target} - playable as ${data.installed_name}`),
        el("div", { class: "row tight" }, [
          el("button", { class: "btn primary small", text: "Back to games", onClick: () => go("#/games") }),
          el("button", {
            class: "btn small",
            text: "Play it now",
            onClick: () => go(`#/play?game=${encodeURIComponent(data.installed_name)}`),
          }),
        ])
      );
    },
    error: (data) => logLine(`error: ${data.kind}: ${data.message}`),
  });
}

function showApproval(host, root, id, job) {
  const approval = job.approval;
  if (!approval) return;

  const acknowledge = el("input", { type: "checkbox" });
  const installButton = el("button", {
    class: "btn primary",
    disabled: true,
    text: approval.target_differs ? "Approve and overwrite" : "Approve and install",
    onClick: async (event) => {
      event.currentTarget.disabled = true;
      try {
        await api.approveCompile(id, approval.target_differs);
      } catch (error) {
        host.append(notice("danger", "Could not install", error.message));
        event.currentTarget.disabled = false;
      }
    },
  });
  acknowledge.addEventListener("change", () => {
    installButton.disabled = !acknowledge.checked;
  });

  const list = (title, items) =>
    items?.length
      ? el("div", { class: "stack tight" }, [
          el("h3", { text: title }),
          el("ul", {}, items.map((item) => el("li", { text: item }))),
        ])
      : null;

  host.replaceChildren(
    el("div", { class: "approval stack" }, [
      el("h2", { text: "Read this before it is installed" }),
      el("p", {
        class: "faint",
        text: "Every gate before this point is automated. This one is not.",
      }),

      el("div", { class: "stack tight" }, [
        el("h3", { text: "What it says it implemented" }),
        el("p", { text: approval.plain_language_summary }),
      ]),
      list("Assumptions it had to make", approval.assumptions),
      list("Open questions it could not resolve", approval.open_questions),

      approval.state_keys
        ? el("div", { class: "stack tight" }, [
            el("h3", { text: "The state schema it chose" }),
            el("pre", { class: "mono-block scroll-x", text: String(approval.state_keys) }),
          ])
        : null,

      approval.catalog
        ? el("div", { class: "stack tight" }, [
            el("h3", { text: "Card catalog" }),
            el("p", {
              class: "faint",
              text:
                `${approval.catalog.known.length} card(s) drafted, ` +
                `${approval.catalog.parked.length} parked, ` +
                `${approval.catalog.not_described.length} left for the table - ` +
                `saved to ${approval.catalog.path}`,
            }),
            list("Drafted", approval.catalog.known),
            list("Parked - the vocabulary could not express these", approval.catalog.parked),
            list("Left for the table", approval.catalog.not_described),
          ])
        : null,

      approval.unknowns !== null && approval.unknowns !== undefined
        ? el("p", {
            class: "faint",
            text:
              `Questions asked during validation: ${approval.unknowns}` +
              (approval.unknowns ? "" : " (nothing was ever described - see the summary)"),
          })
        : null,

      el("details", {}, [
        el("summary", { text: "The generated source (read-only)" }),
        el("pre", { class: "mono-block scroll-x", text: approval.module_source ?? "" }),
        el("button", {
          class: "btn small",
          text: "Copy",
          onClick: () => navigator.clipboard?.writeText(approval.module_source ?? ""),
        }),
      ]),

      approval.target_differs
        ? notice(
            "warn",
            "This will replace an existing module",
            `${approval.target} already exists and differs from this build.`
          )
        : el("p", { class: "faint", text: `It will be installed to ${approval.target}.` }),

      el("label", { class: "check" }, [
        acknowledge,
        el("span", {
          text:
            "I have read the summary, the assumptions and the open questions above.",
        }),
      ]),

      el("div", { class: "row tight" }, [
        installButton,
        el("button", {
          class: "btn danger",
          text: "Discard",
          onClick: async () => {
            await api.rejectCompile(id).catch(() => {});
            host.replaceChildren(
              notice("info", "Discarded", "Nothing was installed. The build stays on disk.")
            );
          },
        }),
      ]),
    ])
  );
}
