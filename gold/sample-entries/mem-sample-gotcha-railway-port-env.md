---
id: mem-sample-gotcha-railway-port-env
type: gotcha
topics: [railway, deployment, ports, environment-variables]
summary: Railway injects PORT at runtime; binding to a hardcoded port (e.g. 3000) makes the service appear healthy but unreachable from the public domain.
projects: [all]
agents: [developer]
staleness_policy: 60d
source_sessions: [<example-session-uuid>]
created: 2026-03-10
last_validated: 2026-03-10
last_queried: null
query_count: 0
confidence: high
supersedes: []
---

# Railway: bind to process.env.PORT, not a hardcoded port

Railway assigns a random internal port to each service at runtime via the `PORT` env var. If your server hardcodes `3000` (or any literal), it binds to the wrong port and Railway's load balancer can't reach it. The service shows "deployed" and "healthy" (because the process is running), but every request from the public domain returns a 502.

## The bug

```typescript
// ❌ BROKEN on Railway
app.listen(3000, () => console.log('Server on 3000'));
```

The container starts, the process listens on 3000, the `/health` endpoint works **inside the container** (which is what makes it look healthy), but Railway's edge proxy is trying to forward traffic to whatever it set in `$PORT` (e.g. 8080). 502 Bad Gateway from outside.

## The fix

```typescript
// ✅ CORRECT
const port = Number(process.env.PORT) || 3000;   // 3000 fallback for local dev
app.listen(port, () => console.log(`Server on ${port}`));
```

Same pattern for any framework:

| Framework | Read `process.env.PORT` |
|---|---|
| Express / Fastify | `app.listen(process.env.PORT)` |
| Next.js (custom server) | `next.start({ port: process.env.PORT })` |
| Remix (vite) | uses PORT automatically; double-check `vite.config.ts` doesn't override |
| Python uvicorn | `uvicorn.run(app, port=int(os.environ.get("PORT", 3000)))` |

## How to spot it

Symptoms:
- Railway dashboard shows green "Active" and "Healthy"
- Build logs are clean
- Public domain returns 502 on every request
- `curl <internal-private-domain>:3000` works (which is the misleading clue)

If you see a 502 on a service that "looks healthy", grep your code for hardcoded ports first — it's the most common cause and takes 30 seconds to confirm.

## Local dev compatibility

The `|| 3000` fallback in the fix means local `npm run dev` still works (PORT isn't set, falls through to 3000). Don't be tempted to delete the fallback — you'll forget and waste 5 minutes wondering why local dev broke.
