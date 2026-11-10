// A temporary opt-in microphone relay. The real assistant remains the primary
// destination; an optional secondary stream is dropped if its reader is slow.
// Nothing is recorded on disk or sent over an unauthenticated network socket.
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

type relay struct {
	path, backup     string
	receiver, target *net.UnixConn
	owned            os.FileInfo
	frames           chan []byte
	captureAllowed   func() bool
	guardPipe        *os.File
	guardProcess     *exec.Cmd
	stationLock      *os.File
	workUntil        atomic.Int64
}

// Work mode must be renewed frequently by the authenticated SSH client.
// Lost control or a stalled Mac automatically restores the original chat.
const workLease = 1500 * time.Millisecond

var monotonicStart = time.Now()

func (r *relay) inWorkMode() bool {
	return time.Since(monotonicStart).Nanoseconds() < r.workUntil.Load()
}

func (r *relay) controls(reader io.Reader, cancel context.CancelFunc) {
	defer cancel()
	defer r.workUntil.Store(0)
	buf := make([]byte, 1)
	for {
		if _, err := io.ReadFull(reader, buf); err != nil {
			return
		}
		switch buf[0] {
		case 'W':
			r.workUntil.Store(time.Since(monotonicStart).Nanoseconds() + int64(workLease))
		case 'N':
			r.workUntil.Store(0)
		default:
			return
		}
	}
}

// A separate watchdog restores only the exact original socket if the relay is
// killed, while refusing to replace a socket created by anyone else.
func socketIdentity(st os.FileInfo) (string, string) {
	s := st.Sys().(*syscall.Stat_t)
	return strconv.FormatUint(uint64(s.Dev), 10), strconv.FormatUint(uint64(s.Ino), 10)
}

func matches(path, dev, ino string) bool {
	st, err := os.Lstat(path)
	if err != nil || st.Mode()&os.ModeSocket == 0 {
		return false
	}
	d, i := socketIdentity(st)
	return d == dev && i == ino
}

func guard(args []string) {
	if len(args) != 7 {
		return
	}
	path, backup, temp := args[0], args[1], args[2]
	originalDev, originalIno, relayDev, relayIno := args[3], args[4], args[5], args[6]
	control := os.NewFile(3, "control")
	ready := os.NewFile(4, "ready")
	if control == nil || ready == nil {
		return
	}
	_, _ = ready.Write([]byte{1})
	_ = ready.Close()
	_, _ = io.Copy(io.Discard, control)
	_ = control.Close()
	if matches(temp, relayDev, relayIno) {
		_ = os.Remove(temp)
	}
	if !matches(backup, originalDev, originalIno) {
		return
	}
	if matches(path, relayDev, relayIno) {
		_ = os.Remove(path)
	}
	// Link fails rather than overwriting a newer owner.
	_ = restoreSocket(backup, path)
}

func init() {
	if len(os.Args) > 1 && os.Args[1] == "--guard" {
		guard(os.Args[2:])
		os.Exit(0)
	}
}

func startGuard(path, backup, temp string, original, owned os.FileInfo) (*os.File, *exec.Cmd, error) {
	controlRead, controlWrite, err := os.Pipe()
	if err != nil {
		return nil, nil, err
	}
	readyRead, readyWrite, err := os.Pipe()
	if err != nil {
		controlRead.Close()
		controlWrite.Close()
		return nil, nil, err
	}
	exe, err := os.Executable()
	if err != nil {
		controlRead.Close()
		controlWrite.Close()
		readyRead.Close()
		readyWrite.Close()
		return nil, nil, err
	}
	od, oi := socketIdentity(original)
	rd, ri := socketIdentity(owned)
	cmd := exec.Command(exe, "--guard", path, backup, temp, od, oi, rd, ri)
	cmd.ExtraFiles = []*os.File{controlRead, readyWrite}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	err = cmd.Start()
	_ = controlRead.Close()
	_ = readyWrite.Close()
	if err != nil {
		controlWrite.Close()
		readyRead.Close()
		return nil, nil, err
	}
	_ = readyRead.SetReadDeadline(time.Now().Add(3 * time.Second))
	ready := make([]byte, 1)
	_, err = io.ReadFull(readyRead, ready)
	_ = readyRead.Close()
	if err != nil {
		controlWrite.Close()
		_ = cmd.Wait()
		return nil, nil, err
	}
	return controlWrite, cmd, nil
}

// Station 2 labels both inputs MUTE_SWITCH/MUTE_STATE; lo means enabled.
// Reject missing, malformed or conflicting states rather than guessing.
func microphoneEnabled(gpio []byte) bool {
	if strings.IndexByte(string(gpio), 0) >= 0 || len(gpio) > 131072 {
		return false
	}
	found := false
	for _, line := range strings.Split(string(gpio), "\n") {
		if !strings.Contains(line, "|MUTE_SWITCH ") && !strings.Contains(line, "|MUTE_STATE ") {
			continue
		}
		parts := strings.SplitN(line, ")", 2)
		if len(parts) != 2 {
			return false
		}
		level := ""
		for _, word := range strings.Fields(parts[1]) {
			if word == "hi" || word == "lo" {
				if level != "" && level != word {
					return false
				}
				level = word
			}
		}
		if level != "lo" {
			return false
		}
		found = true
	}
	return found
}

