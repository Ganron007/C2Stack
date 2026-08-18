package mtasks

import (
	"reflect"
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
	r := Run(mproto.Task{ID: "t1", Module: "builtin/exec", Args: map[string]any{"command": "echo hi"}}, func() {})
	if r.Status != "ok" || r.ExitCode != 0 {
		t.Fatalf("bad result: %+v", r)
	}
	if string(r.Stdout) != "hi\n" {
		t.Fatalf("bad stdout: %q", r.Stdout)
	}
}

func TestRunExecMissingCommand(t *testing.T) {
	r := Run(mproto.Task{ID: "t1", Module: "builtin/exec", Args: map[string]any{}}, func() {})
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
