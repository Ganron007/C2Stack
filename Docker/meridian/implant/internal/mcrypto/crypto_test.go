package mcrypto

import (
	"bytes"
	"crypto/ecdh"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"hash"
	"testing"
)

func newKeypair(t *testing.T) *ecdh.PrivateKey {
	t.Helper()
	priv, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return priv
}

func TestDeriveKeyMatching(t *testing.T) {
	serverPriv := newKeypair(t)
	clientPriv := newKeypair(t)
	clientNonce := make([]byte, SaltLen)
	serverNonce := make([]byte, SaltLen)
	rand.Read(clientNonce)
	rand.Read(serverNonce)

	clientKey, err := DeriveKey(
		clientPriv,
		B64(serverPriv.PublicKey().Bytes()),
		B64(clientNonce), B64(serverNonce),
	)
	if err != nil {
		t.Fatal(err)
	}

	// server side: derive from its private key + client pub
	serverKey := deriveFromServerKey(t, serverPriv, clientPriv.PublicKey(), clientNonce, serverNonce)
	if !bytes.Equal(clientKey, serverKey) {
		t.Fatalf("keys differ: %x vs %x", clientKey, serverKey)
	}
}

// deriveFromServerKey mirrors the server (meridian/crypto.py from_exchange):
// shared = X25519(serverPriv, clientPub); key = HKDF(shared, salt=cn||sn, info=AADInfo).
func deriveFromServerKey(t *testing.T, serverPriv *ecdh.PrivateKey, clientPub *ecdh.PublicKey, clientNonce, serverNonce []byte) []byte {
	t.Helper()
	shared, err := serverPriv.ECDH(clientPub)
	if err != nil {
		t.Fatal(err)
	}
	salt := append(append([]byte{}, clientNonce...), serverNonce...)
	key, err := hkdf.Key[hash.Hash](sha256.New, shared, salt, AADInfo, KeyLen)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func TestSealOpenRoundtrip(t *testing.T) {
	key := make([]byte, KeyLen)
	rand.Read(key)
	s1, err := New("sess1", key)
	if err != nil {
		t.Fatal(err)
	}
	s2, err := New("sess1", key)
	if err != nil {
		t.Fatal(err)
	}

	env, err := s1.Seal([]byte("hello world"))
	if err != nil {
		t.Fatal(err)
	}
	pt, err := s2.Open(env["nonce"], env["ct"])
	if err != nil {
		t.Fatal(err)
	}
	if string(pt) != "hello world" {
		t.Fatalf("got %q", pt)
	}
}

func TestAADBinding(t *testing.T) {
	key := make([]byte, KeyLen)
	rand.Read(key)
	s1, _ := New("sid-a", key)
	s2, _ := New("sid-b", key) // same key, different session id

	env, _ := s1.Seal([]byte("secret"))
	if _, err := s2.Open(env["nonce"], env["ct"]); err == nil {
		t.Fatal("expected AAD binding to reject cross-session decrypt")
	}
}

func TestTamperDetected(t *testing.T) {
	key := make([]byte, KeyLen)
	rand.Read(key)
	s, _ := New("sess1", key)

	env, _ := s.Seal([]byte("secret"))
	ct, _ := base64.StdEncoding.DecodeString(env["ct"])
	ct[len(ct)-1] ^= 0xFF
	env["ct"] = base64.StdEncoding.EncodeToString(ct)
	if _, err := s.Open(env["nonce"], env["ct"]); err == nil {
		t.Fatal("expected tampered ciphertext to fail")
	}
}

func TestCompactRoundtrip(t *testing.T) {
	key := make([]byte, KeyLen)
	rand.Read(key)
	s1, _ := New("sess1", key)
	s2, _ := New("sess1", key)

	framed, err := s1.SealCompact([]byte("dns payload"))
	if err != nil {
		t.Fatal(err)
	}
	if framed[0] != FrameCheckin {
		t.Fatalf("bad frame tag %x", framed[0])
	}
	pt, err := s2.OpenCompact(framed)
	if err != nil {
		t.Fatal(err)
	}
	if string(pt) != "dns payload" {
		t.Fatalf("got %q", pt)
	}

	// tamper
	framed[len(framed)-1] ^= 0xFF
	if _, err := s2.OpenCompact(framed); err == nil {
		t.Fatal("expected tamper to fail")
	}
}

func TestB32(t *testing.T) {
	// must match the server's dns_listener._b32e (UPPERCASE, no padding)
	data := []byte("the quick brown fox")
	enc := B32E(data)
	want := "ORUGKIDROVUWG2ZAMJZG653OEBTG66A"
	if enc != want {
		t.Fatalf("unexpected b32: %s", enc)
	}
	dec, err := B32D(enc)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(dec, data) {
		t.Fatalf("roundtrip mismatch")
	}
}

func TestB64(t *testing.T) {
	data := []byte("payload\x00bytes")
	got, err := B64D(B64(data))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, data) {
		t.Fatalf("b64 roundtrip mismatch")
	}
}

func TestSessionIDHex(t *testing.T) {
	// session ids are 16 raw bytes; verify hex roundtrip used by DNS framing
	sid := make([]byte, 16)
	rand.Read(sid)
	if len(hex.EncodeToString(sid)) != 32 {
		t.Fatalf("bad sid hex length")
	}
}
