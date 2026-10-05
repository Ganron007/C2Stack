"""Offline frame-shape tests for havoc_client.run() (no teamserver needed).

Every case mirrors a ConsoleInput.cc branch + its CommandSend.cc frame and the
daemon-side switch in teamserver/pkg/agent/demons.go. These lock the wire
shapes so a refactor cannot silently change what the Demon receives; live
execution against a real Demon is tracked separately (matrix section 4.3).
"""

import asyncio
import base64
import json

import pytest

import havoc_client as hv


def frame_for(line, **kw):
    client = hv.HavocClient()
    captured = {}

    async def fake_collect(demon_id, frame, command, task_id, chunks, wait):
        captured.update(json.loads(frame)["Body"]["Info"])
        return {"task_id": task_id}

    client._collect = fake_collect
    asyncio.run(client.run("ABC123", line, wait=1, **kw))
    return captured


@pytest.mark.parametrize("line,expect", [
    ("shell whoami", {"CommandID": "4112", "ProcCommand": "4"}),
    ("powershell Get-Process", {"CommandID": "4112"}),
    ("ls C:\\Windows", {"CommandID": "15", "SubCommand": "dir"}),
    ("pwd", {"CommandID": "15", "SubCommand": "pwd", "Arguments": ""}),
    ("cat C:\\a.txt", {"CommandID": "15", "SubCommand": "cat"}),
    ("cp a b", {"CommandID": "15", "SubCommand": "cp"}),
    ("ps", {"CommandID": "12"}),
    ("sleep 10", {"CommandID": "11"}),
    ("sleep 10 5", {"CommandID": "11"}),
    ("checkin", {"CommandID": "100"}),
    ("token list", {"CommandID": "40", "SubCommand": "list"}),
    ("config Sleep", {"CommandID": "2500", "ConfigKey": "Sleep"}),
    ("screenshot", {"CommandID": "2510"}),
    ("net domain", {"CommandID": "2100", "NetCommand": "1"}),
    ("job list", {"CommandID": "21", "Command": "list"}),
    # `task` is teamserver-side (CommandID "Teamserver"), not a Demon JOB.
    ("task list", {"CommandID": "Teamserver", "Command": "task::list"}),
    ("exit thread", {"CommandID": "92", "ExitMethod": "thread"}),
    # SOCKET takes the full two-word subcommand (demons.go has no default
    # case — a bare "socks" silently becomes an empty job).
    ("socks list", {"CommandID": "2540", "Command": "socks list",
                    "Params": ""}),
    ("socks add 1080", {"CommandID": "2540", "Command": "socks add",
                         "Params": "1080"}),
    ("rportfwd list", {"CommandID": "2540", "Command": "rportfwd list",
                       "Params": ""}),
    # rportfwd add joins GUI-style with ";" (ConsoleInput.cc:1600).
    ("rportfwd add 0.0.0.0 8080 127.0.0.1 80",
     {"CommandID": "2540", "Command": "rportfwd add",
      "Params": "0.0.0.0;8080;127.0.0.1;80"}),
    # PIVOT takes a NUMERIC command (1/10/11 — ConsoleInput.cc:2134+).
    ("pivot list", {"CommandID": "2520", "Command": "1", "Param": ""}),
    ("pivot connect WS01 agent-pipe",
     {"CommandID": "2520", "Command": "10",
      "Param": "\\\\WS01\\pipe\\agent-pipe"}),
    ("pivot disconnect abc123",
     {"CommandID": "2520", "Command": "11", "Param": "abc123"}),
    # ptt defaults Luid to "0" like the GUI (empty Luid errors teamserver-side).
    ("ptt QUJD", {"CommandID": "2550", "Command": "ptt", "Ticket": "QUJD",
                  "Luid": "0"}),
    ("ptt QUJD /luid 0x3e7", {"CommandID": "2550", "Command": "ptt",
                              "Luid": "0x3e7"}),
])
def test_run_builds_correct_frame(line, expect):
    info = frame_for(line)
    for key, value in expect.items():
        assert info.get(key) == value, f"{line!r}: {key}={info.get(key)!r}"


@pytest.mark.parametrize("line", [
    "cp onlyone", "exit nukes", "task explode", "sleep", "token",
    "upload C:\\x", "config", "net", "cat", "socks", "socks frobnicate",
    "rportfwd", "rportfwd add 1 2 3", "pivot", "pivot connect onlyone",
    "pivot explode", "ptt", "shellcode-inject", "shellcode-inject abc",
    "dll-inject", "dll-inject abc",
])
def test_run_rejects_bad_input(line):
    with pytest.raises(hv.BackendError):
        frame_for(line)


def test_bare_line_falls_back_to_shell():
    """A line that is not a catalogue word runs as `shell` (cross-framework
    expectation), it does not error."""
    info = frame_for("whoami /priv")
    assert info["CommandID"] == "4112"
    assert info["ProcCommand"] == "4"


def test_upload_needs_content():
    info = frame_for("upload C:\\out.bin", upload_data_b64="aGk=")
    assert info["SubCommand"] == "upload"
    assert ";" in info["Arguments"]


def test_fs_arg_shapes():
    info = frame_for("cat C:\\a.txt")
    assert base64.b64decode(info["Arguments"]).decode() == "C:\\a.txt"
    info = frame_for("cp a b")
    first, second = info["Arguments"].split(";")
    assert base64.b64decode(first).decode() == "a"
    assert base64.b64decode(second).decode() == "b"
    info = frame_for("ls C:\\Windows")
    assert info["Arguments"].startswith("C:\\Windows;false;false;false;false;;;")
    info = frame_for("sleep 10 5")
    assert info["Arguments"] == "10;5"


def test_catalogue_lists_everything_run_accepts():
    for name in hv.COMMANDS:
        assert "help" in hv.COMMANDS[name]
        assert "cmd" in hv.COMMANDS[name]
