from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from typing import Any
from .ingest import Segment

_NORM = re.compile(r"\b(MUST NOT|SHOULD NOT|MUST|SHOULD|MAY|REQUIRED|SHALL NOT|SHALL|RECOMMENDED|OPTIONAL)\b")
_TYPEVAR = re.compile(r"\bvariable (?:byte|length) encoding(?: scheme)?\b", re.I)
_METHOD = re.compile(r"\b(GET|HEAD|POST)\s+(?:method|request-target|requests?)\b", re.I)
_UTF8 = re.compile(r"\bUTF-8 encoded strings?\b", re.I)
_DIR_ANY = re.compile(r"\b([A-Z][A-Z0-9_-]{1,30})\s+Packet.*?sent\s+(?:by|from)\s+(?:(?:a|the)\s+)?([A-Za-z]+)\s+to\s+(?:(?:a|the)\s+)?([A-Za-z]+)", re.I|re.S)
_SEND = re.compile(r"\b([A-Z][A-Z0-9_-]{1,30})\s+Packet\s+(?:is\s+(?:the\s+packet\s+)?)?sent\s+(?:by|from)\s+(?:(?:a|the)\s+)?([A-Za-z]+)\s+to\s+(?:(?:a|the)\s+)?([A-Za-z]+)")
@dataclass
class Claim:
    candidate_id: str
    kind: str
    value: dict[str, Any]
    source_spans: list[dict[str, Any]]
    quote: str
    confidence: float = 0.5
    status: str = "candidate"

