"""Meridian operator console.

Interactive console and one-shot subcommands. Everything here talks to a local
App instance (listeners run in background threads).

    $ meridian                          # interactive console
    $ meridian listener add -t http -p 8080 -n main
    $ meridian start
    $ meridian report -o report.md
"""

from __future__ import annotations

import base64
import shlex
import threading
import time

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .app import App
from .config import ListenerConfig
from .models import TaskResult

console = Console()


# ---------------------------------------------------------------------------
# helpers
def _short(sid: str) -> str:
    return sid[:8]


def _session_table(app: App) -> Table:
    t = Table(title="Sessions", box=None)
    for col in ("id", "host", "os", "user", "listener", "last-seen", "status"):
        t.add_column(col)
    now = time.time()
    for s in app.sessions.list():
        alive = "● alive" if now - s.last_seen < max(60, s.interval * 3) else "○ stale"
        t.add_row(
            _short(s.id), s.hostname, f"{s.os}/{s.arch}", s.user,
            s.listener, f"{now - s.last_seen:.0f}s", alive,
        )
    return t


def _result_reader(app: App, get_session: callable) -> threading.Thread:
    """Background thread that streams new results into the console."""

    last: dict[str, float] = {}

    def run() -> None:
        while True:
            try:
                results = app.tasks.results()
                for r in results:
                    sid = r.session_id
                    if r.id in last:
                        continue
                    last[r.id] = r.ts
                    cur = get_session()
                    if cur and cur == sid:
                        _print_result(r)
            except Exception:
                pass
            time.sleep(1)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def _print_result(r: TaskResult) -> None:
    console.print(
        f"[dim][{r.session_id[:8]}][/dim] task {r.task_id[:8]} "
        f"-> [green]{r.status}[/green] (exit {r.exit_code})"
    )
    if r.stdout:
        console.print(_safe_out(r.stdout))
    if r.stderr:
        console.print(f"[red]{_safe_out(r.stderr)}[/red]")


def _safe_out(b: bytes) -> str:
    try:
        return b.decode("utf-8", errors="replace")
    except Exception:
        return b.hex()


# ---------------------------------------------------------------------------
# interactive console
HELP = """\
Meridian console commands:
  listeners [list]              list running listeners
  listener add <transport> <port> [--name N] [--host H] [--domain D]
  listener start <name>         start a configured listener
  listener stop <name>          stop a listener
  sessions                      list sessions
  use <id|prefix>               select a session
  info                          show current session
  exec <cmd...>                 run a command on the current session
  shell <cmd...>                shell (same as exec in this build)
  download <path>               retrieve a file from the implant
  upload <local> <remote>       send a file to the implant
  sleep <sec> [jitter%]         reconfigure beaconing
  exit_implant                  ask the implant to exit cleanly
  results [session]             show stored results
  modules                       list server-side modules
  module <name> [key=value ...] run a server-side module
  report [--out F] [--fmt F]    write an engagement report
  help                          this help
  quit                          leave the console
"""


