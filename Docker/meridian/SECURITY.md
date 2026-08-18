# Security

Meridian is an offensive tool. This file states the intended use and the
guarantees the code makes. Read it before running anything.

## Intended use

Meridian is designed for **authorized red team operations and lab use only**:
systems you own, systems you are explicitly contracted to test, or isolated
lab environments (localhost, virtual machines, dedicated test networks). You
are responsible for obtaining authorization before using it. Unauthorized use
may be a crime in your jurisdiction.

## Reporting issues

If you find a security issue in Meridian (crypto misuse, information leak, an
escape in a task module, ...), do not open a public issue. Report it privately
to the maintainers, then the project will be fixed before disclosure. Include
a minimal reproduction and the affected version.

## Threat model

- **In transit** — session traffic is encrypted with AES-256-GCM under a key
  derived per-session from an X25519 exchange. The AEAD AAD binds every
  message to `meridian/v1/<session_id>`, so cross-session replay fails
  decryption. KEX itself is not authenticated against a pre-provisioned server
  fingerprint: on plain HTTP a network adversary can present its own keypair.
  Use HTTPS (and pinning if the deployment supports it) for anything beyond
  loopback.
- **At rest** — task results are encrypted (AES-256-GCM) with the server
  master key by default (`store_results: encrypted`). The master key is a
  0600 file; if the operator host is compromised, so is the data.
- **On the implant host** — the implant does not hide. Process list, network
  connections and disk artifacts are visible to any local inspection tool.

## Operational rules

1. Deploy only against authorized targets.
2. Prefer HTTPS listeners and remove `MERIDIAN_INSECURE` in any environment
   where traffic is inspectable.
3. Keep `master.key` and the state directory on a host you control; rotate
   them between engagements.
4. Review `events.jsonl` and hand it to the blue team post-engagement.
5. Destroy state directories and revoke listener configs when an engagement
   ends.

## Build & supply chain

- The implant depends only on the Go standard library.
- Server dependencies are pinned in `pyproject.toml`; verify hashes when you
  install in production.
- The server generates its keypair and master key on first run; both are
  created with `0600` permissions.
