package events

import "testing"

func TestDemonOutputIncludesTaskIDWhenProvided(t *testing.T) {
	withTaskID := Demons.DemonOutput("ABC123", 4112, "output", "00ABC123")
	if got := withTaskID.Body.Info["TaskID"]; got != "00ABC123" {
		t.Fatalf("TaskID = %v, want %q", got, "00ABC123")
	}

	legacy := Demons.DemonOutput("ABC123", 4112, "output")
	if _, ok := legacy.Body.Info["TaskID"]; ok {
		t.Fatal("legacy output unexpectedly contains a TaskID")
	}

	fromOutput := Demons.DemonOutput("ABC123", 10, `{"TaskID":"00ABC124"}`)
	if got := fromOutput.Body.Info["TaskID"]; got != "00ABC124" {
		t.Fatalf("TaskID from output payload = %v, want %q", got, "00ABC124")
	}
}
