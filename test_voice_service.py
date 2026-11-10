import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import voice_service
from speech_segments import FRAME_BYTES


class FakeWatch:
    def __init__(self,_):pass
    def start(self):pass
    def active(self):return True
    def close(self):pass


class FakeRelay:
    instance=None
    def __init__(self,*_):
        read,write=os.pipe();self.write_fd=write
        self.process=SimpleNamespace(stdout=os.fdopen(read,'rb',buffering=0))
        self.controls=[];self.open=True;self.started=threading.Event()
        FakeRelay.instance=self
    def start(self):self.started.set()
    def alive(self):return self.open
    def send_control(self,data):self.controls.append(data)
    def send(self,data):os.write(self.write_fd,data)
    def close(self):
        if self.open:
            self.open=False;os.close(self.write_fd);self.process.stdout.close()


class FakeWork:
    def __init__(self):
        self.ready=threading.Event();self.finished=threading.Event();self.return_to_chat=threading.Event();self.closed=False
    def start(self):self.ready.set()
    def send_microphone(self,_):pass
    def next_speaker(self,timeout=.2):
        if timeout:time.sleep(min(timeout,.02))
        return None
    def close(self):self.ready.clear();self.finished.set();self.closed=True


class FakePlayer:
    def __init__(self,_):self.closed=False
    def start(self):pass
    def alive(self):return not self.closed
    def write(self,_):pass
    def close(self):self.closed=True


class VoiceServiceTests(unittest.TestCase):
    def test_fake_station_voice_transition_and_stop_restore_chat(self):
        stop=threading.Event()
        with patch.multiple(voice_service,verify_model=lambda:True,cue_bytes=lambda:b'',load=lambda _: {},
                            Station=lambda _:object(),StatusWatch=FakeWatch,StationRelay=FakeRelay,
                            StationPlayer=FakePlayer,WorkSession=FakeWork,recognize=lambda _:'work'):
            worker=threading.Thread(target=voice_service.run,args=(stop,));worker.start()
            deadline=time.monotonic()+3
            while FakeRelay.instance is None and time.monotonic()<deadline:time.sleep(.01)
            relay=FakeRelay.instance;self.assertIsNotNone(relay);self.assertTrue(relay.started.wait(2))
            frame=(1000).to_bytes(2,'little',signed=True)*320
            def send_audio():
                for _ in range(12):relay.send(frame)
                for _ in range(45):relay.send(bytes(FRAME_BYTES))
            writer=threading.Thread(target=send_audio);writer.start()
            deadline=time.monotonic()+5
            while b'W' not in relay.controls and time.monotonic()<deadline:time.sleep(.01)
            self.assertIn(b'W',relay.controls)
            stop.set();writer.join(timeout=2);worker.join(timeout=5)
            self.assertFalse(worker.is_alive());self.assertEqual(relay.controls[-1],b'N');self.assertFalse(relay.open)


if __name__=='__main__':unittest.main()