func physicalMicrophoneEnabled() bool {
	state, err := os.ReadFile("/sys/kernel/debug/gpio")
	return err == nil && microphoneEnabled(state)
}

// Link is atomic and fails if another process has already claimed the path.
func restoreSocket(backup, path string) error {
	if err := os.Link(backup, path); err != nil {
		return err
	}
	return os.Remove(backup)
}

func openRelay(path string) (*relay, error) {
	return openRelayWithHook(path, nil)
}

func openRelayWithHook(path string, hook func(string)) (*relay, error) {
	checkpoint := func(name string) {
		if hook != nil {
			hook(name)
		}
	}
	path = filepath.Clean(path)
	st, err := os.Lstat(path)
	if err != nil || st.Mode()&os.ModeSocket == 0 {
		return nil, errors.New("microphone socket unavailable")
	}
	// Prevent two new relays from racing; refuse an existing older relay too.
	lock, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = lock.Close()
		return nil, errors.New("another relay is active")
	}
	locked := true
	defer func() {
		if locked {
			_ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
			_ = lock.Close()
		}
	}()
	current, err := os.Lstat(path)
	if err != nil || !os.SameFile(current, st) {
		return nil, errors.New("microphone socket changed")
	}
	backups, err := filepath.Glob(path + ".relay-*")
	if err != nil || len(backups) > 0 {
		return nil, errors.New("another relay or an unfinished recovery exists")
	}
	backup := path + ".relay-" + strconv.Itoa(os.Getpid())
	if _, err := os.Lstat(backup); !os.IsNotExist(err) {
		return nil, errors.New("backup already exists")
	}
	temp := path + ".new-" + strconv.Itoa(os.Getpid())
	receiver, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: temp, Net: "unixgram"})
	if err != nil {
		return nil, err
	}
	owned, err := os.Lstat(temp)
	if err != nil {
		receiver.Close()
		_ = os.Remove(temp)
		return nil, err
	}
	guardPipe, guardProcess, err := startGuard(path, backup, temp, st, owned)
	if err != nil {
		receiver.Close()
		_ = os.Remove(temp)
		return nil, err
	}
	checkpoint("guard-ready")
	ready := false
	defer func() {
		if !ready {
			_ = receiver.Close()
			_ = guardPipe.Close()
			_ = guardProcess.Wait()
		}
	}()
	if err = os.Rename(path, backup); err != nil {
		return nil, err
	}
	checkpoint("renamed")
	if backupInfo, e := os.Lstat(backup); e != nil || !os.SameFile(backupInfo, st) {
		return nil, errors.New("original socket changed")
	}
	if err = os.Link(temp, path); err != nil {
		return nil, err
	}
	checkpoint("linked")
	if err = os.Remove(temp); err != nil {
		return nil, err
	}
	checkpoint("temp-removed")
	target, err := net.DialUnix("unixgram", nil, &net.UnixAddr{Name: backup, Net: "unixgram"})
	if err != nil {
		return nil, err
	}
	ready = true
	locked = false
	return &relay{path: path, backup: backup, receiver: receiver, target: target, owned: owned,
		frames: make(chan []byte, 50), captureAllowed: physicalMicrophoneEnabled,
		guardPipe: guardPipe, guardProcess: guardProcess, stationLock: lock}, nil
}

func (r *relay) close() error {
	defer func() {
		_ = r.guardPipe.Close()
		_ = r.guardProcess.Wait()
		_ = syscall.Flock(int(r.stationLock.Fd()), syscall.LOCK_UN)
		_ = r.stationLock.Close()
	}()
	_ = r.receiver.Close()
	_ = r.target.Close()
	current, err := os.Lstat(r.path)
	if err == nil && os.SameFile(current, r.owned) {
		if err = os.Remove(r.path); err != nil {
			return err
		}
	} else if err == nil {
		return errors.New("socket replaced; preserving current owner")
	} else if !os.IsNotExist(err) {
		return err
	}
	return restoreSocket(r.backup, r.path)
}

func (r *relay) run(ctx context.Context, secondary io.Writer) {
	go func() {
		for frame := range r.frames {
			if !r.captureAllowed() {
				continue
			}
			if _, err := secondary.Write(frame); err != nil {
				return
			}
		}
	}()
	defer close(r.frames)
	for {
		_ = r.receiver.SetReadDeadline(time.Now().Add(250 * time.Millisecond))
		buf := make([]byte, 8192)
		n, _, err := r.receiver.ReadFromUnix(buf)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			if nerr, ok := err.(net.Error); ok && nerr.Timeout() {
				continue
			}
			return
		}
		primary := buf[:n]
		if r.inWorkMode() {
			primary = make([]byte, n)
		}
		_ = r.target.SetWriteDeadline(time.Now().Add(30 * time.Millisecond))
		if _, err = r.target.Write(primary); err != nil {
			return
		}
		if !r.captureAllowed() {
			continue
		}
		copyFrame := append([]byte(nil), buf[:n]...)
		select {
		case r.frames <- copyFrame:
		default:
		}
	}
}

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "specify microphone socket")
		os.Exit(2)
	}
	r, err := openRelay(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, "microphone relay unavailable")
		os.Exit(1)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	defer r.close()
	go r.controls(os.Stdin, cancel)
	fmt.Fprintln(os.Stderr, "relay-ready")
	r.run(ctx, os.Stdout)
}
