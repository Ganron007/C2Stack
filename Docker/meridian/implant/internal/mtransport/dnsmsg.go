package mtransport

import (
	"encoding/binary"
	"errors"
	"fmt"
	"strings"
)

var (
	errShort = errors.New("dns: short packet")
	errName  = errors.New("dns: bad name")
)

type dnsRR struct {
	Name string
	Type uint16
	TXT  []string
}

type dnsResponse struct {
	ID      uint16
	Answers []dnsRR
}

func appendName(b []byte, name string) ([]byte, error) {
	labels := strings.Split(name, ".")
	for _, l := range labels {
		if len(l) == 0 || len(l) > 63 {
			return nil, errName
		}
		b = append(b, byte(len(l)))
		b = append(b, l...)
	}
	return append(b, 0), nil
}

// buildQuery builds a single-question UDP query for the given name/type.
func buildQuery(id uint16, name string, qtype uint16) ([]byte, error) {
	b := make([]byte, 0, 64+len(name))
	b = binary.BigEndian.AppendUint16(b, id)
	b = append(b, 0x01, 0x00) // flags: RD
	b = binary.BigEndian.AppendUint16(b, 1)
	b = binary.BigEndian.AppendUint16(b, 0)
	b = binary.BigEndian.AppendUint16(b, 0)
	b = binary.BigEndian.AppendUint16(b, 0)
	var err error
	if b, err = appendName(b, name); err != nil {
		return nil, err
	}
	b = binary.BigEndian.AppendUint16(b, qtype)
	b = binary.BigEndian.AppendUint16(b, 1) // class IN
	return b, nil
}

func readName(msg []byte, off int) (string, int, error) {
	var sb strings.Builder
	pos := off
	end := off
	jumped := false
	hops := 0
	for {
		if pos >= len(msg) {
			return "", 0, errShort
		}
		l := int(msg[pos])
		if l&0xC0 == 0xC0 {
			if pos+1 >= len(msg) {
				return "", 0, errShort
			}
			ptr := int(binary.BigEndian.Uint16(msg[pos:pos+2]) & 0x3FFF)
			if !jumped {
				end = pos + 2
				jumped = true
			}
			pos = ptr
			hops++
			if hops > 64 {
				return "", 0, errName
			}
			continue
		}
		pos++
		if l == 0 {
			break
		}
		if pos+l > len(msg) {
			return "", 0, errShort
		}
		if sb.Len() > 0 {
			sb.WriteByte('.')
		}
		sb.Write(msg[pos : pos+l])
		pos += l
	}
	if !jumped {
		end = pos
	}
	return sb.String(), end, nil
}

func parseResponse(msg []byte) (*dnsResponse, error) {
	if len(msg) < 12 {
		return nil, errShort
	}
	resp := &dnsResponse{ID: binary.BigEndian.Uint16(msg[0:2])}
	flags := binary.BigEndian.Uint16(msg[2:4])
	if flags&0x8000 == 0 {
		return nil, errors.New("dns: not a response")
	}
	if rcode := flags & 0x000F; rcode != 0 {
		return nil, fmt.Errorf("dns: rcode %d", rcode)
	}
	qd := int(binary.BigEndian.Uint16(msg[4:6]))
	an := int(binary.BigEndian.Uint16(msg[6:8]))
	off := 12
	for i := 0; i < qd; i++ {
		_, o, err := readName(msg, off)
		if err != nil {
			return nil, err
		}
		off = o + 4
	}
	for i := 0; i < an; i++ {
		_, o, err := readName(msg, off)
		if err != nil {
			return nil, err
		}
		off = o
		if off+10 > len(msg) {
			return nil, errShort
		}
		typ := binary.BigEndian.Uint16(msg[off : off+2])
		rdlen := int(binary.BigEndian.Uint16(msg[off+8 : off+10]))
		off += 10
		if off+rdlen > len(msg) {
			return nil, errShort
		}
		rdata := msg[off : off+rdlen]
		off += rdlen
		if typ != 16 { // TXT
			continue
		}
		rr := dnsRR{Type: typ}
		p := 0
		for p < len(rdata) {
			l := int(rdata[p])
			p++
			if p+l > len(rdata) {
				return nil, errShort
			}
			rr.TXT = append(rr.TXT, string(rdata[p:p+l]))
			p += l
		}
		resp.Answers = append(resp.Answers, rr)
	}
	return resp, nil
}
