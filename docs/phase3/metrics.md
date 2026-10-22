# Phase 3: service metrics for operators

Scope: a Prometheus text endpoint that an operator can scrape. It is off by default and needs a bearer token when on. It reports request counts and latency for this API process, plus counts read from the database at each scrape. It names no user. The rest of the API is in `docs/api.md`, which this document does not edit.

Tested against PostgreSQL with a real scrape (see Tests). No live Prometheus server was run.

## Enabling

| Variable | Purpose |
| --- | --- |
| `METRICS_TOKEN` | Bearer token a scraper must send. Unset or empty means the endpoint answers 404. |

Generate a token with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. Set it in `.env` (or the container environment) and restart the API. There is no rotation API: change the variable and restart.

## Endpoint

### `GET /api/v1/metrics`

Request: `Authorization: Bearer <METRICS_TOKEN>`. The scheme name is case-insensitive.

| Status | When | Body |
| --- | --- | --- |
| 200 | Token matches | Prometheus exposition text. `Content-Type: text/plain; version=0.0.4` |
| 401 | Header missing, not Bearer, empty, or wrong token | Empty. `WWW-Authenticate: Bearer` |
| 404 | `METRICS_TOKEN` unset or empty | The same JSON as an unknown path: `{"error": {"code": "not_found", "message": "Not Found", "details": {}}, "request_id": "..."}` |
| 503 | The database could not be read during the scrape | `{"error": {"code": "metrics_unavailable", "message": "Metrics are unavailable: the database could not be read.", "details": {}}, "request_id": "..."}`. No database detail is returned. |

Checks in the middleware:

- The origin check and the CSRF check apply only to POST, PUT, PATCH and DELETE. A scrape is GET, so neither applies and no exemption was added. A test sends a foreign `Origin` and gets 200.
- The per-client-IP API rate limit (`API_RATE_LIMIT_PER_MINUTE`, default 600 a minute) still applies to the scrape.
- Scrapes are not counted in the request metrics.
- The token is checked before any database query, so an unauthenticated caller costs one hash comparison.

## Metrics

| Metric | Type | Labels | Source |
| --- | --- | --- | --- |
| `jarvis_build_info` | gauge | `version` | Application version. Always 1. |
| `jarvis_http_requests_total` | counter | `method`, `route`, `status` | In-process. Every `/api/v1` response, including refusals and errors. |
| `jarvis_http_request_duration_seconds` | histogram | `method`, `route` | In-process. Buckets 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10 and +Inf. |
| `jarvis_tasks` | gauge | `status` | `tasks` table, all users, at scrape time. Every status is emitted, as 0 when empty. |
| `jarvis_devices` | gauge | `connection_status` (`online`, `offline`) | Paired devices that are not revoked, across all users. Online means the device hub holds a live connection, read at scrape time. |
| `jarvis_ai_requests_total` | counter | `provider`, `outcome` (`ok`, `error`) | `usage_records`, all operations, all time, at scrape time. |
| `jarvis_background_workers_running` | gauge | none | 1 when the scheduler's loop task is alive, else 0. It is 0 when `RUN_BACKGROUND_WORKERS` is false. |

### Label rules

- `route` is the path template of the matched route with every parameter shown as `{id}`. For example, `GET /api/v1/tasks/<uuid>?x=1` is labelled `/api/v1/tasks/{id}`. The query string is never in a label.
- Requests that match no route are labelled `unmatched`. So are requests refused before routing (origin, CSRF, rate limit), and their status is still counted.
- `method` keeps GET, HEAD, POST, PUT, PATCH, DELETE and OPTIONS. Any other method token is labelled `OTHER`.
- No label holds a user, an email, a user id or a raw path.

Why templates rather than the raw path: some routes take free-form text, such as the provider id in `/api/v1/providers/{provider_id}`. A raw path would add a new series for each value that a client sends. The template keeps the set of routes fixed.

Label values are escaped as the exposition format requires: backslash, double quote and newline.

## Example

```text
# HELP jarvis_tasks Tasks by status, across all users, read at scrape time.
# TYPE jarvis_tasks gauge
jarvis_tasks{status="cancelled"} 0
jarvis_tasks{status="completed"} 2
jarvis_tasks{status="failed"} 1
jarvis_tasks{status="queued"} 1
```

## Prometheus configuration

```yaml
scrape_configs:
  - job_name: jarvis
    scheme: https
    metrics_path: /api/v1/metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/jarvis-metrics-token
    static_configs:
      - targets: ["jarvis.example.internal:443"]
```

Keep the token in a file readable only by Prometheus. Serve the API over HTTPS. Production configuration already requires it.

## Security notes

- The token is compared with `hmac.compare_digest`. Both the presented value and the configured value are hashed with SHA-256 first, so the comparison time does not depend on the token's length.
- The token is never logged, returned or echoed. The output of the endpoint is tested not to contain it.
- An unset or empty token gives the same 404 as an unknown path, so the endpoint's existence is not revealed.
- A 401 has an empty body and reveals nothing about why the check failed.
- No user identifier is in the output. Tests assert that the owner's email, the other user's email, both user ids and the token do not appear in the body.
- A database error is logged with its exception class only. The response has no database detail.
- The route label is never a raw client path, so unauthenticated requests cannot grow the number of series.

## Limitations

- Request counters and the duration histogram are per process and reset on restart. With several API processes, scrape each one and aggregate in PromQL (`sum by (route, status)`).
- The database gauges and `jarvis_ai_requests_total` are read at each scrape with `GROUP BY` queries. `usage_records` grows with every provider call and nothing indexes the aggregate, so a scrape gets slower as the table grows. This is acceptable at personal scale and is not optimised.
- `jarvis_ai_requests_total` counts every operation (chat, stream, test, list_models). It is a count of recorded rows, so it can fall when a user is deleted and the rows cascade.
- The duration is the time to the response headers. A server-sent event stream is timed to its headers only, not to the end of the stream.
- Requests refused before routing are all labelled `unmatched`, not by the route they were aimed at.
- The route template comes from FastAPI 0.146's effective route context for nested routers (`scope["fastapi"]`), with `scope["route"]` as the fallback. Both are pinned by `fastapi==0.146.0`. A FastAPI upgrade must re-check the `route` label.
- The scheduler state is read from its loop task. A dedicated public status property would be cleaner.

## Tests

- Unit (`services/api/tests/unit/test_metrics_format.py`): escaping, route labels, histogram buckets (cumulative, inclusive), counter increments, and middleware recording with a fake clock.
- Integration (`services/api/tests/integration/test_metrics_endpoint.py`): 404 when unset (and for an empty token), 401 without a body, 200 with the right token and the exact content type, tasks and devices and AI gauges against seeded rows, workers gauge, route label without ids or query, refused requests, scrapes not counted, 503 on database failure, and no user email or id in the output.
