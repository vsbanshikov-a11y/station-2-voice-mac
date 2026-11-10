package main

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

type capture chan []byte

func (c capture) Write(p []byte) (int, error) { c <- append([]byte(nil), p...); return len(p), nil }

func TestRelayForwardsAudioAndRestoresSocket(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mic.sock")
	original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	relay, err := openRelay(path)
	if err != nil {
		t.Fatal(err)
	}
	relay.captureAllowed = func() bool { return true }
	ctx, cancel := context.WithCancel(context.Background())
	secondary := make(capture, 1)
	finished := make(chan struct{})
	go func() { relay.run(ctx, secondary); close(finished) }()
	source, err := net.DialUnix("unixgram", nil, &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	sample := []byte{1, 2, 3, 4}
	if _, err = source.Write(sample); err != nil {
		t.Fatal(err)
	}
	_ = original.SetReadDeadline(time.Now().Add(time.Second))
	got := make([]byte, 10)
	n, _, err := original.ReadFromUnix(got)
	if err != nil || !bytes.Equal(got[:n], sample) {
		t.Fatalf("primary audio not forwarded: %v", err)
	}
	select {
	case got := <-secondary:
		if !bytes.Equal(got, sample) {
			t.Fatal("secondary audio mismatch")
		}
	case <-time.After(time.Second):
		t.Fatal("secondary audio missing")
	}
	cancel()
	<-finished
	if err = relay.close(); err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		t.Fatal("original socket not restored")
	}
}

func TestRelayKillHelper(t *testing.T) {
	path := os.Getenv("STATION_TEST_RELAY_PATH")
	if path == "" {
		return
	}
	stopAt := os.Getenv("STATION_TEST_STOP_AT")
	r, err := openRelayWithHook(path, func(step string) {
		if step == stopAt {
			_ = syscall.Kill(os.Getpid(), syscall.SIGKILL)
		}
	})
	if err != nil {
		t.Fatal(err)
	}
	fmt.Println("ready")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	defer r.close()
	r.run(ctx, &bytes.Buffer{})
}

func TestWatchdogRestoresAcrossStartupCrashes(t *testing.T) {
	for _, step := range []string{"guard-ready", "renamed", "linked", "temp-removed"} {
		t.Run(step, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "mic.sock")
			original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
			if err != nil {
				t.Fatal(err)
			}
			defer original.Close()
			cmd := exec.Command(os.Args[0], "-test.run=^TestRelayKillHelper$")
			cmd.Env = append(os.Environ(), "STATION_TEST_RELAY_PATH="+path, "STATION_TEST_STOP_AT="+step)
			if err = cmd.Start(); err != nil {
				t.Fatal(err)
			}
			if err = cmd.Wait(); err == nil {
				t.Fatal("helper was expected to be killed")
			}
			deadline := time.Now().Add(5 * time.Second)
			for {
				source, dialErr := net.DialUnix("unixgram", nil, &net.UnixAddr{Name: path, Net: "unixgram"})
				if dialErr == nil {
					_, _ = source.Write([]byte("restored"))
					_ = source.Close()
					_ = original.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
					buf := make([]byte, 32)
					if n, _, readErr := original.ReadFromUnix(buf); readErr == nil && string(buf[:n]) == "restored" {
						break
					}
				}
				if time.Now().After(deadline) {
					t.Fatal("watchdog did not restore original socket")
				}
				time.Sleep(25 * time.Millisecond)
			}
		})
	}
}

func TestWatchdogRestoresAfterKill(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mic.sock")
	original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	cmd := exec.Command(os.Args[0], "-test.run=^TestRelayKillHelper$")
	cmd.Env = append(os.Environ(), "STATION_TEST_RELAY_PATH="+path)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(stdout).ReadString('\n')
	if err != nil || line != "ready\n" {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		t.Fatalf("helper not ready: %v", err)
	}
	if err = cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = cmd.Wait()
	deadline := time.Now().Add(5 * time.Second)
	for {
		_, e := os.Lstat(path)
		if e == nil {
			// The original listener can receive a fresh datagram once restored.
			source, dialErr := net.DialUnix("unixgram", nil, &net.UnixAddr{Name: path, Net: "unixgram"})
			if dialErr == nil {
				_, _ = source.Write([]byte("restored"))
				_ = source.Close()
				_ = original.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
				buf := make([]byte, 32)
				if n, _, readErr := original.ReadFromUnix(buf); readErr == nil && string(buf[:n]) == "restored" {
					break
				}
			}
		}
		if time.Now().After(deadline) {
			t.Fatal("watchdog did not restore original socket")
		}
		time.Sleep(25 * time.Millisecond)
	}
}

func TestMicrophoneSwitchFailsClosed(t *testing.T) {
	cases := []struct {
		state string
		want  bool
	}{
		{"gpio-421 ( |MUTE_SWITCH ) in lo\ngpio-464 ( |MUTE_STATE ) in lo", true},
		{"gpio-421 ( |MUTE_SWITCH ) in hi\ngpio-464 ( |MUTE_STATE ) in hi", false},
		{"gpio-421 ( |MUTE_SWITCH ) in lo\ngpio-464 ( |MUTE_STATE ) in hi", false},
		{"gpio-421 ( |MUTE_SWITCH ) in lo hi", false},
		{"gpio-421 ( |MUTE_SWITCH ) in unknown", false},
		{"gpio-421 ( |MUTE_SWITCH ) in lo\x00", false},
		{"gpio-100 (|mute-switch ) in hi IRQ", false},
		{"gpio-200 (other) in hi", false},
		{"", false},
	}
	for _, tc := range cases {
		if got := microphoneEnabled([]byte(tc.state)); got != tc.want {
			t.Fatalf("switch state %q: got %v, want %v", tc.state, got, tc.want)
		}
	}
}

