"""Regression tests for the Meridian code-review findings (#3-#30).

Each test maps to a numbered finding in doc/internal/CAPABILITY-MATRIX.md
section 2.4. Run from the repo root:

    python -m pytest Docker/meridian/tests/ -q
"""

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meridian import AAD_INFO  # noqa: E402
from meridian.crypto import (  # noqa: E402
    CryptoError,
    ReplayError,
    SessionCrypto,
    generate_server_keypair,
)


def _session(sid="testsession01"):
    priv_b64, pub_b64 = generate_server_keypair()
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )

    from meridian.crypto import b64d, b64e, new_salt

    client_priv = X25519PrivateKey.generate()
    client_pub_b64 = b64e(client_priv.public_key().public_bytes_raw())
    client_nonce = b64e(new_salt())
    server_nonce = b64e(new_salt())
    server_priv = X25519PrivateKey.from_private_bytes(b64d(priv_b64))
    crypto = SessionCrypto.from_exchange(
        sid, server_priv, client_pub_b64, client_nonce, server_nonce
    )
    # client side key derivation (mirror of server)
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    shared = client_priv.exchange(X25519PublicKey.from_public_bytes(b64d(pub_b64)))
    key = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=b64d(client_nonce) + b64d(server_nonce),
        info=AAD_INFO.encode(),
    ).derive(shared)
    return SessionCrypto(sid, key), crypto


# --- #7: replay protection -----------------------------------------------
def test_envelope_replay_rejected():
    sender, receiver = _session()
    env = sender.seal(b'{"type":"checkin"}')
    assert receiver.open(env) == b'{"type":"checkin"}'
    with pytest.raises(ReplayError):
        receiver.open(env)


def test_replay_is_still_a_crypto_error():
    """Wire handlers catch CryptoError; a ReplayError must keep hitting them."""
    sender, receiver = _session()
    env = sender.seal(b"{}")
    receiver.open(env)
    with pytest.raises(CryptoError):
        receiver.open(env)


def test_compact_replay_rejected():
    sender, receiver = _session()
    frame = sender.seal_compact(b"{}")
    assert receiver.open_compact(frame) == b"{}"
    with pytest.raises(ReplayError):
        receiver.open_compact(frame)


def test_tamper_is_not_a_replay():
    sender, receiver = _session()
    env = sender.seal(b"{}")
    env["ct"] = env["ct"][:-2] + ("AA" if not env["ct"].endswith("AA") else "BB")
    with pytest.raises(CryptoError) as ei:
        receiver.open(env)
    assert not isinstance(ei.value, ReplayError)


def _manager(tmp_path, **kw):
    from meridian.config import Config
    from meridian.db import Database
    from meridian.sessions import SessionManager

    cfg = Config.load(tmp_path / "state")
    db = Database(cfg.db_path, master_key=cfg.master_key,
                  encrypt_results=cfg.store_results == "encrypted")
    return SessionManager(db, cfg.server_priv_b64, cfg.server_pub_b64), db


def _kex_payload():
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from meridian.crypto import b64e, new_salt

    priv = X25519PrivateKey.generate()
    return (b64e(priv.public_key().public_bytes_raw()), b64e(new_salt()))


# --- #4: bounded sessions + reaping --------------------------------------
def test_kex_refused_when_table_full(tmp_path, monkeypatch):
    import meridian.sessions as sess_mod

    mgr, _ = _manager(tmp_path)
    monkeypatch.setattr(sess_mod, "MAX_SESSIONS", 3)
    pub, nonce = _kex_payload()
    for _ in range(3):
        mgr.kex(client_pub_b64=pub, client_nonce_b64=nonce)
    with pytest.raises(CryptoError, match="session table full"):
        mgr.kex(client_pub_b64=pub, client_nonce_b64=nonce)


