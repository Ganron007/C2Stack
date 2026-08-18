package mtransport

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"strings"
	"testing"
)

func TestBuildQuery(t *testing.T) {
	pkt, err := buildQuery(0x1234, "g.deadbeef.c2.test", 16)
	if err != nil {
		t.Fatal(err)
	}
	if binary.BigEndian.Uint16(pkt[0:2]) != 0x1234 {
		t.Fatal("bad id")
	}
	// question name starts at offset 12
	name := pkt[12 : len(pkt)-4]
	want := []byte{
		1, 'g', 8, 'd', 'e', 'a', 'd', 'b', 'e', 'e', 'f',
		2, 'c', '2', 4, 't', 'e', 's', 't', 0,
	}
	if !bytes.Equal(name, want) {
		t.Fatalf("bad question name: %x", name)
	}
	if binary.BigEndian.Uint16(pkt[len(pkt)-4:len(pkt)-2]) != 16 {
		t.Fatal("bad qtype")
	}
}

func TestParseResponse(t *testing.T) {
	// Craft a response with one TXT answer: "00:1:YXJ5eDF5MXkx"
	qname := []byte{1, 'g', 8, 'd', 'e', 'a', 'd', 'b', 'e', 'e', 'f', 2, 'c', '2', 4, 't', 'e', 's', 't', 0}
	txt := "00:1:YXJ5eDF5MXkx"
	var buf bytes.Buffer

	write16 := func(v uint16) { _ = binary.Write(&buf, binary.BigEndian, v) }
	write16(0x4321)               // id
	write16(0x8180)               // flags: QR RD RA
	write16(1)                    // qdcount
	write16(1)                    // ancount
	write16(0)                    // nscount
	write16(0)                    // arcount
	buf.Write(qname)              // question name
	write16(16)                   // qtype TXT
	write16(1)                    // qclass IN
	buf.Write([]byte{0xC0, 0x0C}) // answer name -> compression pointer
	write16(16)                   // type TXT
	write16(1)                    // class IN
	_ = binary.Write(&buf, binary.BigEndian, uint32(60))
	write16(uint16(1 + len(txt))) // rdlength
	buf.WriteByte(byte(len(txt))) // TXT length octet
	buf.WriteString(txt)          // TXT data

	resp, err := parseResponse(buf.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if resp.ID != 0x4321 {
		t.Fatalf("bad id %x", resp.ID)
	}
	if len(resp.Answers) != 1 || resp.Answers[0].Type != 16 {
		t.Fatalf("bad answers: %+v", resp.Answers)
	}
	if len(resp.Answers[0].TXT) != 1 || resp.Answers[0].TXT[0] != txt {
		t.Fatalf("bad txt: %+v", resp.Answers[0].TXT)
	}
}

func TestParseResponseNxDomain(t *testing.T) {
	var buf bytes.Buffer
	write16 := func(v uint16) { _ = binary.Write(&buf, binary.BigEndian, v) }
	write16(1)
	write16(0x8183) // NXDOMAIN
	write16(0)
	write16(0)
	write16(0)
	write16(0)
	if _, err := parseResponse(buf.Bytes()); err == nil {
		t.Fatal("expected NXDOMAIN error")
	}
}

func TestReassemble(t *testing.T) {
	// single record
	recs := []string{"00:1:YWI="}
	data, err := reassemble(recs)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "ab" {
		t.Fatalf("got %q", data)
	}

	// multi-record: payload large enough to span 180-char b64 records
	payload := []byte(strings.Repeat("A", 140))
	b64 := base64.StdEncoding.EncodeToString(payload)
	var recs2 []string
	for i := 0; i < len(b64); i += 180 {
		end := i + 180
		if end > len(b64) {
			end = len(b64)
		}
		if i == 0 {
			recs2 = append(recs2, fmt.Sprintf("00:%d:%s", (len(b64)+179)/180, b64[i:end]))
		} else {
			recs2 = append(recs2, fmt.Sprintf("%02d:%s", i/180, b64[i:end]))
		}
	}
	got, err := reassemble(recs2)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("multi-record reassemble mismatch")
	}
}
