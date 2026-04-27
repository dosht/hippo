---
id: mem-sample-pattern-webhook-idempotency
type: pattern
topics: [webhooks, idempotency, retry, deduplication]
summary: Use the provider's webhook_id as a unique constraint on a webhook_events table to deduplicate retries without losing events.
projects: [all]
agents: [developer, architect]
staleness_policy: never
source_sessions: [<example-session-uuid>]
created: 2026-02-03
last_validated: 2026-02-03
last_queried: null
query_count: 0
confidence: high
supersedes: []
---

# Webhook idempotency via provider-supplied webhook_id

Webhook providers (Stripe, LemonSqueezy, GitHub, etc.) retry on non-2xx responses or timeouts. The same logical event can hit your endpoint two or more times. To handle this without dropping events or processing them twice, use the provider's `webhook_id` as a uniqueness boundary at the database layer.

## Schema

```sql
CREATE TABLE webhook_events (
  webhook_id TEXT PRIMARY KEY,        -- from provider (e.g. evt_1Abc... for Stripe)
  provider   TEXT NOT NULL,           -- 'stripe' | 'lemonsqueezy' | ...
  event_type TEXT NOT NULL,           -- 'subscription_created' | etc.
  payload    JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  processed_at TIMESTAMPTZ            -- nullable; set when handler completes
);
```

## Handler shape

```typescript
export async function POST(req: Request) {
  const payload = await req.json();
  const webhookId = payload.meta.webhook_id;       // provider-specific path

  // INSERT ON CONFLICT DO NOTHING returns 0 rows if already present
  const { rowCount } = await db.query(
    `INSERT INTO webhook_events (webhook_id, provider, event_type, payload)
     VALUES ($1, $2, $3, $4)
     ON CONFLICT (webhook_id) DO NOTHING`,
    [webhookId, 'stripe', payload.type, payload]
  );

  if (rowCount === 0) {
    // Already processed (or in-flight). Return 200 so provider stops retrying.
    return new Response(null, { status: 200 });
  }

  // First time seeing this event — do the actual work.
  await processEvent(payload);

  await db.query(
    `UPDATE webhook_events SET processed_at = NOW() WHERE webhook_id = $1`,
    [webhookId]
  );

  return new Response(null, { status: 200 });
}
```

## Why this beats alternatives

- **Idempotency keys in app memory:** lost on deploy, can't span instances. DB-backed dedup survives both.
- **Time-window dedup ("ignore if same event in last 60s"):** legitimate retries after long outages get dropped.
- **No dedup, just idempotent business logic:** works for some operations (UPSERTs) but not others (charging a card, sending an email). Better to dedup at the boundary.

The `processed_at` column is the audit trail: row-exists-but-processed_at-null = handler crashed mid-flight, worth investigating. Row-exists-and-processed_at-set = clean dedup.

## Always 200

Critical: even when you reject (because dedup), respond 200. Webhook providers retry on non-2xx, so a 4xx response triggers retries that just hit the same conflict. 200 with empty body is the correct "I got it, stop sending" signal.