def test_reap_marks_missed_checkins_dead(tmp_path):
    mgr, _ = _manager(tmp_path)
    pub, nonce = _kex_payload()
    rep = mgr.kex(client_pub_b64=pub, client_nonce_b64=nonce)
    # pretend the last checkin was 2 hours ago
    mgr._db.touch_session(rep["session_id"], 0.0)
    assert mgr.get(rep["session_id"]).alive is True
    assert mgr.reap(now=7200.0) == 1
    assert mgr.get(rep["session_id"]).alive is False
    # key material dropped too, so the cap cannot be wedged by corpses
    assert mgr.get_crypto(rep["session_id"]) is None


# --- #6: checkin applies atomically --------------------------------------
def test_checkin_touch_meta_and_results_commit_together(tmp_path):
    from meridian.models import Task

    mgr, db = _manager(tmp_path)
    pub, nonce = _kex_payload()
    rep = mgr.kex(client_pub_b64=pub, client_nonce_b64=nonce)
    sid = rep["session_id"]
    task = Task(id="t" * 32, session_id=sid, module="builtin/exec",
                args={"command": "whoami"})
    db.insert_task(task)
    mgr.checkin(sid, [{"id": task.id, "status": "ok", "exit_code": 0,
                       "stdout_b64": "d3MxXHZhZ3JhbnQK", "ts": 1.0}],
                meta={"hostname": "win-target", "user": "WIN-TARGET\\operator"})
    s = mgr.get(sid)
    assert s.hostname == "win-target"
    assert s.user == "WIN-TARGET\\operator"
    assert db.get_task(task.id).completed is True
    assert len(db.list_results(sid)) == 1


# --- #20: session meta survives a restart ---------------------------------
def test_meta_persisted_across_db_reopen(tmp_path):
    from meridian.db import Database

    mgr, db = _manager(tmp_path)
    pub, nonce = _kex_payload()
    rep = mgr.kex(client_pub_b64=pub, client_nonce_b64=nonce,
                  meta={"hostname": "win-target", "custom": "keepme"})
    mgr.checkin(rep["session_id"], [], meta={"user": "WIN-TARGET\\operator"})
    dbfile = tmp_path / "state" / "meridian.db"
    db2 = Database(dbfile, master_key=b"x" * 32, encrypt_results=False)
    s = db2.get_session(rep["session_id"])
    assert s.meta.get("custom") == "keepme"
    assert s.user == "WIN-TARGET\\operator"


# --- #13: store_results actually encrypts ----------------------------------
def test_encrypted_results_round_trip(tmp_path):
    from meridian.config import Config
    from meridian.db import Database
    from meridian.models import Task, TaskResult

    cfg = Config.load(tmp_path / "enc")
    assert cfg.store_results == "encrypted"  # entrypoint seed value
    db = Database(cfg.db_path, master_key=cfg.master_key, encrypt_results=True)
    t = Task(id="e" * 32, session_id="s" * 32, module="builtin/exec",
             args={"command": "whoami"})
    db.insert_task(t)
    db.insert_result(TaskResult(id="r" * 32, task_id=t.id, session_id=t.session_id,
                               status="ok", stdout=b"secret-bytes"))
    raw = db._conn.execute("SELECT stdout FROM results").fetchone()[0]
    assert raw.startswith("enc:"), "results must be encrypted at rest"
    assert db.list_results(t.session_id)[0].stdout == b"secret-bytes"


def test_plain_results_stay_readable(tmp_path):
    from meridian.db import Database

    db = Database(tmp_path / "plain.db", master_key=None, encrypt_results=False)
    assert db._unpack(db._pack(b"abc")) == b"abc"


# --- #15: DNS domain drift is surfaced --------------------------------------
def test_dns_domain_drift_warns(tmp_path, monkeypatch, caplog):
    import json

    from meridian.config import Config

    state = tmp_path / "drift"
    Config.load(state)  # creates keys; config.json overwritten below
    (state / "config.json").write_text(json.dumps({
        "listeners": [{"name": "dns-c2", "transport": "dns", "host": "0.0.0.0",
                       "port": 5353, "domain": "old.example"}],
    }))
    monkeypatch.setenv("MERIDIAN_DNS_DOMAIN", "new.example")
    with caplog.at_level("WARNING", logger="meridian.config"):
        loaded = Config.load(state)
    assert loaded.listeners[0].domain == "old.example"  # stored config wins
    assert any("dns_domain_drift" in r.message or
               getattr(r, "event", "") == "dns_domain_drift"
               for r in caplog.records)


