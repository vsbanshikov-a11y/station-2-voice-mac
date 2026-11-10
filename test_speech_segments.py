import io
from pathlib import Path
import unittest
import wave

from recognizer import MODEL, WHISPER, recognize
from speech_segments import FRAME_BYTES, Segmenter, wav_bytes


class SegmentationTests(unittest.TestCase):
    def test_silence_and_short_noise_do_not_become_commands(self):
        s = Segmenter()
        self.assertEqual(s.feed(bytes(FRAME_BYTES * 30)), [])
        noise = (1000).to_bytes(2, 'little', signed=True) * 320
        self.assertEqual(s.feed(noise * 2), [])
        self.assertEqual(s.feed(bytes(FRAME_BYTES * 50)), [])

    def test_fragmented_pcm_is_reassembled(self):
        s = Segmenter(threshold=300, min_voiced_frames=2, silence_frames=2)
        frame = (1000).to_bytes(2, 'little', signed=True) * 320
        data = frame * 3 + bytes(FRAME_BYTES * 2)
        result = []
        for i in range(0, len(data), 137):
            result += s.feed(data[i:i+137])
        self.assertEqual(len(result), 1)
        with wave.open(io.BytesIO(result[0]), 'rb') as audio:
            self.assertEqual((audio.getnchannels(), audio.getframerate()), (1, 16000))

    def test_reset_discards_unfinished_private_audio(self):
        s = Segmenter()
        frame = (1000).to_bytes(2, 'little', signed=True) * 320
        s.feed(frame * 12)
        s.reset()
        self.assertEqual(s.feed(bytes(FRAME_BYTES * 45)), [])

    @unittest.skipUnless(MODEL.is_file() and Path(WHISPER).is_file() and
                         (Path(__file__).parent/'tests/data/synthetic_work_phrase.wav').is_file(),
                         'local speech model or generated sample unavailable')
    def test_synthetic_phrase_survives_stream_segmentation(self):
        path = Path(__file__).parent / 'tests/data/synthetic_work_phrase.wav'
        with wave.open(str(path), 'rb') as audio:
            pcm = audio.readframes(audio.getnframes())
        s = Segmenter()
        output = []
        stream = bytes(FRAME_BYTES * 10) + pcm + bytes(FRAME_BYTES * 50)
        for i in range(0, len(stream), FRAME_BYTES):
            output += s.feed(stream[i:i+FRAME_BYTES])
        self.assertEqual(len(output), 1)
        self.assertEqual(recognize(output[0]), 'work')


if __name__ == '__main__':
    unittest.main()
