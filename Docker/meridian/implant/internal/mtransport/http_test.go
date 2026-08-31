package mtransport

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHTTPPrefixAndHeader(t *testing.T) {
	var gotPath, gotHeader string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		gotHeader = r.Header.Get("X-Request-ID")
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"session_id":"s","server_pub":"p","server_nonce":"n"}`))
	}))
	defer srv.Close()

	h := NewHTTP(srv.URL, false, 0, 30, 0.2, "/gateway/v1/telemetry", "X-Request-ID", "cadre-c2")
	_, err := h.Kex("clientPub", "clientNonce")
	if err != nil {
		t.Fatal(err)
	}
	if gotPath != "/gateway/v1/telemetry/api/v1/kex" {
		t.Fatalf("bad path: %q", gotPath)
	}
	if gotHeader != "cadre-c2" {
		t.Fatalf("bad header: %q", gotHeader)
	}
}

func TestHTTPNoHeaderWhenDisabled(t *testing.T) {
	var gotHeader string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotHeader = r.Header.Get("X-Request-ID")
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"session_id":"s","server_pub":"p","server_nonce":"n"}`))
	}))
	defer srv.Close()

	h := NewHTTP(srv.URL, false, 0, 30, 0.2, "", "X-Request-ID", "")
	_, err := h.Kex("clientPub", "clientNonce")
	if err != nil {
		t.Fatal(err)
	}
	if gotHeader != "" {
		t.Fatalf("header should be disabled, got %q", gotHeader)
	}
}