# --- #24: unknown blob encodings fail loudly --------------------------------
def test_unpack_rejects_unknown_scheme(tmp_path):
    from meridian.db import Database

    db = Database(tmp_path / "u.db", master_key=None, encrypt_results=False)
    with pytest.raises(ValueError, match="unknown result blob encoding"):
        db._unpack("weird:abcdefgh")


def test_corrupt_row_does_not_nuke_listing(tmp_path):
    from meridian.db import Database
    from meridian.models import Task, TaskResult

    db = Database(tmp_path / "c.db", master_key=None, encrypt_results=False)
    t = Task(id="c" * 32, session_id="s" * 32, module="builtin/exec", args={})
    db.insert_task(t)
    db.insert_result(TaskResult(id="r" * 32, task_id=t.id, session_id=t.session_id,
                               status="ok", stdout=b"fine"))
    db._conn.execute("UPDATE results SET stdout='garbage-no-scheme'")
    db._conn.commit()
    rows = db.list_results(t.session_id)
    assert len(rows) == 1
    assert rows[0].stdout == b"[unreadable stored blob]"


# --- #25: state dir evaluated lazily ----------------------------------------
def test_state_dir_reads_env_at_call_time(tmp_path, monkeypatch):
    from meridian.config import Config, default_state_dir

    monkeypatch.setenv("MERIDIAN_STATE", str(tmp_path / "late"))
    assert default_state_dir() == tmp_path / "late"
    cfg = Config.load(None)
    assert cfg.state_dir == tmp_path / "late"


# --- #3: backend header gate ------------------------------------------------
def _http_listener(tmp_path, **cfg_kw):
    from meridian.app import App
    from meridian.config import ListenerConfig
    from meridian.listeners.http_listener import AioHttpListener

    app = App.load(tmp_path / "hgate")
    kw = {"name": "http-c2", "transport": "http", "host": "127.0.0.1",
          "port": 18080}
    kw.update(cfg_kw)
    return AioHttpListener(app, ListenerConfig(**kw))


def _req(headers):
    from types import SimpleNamespace

    return SimpleNamespace(headers=headers)


def test_header_gate_disabled_by_default(tmp_path):
    assert _http_listener(tmp_path)._header_gate(_req({})) is True


def test_header_gate_rejects_without_header(tmp_path):
    li = _http_listener(tmp_path, expect_header_name="X-Request-ID",
                        expect_header_value="cadre-c2")
    assert li._header_gate(_req({})) is False
    assert li._header_gate(_req({"X-Request-ID": "wrong"})) is False
    assert li._header_gate(_req({"X-Request-ID": "cadre-c2"})) is True


def test_header_gate_filled_from_env(tmp_path, monkeypatch):
    import json

    from meridian.config import Config

    monkeypatch.setenv("MERIDIAN_REQUIRE_HEADER_NAME", "X-Request-ID")
    monkeypatch.setenv("MERIDIAN_REQUIRE_HEADER_VALUE", "cadre-c2")
    state = tmp_path / "genv"
    Config.load(state)  # creates keys; no listeners yet
    (state / "config.json").write_text(json.dumps({
        "listeners": [{"name": "http-c2", "transport": "http",
                       "host": "0.0.0.0", "port": 8080}],
    }))
    loaded = Config.load(state)
    assert loaded.listeners[0].expect_header_name == "X-Request-ID"
    assert loaded.listeners[0].expect_header_value == "cadre-c2"


# --- #12: WS route under the prefix ------------------------------------------
def test_ws_route_registered_with_prefix(tmp_path):
    li = _http_listener(tmp_path)
    li.cfg.uri_prefix = "/gateway/v1/telemetry"
    paths = set()
    for res in li._build_app().router.resources():
        info = res.get_info()
        paths.add(info.get("path", ""))
    assert "/api/v1/ws" in paths
    assert "/gateway/v1/telemetry/api/v1/ws" in paths
    assert "/gateway/v1/telemetry/api/v1/kex" in paths


