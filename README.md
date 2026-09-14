# Project GodZilla

Project GodZilla is a production-oriented, scientific, multi-alpha intraday long/short
quantitative trading platform for liquid NSE India instruments. Phase 1 supplies only the
engineering foundation. It contains no broker connectivity, market-data integration, trading
logic, portfolio decisions, or executable risk decisions.

The mandatory future flow is:

`market data -> features -> market-state router -> independent alpha engines -> alpha ensemble -> portfolio allocator -> independent risk engine -> execution engine -> broker adapter`

Alpha modules never call brokers. Portfolio and independent risk approval are required before
an execution plan can reach a broker adapter. Risk-critical uncertainty fails closed.

## Phase 1 capabilities

- Python 3.12 package using a `src` layout.
- Typed Pydantic v2 configuration with base, mode, compliance, and environment layers.
- PAPER is the default. LIVE configuration fails until deployment, leadership, compliance,
  credential, and safety prerequisites are explicit.
- Timezone-aware injected clocks, strongly separated UUID internal IDs, structured JSON logs
  with redaction, health aggregation, and an explicit lifecycle state machine.
- Local CLI, unit tests, coverage gate, static checks, container skeleton, and CI without deploy.

The numeric settings in `config/base.yaml` come from Architecture v1.0. They are conservative
research/paper starting values, are not optimized, and are not evidence of expected returns.

## Configuration

Effective precedence is:

1. `config/base.yaml`
2. mode profile: `research.yaml`, `paper.yaml`, or `production.yaml`
3. `config/compliance.yaml`
4. environment variables prefixed with `GODZILLA_`, using `__` for nesting

Examples include `GODZILLA_MODE=RESEARCH`, `GODZILLA_LOGGING__LEVEL=DEBUG`, and
`GODZILLA_RISK__MAX_POSITIONS=4`. Secrets are accepted only through the environment, excluded
from configuration serialization and hashing, and represented by blank placeholders in
`.env.example`. The application does not automatically read `.env` files.

Check the default effective configuration:

```bash
godzilla config-check --config-dir config
```

The shipped `production.yaml` is intentionally blocked. Deployment approval and LIVE trading
enablement are separate fields. Current compliance and broker-route facts must be supplied and
verified before a LIVE configuration can validate.

## Development

```bash
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
make ci
```

On Linux/macOS use `.venv/bin/python`. The Makefile provides `install`, `lint`, `format`,
`typecheck`, `test`, `coverage`, `run`, `config-check`, `docker-build`, `docker-up`,
`docker-down`, and `ci` commands.

Container development uses `docker compose up --build`. The container runs as an unprivileged
user and contains no credentials. Stop it with Ctrl+C or `docker compose down`; SIGINT and
SIGTERM cause explicit STOPPING and STOPPED lifecycle transitions.

## Architecture records

- [`ADR-0001-foundation-stack.md`](docs/adr/ADR-0001-foundation-stack.md)

Phase 2 has not started.
