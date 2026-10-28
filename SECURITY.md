# Security policy

## Supported versions

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |
| < 1.0 | No |

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **private vulnerability
reporting**: open the repository's **Security** tab and choose **Report a
vulnerability**
([direct link](https://github.com/berkdogutan/ApiWatch/security/advisories/new)).
Do not open a public issue for security problems.

Please include:
- the affected version or commit;
- steps to reproduce, or a proof of concept;
- the impact you expect, for example which network locations become reachable.

This is a solo-maintained project, so responses are on a best-effort basis. I will
acknowledge your report, keep you updated, and credit you in the release notes unless
you prefer otherwise.

## Security model

APIWatch is a **single-user application for local use**:

- It has **no authentication**. Anyone who can reach the server can read monitoring data,
  create monitors, and make the server send `GET` and `HEAD` requests to public addresses.
- It binds to `127.0.0.1` by default. Do not expose it to untrusted networks or the
  public internet.
- Run it with a single Uvicorn worker.

### Protections in place

- **Outbound requests:**
  - only `http` and `https`, with no credentials in URLs and no redirects followed;
  - URLs are parsed by the same parser that sends the request, and international host
    names are resolved by their IDNA 2008 name, the one sent as `Host` and TLS SNI;
  - every resolved address must be public, which rules out loopback, private,
    link-local/metadata, CGNAT, multicast, reserved and IPv6 transition ranges (listed
    explicitly, independent of the Python version);
  - targets are re-validated before each check, and the request connects to the
    validated address;
  - cookies are never stored or sent, and proxy settings and `.netrc` are ignored;
  - bodies are requested uncompressed and read only up to a size limit (compressed
    bodies are inflated with the same limit), and the timeout is a deadline for the
    whole check.
- **Requests to APIWatch:**
  - unknown `Host` headers are rejected (`APIWATCH_TRUSTED_HOSTS`);
  - cross-origin browser writes are rejected;
  - pages cannot be framed.
- **Data:** no response bodies, authorization headers or secrets are stored; error
  messages that could quote response bytes are replaced by fixed text. JSON change
  detection keeps only top-level key names and value types.

### Known limitations (not considered vulnerabilities)

- **Public relays.** A public host can still relay traffic to internal systems (open
  proxies, port forwards, load balancers in front of private networks). APIWatch cannot
  detect this.
- **Allowlists.** `APIWATCH_ALLOWED_TARGET_HOSTS` restricts which names can be monitored,
  not which public addresses those names point to. It is not a complete defence against
  SSRF.
- **Localhost.** With `APIWATCH_ALLOW_LOCALHOST=true`, every service on the loopback
  interface can receive requests. Use this setting only for local development.
- **Plain-text URLs.** Monitor URLs are stored and displayed in plain text. Do not put
  secrets in them.

Reports about deployments that ignore the model above (for example, exposing the
unauthenticated server publicly) are out of scope. Bypasses of the protections listed
above are in scope.
