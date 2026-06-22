# esepro

A very simple SIP (Session Initiation Protocol) proxy server.

`esepro` is a deliberately minimal SIP Stateless Proxy + Registrar
(Python 3, standard library only, single file) intended for education,
experimentation, and interoperability testing — **not** production use.

## Purpose: a test peer for SIP software

`esepro` exists to be the *counterpart* when you test other SIP software.
You cannot exercise a SIP implementation in isolation — something has to
receive its messages and react. `esepro` is that something: a small,
predictable peer that accepts SIP requests, registers contacts, and
proxies or answers, so the software under test has someone to talk to.

## Hackable by design

A test often needs a behavior `esepro` doesn't have yet — a particular
response, an injected error, a tweaked header. That is why it is a single
readable script in a scripting language (Python) rather than a compiled
binary: **you are expected to edit it.** Readability is prioritized over
feature completeness precisely so that adding the one behavior your test
needs stays a small, local change.

## Design rationale

Its design is defined as much by what it *omits* as by what it does.
See [DESIGN.md](DESIGN.md) for the **Non-Goals & Design Rationale** —
what is intentionally cut and why each cut is safe within scope.

## Usage

```sh
python3 esepro.py <domain> <listen-ip> <listen-port> [routes-file]
# e.g.
python3 esepro.py example.com 0.0.0.0 5060
python3 esepro.py example.com 0.0.0.0 5060 routes.txt
```

### Static routes (for devices that don't REGISTER)

A request addressed to an external host is relayed by its Request-URI
without any registration. But to reach a device *through this domain*
(`sip:bob@example.com`), the proxy needs an AOR→Contact mapping — which
normally only arrives via REGISTER. For devices that never register,
provide them in an optional routes file: one `AOR Contact` per line,
`#` comments and blank lines ignored.

```
# routes.txt — AOR  Contact
bob    sip:bob@10.0.0.50:5080
carol  sip:carol@10.0.0.51:5060
```

Entries are loaded into the location service at startup; a later
REGISTER for the same AOR overrides the static entry.

## Tests

Unit tests for the message parsing and proxy logic live in a separate
file, `test_esepro.py`, so that `esepro.py` itself stays minimal. They
use only the standard library (`unittest`):

```sh
python3 -m unittest        # or: python3 -m unittest -v
```