func TestMutedAudioNeverReachesSecondary(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mic.sock")
	original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	r, err := openRelay(path)
	if err != nil {
		t.Fatal(err)
	}
	r.captureAllowed = func() bool { return false }
	ctx, cancel := context.WithCancel(context.Background())
	secondary := make(capture, 1)
	finished := make(chan struct{})
	go func() { r.run(ctx, secondary); close(finished) }()
	source, err := net.DialUnix("unixgram", nil, &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	if _, err := source.Write([]byte{1, 2, 3, 4}); err != nil {
		t.Fatal(err)
	}
	_ = original.SetReadDeadline(time.Now().Add(time.Second))
	if _, _, err := original.ReadFromUnix(make([]byte, 10)); err != nil {
		t.Fatal(err)
	}
	select {
	case <-secondary:
		t.Fatal("muted audio reached secondary")
	case <-time.After(100 * time.Millisecond):
	}
	cancel()
	<-finished
	if err := r.close(); err != nil {
		t.Fatal(err)
	}
}

func TestWorkModeSilencesOnlyTheOriginalAndLeaseRestoresIt(t *testing.T) {
	dir, err := os.MkdirTemp("", "sg-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	path := filepath.Join(dir, "mic.sock")
	original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	r, err := openRelay(path)
	if err != nil {
		t.Fatal(err)
	}
	r.captureAllowed = func() bool { return true }
	ctx, cancel := context.WithCancel(context.Background())
	secondary := make(capture, 3)
	done := make(chan struct{})
	go func() { r.run(ctx, secondary); close(done) }()
	source, err := net.DialUnix("unixgram", nil, &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer source.Close()
	sample := []byte{1, 2, 3, 4}
	r.workUntil.Store(time.Since(monotonicStart).Nanoseconds() + int64(time.Second))
	if _, err := source.Write(sample); err != nil {
		t.Fatal(err)
	}
	_ = original.SetReadDeadline(time.Now().Add(time.Second))
	buf := make([]byte, 10)
	n, _, err := original.ReadFromUnix(buf)
	if err != nil || !bytes.Equal(buf[:n], make([]byte, len(sample))) {
		t.Fatal("original chat was not silenced during work")
	}
	select {
	case got := <-secondary:
		if !bytes.Equal(got, sample) {
			t.Fatal("work audio mismatch")
		}
	case <-time.After(time.Second):
		t.Fatal("work audio missing")
	}
	r.workUntil.Store(0)
	if _, err := source.Write(sample); err != nil {
		t.Fatal(err)
	}
	n, _, err = original.ReadFromUnix(buf)
	if err != nil || !bytes.Equal(buf[:n], sample) {
		t.Fatal("original chat did not recover")
	}
	cancel()
	<-done
	if err := r.close(); err != nil {
		t.Fatal(err)
	}
}

func TestControlDisconnectRestoresChat(t *testing.T) {
	r := &relay{}
	reader, writer := io.Pipe()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { r.controls(reader, cancel); close(done) }()
	if _, err := writer.Write([]byte{'W'}); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(time.Second)
	for !r.inWorkMode() && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if !r.inWorkMode() {
		t.Fatal("work lease not activated")
	}
	_ = writer.Close()
	<-done
	if r.inWorkMode() || ctx.Err() == nil {
		t.Fatal("disconnect must end relay and restore chat")
	}
}

func TestExpiredLeaseNeverKeepsChatMuted(t *testing.T) {
	r := &relay{}
	r.workUntil.Store(time.Since(monotonicStart).Nanoseconds() + int64(5*time.Millisecond))
	if !r.inWorkMode() {
		t.Fatal("lease should start active")
	}
	time.Sleep(10 * time.Millisecond)
	if r.inWorkMode() {
		t.Fatal("expired lease left original chat muted")
	}
}

func TestRejectsNonSocket(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mic.sock")
	if err := os.WriteFile(path, []byte("keep"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := openRelay(path); err == nil {
		t.Fatal("must reject regular files")
	}
	content, _ := os.ReadFile(path)
	if string(content) != "keep" {
		t.Fatal("file changed")
	}
}

func TestAnotherRelayCannotTakeOver(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mic.sock")
	original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	r, err := openRelay(path)
	if err != nil {
		t.Fatal(err)
	}
	if second, secondErr := openRelay(path); secondErr == nil {
		_ = second.close()
		t.Fatal("second relay must not take over")
	}
	if err = r.close(); err != nil {
		t.Fatal(err)
	}
}

func TestDoesNotOverwriteNewSocketOwner(t *testing.T) {
	path := filepath.Join(t.TempDir(), "mic.sock")
	original, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer original.Close()
	r, err := openRelay(path)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	replacement, err := net.ListenUnixgram("unixgram", &net.UnixAddr{Name: path, Net: "unixgram"})
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Close()
	if err = r.close(); err == nil {
		t.Fatal("replacement must be preserved")
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		t.Fatal("replacement lost")
	}
}
