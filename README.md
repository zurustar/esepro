# esepro

A very simple SIP (Session Initiation Protocol) proxy server.

`esepro` is a deliberately minimal SIP Stateless Proxy + Registrar
(Python 3, standard library only, single file) intended for education,
experimentation, and interoperability testing — **not** production use.

Its design is defined as much by what it *omits* as by what it does.
See [DESIGN.md](DESIGN.md) for the **Non-Goals & Design Rationale** —
what is intentionally cut, why each cut is safe within scope, and the
known defects that fell outside that rationale.