# --- #30: HTTP connection cap -------------------------------------------------
def test_inflight_cap_sheds_load():
    import asyncio
    from types import SimpleNamespace

    from aiohttp import web

    from meridian.listeners.http_listener import InflightCap

    cap = InflightCap(limit=1)
    entered = threading.Event()
    release = threading.Event()

    async def slow(request):
        entered.set()
        await asyncio.get_event_loop().run_in_executor(None, release.wait, 5)
        return web.Response(text="ok")

    async def run():
        loop = asyncio.get_event_loop()
        req = SimpleNamespace(app={})
        t1 = asyncio.ensure_future(cap.middleware(req, slow))
        await loop.run_in_executor(None, entered.wait, 5)
        assert entered.is_set()
        # second request while the first holds the only slot
        resp = await cap.middleware(req, slow)
        assert resp.status == 503
        release.set()
        assert (await t1).status == 200
        assert cap.inflight == 0

    asyncio.run(run())


# --- DNS: #8 terminator, #9 retention, #10 cap --------------------------------
def _dns_resolver(tmp_path, responder=b"R" * 10):
    from types import SimpleNamespace

    from meridian.listeners.dns_listener import MeridianResolver

    calls = []

    def dispatch(marker, msgid, payload):
        calls.append((marker, msgid, payload))
        return responder

    fake = SimpleNamespace(cfg=SimpleNamespace(domain="c2.test"),
                           dispatch=dispatch)
    return MeridianResolver(fake), calls


def _ask(resolver, name):
    from dnslib import DNSRecord

    q = DNSRecord.question(name, "TXT")
    reply = resolver.resolve(q, None)
    return [str(r.rdata).strip('"') for r in reply.rr]


def _b32(b: bytes) -> str:
    import base64

    return base64.b32encode(b).decode().rstrip("=")


def test_dns_exact_multiple_needs_terminator(tmp_path):
    # 36-byte payload = one full chunk, no short chunk: without `ue` the
    # server must NOT dispatch (it cannot know the message is complete).
    resolver, calls = _dns_resolver(tmp_path)
    payload = b"A" * 36
    assert _ask(resolver, f"uc.abcdef01.0.{_b32(payload)}.c2.test") == ["ok"]
    assert _ask(resolver, "g.abcdef01.c2.test") == ["P"]
    assert calls == []
    # terminator with the wrong count must not dispatch a partial message
    assert _ask(resolver, "ue.abcdef01.2.c2.test") == ["ok"]
    assert calls == []
    # correct terminator dispatches exactly once
    assert _ask(resolver, "ue.abcdef01.1.c2.test") == ["ok"]
    assert len(calls) == 1 and calls[0][2] == payload
    # and a repeat terminator does not dispatch again
    assert _ask(resolver, "ue.abcdef01.1.c2.test") == ["ok"]
    assert len(calls) == 1


def test_dns_short_chunk_still_completes_without_terminator(tmp_path):
    # backward compat: old implants never send `ue`
    resolver, calls = _dns_resolver(tmp_path)
    assert _ask(resolver, f"uc.abcdef02.0.{_b32(b'B' * 36)}.c2.test") == ["ok"]
    assert _ask(resolver, f"uc.abcdef02.1.{_b32(b'xx')}.c2.test") == ["ok"]
    assert len(calls) == 1 and calls[0][2] == b"B" * 36 + b"xx"


def test_dns_poll_serves_response_repeatedly(tmp_path):
    resolver, calls = _dns_resolver(tmp_path, responder=b"hello")
    assert _ask(resolver, f"uc.abcdef03.0.{_b32(b'hi')}.c2.test") == ["ok"]
    first = _ask(resolver, "g.abcdef03.c2.test")
    assert first != ["P"] and first != ["err"]
    # same response again: a lost UDP reply no longer wedges the implant
    assert _ask(resolver, "g.abcdef03.c2.test") == first
    assert _ask(resolver, "g.abcdef03.c2.test") == first
    # ...but retention is bounded: after POLL_DELIVERIES the slot is freed
    assert _ask(resolver, "g.abcdef03.c2.test") == ["P"]


