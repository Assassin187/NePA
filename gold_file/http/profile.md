# HTTP fixed-length profile 1

Three manual inputs: specIR.json (Spec3.0), target.json (C99 server Target1.0), acceptance.json (Acceptance1.0). The target is byte-identical to the MQTT target.

This locally authored profile defines the bounded experiment, not a claim of full HTTP compliance. Requirement source_ref quotes below are this profile wording, not verbatim RFC quotations. Standard section references distinguish selected standard behavior from application/subset decisions.

Sources: [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html), [RFC 9112](https://www.rfc-editor.org/rfc/rfc9112.html). Chunked decoding is required by the full standard and is deliberately excluded here. No TLS/proxy/upgrade/cache/HTTP2 functionality.

## HTTP-SCOPE

Profile scope

DEFINITION: The application implements only the fixed-length HTTP/1.1 origin-server subset in this profile. It is not a full HTTP/1.1 receiver. Chunked, TLS, proxies, upgrade, caching and HTTP/2 are excluded.

## HTTP-STREAM

RFC 9112 sections 2, 9

MUST: Process an ordered TCP byte stream independently of receive boundaries. A request may arrive in many fragments, and one receive may contain multiple requests.

## HTTP-REQUEST-LINE

RFC 9112 section 3; subset policy

MUST: Parse method SP request-target SP HTTP/1.1 CRLF as the request line. Methods are case-sensitive. This subset accepts origin-form paths; malformed request lines produce 400 and connection close.

## HTTP-CRLF

RFC 9112 section 2.1

MUST: Use CRLF to terminate the request/status line and each header line, and an empty CRLF line to end headers. Parse octets, not Unicode messages. Only CRLF input is in this subset.

## HTTP-FIELD-NAMES

RFC 9110 section 5.1

MUST: Match HTTP header field names case-insensitively; ignore well-formed unrecognized header fields in this subset.

## HTTP-FIELD-SYNTAX

RFC 9112 section 5

MUST: Parse header fields as a token name immediately followed by colon and value. Reject whitespace before colon, missing colon and folded lines with 400 then close.

## HTTP-OWS

RFC 9112 section 5.1

MUST: Remove leading/trailing space or horizontal-tab whitespace from a header field value before interpreting it.

## HTTP-HOST

RFC 9112 section 3.2; nonempty-host subset

MUST: Require exactly one Host field containing a valid nonempty host with optional port; absent, repeated or invalid Host (including embedded whitespace) produces 400 and close.

## HTTP-BODY

RFC 9112 section 6.3

MUST: When Content-Length is present and no Transfer-Encoding exists, consume exactly that decimal number of octets as the request body. Treat body bytes as opaque, including NUL and CRLF. Without either field, the request body has zero bytes.

## HTTP-LENGTH-INVALID

RFC 9112 section 6.3

MUST: Content-Length contains one or more decimal digits only after trimming optional whitespace. Negative, mixed-character or overflowing lengths produce 400 then close; do not process their body as subsequent requests.

## HTTP-LENGTH-CONFLICT

RFC 9112 section 6.3; permitted rejection policy

MUST: Reject conflicting Content-Length values with 400 then close. This subset also rejects repeated identical Content-Length fields and comma-separated Content-Length lists with 400 then close.

## HTTP-INCOMPLETE-WAIT

RFC 9112 sections 2, 6.3

MUST: Keep incomplete request lines, headers and bodies buffered until complete or EOF. Do not emit an early successful response or discard a valid incomplete prefix. Preserve any bytes after the complete body for the next request.

## HTTP-TRUNCATED

RFC 9112 section 8; close-without-response policy

MUST: If the client sends EOF before a complete request/body arrives, close that connection without sending a response in this profile. Do not treat a partial body as complete.

## HTTP-RESPONSE

RFC 9112 section 4

MUST: Send responses using HTTP/1.1 SP three-digit-status SP reason-phrase CRLF followed by headers, empty CRLF and the allowed body.

## HTTP-RESPONSE-LENGTH

RFC 9110 section 8.6; profile framing

MUST: Every response in this profile includes exactly one Content-Length giving its body length in octets. Responses never use Transfer-Encoding. For HEAD this length describes the corresponding GET representation while no body is sent.

## HTTP-HEAD

RFC 9110 section 9.3.2

MUST NOT: Never send a response body for HEAD, including errors. Its Content-Length describes the response that GET would have returned for the same path.

## HTTP-PERSISTENCE

RFC 9112 section 9.3

MUST: Keep a successfully processed HTTP/1.1 connection open for subsequent requests unless Connection: close was requested. Maintain parsing state separately per connection.

## HTTP-PIPELINE

RFC 9112 section 9.3.2

MUST: For multiple requests on one connection, send complete responses in request order; an incomplete trailing request must not lose earlier complete requests or their responses.

## HTTP-CLOSE

RFC 9112 section 9.6

MUST: For Connection: close (case-insensitive token), complete the response, include Connection: close, and close the connection without processing further requests.

## HTTP-BAD-REQUEST

RFC 9112 sections 2.2, 5; profile error policy

MUST: Malformed requests in the selected syntax produce a 400 response with explicit Content-Length and Connection: close, followed by connection closure. Complete all response writes before close.

## HTTP-ISOLATION

Profile application behavior

MUST: A malformed or truncated client connection must not terminate the listening server or prevent other clients from completing requests. Release each disconnected connection state.

## HTTP-SUBSET-TRANSFER

Profile limitation; RFC 9112 section 7.1 requires chunked for full receivers

MUST: Reject any Transfer-Encoding field with 400 and Connection: close, including Transfer-Encoding together with Content-Length. Do not parse chunked data. This is a deliberate subset limitation, not full HTTP/1.1 compliance.

## HTTP-APP-GET

Application route contract

MUST: GET / returns status 200 with exact body bytes nepa followed by LF (five octets) and Content-Length: 5.

## HTTP-APP-HEAD

Application route contract

MUST: HEAD / returns 200 with Content-Length: 5 and no response body.

## HTTP-APP-ECHO

Application route contract

MUST: POST /echo returns 200 with the request body byte-for-byte, including empty and binary bodies, with the corresponding Content-Length. No transformation is permitted.

## HTTP-APP-ROUTES

Application route contract

MUST: For implemented methods GET, HEAD and POST, any other method/path combination returns 404 with zero body bytes and Content-Length: 0.

## HTTP-APP-METHODS

Application route contract

MUST: Syntactically valid methods other than GET, HEAD and POST return 501 with zero body bytes and Content-Length: 0.
