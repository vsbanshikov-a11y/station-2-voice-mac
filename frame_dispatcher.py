"""Route bounded 16-kHz PCM without recording conversation or transcripts."""
import threading

from mode_switch import Mode
from speech_segments import Segmenter


class FrameDispatcher:
    def __init__(self,router,recognize):
        self.router=router;self.recognize=recognize;self.segmenter=Segmenter()
        self.last_mode=Mode.CHAT;self.busy=threading.Lock();self.closed=threading.Event()

    def feed(self,frame):
        if self.closed.is_set():return
        with self.router.lock:mode=self.router.mode
        if mode is not self.last_mode:
            self.segmenter.reset();self.last_mode=mode
        if mode is Mode.WORK:
            self.router.feed(frame)
            return
        if mode is not Mode.CHAT:return
        for utterance in self.segmenter.feed(frame):
            if not self.busy.acquire(blocking=False):continue
            threading.Thread(target=self._recognize,args=(utterance,),daemon=True).start()

    def _recognize(self,utterance):
        try:
            action=self.recognize(utterance)
            if action=='work' and not self.closed.is_set():self.router.start_work()
        except Exception:
            pass  # Failure must leave the original chat running.
        finally:self.busy.release()

    def close(self):
        self.closed.set();self.segmenter.reset();self.router.close()
