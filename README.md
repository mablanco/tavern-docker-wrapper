# tavern-docker-wrapper

Run your [Tavern](https://taverntesting.github.io/) API test suites as synthetic monitoring.

If you already test your APIs with Tavern, the same `.tavern.yaml` files can keep checking
production: this container runs them on a schedule and adds

- a **status dashboard** that refreshes itself,
- **webhook alerts** (Slack-compatible) when a service goes down and when it recovers,
- **Prometheus metrics** for every service,
- a **one-shot mode** for CI pipelines.

Because the checks are Tavern tests, they can be multi-stage flows: log in, save the token,
call the API with it, create a resource and read it back. They can also validate JSON schemas,
call Python functions and talk MQTT or gRPC.

## When to use something else

This is a small, focused tool. If you don't have Tavern tests to reuse, and single requests with
conditions on status, body and latency are enough for you, a general purpose monitor will serve
you better: [Gatus](https://github.com/TwiN/gatus) (configured in YAML, with history, many
alerting providers and a tiny footprint) or [Uptime Kuma](https://github.com/louislam/uptime-kuma)
(configured from its web UI). tavern-docker-wrapper keeps only the latest result of each
service, and every check starts a `pytest` process, which is heavier than a plain HTTP request.

## Quick start

```sh
git clone https://github.com/mablanco/tavern-docker-wrapper.git
cd tavern-docker-wrapper
docker compose up --build
```

Then open <http://localhost:8080>. The demo checks a local
[go-httpbin](https://github.com/mccutchen/go-httpbin) with the tests in `examples/`, including a
multi-stage flow in `examples/tests/httpbin-flow.tavern.yaml`.

To check your own services, put a `services.yaml` and your Tavern files in a directory and mount
it as `/config`:

```sh
docker run -d -p 8080:8080 -v "$PWD/checks:/config:ro" ghcr.io/mablanco/tavern-docker-wrapper:latest
```

## How it works

Every `interval` seconds the wrapper runs, for each service, its Tavern file in a separate
`pytest` process, with `{base_url}` and the service `variables` injected through a Tavern
global config. The JUnit report of that run becomes the service status: it is healthy when
every test passes.

| Endpoint      | Content                                                     |
|---------------|-------------------------------------------------------------|
| `/`           | Dashboard, with the failing tests of each service           |
| `/api/status` | Same data as JSON                                           |
| `/metrics`    | Prometheus metrics                                          |
| `/healthz`    | Liveness of the wrapper itself (not of the checked services) |

## Configuration

The services file (`/config/services.yaml` by default) lists what to check:

```yaml
interval: 60        # seconds between two rounds of checks (min 5)
timeout: 60         # max seconds for all the tests of one service
concurrency: 4      # services checked in parallel

alerts:
  webhook_url: ${ALERT_WEBHOOK_URL:-}  # empty: alerts are only logged
  repeat_after: 3600                   # re-alert while still failing; 0 = never

services:
  - name: billing-api
    base_url: https://billing.example.com
    tests: tests/billing.tavern.yaml   # relative to this file
    variables:                         # optional, available as {name} in the tests
      tenant: acme
```

Any string can reference environment variables as `${NAME}` or `${NAME:-default}`, and a
missing variable without default is a startup error.

### Writing tests

Test files are plain Tavern files (one test per YAML document) whose name ends in
`.tavern.yaml`. Use `{base_url}` for the service URL and `{tavern.env_vars.NAME}` for secrets,
so they come from the container environment instead of the repository:

```yaml
---
test_name: invoices endpoint answers
strict:
  - json:off  # Tavern matches JSON bodies strictly by default
stages:
  - name: list invoices
    request:
      url: "{base_url}/invoices"
      method: GET
      headers:
        Authorization: "Bearer {tavern.env_vars.BILLING_TOKEN}"
      timeout: 5
    response:
      status_code: 200
      json:
        status: ok
```

See the [Tavern documentation](https://tavern.readthedocs.io/) for everything a test can do:
JSON schema validation, saving values between stages, retries, MQTT and gRPC...

### Environment variables

| Variable                | Default                 | Purpose                    |
|-------------------------|-------------------------|----------------------------|
| `TAVERN_WRAPPER_CONFIG` | `/config/services.yaml` | Services file              |
| `TAVERN_WRAPPER_HOST`   | `0.0.0.0`               | Listen address             |
| `TAVERN_WRAPPER_PORT`   | `8080`                  | Listen port                |
| `LOG_LEVEL`             | `INFO`                  | Python logging level       |

## Alerts

Alerts are posted as `{"text": "..."}`, which Slack, Mattermost, Rocket.Chat and most chat
incoming webhooks accept. A service that starts failing triggers one alert, listing the failed
tests; while it stays down the alert is repeated every `repeat_after` seconds, and its
recovery sends a final message. Without `webhook_url` the same messages only go to the log.

## Metrics

All metrics carry a `service` label:

| Metric                                         | Type    | Meaning                          |
|------------------------------------------------|---------|----------------------------------|
| `tavern_service_up`                            | gauge   | 1 if every test passed           |
| `tavern_service_tests`                         | gauge   | Tests run in the last check      |
| `tavern_service_tests_failed`                  | gauge   | Tests failed in the last check   |
| `tavern_service_check_duration_seconds`        | gauge   | Duration of the last check       |
| `tavern_service_last_check_timestamp_seconds`  | gauge   | Unix time of the last check      |
| `tavern_service_checks_total`                  | counter | Checks run, by `result`          |

A minimal alerting rule:

```yaml
- alert: ApiCheckFailing
  expr: tavern_service_up == 0
  for: 5m
```

## One-shot mode (CI)

`--once` runs every check a single time and exits with `0` if all of them passed, `1` if any
failed and `2` if the configuration is invalid:

```sh
docker run --rm -v "$PWD/checks:/config:ro" -e BILLING_TOKEN \
  ghcr.io/mablanco/tavern-docker-wrapper:latest --once
```

## Development

```sh
uv sync                      # Python 3.12+ and dev tools
uv run pytest                # unit + integration tests, coverage >= 70 %
uv run ruff format --check . && uv run ruff check .
```

The integration tests run real Tavern checks, including the files in `examples/`, against a
small local server that mimics the go-httpbin endpoints they use, so no network is needed.

## License

[GPL-3.0](LICENSE)
