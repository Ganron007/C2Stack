// Package mtransport implements the beacon transports (HTTP and DNS),
// mirroring the server listeners.
package mtransport

// KexReply is the common outcome of a key exchange.
type KexReply struct {
	SessionID   string
	ServerPub   string
	ServerNonce string
	Interval    int
	Jitter      float64
}

// Transport is a single beacon channel.
type Transport interface {
	Name() string
	// Kex performs the X25519 key exchange. clientPub/Nonce are base64.
	Kex(clientPubB64, clientNonceB64 string) (*KexReply, error)
	// Checkin posts an envelope and returns the reply envelope.
	Checkin(sessionID string, envelope map[string]string) (map[string]string, error)
}
