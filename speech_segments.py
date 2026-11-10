"""Bounded, in-memory segmentation of 16 kHz mono PCM for local mode detection.

No microphone, socket or background service is opened by this module.
"""
from array import array
from collections import deque
import io
import math
import sys
import wave

RATE = 16_000
SAMPLE_BYTES = 2
FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_BYTES


def wav_bytes(pcm):
    if not pcm or len(pcm) % SAMPLE_BYTES:
        raise ValueError('invalid PCM audio')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_BYTES)
        out.setframerate(RATE)
        out.writeframes(pcm)
    return buf.getvalue()


def rms(frame):
    samples = array('h')
    samples.frombytes(frame)
    if sys.byteorder != 'little':
        samples.byteswap()
    return math.sqrt(sum(n*n for n in samples) / max(len(samples), 1))


class Segmenter:
    """Return complete bounded utterances; reset on mute or stream interruption."""
    def __init__(self, threshold=300, pre_frames=15, silence_frames=40,
                 min_voiced_frames=8, max_frames=250):
        self.threshold = threshold
        self.pre_frames = pre_frames
        self.silence_frames = silence_frames
        self.min_voiced_frames = min_voiced_frames
        self.max_frames = max_frames
        self.reset()

    def reset(self):
        self.partial = bytearray()
        self.before = deque(maxlen=self.pre_frames)
        self.active = []
        self.silence = 0
        self.voiced = 0

    def feed(self, pcm):
        if not isinstance(pcm, bytes):
            raise TypeError('PCM frame must be bytes')
        if len(pcm) > 64 * FRAME_BYTES:
            raise ValueError('input burst too large')
        self.partial.extend(pcm)
        output = []
        while len(self.partial) >= FRAME_BYTES:
            frame = bytes(self.partial[:FRAME_BYTES])
            del self.partial[:FRAME_BYTES]
            voiced = rms(frame) >= self.threshold
            if not self.active:
                if voiced:
                    self.active = list(self.before) + [frame]
                    self.before.clear()
                    self.voiced = 1
                    self.silence = 0
                else:
                    self.before.append(frame)
                continue
            self.active.append(frame)
            if voiced:
                self.voiced += 1
                self.silence = 0
            else:
                self.silence += 1
            if self.silence >= self.silence_frames or len(self.active) >= self.max_frames:
                if self.voiced >= self.min_voiced_frames:
                    output.append(wav_bytes(b''.join(self.active)))
                self.active = []
                self.before.clear()
                self.silence = 0
                self.voiced = 0
        return output