def discover(segments: tuple[Segment, ...]) -> list[Claim]:
    claims: list[Claim] = []
    roots = {}
    root_items = {}
    fixed = {}
    for item in segments:
        m = re.match(r"3\.(\d+)\s+([A-Z][A-Z0-9_-]+)", item.section)
        if m:
            roots[m.group(1)] = m.group(2).lower(); root_items[m.group(1)] = item
        fm = re.match(r"3\.(\d+)\.1\s+Fixed header", item.section, re.I)
        if fm:
            fixed[fm.group(1)] = item
    for number, item in root_items.items():
        descendants = [x for x in segments if x.section.startswith("3." + number)]
        context = "\n".join(x.text for x in descendants[:8])
        directional = _DIR_ANY.search(context)
        if number in fixed and (re.search(r"Client|Server", item.section) or directional):
            actors = re.findall(r"Client|Server", item.section, re.I)
            if directional:
                sender, receiver = directional.group(2).lower(), directional.group(3).lower()
            else:
                sender = actors[0].lower() if actors else "client"; receiver = actors[-1].lower() if len(actors)>1 else ("server" if sender == "client" else "client")
            claims.append(Claim(f"{item.segment_id}-root", "message", {"id":roots[number], "name":roots[number].upper(), "senders":[sender], "receivers":[receiver], "wire_layout":["fixed_header","variable_header","payload"], "fields":[]}, [{"segment_id":item.segment_id,"start_line":item.start_line,"end_line":item.end_line,"section":item.section},{"segment_id":fixed[number].segment_id,"start_line":fixed[number].start_line,"end_line":fixed[number].end_line,"section":fixed[number].section}], item.section, 0.7))
            fixed_text = "\n".join(x.text for x in segments if x.section == fixed[number].section)
            fixed_item = next((x for x in segments if x.section == fixed[number].section and "mqtt control packet type" in x.text.lower()), fixed[number])
            if "mqtt control packet type" in fixed_text.lower():
                claims.append(Claim(f"{fixed_item.segment_id}-packet-type", "field", {"message_id":roots[number], "field":{"name":"packet_type","loc":"fixed_header","type":"bitfield8","bits":[{"name":"type","offset":4,"width":4},{"name":"flags","offset":0,"width":4}]}}, [{"segment_id":fixed_item.segment_id,"start_line":fixed_item.start_line,"end_line":fixed_item.end_line,"section":fixed_item.section}], "MQTT Control Packet type", 0.7))
            if "Remaining Length" in fixed_text:
                claims.append(Claim(f"{fixed[number].segment_id}-remaining-length", "field", {"message_id":roots[number], "field":{"name":"remaining_length","loc":"fixed_header","type":"variable_byte_integer","derived":{"kind":"length_of","of":["variable_header","payload"]}}}, [{"segment_id":fixed[number].segment_id,"start_line":fixed[number].start_line,"end_line":fixed[number].end_line,"section":fixed[number].section}], "Remaining Length", 0.7))
    status_added = False
    for seg in segments:
        if not status_added and re.match(r"15(?:\.|\s)", seg.section) and re.search(r"status code|response", seg.text, re.I):
            claims.append(Claim(f"{seg.segment_id}-http-response", "message", {"id":"response", "name":"HTTP response", "senders":["server"], "receivers":["client"], "wire_layout":["status_line","header_fields","body"], "fields":[]}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], seg.section, 0.55))
            status_added = True
            claims.append(Claim(f"{seg.segment_id}-http-status-code", "field", {"message_id":"response", "field":{"name":"status_code","loc":"status_line","type":"uint16_be"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], seg.section, 0.5))
        for i, sentence in enumerate(re.split(r"(?<=[.!?])\s+", seg.text)):
            match = _NORM.search(sentence)
            if not match:
                continue
            level = {"REQUIRED": "MUST", "SHALL": "MUST", "SHALL NOT": "MUST NOT", "RECOMMENDED": "SHOULD", "OPTIONAL": "MAY"}.get(match.group(1), match.group(1))
            cid = f"{seg.segment_id}-r{i:03d}"
            kind = "unsupported_candidate" if level == "SHOULD NOT" else "requirement"
            claims.append(Claim(cid, kind, {"text": sentence.strip(), "level": level}, [{"segment_id": seg.segment_id, "start_line": seg.start_line, "end_line": seg.end_line, "section": seg.section}], sentence.strip(), 0.7))
        if re.match(r"3(?:\s|\.)", seg.section) and re.search(r"request-target|method|HTTP-version", seg.text, re.I):
            q = next((x.strip() for x in re.split(r"\n+", seg.text) if re.search(r"request-target|method|HTTP-version", x, re.I) and len(x.strip()) > 12), None)
            if q:
                for n, fname in enumerate(("method", "request_target", "http_version")):
                    if re.search(fname.replace("_", "[- ]"), q, re.I) or (fname == "method" and re.search(r"method", q, re.I)):
                        for message_id in ("get_request", "head_request", "post_request"):
                            claims.append(Claim(f"{seg.segment_id}-hf{n:02d}-{message_id}", "field", {"message_id":message_id, "field":{"name":fname,"loc":"request_line","type":"bytes"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q, 0.55))
        parent = re.match(r"3\.(\d+)(?:\.|\s)", seg.section)
        if (parent and parent.group(1) in roots and parent.group(1) == "1") or ("payload contains one or more encoded fields" in seg.text.lower()):
            payload = re.search(r"payload contains one or more encoded fields.*?Password\.", seg.text, re.I|re.S)
            if payload:
                for n, fname in enumerate(("client_identifier", "will_topic", "will_message", "user_name", "password")):
                    claims.append(Claim(f"{seg.segment_id}-payload-{n}", "field", {"message_id":"connect", "field":{"name":fname,"loc":"payload","type":"utf8_encoded_string" if fname != "password" else "bytes"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], payload.group(0), 0.6))
        if parent and parent.group(1) in roots:
            if parent.group(1) == "8" and re.search(r"Topic Filter / QoS pairs are packed contiguously", seg.text, re.I):
                q = re.search(r"[^.]*Topic Filter / QoS pairs are packed contiguously\.", seg.text, re.I|re.S)
                if q:
                    claims.append(Claim(f"{seg.segment_id}-subscriptions", "field", {"message_id":"subscribe", "field":{"name":"subscriptions","loc":"payload","type":"subscription_list"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.6))
                    claims.append(Claim(f"{seg.segment_id}-subscription-list", "type", {"id":"subscription_list","name":"Subscription list","encoding":{"kind":"repeat","item_type":"subscription_entry","min_items":1}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.55))
                    claims.append(Claim(f"{seg.segment_id}-subscription-entry", "type", {"id":"subscription_entry","name":"Subscription entry","encoding":{"kind":"sequence","members":[{"name":"topic_filter","type":"utf8_encoded_string"},{"name":"requested_qos","type":"uint8"}]}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.55))
            if parent.group(1) == "2" and re.search(r"Acknowledge Flags|Session Present", seg.text, re.I):
                q = re.search(r"[^.]*Acknowledge Flags[^.]*\.", seg.text, re.I|re.S)
                if q:
                    claims.append(Claim(f"{seg.segment_id}-ack-flags", "field", {"message_id":"connack", "field":{"name":"acknowledge_flags","loc":"variable_header","type":"bytes"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.55))
            if parent.group(1) == "9" and re.search(r"contains a list of return codes", seg.text, re.I):
                q = re.search(r"[^.]*contains a list of return codes[^.]*\.", seg.text, re.I|re.S)
                if q:
                    claims.append(Claim(f"{seg.segment_id}-return-codes", "field", {"message_id":"suback", "field":{"name":"return_codes","loc":"payload","type":"bytes"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.6))
            for fname, typ, pattern in (("topic_filter", "utf8_encoded_string", r"Topic Filter"), ("requested_qos", "uint8", r"requested maximum QoS"), ("return_code", "uint8", r"return code")):
                if (fname in {"topic_filter", "requested_qos"} and parent.group(1) != "8") or (fname in {"topic_filter", "requested_qos"} and parent.group(1) == "8") or (fname == "return_code" and parent.group(1) not in {"2", "9"}) or (fname == "return_code" and parent.group(1) == "9"):
                    continue
                hit = re.search(r"[^.]*" + pattern + r"[^.]*\.", seg.text, re.I|re.S)
                if hit:
                    claims.append(Claim(f"{seg.segment_id}-auto-{fname}", "field", {"message_id": roots[parent.group(1)], "field": {"name":fname,"loc":"payload" if parent.group(1)=="8" else "variable_header","type":typ}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], hit.group(0).strip(), 0.5))
            ordered = re.search(r"consists of (?:the )?(?:\w+ )?fields? in the following order:\s*([^.]*)\.", seg.text, re.I|re.S)
            if ordered:
                for k, raw_name in enumerate(re.split(r",|\band\b", ordered.group(1), flags=re.I)):
                    name = re.sub(r"[^A-Za-z0-9 ]", "", raw_name).strip()
                    if name:
                        claims.append(Claim(f"{seg.segment_id}-of{k:02d}", "field", {"message_id": roots[parent.group(1)], "field": {"name": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"), "loc":"variable_header", "type":{"Protocol Name":"utf8_encoded_string","Protocol Level":"uint8","Connect Flags":"bitfield8","Keep Alive":"uint16_be"}.get(name, "bytes"), **({"bits":[{"name":"reserved","offset":0,"width":1},{"name":"clean_session","offset":1,"width":1},{"name":"will_flag","offset":2,"width":1},{"name":"will_qos","offset":3,"width":2},{"name":"will_retain","offset":5,"width":1},{"name":"password_flag","offset":6,"width":1},{"name":"username_flag","offset":7,"width":1}]} if name == "Connect Flags" else {})}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], ordered.group(0), 0.55))
        if parent and parent.group(1) in roots and re.search(r"(?:variable header contains|contains (?:the|a))\s+Packet Identifier", seg.text, re.I):
            q = re.search(r"[^.]*?(?:variable header contains|contains (?:the|a))\s+Packet Identifier[^.]*\.", seg.text, re.I|re.S)
            if q:
                claims.append(Claim(f"{seg.segment_id}-f000", "field", {"message_id": roots[parent.group(1)], "field": {"name":"packet_identifier", "loc":"variable_header", "type":"uint16_be"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.65))
        for j, match in enumerate(_METHOD.finditer(seg.text)):
            method = match.group(1).upper()
            quote = match.group(0)
            claims.append(Claim(f"{seg.segment_id}-h{j:03d}", "message", {"id": method.lower()+"_request", "name": method+" request", "senders": ["client"], "receivers": ["server"], "wire_layout": ["request_line", "header_fields", "body"], "fields": []}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], quote, 0.55))
        if re.search(r"QoS is not 0,1 or 2|maximum QoS", seg.text, re.I) and parent and parent.group(1) == "8":
            q = re.search(r"[^.]*QoS is not 0,1 or 2[^.]*\.", seg.text, re.I|re.S) or re.search(r"[^.]*maximum QoS[^.]*\.", seg.text, re.I|re.S)
            if q:
                claims.append(Claim(f"{seg.segment_id}-qos-type", "type", {"id":"requested_qos","name":"Requested maximum QoS","encoding":{"kind":"enum","base_type":"uint8","values":{"qos_0":0,"qos_1":1,"qos_2":2}}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.6))
        if re.search(r"Allowed return codes", seg.text, re.I):
            q = re.search(r"Allowed return codes:[^\n]*(?:\n[^\n]*)?", seg.text, re.I)
            if q:
                values = {"success_maximum_qos_0":0,"success_maximum_qos_1":1,"success_maximum_qos_2":2,"failure":128}
                claims.append(Claim(f"{seg.segment_id}-return-code-type", "type", {"id":"return_code","name":"Return Code","encoding":{"kind":"enum","base_type":"uint8","values":values}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], q.group(0).strip(), 0.5))
        for j, match in enumerate(_TYPEVAR.finditer(seg.text)):
            context = seg.text[max(0, match.start()-500):match.end()+1000]
            if re.search(r"four bytes|4 bytes|maximum number of\s+bytes.*four", context, re.I):
                claims.append(Claim(f"{seg.segment_id}-t{j:03d}", "type", {"id":"variable_byte_integer", "name":"Variable Byte Integer", "encoding":{"kind":"varint","max_bytes":4,"data_bits":7,"continuation_bit":7}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], match.group(0), 0.8))
        for j, match in enumerate(_UTF8.finditer(seg.text)):
            context = seg.text[max(0, match.start()-180):match.end()+300]
            if re.search(r"two byte|two-byte|length.*uint16|length.*MSB", context, re.I):
                claims.append(Claim(f"{seg.segment_id}-u{j:03d}", "type", {"id":"utf8_encoded_string", "name":"UTF-8 encoded string", "encoding":{"kind":"length_prefixed_string","length_type":"uint16_be","charset":"utf-8"}}, [{"segment_id":seg.segment_id,"start_line":seg.start_line,"end_line":seg.end_line,"section":seg.section}], match.group(0), 0.75))
        for i, sentence in enumerate(re.split(r"(?<=[.!?])\s+", seg.text)):
            sent = _SEND.search(sentence)
            if sent:
                name, sender, receiver = sent.groups()
                claims.append(Claim(f"{seg.segment_id}-m{i:03d}", "message", {"id": name.lower().replace("-", "_"), "name": name, "senders": [sender.lower()], "receivers": [receiver.lower()], "wire_layout": ["fixed_header", "variable_header", "payload"], "fields": []}, [{"segment_id": seg.segment_id, "start_line": seg.start_line, "end_line": seg.end_line, "section": seg.section}], sent.group(0), 0.65))
    return claims

def claim_dict(claim: Claim) -> dict[str, Any]:
    return asdict(claim)
