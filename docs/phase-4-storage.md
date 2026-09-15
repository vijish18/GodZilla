# Phase 4 storage and replay operations

## Isolated tests

```powershell
.venv\Scripts\python.exe scripts/test_postgres.py
```

This starts a uniquely named PostgreSQL container, generates credentials in memory, runs the
complete unit/integration/replay suite, and removes only that test project on exit. It never
uses an existing application database. Docker must be running. `make ci` and GitHub Actions use
the same runner. `pytest -m 'not postgres'` runs offline checks; it does not prove migrations.

## Local development database

Set `GODZILLA_POSTGRES_PASSWORD` in your shell from your local secret store, then run:

```text
docker compose --profile storage up -d postgres
```

The database is `godzilla`, user `godzilla`, and its port defaults to loopback `55432` (override
`GODZILLA_POSTGRES_PORT`). Data is retained in the Compose `postgres_data` volume. Set
`GODZILLA_DATABASE_URL` to a `postgresql+psycopg` URL for that database using URL-encoded
credentials, then run:

```text
python -m alembic upgrade head
python -m alembic current
```

No credentials belong in `alembic.ini`, tracked configuration or shell history. The example env
file contains blank placeholders only. The Python application does not load `.env` implicitly;
Compose follows its own standard interpolation behavior. Migrations are explicit, never run
silently by the trading process. The Docker image includes Alembic and migrations.

## Event replay

```text
python -m godzilla.core.events.cli data/events/ordered-session.jsonl
```

The fixture is synthetic and checks receipt-time ties and a delayed occurrence timestamp. The
CLI emits a deterministic count, final replay clock and output hash. Database events can be read
through `PostgresRepository.read_events()` and passed to `ReplayEngine.run(events, consumer)`.
Consumers should use durable checkpoints/idempotency when their side effects are introduced.

## Transaction use

Construct the engine with `make_engine`, inject `PostgresRepository` into `EventJournal`, and
publish a validated envelope with a stable `idempotency_key`. The journal emits downstream only
after a successful commit. Optional `SystemStateSnapshot` must reference the same event and
receipt instant. Instrument imports use `EventJournal.save_instruments` for the same failure
latch. Reads return domain models, not ORM objects.

On critical write/commit uncertainty, the journal blocks subsequent writes and reports blocking
health. Do not recreate it merely to bypass the latch: first reconcile stored identities and
recover through replay under the application's future controlled recovery procedure. There is
no broker connectivity, strategy implementation or trading enablement in this phase.
