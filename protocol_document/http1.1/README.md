# HTTP/1.1 RFC document set

This directory contains the HTTP documents currently used by the RFC→Spec scope.
The files are unmodified plain-text publications downloaded from the official
RFC Editor on 2026-09-13.

| File | RFC | Scope | Official source |
|---|---:|---|---|
| `rfc9110-http-semantics.txt` | 9110 | HTTP architecture, methods, fields, status codes, authentication, conditional and range requests | <https://www.rfc-editor.org/rfc/rfc9110.txt> |
| `rfc9112-http-1.1.txt` | 9112 | HTTP/1.1 message syntax, framing, connection management, transfer codings, and upgrade behavior | <https://www.rfc-editor.org/rfc/rfc9112.txt> |

RFC 9112 is the HTTP/1.1 version-specific specification and relies on the
semantics defined by RFC 9110. RFC 9111 caching is outside the current extraction
scope and is therefore not retained. Obsolete RFC 7230–7235 and RFC 2616 copies
are not duplicated here.

## SHA-256

```text
21c1cdce6ab0e5509b04d84a28000836c7a087cf786efe6f04877ebfff47232a  rfc9110-http-semantics.txt
e4f426bac6206b67fdf9e0da826154f70588db2133a0a86b15cde4ff725d8937  rfc9112-http-1.1.txt
```
