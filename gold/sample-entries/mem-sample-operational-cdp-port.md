---
id: mem-sample-operational-cdp-port
type: operational
topics: [chrome-devtools, cdp, ports, browser-automation]
summary: Use port 9224 (not the default 9222) when connecting to a separate Chrome instance to avoid profile conflicts with the user's normal browser.
projects: [all]
agents: [developer, tester]
staleness_policy: 90d
source_sessions: [<example-session-uuid>]
created: 2026-01-15
last_validated: 2026-01-15
last_queried: null
query_count: 0
confidence: medium
supersedes: []
---

# Use port 9224 for separate Chrome instance under CDP

When automating a browser via Chrome DevTools Protocol while the user has their own Chrome open, do **not** use the default CDP port 9222. The user's main Chrome instance often binds to it, and a second instance launching against the same port fails silently or hijacks the wrong profile.

## What works

```bash
google-chrome \
  --remote-debugging-port=9224 \
  --user-data-dir=/tmp/cdp-profile \
  --no-first-run \
  --no-default-browser-check
```

Then connect from your automation client:

```javascript
const browser = await chromium.connectOverCDP('http://localhost:9224');
```

## Why

Chrome's `--remote-debugging-port` flag silently no-ops if another Chrome process already owns the port. Worse: if the user's profile is already attached, your automation script ends up driving the user's actual browser, with their tabs and cookies. This corrupts their session and is confusing to debug because there's no error message.

## Discovery context

Surfaced when an end-to-end test against a payment flow started clicking around in the user's real browser. The test harness was launching with `--remote-debugging-port=9222`, which silently bound to the existing Chrome.

The 9224 convention isn't standardized — it's just a port not commonly used by any tool I've seen. Pick anything in the 9224-9230 range; 9223 is sometimes used by Edge.
