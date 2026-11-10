"""Opt-in station voice router. Does not change routers or install itself."""
from pathlib import Path
import fcntl
import os
import select
import signal
import sys
import threading
import time
import wave

ROOT=Path(__file__).resolve().parent
os.environ.setdefault('ALISA_HOME',str(Path.home()/'.local/state/station2-codex-voice'))
ALISA_REPO=Path(os.environ.get('STATION_ALISA_REPO',str(ROOT.parent/'alisa-voice'))).expanduser()
sys.path.insert(0,str(ALISA_REPO))

from alisa_cli.state import load
from alisa_cli.runtime import status
from alisa_cli.ssh import Station
from frame_dispatcher import FrameDispatcher
from mode_router import ModeRouter
from recognizer import recognize,verify_model
from speech_segments import FRAME_BYTES
from station_transport import StationPlayer,StationRelay
from work_session import WorkSession


def cue_bytes():
    with wave.open(str(ROOT/'assets/work_ready.wav'),'rb') as f:
        if (f.getnchannels(),f.getframerate(),f.getsampwidth())!=(2,48000,2):
            raise RuntimeError('invalid_voice_cue')
        return f.readframes(f.getnframes())


def report(event):
    # Only fixed lifecycle labels are emitted; no speech or account data.
    print('station-voice:',event,flush=True)


class StatusWatch:
    def __init__(self,config):
        self.config=config;self.stop_event=threading.Event();self.lock=threading.Lock()
        self.phase=None;self.updated=0;self.thread=threading.Thread(target=self._run,daemon=True)
    def start(self):self.thread.start()
    def _run(self):
        while not self.stop_event.is_set():
            try:
                s=status(self.config);phase=s.get('phase') if s.get('agent_connected') else None
            except Exception:phase=None
            with self.lock:self.phase=phase;self.updated=time.monotonic()
            self.stop_event.wait(5)
    def active(self):
        with self.lock:return self.phase=='active' and time.monotonic()-self.updated<18
    def close(self):self.stop_event.set()


def run(stop):
    if not verify_model():raise RuntimeError('speech_model_invalid')
    binary=ROOT/'relay/station-relay'
    if not binary.is_file():raise RuntimeError('station_relay_missing')
    cue=cue_bytes();config=load('native');station=Station(config)
    watch=StatusWatch(config);watch.start()
    try:
        while not stop.is_set():
            if not watch.active():stop.wait(2);continue
            relay=StationRelay(station,binary);dispatcher=None
            try:
                relay.start()
                router=ModeRouter(relay,lambda:WorkSession(),lambda:StationPlayer(station),cue,report)
                dispatcher=FrameDispatcher(router,recognize)
                report('listening')
                carry=bytearray()
                while not stop.is_set() and relay.alive() and watch.active():
                    ready=select.select([relay.process.stdout],[],[],.3)[0]
                    if not ready:continue
                    data=os.read(relay.process.stdout.fileno(),4096)
                    if not data:break
                    carry.extend(data)
                    while len(carry)>=FRAME_BYTES:
                        frame=bytes(carry[:FRAME_BYTES]);del carry[:FRAME_BYTES]
                        dispatcher.feed(frame)
            except Exception:
                report('connection_unavailable')  # Fixed message only; no private data.
            finally:
                if dispatcher:dispatcher.close()
                relay.close()
            stop.wait(3)
    finally:watch.close()


def main():
    # A private single-instance lock avoids two competing socket relays.
    lock_path=ROOT/'voice-service.lock'
    fd=os.open(lock_path,os.O_RDWR|os.O_CREAT,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Голосовой помощник уже запущен')
        stop=threading.Event()
        signal.signal(signal.SIGINT,lambda *_:stop.set())
        signal.signal(signal.SIGTERM,lambda *_:stop.set())
        run(stop)
    finally:os.close(fd)


if __name__=='__main__':main()
