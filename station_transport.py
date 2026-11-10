"""Authenticated, temporary station transports; no connection on import."""
from pathlib import Path
import os
import re
import select
import shlex
import subprocess
import threading
import time


SOCKET='/tmp/alisa-standalone/microphone.sock'


class StationRelay:
    def __init__(self, station, binary):
        self.station=station;self.binary=Path(binary);self.process=None;self.directory=None
        self.lock=threading.Lock()

    def start(self):
        try:
            result=self.station.run('umask 077; mktemp -d /tmp/station-voice.XXXXXXXX',timeout=10)
            directory=result.stdout.decode('ascii').strip()
            if not re.fullmatch(r'/tmp/station-voice\.[A-Za-z0-9]+',directory):
                raise RuntimeError('temporary_directory_invalid')
            self.directory=directory
            data=self.binary.read_bytes()
            self.station.put(directory+'/relay',data,mode=0o700)
            d=shlex.quote(directory)
            # Only our freshly-created private directory is removed by this shell.
            command=(f'cd {d} || exit 1; '
                f'trap \'rm -f {d}/relay; rmdir {d} 2>/dev/null || true\' EXIT; '
                f'{d}/relay {shlex.quote(SOCKET)}')
            self.process=subprocess.Popen(self.station.args()+[command],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
            until=time.monotonic()+10;output=b''
            while time.monotonic()<until:
                if self.process.poll() is not None:break
                if select.select([self.process.stderr],[],[],.2)[0]:
                    data=self.process.stderr.read(1)
                    if not data:break
                    output+=data
                    if output.endswith(b'\n'):
                        if output.strip()==b'relay-ready':return
                        output=b''
                    if len(output)>512:break
            raise RuntimeError('station_relay_unavailable')
        except Exception:
            self.close()
            raise

    def send_control(self,value):
        if value not in (b'W',b'N'):raise ValueError('invalid control')
        with self.lock:
            if not self.process or self.process.poll() is not None:raise RuntimeError('station_relay_stopped')
            self.process.stdin.write(value);self.process.stdin.flush()

    def read(self,size=4096):
        if not self.process:return b''
        return self.process.stdout.read(size)

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def close(self):
        proc=self.process;self.process=None
        if proc:
            try:
                if proc.stdin:proc.stdin.close()
            except OSError:pass
            try:proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            for stream in (proc.stdout,proc.stderr):
                if stream:stream.close()
        if self.directory:
            d=shlex.quote(self.directory)
            try:self.station.run(f'rm -f {d}/relay {d}/relay.new; rmdir {d} 2>/dev/null || true',check=False,timeout=8)
            except Exception:pass
            self.directory=None


class StationPlayer:
    def __init__(self,station):self.station=station;self.process=None

    def start(self):
        command='aplay -q -D default -t raw -f S16_LE -c 2 -r 48000'
        self.process=subprocess.Popen(self.station.args()+[command],stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,bufsize=0)
        os.set_blocking(self.process.stdin.fileno(),False)
        time.sleep(.3)
        if self.process.poll() is not None:raise RuntimeError('station_player_unavailable')

    def alive(self):return self.process is not None and self.process.poll() is None

    def write(self,pcm):
        if not isinstance(pcm,bytes) or len(pcm)%4:raise ValueError('invalid playback PCM')
        if not self.alive():raise RuntimeError('station_player_stopped')
        view=memoryview(pcm);deadline=time.monotonic()+1
        while view:
            if time.monotonic()>=deadline:raise RuntimeError('station_playback_timeout')
            try:count=os.write(self.process.stdin.fileno(),view)
            except BlockingIOError:
                select.select([],[self.process.stdin],[],.1)
                continue
            if not count:raise RuntimeError('station_playback_disconnected')
            view=view[count:]

    def close(self):
        proc=self.process;self.process=None
        if not proc:return
        try:proc.stdin.close()
        except (BrokenPipeError,OSError):pass
        try:proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:proc.wait(timeout=2)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
