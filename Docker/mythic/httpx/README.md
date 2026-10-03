# httpx agent configuration — two files, two different shapes

This is the single most error-prone part of wiring Mythic's `httpx` C2 profile.
The profile needs the SAME agent variation expressed in two different JSON
shapes, and using the wrong one fails in a different way each time.

## `agent_variation.json` — the build-time parameter (`raw_c2_config`)

A **single variation object** (top-level `name` + `get` + `post`). This is what
you upload as the `raw_c2_config` FILE parameter on the payload build. The
profile's `builder.go` unmarshals it into one `AgentVariations` value and calls
`validateAndUpdateConfig`, which rejects it with `Missing name for agent
variation` if the shape is wrong.

## `agent_configs.json` — the runtime file the profile container serves from

A **map keyed by variation name**: `{ "<name>": { <variation> } }`. The httpx
webserver's `main.go` unmarshals this into `map[string]AgentVariations`; a bare
object makes it log `Failed to unmarshal config bytes` and `os.Exit(1)`, so the
listener never binds and the redirector returns 502.

That is why the committed `agent_configs.json` wraps `agent_variation.json`:
the compose service bind-mounts it into the container so routes exist from first
boot, and the build merges the uploaded variation into the same file at runtime.

## Values that matter here

| Field | Value | Why |
|---|---|---|
| `get.uris` / `post.uris` | `/media/uploads/index`, `/media/uploads/data` | MUST start with the redirector prefix that routes to this profile (`MYTHIC_HTTPX_URI_PREFIX=/media/uploads`). The prefix is NOT added automatically — an unprefixed URI falls through to the decoy page. |
| `post.client.message.location` | `"body"` | Not `""`. Docs list only `cookie` / `query` / `header` / `body`. |
| `get.client.transforms[0].action` | `base64url` | Every upstream example uses `base64url`. Padded `base64` emits `=` which is unsafe in a query string. |
| `X-Request-ID: cadre-c2` | present on both | The redirector only proxies when this header matches; without it the agent gets the 406-byte CloudEdge decoy and never registers. |
| `callback_host` (build param) | `http://192.168.77.1` | Host only. A port in it fails the OPSEC check (`callback host is improperly configured!`); the port goes in `callback_port`. The **path** prefix is not needed here because httpx takes the full URI from `uris`. |