def test_dns_oversize_response_refused(tmp_path):
    resolver, calls = _dns_resolver(tmp_path, responder=b"Z" * 8192)
    assert _ask(resolver, f"uc.abcdef04.0.{_b32(b'hi')}.c2.test") == ["ok"]
    assert _ask(resolver, "g.abcdef04.c2.test") == ["err"]


def test_dns_saturation_refused(tmp_path, monkeypatch):
    import meridian.listeners.dns_listener as dns_mod

    monkeypatch.setattr(dns_mod, "MAX_DNS_INFLIGHT", 1)
    resolver, _ = _dns_resolver(tmp_path)
    resolver._inflight.acquire()  # hold the only slot
    try:
        assert _ask(resolver, "ping.c2.test") == ["err"]
    finally:
        resolver._inflight.release()
    assert _ask(resolver, "ping.c2.test") == ["pong"]


# --- #11: stop frees the socket -------------------------------------------------
def _free_port(sock_type):
    import socket

    s = socket.socket(socket.AF_INET, sock_type)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_http_listener_stop_and_rebind(tmp_path):
    import socket
    import time

    from meridian.app import App
    from meridian.config import ListenerConfig
    from meridian.listeners.http_listener import AioHttpListener

    app = App.load(tmp_path / "stophttp")
    port = _free_port(socket.SOCK_STREAM)
    li = AioHttpListener(app, ListenerConfig(name="h", transport="http",
                                             host="127.0.0.1", port=port))
    li.start()
    deadline = time.time() + 10
    from urllib.request import urlopen

    while time.time() < deadline:
        try:
            urlopen(f"http://127.0.0.1:{port}/api/v1/kex", data=b"{}",
                    timeout=2).read()
            break
        except Exception:
            time.sleep(0.2)
    li.stop()
    assert li._thread is None or not li._thread.is_alive()
    # rebind proves the loop/thread/socket are really gone, not lingering
    li2 = AioHttpListener(app, ListenerConfig(name="h", transport="http",
                                              host="127.0.0.1", port=port))
    li2.start()
    try:
        assert li2._thread.is_alive()
    finally:
        li2.stop()
    assert li2._thread is None or not li2._thread.is_alive()


def test_dns_listener_stop_and_rebind(tmp_path):
    import socket
    import time

    from meridian.app import App
    from meridian.config import ListenerConfig
    from meridian.listeners.dns_listener import DnsListener

    app = App.load(tmp_path / "stopdns")
    port = _free_port(socket.SOCK_DGRAM)
    li = DnsListener(app, ListenerConfig(name="d", transport="dns",
                                         host="127.0.0.1", port=port,
                                         domain="c2.test"))
    li.start()
    time.sleep(2)
    assert li._thread.is_alive()
    li.stop()
    assert li._thread is None or not li._thread.is_alive()
    li2 = DnsListener(app, ListenerConfig(name="d", transport="dns",
                                          host="127.0.0.1", port=port,
                                          domain="c2.test"))
    li2.start()
    try:
        assert li2._thread.is_alive()
    finally:
        li2.stop()


# --- DNS end-to-end against the real listener (#8, #9) -------------------------
def _dns_client(port):
    import socket

    from dnslib import QTYPE, DNSRecord

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        try:
            sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except OSError:
            pass

    def ask(name):
        q = DNSRecord.question(name, "TXT")
        sock.sendto(q.pack(), ("127.0.0.1", port))
        data, _ = sock.recvfrom(8192)
        reply = DNSRecord.parse(data)
        out = []
        for r in reply.rr:
            if r.rtype == QTYPE.TXT:
                out.extend(seg.decode() for seg in r.rdata.data)
        return out

    return ask, sock.close


