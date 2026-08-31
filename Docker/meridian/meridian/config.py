"""Server configuration: listener topology, crypto keys, storage."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .crypto import generate_server_keypair

DEFAULT_STATE_DIR = Path(os.environ.get("MERIDIAN_STATE", "~/.meridian")).expanduser()


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
        state_dir = Path(state_dir or DEFAULT_STATE_DIR).expanduser()
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
                cfg.listeners.append(found)
        return cfg

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
