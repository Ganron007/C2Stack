# Apollo builder patch — registry keying CS1009 fix

`builder.py` here is a patched copy of
`/Mythic/apollo/mythic/agent_functions/builder.py` from
`ghcr.io/mythicagents/apollo:v0.0.1.19`, bind-mounted over the container's
original by `docker-compose.yml` (same single-file mechanism as
`rabbitmq_config.json`).

`HttpProfile.cs` here is a patched copy of
`/Mythic/apollo/agent_code/HttpProfile/HttpProfile.cs` from the same image,
bind-mounted for a second upstream bug — see below. Payload agents are
compiled from `agent_code` at build time, so the fix lands in every NEW
payload build (existing payloads keep the bug).

## Bug 2 — proxy_user/proxy_pass silently discarded (HttpProfile.cs)

Upstream builds the proxy as:

```csharp
webClient.Proxy = new WebProxy()
{
    Address = new Uri(ProxyAddress),
    Credentials = new NetworkCredential(ProxyUser, ProxyPass),
    UseDefaultCredentials = false,   // <- setter assigns Credentials = null!
    BypassProxyOnLocal = false
};
```

`WebProxy.UseDefaultCredentials` is not a field — its setter overwrites
`Credentials` (default credentials or **null**). Because C# object
initializers run in written order, `Credentials` is set first and then nulled,
so the agent never sends `Proxy-Authorization`: an authenticated proxy
challenges with 407, `HttpWebRequest` has no credentials to retry with, and
the agent loops on 407 forever. `proxy_host`/`proxy_port` still work (the
requests ARE routed through the proxy), only the credentials are dead. The
same pattern exists in `AzureBlobProfile/AzureBlobProfile.cs` (unused here,
not patched). Fix: set `UseDefaultCredentials = false` **before**
`Credentials`.

Verified live (2026-10-04): a stdlib proxy that 407-challenges with
`Basic` receives `PA=(none)` from the unpatched agent on every checkin; a
correctly-ordered `WebClient` (PowerShell, same .NET stack) completes
407 → retry → forward on the first re-request, proving the mechanism. With
the patched agent source, payloads route through the authenticating proxy
end-to-end — see matrix §1.5.

## The bug (upstream, unpatched as of v0.0.1.19)

Registry keying build params are substituted verbatim into C# string literals
in `Config.cs`:

```python
# Config.cs template:  public static string RegistryPath = "registry_path_here";
templateFile = templateFile.replace(placeholder, val)
```

Every real registry path contains backslashes (`HKLM\SOFTWARE\...`), which the
C# compiler reads as escape sequences — `error CS1009: Unrecognized escape
sequence` and the build dies. C2-profile parameters ARE escaped by the builder
(see the "Replace newlines with escaped versions" branch in `build()`), but the
keying values never are, so `keying_method=Registry` was unusable.

## The patch

In the `keying_method == "Registry"` branch, escape for a C# literal before the
values land in `special_files_map["Config.cs"]`:

```python
registry_path = registry_path.replace('\\', '\\\\').replace('"', '\\"')
registry_value = registry_value.replace('\\', '\\\\').replace('"', '\\"')
```

Runtime semantics are unchanged: the agent's `Program.cs` splits
`Config.RegistryPath` on `'\\'` (hive / subkey / value-name), so the compiled
string must hold single backslashes — doubling them here only survives the
compiler.

## Verified live (2026-10-04, lab target)

- Registry-keyed build (path
  `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProductName`, value
  `Windows 10 Enterprise`, Matches) now **builds** (`build_phase=success`; the
  unpatched builder errors with CS1009 on the same input) and the agent
  **callbacks** on the lab target and tasks end-to-end.
- Negative (value `WRONGVALUE`, Matches): agent exits silently, zero
  callbacks — hash-compare path proven both ways.
