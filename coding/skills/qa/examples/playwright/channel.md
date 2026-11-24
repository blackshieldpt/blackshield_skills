# Channel `web` (Playwright)

The `channel-web-doc` for the sample channel in `channel.spec.ts`. `coding:qa accept`
writes step files using only the actions below, with the listed args. Selectors are
Playwright selectors, for example `role=button[name="Save"]`, `text=Saved` or
`#email`. Paths are relative to the app's `url`; any other origin is refused.

| Action | Args | Marks | Observations printed |
|---|---|---|---|
| `goto` | `path` | read-only | final URL, HTTP status, page title |
| `click` | `selector` | | selector, URL after the click |
| `fill` | `selector`, `value` | | selector, the field's value afterwards |
| `press` | `selector`, `key` | | selector, URL after the key press |
| `text` | `selector` | read-only | inner text of every match |
| `visible` | `selector` | read-only | whether the first match is visible |
| `count` | `selector` | read-only | number of matches |
| `url` | | read-only | current URL |

Actions without the `read-only` mark count as changing state: a criterion that uses
one needs `isolated: yes` in the project rules, or the user's yes during the run.

Each step prints one line, `{"step": <i>, "action": "<action>", "observed": {...}}`.
A selector that matches nothing makes `click`, `fill` and `press` time out, and one
that matches several elements fails them (Playwright's strict mode). Either fails the
run, and the skill then reports the criterion NOT RUN, never FAIL.