def run_console(app: App) -> None:
    console.print(Panel(f"Meridian v{__version__} — authorized use only", border_style="cyan"))
    app.modules.discover()
    current: dict[str, str | None] = {"session": None}
    _result_reader(app, lambda: current["session"])

    def need_session() -> str | None:
        sid = current["session"]
        if sid is None:
            console.print("[red]no session selected — use `sessions` then `use <id>`[/red]")
        return sid

    def parse_args(line: str) -> list[str]:
        try:
            return shlex.split(line)
        except ValueError:
            return line.split()

    while True:
        sid = _short(current["session"]) if current["session"] else "none"
        try:
            line = input(f"[{sid}]> ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye[/dim]")
            break
        if not line.strip():
            continue
        argv = parse_args(line)
        cmd = argv[0].lower()
        args = argv[1:]

        if cmd in ("help", "?"):
            console.print(HELP)
        elif cmd == "quit":
            break
        elif cmd == "listeners":
            for name, _ in app.listeners.items():
                console.print(f"[green]●[/green] {name}")
            if not app.listeners:
                console.print("[dim]no listeners running[/dim]")
        elif cmd == "listener":
            _cmd_listener(app, args)
        elif cmd == "sessions":
            console.print(_session_table(app))
        elif cmd == "use":
            if not args:
                console.print("usage: use <id|prefix>")
                continue
            match = [
                s for s in app.sessions.list()
                if s.id.startswith(args[0]) or s.id[:8] == args[0][:8]
            ]
            if not match:
                console.print("[red]no such session[/red]")
                continue
            current["session"] = match[0].id
            s = match[0]
            host = s.hostname
            console.print(
                f"now in session [bold]{host}[/bold] "
                f"({s.os}/{s.arch}, user {s.user})"
            )
        elif cmd == "info":
            sid = need_session()
            if not sid:
                continue
            s = app.sessions.get(sid)
            if s:
                _print_session(s)
        elif cmd in ("exec", "shell"):
            sid = need_session()
            if not sid or not args:
                continue
            t = app.tasks.create(sid, "builtin/exec", {"command": " ".join(args)})
            console.print(f"queued task {t.id[:8]} ({t.module})")
        elif cmd == "download":
            sid = need_session()
            if not sid or not args:
                continue
            app.tasks.create(sid, "builtin/download", {"path": args[0]})
            console.print(f"queued download of {args[0]}")
        elif cmd == "upload":
            sid = need_session()
            if not sid or len(args) < 2:
                continue
            try:
                with open(args[0], "rb") as f:
                    data = base64.b64encode(f.read()).decode()
            except OSError as exc:
                console.print(f"[red]{exc}[/red]")
                continue
            app.tasks.create(sid, "builtin/upload", {"path": args[1], "data_b64": data})
            console.print(f"queued upload of {args[0]} -> {args[1]}")
        elif cmd == "sleep":
            sid = need_session()
            if not sid or not args:
                continue
            interval = int(args[0])
            jitter = int(args[1]) / 100.0 if len(args) > 1 else 0.2
            app.tasks.create(sid, "builtin/sleep", {"interval": interval, "jitter": jitter})
            console.print(f"queued rebeacon: {interval}s ±{jitter * 100:.0f}%")
        elif cmd == "exit_implant":
            sid = need_session()
            if sid:
                app.tasks.create(sid, "builtin/exit", {})
        elif cmd == "results":
            sid = args[0] if args else current["session"]
            _cmd_results(app, sid)
        elif cmd == "modules":
            for name, m in app.modules.list().items():
                console.print(f"  [cyan]{name}[/cyan] — {m.description}")
        elif cmd == "module":
            _cmd_module(app, args, current["session"])
        elif cmd == "report":
            out = "report.md"
            fmt = "markdown"
            if args and args[0] in ("--out", "-o"):
                out = args[1]
            if "--fmt" in args:
                fmt = args[args.index("--fmt") + 1]
            from .modules.serverside.export import ExportModule

            m = ExportModule()
            m.app = app
            path = m.run({"out": out, "format": fmt}, {})
            console.print(f"[green]report written:[/green] {path}")
        else:
            console.print(f"[dim]unknown command: {cmd} (try `help`)[/dim]")


def _print_session(s) -> None:
    t = Table(box=None)
    for k, v in s.to_dict().items():
        t.add_row(k, str(v))
    console.print(t)


def _cmd_listener(app: App, args: list[str]) -> None:
    if not args:
        console.print("usage: listener [add|start|stop|list] ...")
        return
    sub = args[0]
    if sub == "add":
        if len(args) < 3:
            usage = "usage: listener add <transport> <port> [--name N] [--host H] [--domain D]"
            console.print(usage)
            return
        transport = args[1]
        port = int(args[2])
        name = "--name" in args and args[args.index("--name") + 1] or transport
        host = "--host" in args and args[args.index("--host") + 1] or "0.0.0.0"
        domain = "--domain" in args and args[args.index("--domain") + 1] or "c2.example"
        try:
            cfg = ListenerConfig(
                name=name, transport=transport, host=host, port=port, domain=domain
            )
            app.config.add_listener(cfg)
            console.print(f"listener '{name}' configured (start it with `listener start {name}`)")
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
    elif sub == "start":
        if len(args) < 2:
            console.print("usage: listener start <name>")
            return
        cfg = next((li for li in app.config.listeners if li.name == args[1]), None)
        if cfg is None:
            console.print(f"[red]unknown listener '{args[1]}'[/red]")
            return
        app.start_listener(cfg)
    elif sub == "stop":
        if len(args) < 2:
            console.print("usage: listener stop <name>")
            return
        app.stop_listener(args[1])
    elif sub == "list":
        for li in app.config.listeners:
            running = "●" if li.name in app.listeners else "○"
            console.print(f"{running} [cyan]{li.name}[/cyan] {li.transport}://{li.host}:{li.port}")


