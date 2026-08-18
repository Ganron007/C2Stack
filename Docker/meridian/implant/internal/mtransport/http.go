package mtransport

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// HTTP implements Transport over POST /api/v1/kex and /api/v1/checkin.
type HTTP struct {
	base     string
	client   *http.Client
	interval int
	jitter   float64
}

// NewHTTP builds an HTTP(S) transport. insecure allows self-signed certs in
// lab. interval/jitter are the requested beacon profile sent at KEX.
func NewHTTP(base string, insecure bool, timeout time.Duration, interval int, jitter float64) *HTTP {
	tr := &http.Transport{
		TLSClientConfig:   &tls.Config{InsecureSkipVerify: insecure},
		DisableKeepAlives: true,
	}
	return &HTTP{
		base:     base,
		client:   &http.Client{Transport: tr, Timeout: timeout},
		interval: interval,
		jitter:   jitter,
	}
}

func (h *HTTP) Name() string { return "http" }

func (h *HTTP) post(path string, body any) (map[string]any, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequest("POST", h.base+path, bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := h.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		io.Copy(io.Discard, resp.Body)
		return nil, fmt.Errorf("http: status %d", resp.StatusCode)
	}
	var out map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return out, nil
}

func (h *HTTP) Kex(clientPubB64, clientNonceB64 string) (*KexReply, error) {
	out, err := h.post("/api/v1/kex", map[string]any{
		"client_pub":   clientPubB64,
		"client_nonce": clientNonceB64,
		"profile": map[string]any{
			"interval": h.interval,
			"jitter":   h.jitter,
		},
	})
	if err != nil {
		return nil, err
	}
	reply := &KexReply{}
	if s, ok := out["session_id"].(string); ok {
		reply.SessionID = s
	}
	if s, ok := out["server_pub"].(string); ok {
		reply.ServerPub = s
	}
	if s, ok := out["server_nonce"].(string); ok {
		reply.ServerNonce = s
	}
	if f, ok := out["interval"].(float64); ok {
		reply.Interval = int(f)
	}
	if f, ok := out["jitter"].(float64); ok {
		reply.Jitter = f
	}
	if reply.SessionID == "" || reply.ServerPub == "" || reply.ServerNonce == "" {
		return nil, fmt.Errorf("http: malformed kex reply")
	}
	return reply, nil
}

func (h *HTTP) Checkin(sessionID string, envelope map[string]string) (map[string]string, error) {
	body := map[string]any{"session_id": sessionID}
	for k, v := range envelope {
		body[k] = v
	}
	out, err := h.post("/api/v1/checkin", body)
	if err != nil {
		return nil, err
	}
	nonce, ok1 := out["nonce"].(string)
	ct, ok2 := out["ct"].(string)
	if !ok1 || !ok2 {
		return nil, fmt.Errorf("http: malformed checkin reply")
	}
	return map[string]string{"nonce": nonce, "ct": ct}, nil
}
