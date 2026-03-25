# SSE Event Specification

Endpoint: `GET /api/v1/events`
Protocol: Server-Sent Events (text/event-stream)
Keepalive: `: keepalive\n\n` every 30s if idle

## Wire Format

```
event: <event-type>
data: <json>
```

## Event Types

### Core (from vm_controller)

| Event     | Data                                | When                              |
|-----------|-------------------------------------|-----------------------------------|
| heartbeat | `{}`                                | Guest sends heartbeat             |
| signal    | `{"event": "<name>"}` or `{"event": "<name>", "value": "<v>"}` | Any signal handled via `/api/v1/signal/{event}` |

### Plugin: eden_garden

| Event   | Data                                                        | When                                    |
|---------|-------------------------------------------------------------|-----------------------------------------|
| phase   | `{"phase": "<phase>"}`                                      | Phase transition                        |
| tag     | `{"name": "<tag_name>", "index": <n>, "count": <n>}`       | RFID tag scanned                        |
| move    | `{"character": "auriea"\|"michael", "move": "<move>"}`      | Character movement update               |
| letter  | `{"char": "<c>"}`                                           | Letter typed during world generation    |
| button  | `{"visible": true}` or `{"pressed": true}` or `{"clicked": true}` | Button state change              |
| content | `{"html": "<html>", "url": "<url>"}`                        | Template fetched for world generation   |
| reset   | `{}`                                                        | Artwork reset                           |

### Phases (eden_garden)

`pre-bigbang` → `bigbang` → `post-bigbang` → `done` → `pre-bigbang`

- **pre-bigbang** — Button visible, waiting for visitor
- **bigbang** — Button pressed, parsing tags, world generation in progress
- **post-bigbang** — All tags processed, world built, characters still dancing
- **done** — Both characters at rest, reset allowed

## JavaScript Client Example

```javascript
const events = new EventSource("/api/v1/events");

events.addEventListener("phase", (e) => {
  const { phase } = JSON.parse(e.data);
  console.log("Phase:", phase);
});

events.addEventListener("signal", (e) => {
  const data = JSON.parse(e.data);
  console.log("Signal:", data.event, data.value ?? "");
});

events.addEventListener("tag", (e) => {
  const { name, index, count } = JSON.parse(e.data);
  console.log("Tag scanned:", name, `(${count})`);
});

events.addEventListener("button", (e) => {
  const data = JSON.parse(e.data);
  console.log("Button:", data);
});

events.addEventListener("heartbeat", () => {
  console.log("Guest alive");
});
```

## Notes

- The `signal` event is a pass-through of all `/api/v1/signal/{event}` calls. This includes high-frequency signals like `button-visible` and `heartbeat`.
- Plugin events (`phase`, `tag`, `move`, `button`, etc.) are higher-level, deduplicated state changes pushed by the plugin logic itself.
- Reconnection is handled automatically by `EventSource`. The stream has no event IDs — clients should fetch `/api/v1/state` on reconnect to sync.