def _cmd_results(app: App, sid: str | None) -> None:
    results = app.tasks.results(sid)
    if not results:
        console.print("[dim]no results[/dim]")
        return
    t = Table(box=None)
    for col in ("time", "session", "task", "module", "status", "exit"):
        t.add_column(col)
    for r in results:
        task = app.tasks.get(r.task_id)
        module = task.module if task else "?"
        t.add_row(
            f"{r.ts:.0f}", r.session_id[:8], r.task_id[:8], module,
            r.status, str(r.exit_code),
        )
    console.print(t)


def _cmd_module(app: App, args: list[str], cur: str | None) -> None:
    if not args:
        console.print("usage: module <name> [key=value ...]")
        return
    name = args[0]
    module = app.modules.get(name)
    if module is None:
        console.print(f"[red]unknown module: {name}[/red]")
        return
    kv = {}
    for pair in args[1:]:
        if "=" in pair:
            k, v = pair.split("=", 1)
            kv[k] = v
    if "session_id" not in kv and cur:
        kv["session_id"] = cur
    try:
        out = module.run(kv, {})
        if out is not None:
            console.print(str(out))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]module error: {exc}[/red]")


# ---------------------------------------------------------------------------
# one-shot commands
@click.group(invoke_without_command=True)
@click.option("--state", "state_dir", default=None, help="state directory")
@click.pass_context
def main(ctx: click.Context, state_dir: str | None) -> None:
    """Meridian C2 - operator console. Authorized use only."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        app = App.load(state_dir)
        run_console(app)
        return
    ctx.obj = App.load(state_dir)


@main.command()
@click.pass_obj
def start(app: App) -> None:
    """Start the interactive console."""
    run_console(app)


@main.command()
@click.option("--transport", "-t", required=True, help="http|https|ws|wss|dns")
@click.option("--port", "-p", type=int, required=True)
@click.option("--name", "-n", default=None)
@click.option("--host", default="0.0.0.0")
@click.option("--domain", default="c2.example")
@click.option("--cert", default=None)
@click.option("--key", default=None)
@click.option("--mtls", is_flag=True, help="require client certs (https/wss)")
@click.option("--ca", default=None)
@click.pass_obj
def listener_add(app: App, transport: str, port: int, name, host, domain, cert, key, mtls, ca):
    """Persist a listener configuration."""
    cfg = ListenerConfig(
        name=name or transport, transport=transport, host=host, port=port,
        domain=domain, cert=cert, key=key, mTLS=mtls, ca=ca,
    )
    try:
        app.config.add_listener(cfg)
        click.echo(f"listener '{cfg.name}' saved")
    except ValueError as exc:
        click.echo(f"error: {exc}", err=True)


@main.command()
@click.argument("name")
@click.pass_obj
def listener_start(app: App, name: str):
    """Start a configured listener."""
    cfg = next((li for li in app.config.listeners if li.name == name), None)
    if cfg is None:
        click.echo(f"unknown listener '{name}'", err=True)
        return
    app.start_listener(cfg)
    click.echo(f"listener '{name}' started ({cfg.transport}://{cfg.host}:{cfg.port})")


@main.command()
@click.pass_obj
def sessions(app: App):
    """List sessions."""
    console.print(_session_table(app))


@main.command()
@click.argument("session")
@click.argument("command", nargs=-1)
@click.pass_obj
def exec_cmd(app: App, session: str, command: tuple[str]):
    """Queue a command for a session."""
    match = [s for s in app.sessions.list() if s.id.startswith(session)]
    if not match:
        click.echo("no such session", err=True)
        return
    task = app.tasks.create(match[0].id, "builtin/exec", {"command": " ".join(command)})
    click.echo(f"queued {task.id[:8]}")


@main.command()
@click.option("--out", "-o", default="report.md")
@click.option("--fmt", default="markdown")
@click.pass_obj
def report(app: App, out: str, fmt: str):
    """Write an engagement report."""
    from .modules.base import ServerModuleError
    from .modules.serverside.export import ExportModule

    m = ExportModule()
    m.app = app
    try:
        path = m.run({"out": out, "format": fmt}, {})
    except ServerModuleError as exc:
        click.echo(f"error: {exc}", err=True)
        return
    click.echo(f"report: {path}")


@main.command()
@click.pass_obj
def genkey(app: App):
    """Print the server keypair (for deployment)."""
    click.echo(f"private: {app.config.server_priv_b64}")
    click.echo(f"public:  {app.config.server_pub_b64}")


if __name__ == "__main__":
    main()
