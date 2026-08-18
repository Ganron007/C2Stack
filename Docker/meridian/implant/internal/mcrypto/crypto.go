// Package mcrypto implements the Meridian session crypto primitives.
//
// Mirrors meridian/crypto.py: shared secret via X25519, key derived with
// HKDF-SHA256 (salt = client_nonce||server_nonce, info = "meridian-v1") and
// an AES-256-GCM AEAD envelope whose AAD binds every message to the session id.
// Standard library only.
package mcrypto

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hkdf"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base32"
	"encoding/base64"
	"errors"
	"hash"
)

const (
	AADInfo      = "meridian/v1"
	NonceLen     = 12
	SaltLen      = 16
	KeyLen       = 32
	FrameKex     = 0x01
	FrameCheckin = 0x02
)

var (
	ErrKey       = errors.New("mcrypto: bad key material")
	ErrFrame     = errors.New("mcrypto: bad compact frame")
	ErrDecrypt   = errors.New("mcrypto: decryption failed")
	ErrLen       = errors.New("mcrypto: bad nonce length")
	ErrBadPubLen = errors.New("mcrypto: server public key must be 32 bytes")
)

// B64 / B64D are standard (RFC 4648, padded) base64, matching the server's b64e/b64d.
func B64(data []byte) string        { return base64.StdEncoding.EncodeToString(data) }
func B64D(s string) ([]byte, error) { return base64.StdEncoding.DecodeString(s) }

// B32E is UPPERCASE base32 without padding, matching dns_listener._b32e.
func B32E(data []byte) string {
	return base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(data)
}

// B32D is the inverse of B32E.
func B32D(s string) ([]byte, error) {
	return base32.StdEncoding.WithPadding(base32.NoPadding).DecodeString(s)
}

// DeriveKey computes the 32-byte session key from the client keypair, the
// server public key and both nonces.
func DeriveKey(clientPriv *ecdh.PrivateKey, serverPubB64, clientNonceB64, serverNonceB64 string) ([]byte, error) {
	serverPubRaw, err := B64D(serverPubB64)
	if err != nil {
		return nil, err
	}
	if len(serverPubRaw) != 32 {
		return nil, ErrBadPubLen
	}
	serverPub, err := ecdh.X25519().NewPublicKey(serverPubRaw)
	if err != nil {
		return nil, ErrKey
	}
	shared, err := clientPriv.ECDH(serverPub)
	if err != nil {
		return nil, ErrKey
	}
	clientNonce, err := B64D(clientNonceB64)
	if err != nil {
		return nil, err
	}
	serverNonce, err := B64D(serverNonceB64)
	if err != nil {
		return nil, err
	}
	salt := make([]byte, 0, len(clientNonce)+len(serverNonce))
	salt = append(salt, clientNonce...)
	salt = append(salt, serverNonce...)
	if len(salt) != SaltLen*2 {
		return nil, ErrLen
	}
	key, err := hkdf.Key[hash.Hash](sha256.New, shared, salt, AADInfo, KeyLen)
	if err != nil {
		return nil, err
	}
	return key, nil
}

// Session holds one session's key material.
type Session struct {
	SessionID string
	key       []byte
}

// New builds a session crypto from a derived key.
func New(sessionID string, key []byte) (*Session, error) {
	if len(key) != KeyLen {
		return nil, ErrKey
	}
	if _, err := aes.NewCipher(key); err != nil {
		return nil, err
	}
	return &Session{SessionID: sessionID, key: key}, nil
}

func (s *Session) aad() []byte {
	return []byte(AADInfo + "/" + s.SessionID)
}

// Seal produces the JSON envelope {"nonce","ct"} used by the HTTP transport.
func (s *Session) Seal(plain []byte) (map[string]string, error) {
	gcm, err := s.gcm()
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, NonceLen)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	ct := gcm.Seal(nil, nonce, plain, s.aad())
	return map[string]string{"nonce": B64(nonce), "ct": B64(ct)}, nil
}

// Open decrypts an HTTP envelope.
func (s *Session) Open(nonceB64, ctB64 string) ([]byte, error) {
	nonce, err := B64D(nonceB64)
	if err != nil {
		return nil, err
	}
	if len(nonce) != NonceLen {
		return nil, ErrLen
	}
	ct, err := B64D(ctB64)
	if err != nil {
		return nil, err
	}
	gcm, err := s.gcm()
	if err != nil {
		return nil, err
	}
	pt, err := gcm.Open(nil, nonce, ct, s.aad())
	if err != nil {
		return nil, ErrDecrypt
	}
	return pt, nil
}

// SealCompact produces FRAME_CHECKIN | nonce | ct for the DNS transport.
func (s *Session) SealCompact(plain []byte) ([]byte, error) {
	gcm, err := s.gcm()
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, NonceLen)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	ct := gcm.Seal(nil, nonce, plain, s.aad())
	out := make([]byte, 0, 1+NonceLen+len(ct))
	out = append(out, FrameCheckin)
	out = append(out, nonce...)
	out = append(out, ct...)
	return out, nil
}

// OpenCompact decrypts a DNS checkin frame.
func (s *Session) OpenCompact(framed []byte) ([]byte, error) {
	if len(framed) < 1+NonceLen+16 || framed[0] != FrameCheckin {
		return nil, ErrFrame
	}
	nonce := framed[1 : 1+NonceLen]
	ct := framed[1+NonceLen:]
	gcm, err := s.gcm()
	if err != nil {
		return nil, err
	}
	pt, err := gcm.Open(nil, nonce, ct, s.aad())
	if err != nil {
		return nil, ErrDecrypt
	}
	return pt, nil
}

func (s *Session) gcm() (cipher.AEAD, error) {
	block, err := aes.NewCipher(s.key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}
