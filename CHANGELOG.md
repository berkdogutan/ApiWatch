# Changelog

All notable changes to APIWatch are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [1.0.1] - 2026-10-28

Fixes from a full code review. One change needs action from API clients: approving JSON
changes now requires the reviewed `pending_fields` (see Changed).

### Changed
- Approving JSON changes requires the reviewed `pending_fields` and is rejected with
  `409` if a newer response replaced them. This is incompatible with 1.0.0 API clients;
  the dashboard sends the fields itself.
- JSON inspection with `HEAD` is rejected with `422` (HEAD responses have no body).
  Monitors saved this way by 1.0.0 still load; fix them on the next edit.
- JSON numbers are a single `number` type, so `19.99` → `20` is no longer a type change.
  Baselines saved by 1.0.0 are read accordingly.
- SQLite runs in WAL mode: while APIWatch runs, `apiwatch.db-wal` and `apiwatch.db-shm`
  sit next to the database. Keep `data/` on a local disk, and stop APIWatch before
  copying or moving the database.

### Fixed
- URLs the HTTP client cannot send (for example a DEL character, `i❤.ws`, `010.0.0.1`)
  were accepted and then crashed every check; they are now rejected when saved, and a
  check can no longer raise for them.
- Deeply nested JSON no longer crashes a check; it is reported as an `invalid_json`
  warning.
- Database work no longer runs on the event loop, and SQLite now uses WAL mode, so a slow
  write or a long dashboard query can no longer inflate other checks' response times or
  make them time out.
- A check's result is discarded instead of being stored under a monitor that was edited,
  or deleted and re-created with the same id, while the check was running.
- Editing a monitor during its check no longer skips its next scheduled run.
- A monitor whose host stopped resolving can be paused and edited again (`PUT` only
  re-validates a changed URL).
- Turning JSON inspection off no longer deletes the baseline, so changes made meanwhile
  are reported instead of silently accepted.
- Errors while reading a body for JSON inspection are warnings and no longer turn a
  `200` into DOWN.
- If an address does not answer within its share of the timeout, the next validated
  address is tried.
- `localhost` resolves to all of its loopback addresses, so servers listening only on
  `::1` are reachable with `APIWATCH_ALLOW_LOCALHOST=true`.
- Short outages stay visible in charts of long windows.
- Monitors are spread over the first 30 seconds, so IDs such as 1, 11 and 21 no longer
  start at the same instant.
- `python -m app.database` created no tables; it now does.
- In-memory SQLite URLs work across threads.
- Dashboard: refreshes requested during a load are queued instead of dropped (no more
  mislabelled windows), polling pauses in hidden tabs, unchanged chart data is not
  refetched, the incident count comes from the server totals, the enable/disable
  toggle starts from the server's current configuration, the JSON panel no longer
  claims a match when the latest response could not be compared, and "Add monitor"
  works on the 404 page.
- Out-of-range ids return `422` instead of `500`.

### Security
- The HTTP client no longer stores cookies. Because requests are sent to pinned IP
  addresses, a cookie set by one monitored host could be sent to another hostname on
  the same address.
- International host names are resolved by the same IDNA 2008 name that is sent as
  `Host` and TLS SNI. Before, Python's IDNA 2003 encoding could pin a request to a
  different domain's address (`straße.de` → `strasse.de`).
- Protocol and decoding errors no longer store text that can quote response bytes.
- Response bodies are requested uncompressed and read raw, so `max_response_bytes`
  limits what is decompressed as well.
- IPv6 ranges that embed IPv4 addresses (6to4, Teredo, NAT64, IPv4-compatible) are
  blocked explicitly; on Python 3.12.0–3.12.3, 6to4 counted as public.
- DNS lookups run in a dedicated thread pool, so hung lookups cannot starve other work.

### Tests
- 128 tests (previously 95). The network guard now also blocks asyncio connections to
  IP literals, and `APIWATCH_*` variables from the shell no longer affect the suite.

## [1.0.0] - 2026-10-27

First public release.

### Added
- Monitor management (create, edit, delete, enable/disable, manual check) from the
  dashboard and the REST API. `PUT` replaces the whole configuration and requires every
  field.
- Asynchronous health checks with HTTPX. Results are classified as UP, DEGRADED (slower
  than the monitor's threshold) or DOWN, with an error category and a sanitized message.
- Automatic scheduling with APScheduler 3 (`AsyncIOScheduler`). Jobs are kept in sync with
  monitor changes and rebuilt from SQLite on startup. Each monitor runs at most one check
  at a time, at most 20 checks run concurrently, and monitors start a few seconds apart so
  simultaneous checks don't inflate each other's response times.
- Analytics computed in SQL from stored checks: uptime, average, minimum and maximum
  response time, last successful check. Windows: 24 hours, 7 days or all data.
- Incident tracking: opened after 2 consecutive DOWN checks (configurable), resolved on
  recovery.
- Optional JSON change detection on top-level keys and value types, with explicit
  approval of baseline updates.
- Dark, responsive dashboard (Jinja2, vanilla JavaScript, local SVG charts) with a
  details page per monitor.
- Deterministic local mock API (`demo.mock_api`) and a seed script (`demo.seed`) for an
  offline demo.
- Test suite (95 tests, no network access), Ruff configuration and a GitHub Actions
  workflow for Python 3.12 and 3.13.

### Security
- Monitor targets are restricted to `http`/`https` and to public IP addresses. Loopback
  targets are allowed only with the development-only `APIWATCH_ALLOW_LOCALHOST` setting.
  An optional hostname allowlist is available (`APIWATCH_ALLOWED_TARGET_HOSTS`).
- Targets are re-validated before every check, and the request connects to the
  validated address instead of resolving the name again.
- Redirects are not followed. Proxy settings and `.netrc` are ignored. Responses are read
  only up to a size limit, and the timeout is a deadline for the whole check.
- The local server rejects unknown `Host` headers (`APIWATCH_TRUSTED_HOSTS`) and
  cross-origin browser writes, and forbids framing. It binds to `127.0.0.1` by default.

### Known limitations
- No authentication. Intended for a single local user, not for exposure to untrusted
  networks.
- Run a single Uvicorn worker; multiple workers would duplicate scheduled checks.
- No notifications, history retention or schema migrations.
- See the README section "Security and deployment limitations" for the full list.

[1.0.1]: docs/releases/v1.0.1.md
[1.0.0]: docs/releases/v1.0.0.md
