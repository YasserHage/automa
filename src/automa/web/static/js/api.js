// Every call to the server, in one place.
//
// The API returns FastAPI's `{detail: ...}` on an error, and `detail` is sometimes a
// string and sometimes an object carrying the store's own problem strings (a refused
// `learn`, for instance). `ApiError` keeps both so a caller can render the problems
// verbatim - they are written to be read by a person, and rewording them would lose
// the detail that makes them actionable.

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : (detail?.detail ?? `HTTP ${status}`));
    this.status = status;
    this.detail = detail;
    this.problems = detail?.problems ?? [];
  }
}

async function request(method, path, body, options = {}) {
  const init = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  if (response.status === 204) return null;
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload?.detail ?? payload);
  }
  return payload;
}

export const api = {
  health: () => request("GET", "/api/health"),
  config: () => request("GET", "/api/config"),

  games: () => request("GET", "/api/games"),
  game: (name) => request("GET", `/api/games/${encodeURIComponent(name)}`),
  validateGame: (name, matches = 100) =>
    request("POST", `/api/games/${encodeURIComponent(name)}/validate?matches=${matches}`),
  reloadGame: (name) => request("POST", `/api/games/${encodeURIComponent(name)}/reload`),

  vocabulary: () => request("GET", "/api/vocabulary"),
  catalog: (game) => request("GET", `/api/games/${encodeURIComponent(game)}/catalog`),
  learnCard: (game, cardType, effects, description) =>
    request(
      "PUT",
      `/api/games/${encodeURIComponent(game)}/catalog/cards/${encodeURIComponent(cardType)}`,
      { effects, description }
    ),
  parkCard: (game, cardType, description, reason) =>
    request(
      "POST",
      `/api/games/${encodeURIComponent(game)}/catalog/cards/${encodeURIComponent(cardType)}/park`,
      { description, reason }
    ),
  forgetCard: (game, cardType) =>
    request(
      "DELETE",
      `/api/games/${encodeURIComponent(game)}/catalog/cards/${encodeURIComponent(cardType)}`
    ),
  describeCard: (game, cardType, description) =>
    request(
      "POST",
      `/api/games/${encodeURIComponent(game)}/catalog/cards/${encodeURIComponent(cardType)}/describe`,
      { description }
    ),

  upload: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("POST", "/api/uploads", form);
  },
  startCompile: (body) => request("POST", "/api/compile", body),
  compileJob: (id) => request("GET", `/api/compile/${encodeURIComponent(id)}`),
  approveCompile: (id, overwrite) =>
    request("POST", `/api/compile/${encodeURIComponent(id)}/approve`, {
      acknowledged: true,
      overwrite,
    }),
  rejectCompile: (id) => request("POST", `/api/compile/${encodeURIComponent(id)}/reject`),

  createSession: (body) => request("POST", "/api/sessions", body),
  session: (id) => request("GET", `/api/sessions/${encodeURIComponent(id)}`),
  move: (id, index) =>
    request("POST", `/api/sessions/${encodeURIComponent(id)}/move`, { index }),
  answer: (id, text) =>
    request("POST", `/api/sessions/${encodeURIComponent(id)}/answer`, { text }),
  abandon: (id) => request("DELETE", `/api/sessions/${encodeURIComponent(id)}`),
};
