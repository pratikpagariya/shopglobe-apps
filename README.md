# shopglobe-apps

8 Python microservices. **This is the app layer only — you build the DevOps layer.**
Everything below is the *contract* your Helm charts, ServiceMonitors, SLO queries and
Argo Rollouts analysis will depend on. Don't guess at these values; they're all here.

## Run it

```bash
pip install -r requirements-dev.txt
make up          # postgres + redis + localstack + all 8 services
make test        # contract tests
make lint
curl localhost:8000/api/catalog/products?limit=5
docker compose down -v
```

## The contract

**Every service** exposes exactly these, on **port 8000**:

| Path | Meaning |
|---|---|
| `/healthz` | **Liveness.** Process only, never a dependency. Always 200 while alive. |
| `/readyz` | **Readiness.** 200 with `{"ready":true}`, or **503** with `{"failed":{...}}`. |
| `/metrics` | Prometheus text format. |

Probe traffic is **excluded** from request metrics, so it can't distort your error budget.

**Metric names** (identical across all 8 — build one dashboard, not eight):

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | counter | `service, method, path, status` |
| `http_request_duration_seconds` | histogram | `service, method, path` |
| `http_requests_inflight` | gauge | `service` |
| `service_ready` | gauge | `service` |
| `cache_hits_total` / `cache_misses_total` / `cache_errors_total` | counter | `service` (cart-svc) |
| `queue_messages_processed_total` | counter | `service, result` (payment-worker) |
| `queue_processing_duration_seconds` | histogram | `service` (payment-worker) |
| `queue_depth`, `queue_oldest_message_age_seconds` | gauge | `service` (payment-worker) |

`path` is the **route template** (`/products/{pid}`), never the raw URL — see the test that
enforces it. Histogram buckets are clustered around **300ms** because that's the latency SLO;
`le="0.3"` exists as an exact bucket edge, so your p95 isn't interpolation guesswork.

```promql
# your p95 -- works unchanged for all 8
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))
```

## Services

| Service | Port (local) | Depends on | Ready when | Key endpoints |
|---|---|---|---|---|
| `edge-gateway` | 8000 | auth-svc | auth reachable | `/api/{svc}/{path}` proxy |
| `auth-svc` | 8001 | — | JWT secret injected | `POST /token`, `GET /verify`, `GET /admin` (403 demo) |
| `catalog-svc` | 8002 | **RDS Postgres** | `SELECT 1` | `/products`, `/products/{id}`, **`/products-n1`** |
| `cart-svc` | 8003 | catalog-svc, *Redis* | catalog only | `POST/GET/DELETE /cart/{user}` |
| `order-svc` | 8004 | **SQS**, cart-svc | both | `POST /orders` (202), `GET /orders/{id}` |
| `payment-worker` | 8005 | **SQS** | queue reachable | none — consumer only |
| `notify-svc` | 8006 | **SNS** | topic reachable | `POST /notify` |
| `media-svc` | 8007 | **S3** (+CloudFront) | `head_bucket` | `POST /media/presign` |

**Note `cart-svc`:** Redis is deliberately **not** a readiness check. It's a cache, so losing it
must *degrade* (slower, straight to catalog) not *break*. That distinction is an interview answer.

## Env vars

`SERVICE_NAME` `ENV` `MARKET` `LOG_LEVEL` `PORT` `DRAIN_SECONDS`
`DATABASE_URL` `REDIS_URL` `SQS_QUEUE_URL` `SNS_TOPIC_ARN` `S3_BUCKET` `CDN_DOMAIN`
`AUTH_URL` `CATALOG_URL` `CART_URL` `ORDER_URL` `NOTIFY_URL` `MEDIA_URL`
`UPSTREAM_TIMEOUT_SECONDS` `UPSTREAM_RETRIES` `JWT_SECRET` `JWT_KID` `JWT_TTL_SECONDS`
`CACHE_TTL_SECONDS` `AWS_REGION` `AWS_ENDPOINT_URL`

Full list with defaults: `libs/common/__init__.py`. `AWS_ENDPOINT_URL` points at localstack
locally and must be **empty in AWS**, so boto3 uses the real endpoint and **Pod Identity**
supplies credentials. No access keys, anywhere.

## What your Helm chart must set

- `terminationGracePeriodSeconds` **> `DRAIN_SECONDS` + longest request** (default drain is 5s)
- `preStop: sleep 5` — EndpointSlice removal is eventually consistent, so traffic still arrives
  for a second or two after SIGTERM
- readiness probe → `/readyz`, liveness → `/healthz`. Never the reverse.
- app keep-alive is **75s**, deliberately **above** the ALB's 60s idle timeout. Set it lower and
  you get intermittent 502s when the ALB reuses a connection the app just closed.
- memory limits on everything; be careful with CPU limits on `edge-gateway` (CFS throttling)

## Deliberate teaching artifacts

| Where | What | Find it on |
|---|---|---|
| `catalog-svc /products-n1` | An N+1 query next to the fixed version | Day 2 — `load/n1.js`, compare p95 |
| `Dockerfile` CMD comment | Shell-form vs exec-form SIGTERM handling | Day 1 — time `docker stop` both ways |
| `catalog_svc/db.py` `make_engine` | Pool math that exhausts RDS `max_connections` | Day 4 — scale replicas, watch it break |
| `payment_worker` | Processing time vs SQS visibility timeout | Day 4 — shorten the timeout, watch redelivery |
| `payment_worker` | `amount > 100000` always fails → DLQ | Day 4 — poison message |
| `cart_svc` TTL jitter | Cache stampede prevention | Day 5 — remove jitter, watch Postgres |
| `media_svc` versioned keys | Why you never need a CloudFront invalidation | Day 5 |
| `auth_svc /admin` | 401 vs 403 | Day 5 — chaos drills |

## Known simplifications (call these out yourself in an interview)

1. **One shared `requirements.txt`** → every image carries asyncpg, redis and boto3 whether it
   needs them or not. Fine for a monorepo of 8; at 50 services you'd split per service. This is a
   real image-size and CVE-surface cost, and knowing that you accepted it deliberately is better
   than not noticing.
2. **`CARTS`, `ORDERS`, `SEEN` are in-process dicts.** Real state belongs in Redis/Postgres —
   these exist so the service runs without more infrastructure than the lesson needs. It also
   means **these services are not actually horizontally scalable**, which is itself the point:
   externalising session state is why `cart-svc` has Redis at all.
3. **Alembic isn't wired up** — `seed.sql` creates the schema for local dev. Day 4 adds Alembic as
   an ArgoCD `PreSync` hook, which is where migrations belong (never on app startup — N pods
   would race the same database).
4. **No tracing backend.** `x-request-id` propagates through all 8 and lands in every log line,
   so you can reconstruct a request in Loki. Real OpenTelemetry spans are week 2.
