package mtasks

import (
	"reflect"
	"runtime"
	"testing"

	"meridian/implant/internal/mproto"
)

func TestSplitCommand(t *testing.T) {
	cases := []struct {
		in   string
		want []string
	}{
		{"id", []string{"id"}},
		{"ls -la /tmp", []string{"ls", "-la", "/tmp"}},
		{`echo "hello world"`, []string{"echo", "hello world"}},
		{`echo 'a b' "c d"`, []string{"echo", "a b", "c d"}},
		{"", []string(nil)},
	}
	for _, c := range cases {
		got := splitCommand(c.in)
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("splitCommand(%q) = %v, want %v", c.in, got, c.want)
		}
	}
}

func TestRunExec(t *testing.T) {
	name, arg, wantOut := "echo", "hi", "hi\n"
	if runtime.GOOS == "windows" {
		name, arg, wantOut = "cmd", "/c echo hi", "hi\r\n"
	}
	r := Run(mproto.Task{ID: "t1", Module: "builtin/exec", Args: map[string]any{"command": name + " " + arg}}, func() {})
	if r.Status != "ok" || r.ExitCode != 0 {
		t.Fatalf("bad result: %+v", r)
	}
	if string(r.Stdout) != wantOut {
		t.Fatalf("bad stdout: %q", r.Stdout)
	}
}

func TestRunExecMissingCommand(t *testing.T) {
	cmd := "/nonexistent-xyz"
	if runtime.GOOS == "windows" {
		cmd = "nonexistent-xyz"
	}
	r := Run(mproto.Task{ID: "t1", Module: "builtin/exec", Args: map[string]any{"command": cmd}}, func() {})
	if r.Status != "error" {
		t.Fatalf("expected error, got %+v", r)
	}
}

func TestRunUnknownModule(t *testing.T) {
	r := Run(mproto.Task{ID: "t1", Module: "nope", Args: map[string]any{}}, func() {})
	if r.Status != "error" {
		t.Fatalf("expected error, got %+v", r)
	}
}

func TestRunExit(t *testing.T) {
	stopped := false
	Run(mproto.Task{ID: "t1", Module: "builtin/exit", Args: map[string]any{}}, func() { stopped = true })
	if !stopped {
		t.Fatal("expected stop callback")
	}
}
