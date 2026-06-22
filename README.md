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
what is intentionally cut, why each cut is safe within scope, and the
known defects (now fixed) that fell outside that rationale.
