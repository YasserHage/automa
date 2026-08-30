// A thin wrapper over EventSource.
//
// `EventSource` already handles reconnect and `Last-Event-ID` replay by itself, which
// is the whole reason SSE was chosen over WebSockets here: the traffic is server->
// client push plus discrete client POSTs, which is exactly its shape, and it needs
// nothing beyond FastAPI's StreamingResponse.
//
// `close()` matters more than it looks: a screen that navigates away without closing
// its stream leaves a live connection behind, and browsers cap connections per origin
// - a handful of dead streams and the next one never opens.

export function connect(url, handlers) {
  const source = new EventSource(url);
  const bound = [];

  for (const [type, handler] of Object.entries(handlers)) {
    if (type === "onerror") continue;
    const listener = (event) => {
      let data = {};
      try {
        data = JSON.parse(event.data);
      } catch {
        return;
      }
      handler(data);
    };
    source.addEventListener(type, listener);
    bound.push([type, listener]);
  }

  if (handlers.onerror) {
    source.addEventListener("error", handlers.onerror);
  }

  return {
    close() {
      for (const [type, listener] of bound) source.removeEventListener(type, listener);
      source.close();
    },
    get readyState() {
      return source.readyState;
    },
  };
}
