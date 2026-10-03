"""Server configuration: listener topology, crypto keys, storage."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .crypto import generate_server_keypair

log = logging.getLogger("meridian.config")

def default_state_dir() -> Path:
    """Evaluate the state directory lazily.

    Reading MERIDIAN_STATE at import time froze the value for the whole
    process: tests and embedders that set the variable after importing
    meridian.config silently got ~/.meridian instead.
    """
    return Path(os.environ.get("MERIDIAN_STATE", "~/.meridian")).expanduser()


# Kept for backward compatibility; prefer default_state_dir().
DEFAULT_STATE_DIR = default_state_dir()


@dataclass
class ListenerConfig:
    name: str
    transport: str  # http | https | ws | wss | dns
    host: str = "0.0.0.0"
    port: int = 8080
    domain: str = "c2.example"
    cert: str | None = None  # TLS cert path (https/wss)
    key: str | None = None
    mTLS: bool = False  # require client certificates (https/wss)
    ca: str | None = None
    front_domains: list[str] = field(default_factory=list)  # domain fronting
    uri_prefix: str = ""  # redirector route prefix also accepted on HTTP routes
    # Backend header gate (finding #3): when both are set, the HTTP listener
    # only answers requests carrying this header/value. Unset (default) keeps
    # the old behavior for direct-backend lab use. The implant always sends
    # MERIDIAN_HTTP_HEADER_NAME/VALUE (defaulting to X-Request-ID/cadre-c2),
    # so enabling this with the same values breaks no default implant.
    expect_header_name: str | None = None
    expect_header_value: str | None = None


@dataclass
class Config:
    state_dir: Path
    server_priv_b64: str
    server_pub_b64: str
    listeners: list[ListenerConfig] = field(default_factory=list)
    default_interval: int = 30
    default_jitter: float = 0.2
    store_results: str = "encrypted"  # or "plain"
    db_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.db_path = self.state_dir / "meridian.db"
        self.master_key = None

    @classmethod
    def load(cls, state_dir: Path | None = None) -> Config:
        state_dir = Path(state_dir or default_state_dir()).expanduser()
        state_dir.mkdir(parents=True, exist_ok=True)
        priv = pub = None
        key_file = state_dir / "server.key"
        cfg_file = state_dir / "config.json"
        if key_file.exists():
            priv, pub = key_file.read_text().strip().splitlines()
        else:
            priv, pub = generate_server_keypair()
            key_file.write_text(f"{priv}\n{pub}\n")
            key_file.chmod(0o600)
        cfg = cls(
            state_dir=state_dir,
            server_priv_b64=priv,
            server_pub_b64=pub,
        )
        cfg.load_master_key()
        if cfg_file.exists():
            data = json.loads(cfg_file.read_text())
            cfg.default_interval = data.get("interval", cfg.default_interval)
            cfg.default_jitter = data.get("jitter", cfg.default_jitter)
            cfg.store_results = data.get("store_results", cfg.store_results)
            for li in data.get("listeners", []):
                found = ListenerConfig(**li)
                if not li.get("uri_prefix"):
                    found.uri_prefix = os.environ.get("MERIDIAN_URI_PREFIX", "")
                # Fill the gate from the environment only when the stored
                # listener says nothing about it; a half-configured JSON pair
                # must not be silently completed with an unrelated env value.
                if not li.get("expect_header_name") and not li.get("expect_header_value"):
                    found.expect_header_name = os.environ.get("MERIDIAN_REQUIRE_HEADER_NAME")
                    found.expect_header_value = os.environ.get("MERIDIAN_REQUIRE_HEADER_VALUE")
                cfg.listeners.append(found)
        cfg._warn_dns_domain_drift()
        return cfg

    def _warn_dns_domain_drift(self) -> None:
        """Warn when MERIDIAN_DNS_DOMAIN disagrees with a stored listener.

        The entrypoint seeds config.json from the env var on a FRESH volume
        only; an existing volume keeps the domain it was created with. Without
        this warning an operator who changes the env var gets a listener on
        the old domain with zero indication why new implants cannot resolve.
        The stored config wins deliberately (CLI customization must survive
        restarts); to move domains, edit config.json or recreate the volume.
        """
        env_domain = (os.environ.get("MERIDIAN_DNS_DOMAIN") or "").strip().rstrip(".")
        if not env_domain:
            return
        for li in self.listeners:
            if li.transport == "dns" and li.domain.rstrip(".") != env_domain:
                log.warning(
                    "dns listener '%s' uses domain '%s' but MERIDIAN_DNS_DOMAIN=%s; "
                    "stored config wins (see Doc/Docker.md migration note)",
                    li.name, li.domain, env_domain,
                    extra={"event": "dns_domain_drift", "listener": li.name,
                           "stored": li.domain, "env": env_domain},
                )

    def save(self) -> None:
        data = {
            "interval": self.default_interval,
            "jitter": self.default_jitter,
            "store_results": self.store_results,
            "listeners": [
                {
                    "name": li.name,
                    "transport": li.transport,
                    "host": li.host,
                    "port": li.port,
                    "domain": li.domain,
                    "cert": li.cert,
                    "key": li.key,
                    "mTLS": li.mTLS,
                    "ca": li.ca,
                    "front_domains": li.front_domains,
                    "uri_prefix": li.uri_prefix,
                    "expect_header_name": li.expect_header_name,
                    "expect_header_value": li.expect_header_value,
                }
                for li in self.listeners
            ],
        }
        (self.state_dir / "config.json").write_text(json.dumps(data, indent=2))

    def load_master_key(self) -> None:
        mk = self.state_dir / "master.key"
        if mk.exists():
            self.master_key = mk.read_bytes()
        else:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            key = AESGCM.generate_key(bit_length=256)
            mk.write_bytes(key)
            mk.chmod(0o600)
            self.master_key = key

    def add_listener(self, cfg: ListenerConfig) -> None:
        if any(li.name == cfg.name for li in self.listeners):
            raise ValueError(f"listener '{cfg.name}' already exists")
        self.listeners.append(cfg)
        self.save()