def _dns_kex(ask, app):
    """KEX handshake exactly like the implant's (49-byte frame, 36+13)."""
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.x25519 import (
        X25519PrivateKey,
        X25519PublicKey,
    )
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    from meridian import AAD_INFO
    from meridian.crypto import SessionCrypto, new_salt

    priv = X25519PrivateKey.generate()
    cnonce = new_salt()
    frame = b"\x01" + priv.public_key().public_bytes_raw() + cnonce
    msgid = "a1b2c3d4"
    for i in range(0, len(frame), 36):
        chunk = frame[i:i + 36]
        recs = ask(f"uk.{msgid}.{i // 36}.{_b32(chunk)}.c2.test")
        assert recs == ["ok"], recs
    recs = ask(f"g.{msgid}.c2.test")
    assert recs != ["P"], "kex poll returned pending"
    blob = "".join(
        r.split(":", 2)[2] if i == 0 else r.split(":", 1)[1]
        for i, r in enumerate(recs)
    )
    raw = base64.b64decode(blob)
    assert raw[0] == 0x01
    sid = raw[1:17].hex()
    shared = priv.exchange(X25519PublicKey.from_public_bytes(raw[17:49]))
    key = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=cnonce + raw[49:65],
               info=AAD_INFO.encode()).derive(shared)
    return SessionCrypto(sid, key), msgid


def test_dns_kex_task_result_round_trip(tmp_path):
    import json

    from meridian.app import App
    from meridian.config import ListenerConfig
    from meridian.listeners.dns_listener import DnsListener

    app = App.load(tmp_path / "dnse2e")
    port = _free_port(__import__("socket").SOCK_DGRAM)
    li = DnsListener(app, ListenerConfig(name="d", transport="dns",
                                         host="127.0.0.1", port=port,
                                         domain="c2.test"))
    li.start()
    try:
        ask, close = _dns_client(port)
        try:
            crypto, _ = _dns_kex(ask, app)
            task = app.tasks.create(crypto.session_id, "builtin/exec",
                                    {"command": "whoami"})
            # checkin carrying no results; reply must contain the queued task
            body = json.dumps({"results": []}).encode()
            sealed = crypto.seal_compact(body)
            for i in range(0, len(sealed), 36):
                recs = ask(f"uc.{crypto.session_id}.{i // 36}."
                           f"{_b32(sealed[i:i + 36])}.c2.test")
                assert recs == ["ok"], recs
            recs = ask(f"g.{crypto.session_id}.c2.test")
            assert recs != ["P"] and recs != ["err"], recs
            blob = "".join(
                r.split(":", 2)[2] if i == 0 else r.split(":", 1)[1]
                for i, r in enumerate(recs)
            )
            import base64

            reply = json.loads(crypto.open_compact(base64.b64decode(blob)))
            assert reply["session_id"] == crypto.session_id
            got = [t["id"] for t in reply["tasks"]]
            assert task.id in got
        finally:
            close()
    finally:
        li.stop()


def test_dns_exact_multiple_checkin_completes_via_terminator(tmp_path):
    import base64
    import json

    from meridian.app import App
    from meridian.config import ListenerConfig
    from meridian.listeners.dns_listener import DnsListener

    app = App.load(tmp_path / "dns36")
    port = _free_port(__import__("socket").SOCK_DGRAM)
    li = DnsListener(app, ListenerConfig(name="d", transport="dns",
                                         host="127.0.0.1", port=port,
                                         domain="c2.test"))
    li.start()
    try:
        ask, close = _dns_client(port)
        try:
            crypto, _ = _dns_kex(ask, app)
            # pad the plaintext so the sealed frame is an exact multiple of 36
            pad = 0
            while True:
                body = json.dumps({"results": [], "pad": "x" * pad}).encode()
                if (1 + 12 + len(body) + 16) % 36 == 0:
                    break
                pad += 1
            sealed = crypto.seal_compact(body)
            assert len(sealed) % 36 == 0
            n = len(sealed) // 36
            for i in range(n):
                recs = ask(f"uc.{crypto.session_id}.{i}."
                           f"{_b32(sealed[i * 36:(i + 1) * 36])}.c2.test")
                assert recs == ["ok"], recs
            # no short chunk: server must still be buffering
            assert ask(f"g.{crypto.session_id}.c2.test") == ["P"]
            # terminator completes it
            assert ask(f"ue.{crypto.session_id}.{n}.c2.test") == ["ok"]
            recs = ask(f"g.{crypto.session_id}.c2.test")
            assert recs != ["P"], "terminated message still pending"
            blob = "".join(
                r.split(":", 2)[2] if i == 0 else r.split(":", 1)[1]
                for i, r in enumerate(recs)
            )
            reply = json.loads(crypto.open_compact(base64.b64decode(blob)))
            assert reply["session_id"] == crypto.session_id
        finally:
            close()
    finally:
        li.stop()


