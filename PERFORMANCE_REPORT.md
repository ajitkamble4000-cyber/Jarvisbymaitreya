# Performance report for 1.1.0-rc.1

## Measurements available

- Backend full suite baseline: 1,161 passed in about 230 seconds on the managed runner.
- Web production build: passed; Vite emitted the existing bundle without a build error.
- Desktop production bundle and Windows ZIP cross-build: passed.
- Local Docker API and PostgreSQL reached healthy state and served authenticated requests.

These are build and reliability observations, not formal latency benchmarks. The environment has no controlled baseline for CPU, memory, provider latency, audio latency, cold Windows startup or Android lifecycle performance.

## Code-level findings

- Chat streaming uses short database sessions rather than holding a transaction while waiting for a provider.
- Cancellation paths are covered in backend and web tests. Android still blocks on `HttpURLConnection` and needs an explicit disconnect path.
- Metrics aggregate the growing usage table at scrape time; repository documentation already identifies that query as acceptable only at personal scale.
- The EQ assessment is a bounded pass over at most six recent user messages and fixed regular expressions. It adds no external call and no persisted write.
- Docker images use multi-stage builds, and the web runtime serves static assets through nginx.

## Recommended benchmarks before production

Measure p50/p95 startup, first-token and full-response latency; concurrent streams; API RSS/CPU; PostgreSQL query plans at realistic history sizes; Electron cold start/tray readiness on Windows; Android reconnect/cancellation; and ElevenLabs first-audio/interruption latency after that provider exists. Record hardware, dataset, provider/model and sample size with every result.
