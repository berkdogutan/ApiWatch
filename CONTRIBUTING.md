# Contributing to APIWatch

Thanks for your interest. APIWatch is a small project maintained by one person. Focused
bug reports and pull requests are the most helpful contributions.

## Scope

APIWatch aims to stay a lightweight, single-process tool for local use. Changes that keep
it simple are favoured. Larger additions (authentication, notification channels,
alternative databases) are best discussed in an issue first.

## Development setup

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # create .venv from uv.lock
uv run apiwatch                           # http://127.0.0.1:8000
uv run python -m demo.mock_api            # optional: local demo API on :8001
```

To monitor the demo API, start APIWatch with `APIWATCH_ALLOW_LOCALHOST=true` and run
`uv run python -m demo.seed`. See the README for details.

## Checks to run before opening a pull request

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

CI runs the same commands on Python 3.12 and 3.13.

Guidelines:
- Add or update tests for behaviour changes. Tests must not use the network: use
  `httpx.MockTransport` for HTTP and the fake resolver in `tests/conftest.py`. Real DNS
  lookups and outbound connections fail on purpose.
- Keep changes that touch URL validation or outbound requests conservative, and explain
  the security impact in the pull request.
- If you change dependencies, update `uv.lock` with `uv lock` and commit it.
- If you change behaviour, update the README (and `CHANGELOG.md` under an "Unreleased"
  heading).

## Commits and pull requests

- Keep each pull request to one topic and describe why the change is needed.
- Write commit messages in the imperative mood, for example "Fix incident duration for
  open incidents".
- Make sure the checks above pass.

## Reporting bugs

Open an issue with the steps to reproduce, what you expected, and what happened. Include
your OS, Python version and the relevant log output.

Please **do not** report security vulnerabilities in public issues. See
[SECURITY.md](SECURITY.md).