def test_results_json_surfaces_download_bytes(tmp_path, monkeypatch):
    import json

    from click.testing import CliRunner
    from meridian import cli as cli_mod
    from meridian.app import App
    from meridian.models import Task, TaskResult

    state = tmp_path / "dljson"
    app = App.load(state)
    monkeypatch.setattr(cli_mod, "App", type("App", (), {"load": staticmethod(lambda *a: app)}))
    pub, nonce = _kex_payload()
    sid = app.sessions.kex(client_pub_b64=pub, client_nonce_b64=nonce)["session_id"]
    t = Task(id="t" * 32, session_id=sid, module="builtin/download", args={})
    app.db.insert_task(t)
    app.db.insert_result(TaskResult(id="r" * 32, task_id=t.id, session_id=sid,
                                   status="ok", stdout=b"", data=b"file-bytes"))
    out = CliRunner().invoke(cli_mod.main, ["--state", str(state),
                                            "results", "--json", sid[:8]])
    assert out.exit_code == 0, out.output
    rows = json.loads(out.output.strip().splitlines()[-1])
    import base64

    assert base64.b64decode(rows[0]["data_b64"]) == b"file-bytes"


def test_results_resolves_session_prefix(tmp_path):
    from meridian.app import App
    from meridian.cli import _resolve_session

    app = App.load(tmp_path / "prefix")
    pub, nonce = _kex_payload()
    rep = app.sessions.kex(client_pub_b64=pub, client_nonce_b64=nonce)
    assert _resolve_session(app, rep["session_id"][:8]) == rep["session_id"]
    assert _resolve_session(app, rep["session_id"]) == rep["session_id"]
    assert _resolve_session(app, "nope-nope") is None
    assert _resolve_session(app, None) is None


# --- #26: CLI option parsing ---------------------------------------------------
def test_opt_helper():
    from meridian.cli import _MISSING, _opt

    assert _opt(["--out", "f.md"], "--out", "-o") == "f.md"
    assert _opt(["-o", "f.md"], "--out", "-o") == "f.md"
    assert _opt(["--out"], "--out", "-o") is _MISSING
    assert _opt(["report"], "--out", "-o") is None


# --- #17: incremental results ---------------------------------------------------
def test_list_results_since(tmp_path):
    from meridian.db import Database
    from meridian.models import Task, TaskResult

    db = Database(tmp_path / "s.db", master_key=None, encrypt_results=False)
    t = Task(id="s" * 32, session_id="x" * 32, module="builtin/exec", args={})
    db.insert_task(t)
    for i in range(3):
        db.insert_result(TaskResult(id=f"r{i}" + "0" * 30, task_id=t.id,
                                   session_id=t.session_id, status="ok",
                                   stdout=b"x", ts=float(100 + i)))
    assert len(db.list_results(t.session_id)) == 3
    assert len(db.list_results(t.session_id, since=101.0)) == 1
    assert db.list_results(t.session_id, since=200.0) == []


def test_concurrent_duplicate_open_only_one_wins():
    sender, receiver = _session()
    env = sender.seal(b"{}")
    wins, replays = [], []
    barrier = threading.Barrier(8)

    def attempt():
        barrier.wait()
        try:
            receiver.open(dict(env))
            wins.append(1)
        except ReplayError:
            replays.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    assert len(replays) == 7
