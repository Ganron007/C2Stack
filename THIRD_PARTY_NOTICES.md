# Third-Party Notices & Licenses

C2Stack vendors or references the following third-party projects. Each
vendored tree keeps its upstream license text intact in the repository.

| Project | Upstream | License | How it's used in this repo | In-repo location |
|---|---|---|---|---|
| **Havoc** | https://github.com/HavocFramework/Havoc | GPL-3.0 | Source vendored and built by `Docker/havoc/Dockerfile` | `Docker/havoc/src/` (upstream `LICENSE` preserved) |
| **Adaptix** | https://github.com/Adaptix-Framework/AdaptixC2 | GPL-3.0 | Source vendored and built by `Docker/adaptix/Dockerfile` | `Docker/adaptix/src/` (upstream `LICENSE` preserved) |
| **Sliver** | https://github.com/BishopFox/sliver | GPL-3.0 | Official `v1.7.6` release binaries, downloaded *at build time* (SHA-256 pinned); the binaries are **not** committed to this repo | `Docker/sliver/Dockerfile` |
| **Mythic** | https://github.com/its-a-feature/Mythic | BSD-3-Clause | Prebuilt upstream images referenced by compose (`ghcr.io/its-a-feature/mythic_server`, postgres, rabbitmq); nothing vendored | `Docker/docker-compose.yml`, `Docker/mythic/README.md` |
| **Meridian** | (in-repo) | MIT (per upstream README) | Custom Python server + Go implant maintained in this repo | `Docker/meridian/` |
| **C2Stack original code** | (this repo) | MIT License with Commons Clause | Root `LICENSE` | `LICENSE` |

## Modifications to vendored trees

Per GPL §5(a), modified conveyed source must carry a prominent notice.

- **Havoc** (`Docker/havoc/src/`, Aug 2026): upstream source is vendored
  **unmodified functionally**; only build artifacts, runtime data, `.git`
  metadata, and the Qt `client/Modules` subtree were removed to keep the
  tree lean. The teamserver binary is built by our Dockerfile from the
  vendored source (`make dev-ts-compile`).
- **Adaptix** (`Docker/adaptix/src/`, Aug 2026): vendored **unmodified**
  (only `.git` metadata removed).

## Notes on GPL obligations

- **Havoc / Adaptix**: full GPL-3.0 texts ship inside the vendored trees,
  so recipients of this repo receive the license along with the source.
  Corresponding source is available here in full.
- **Sliver**: the repository does **not** redistribute the GPL binaries
  (they exceed GitHub's per-file limits and are deliberately gitignored).
  End users obtain them from the official BishopFox release through the
  build, under upstream terms.

## Disclaimer

These projects are provided "AS IS", without warranty of any kind, by their
respective copyright holders. C2Stack is a training environment for
authorized security testing only.
