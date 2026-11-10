from pathlib import Path
import threading
import time
import unittest
import wave

from frame_dispatcher import FrameDispatcher
from mode_switch import Mode
from recognizer import MODEL, WHISPER, recognize
from speech_segments import FRAME_BYTES


class Router:
    def __init__(self):self.lock=threading.RLock();self.mode=Mode.CHAT;self.started=threading.Event();self.received=[]
    def start_work(self):
        with self.lock:self.mode=Mode.WORK
        self.started.set()
    def feed(self,frame):self.received.append(frame)
    def close(self):self.mode=Mode.CHAT


class DispatcherTests(unittest.TestCase):
    @unittest.skipUnless(MODEL.is_file() and Path(WHISPER).is_file() and
                         (Path(__file__).parent/'tests/data/work_without_prefix.wav').is_file(),
                         'local speech model or generated sample unavailable')
    def test_exact_synthetic_phrase_starts_and_routes_only_later_audio(self):
        router=Router();dispatch=FrameDispatcher(router,recognize)
        with wave.open(str(Path(__file__).parent/'tests/data/work_without_prefix.wav'),'rb') as sample:
            pcm=sample.readframes(sample.getnframes())
        data=bytes(FRAME_BYTES*10)+pcm+bytes(FRAME_BYTES*55)
        for i in range(0,len(data),FRAME_BYTES):dispatch.feed(data[i:i+FRAME_BYTES])
        self.assertTrue(router.started.wait(timeout=10))
        dispatch.feed(b'\x01\x02'*320)
        self.assertEqual(router.received,[b'\x01\x02'*320])
        dispatch.close()

    def test_non_command_does_not_switch(self):
        router=Router();dispatch=FrameDispatcher(router,lambda _:None)
        frame=(1000).to_bytes(2,'little',signed=True)*320
        for _ in range(12):dispatch.feed(frame)
        for _ in range(45):dispatch.feed(bytes(FRAME_BYTES))
        time.sleep(.05)
        self.assertFalse(router.started.is_set());self.assertEqual(router.mode,Mode.CHAT)
        dispatch.close()


if __name__=='__main__':unittest.main()
