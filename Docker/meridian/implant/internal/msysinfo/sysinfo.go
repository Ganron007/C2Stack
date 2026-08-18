// Package msysinfo collects host metadata reported during the first checkin.
package msysinfo

import (
	"net"
	"os"
	"runtime"
	"strconv"
)

// Collect returns the host metadata map.
func Collect() map[string]any {
	hostname, _ := os.Hostname()
	m := map[string]any{
		"hostname": hostname,
		"os":       runtime.GOOS,
		"arch":     runtime.GOARCH,
		"pid":      os.Getpid(),
		"uid":      strconv.Itoa(os.Getuid()),
		"user":     currentUser(),
		"kernel":   kernelVersion(),
		"ips":      interfaceIPs(),
		"mac":      primaryMAC(),
	}
	return m
}

func interfaceIPs() []string {
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return []string{}
	}
	var out []string
	for _, a := range addrs {
		ipnet, ok := a.(*net.IPNet)
		if !ok {
			continue
		}
		ip := ipnet.IP
		if ip.IsLoopback() || ip.IsUnspecified() {
			continue
		}
		out = append(out, ip.String())
	}
	return out
}

func primaryMAC() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	for _, i := range ifaces {
		if i.Flags&net.FlagLoopback != 0 || len(i.HardwareAddr) == 0 {
			continue
		}
		return i.HardwareAddr.String()
	}
	return ""
}
