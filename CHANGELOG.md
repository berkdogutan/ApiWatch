# Changelog

All notable changes to APIWatch are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

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

[1.0.0]: docs/releases/v1.0.0.md
