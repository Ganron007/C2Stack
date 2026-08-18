//go:build !linux

package msysinfo

import (
	"os/user"
	"runtime"
)

func currentUser() string {
	if u, err := user.Current(); err == nil {
		return u.Username
	}
	return ""
}

func kernelVersion() string {
	return runtime.GOOS
}
