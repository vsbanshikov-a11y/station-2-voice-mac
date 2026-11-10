"""Local synthetic phrase only: no microphone, network, or account access."""
from pathlib import Path
import unittest

from mode_switch import Switcher
from recognizer import MODEL, WHISPER, recognize


class SyntheticAudioTest(unittest.TestCase):
    @unittest.skipUnless(MODEL.is_file() and Path(WHISPER).is_file() and
                         (Path(__file__).parent/'tests/data/synthetic_work_phrase.wav').is_file(),
                         'local speech model or generated sample unavailable')
    def test_phrase_detected_without_faking_work_readiness(self):
        wav = (Path(__file__).parent / 'tests/data/synthetic_work_phrase.wav').read_bytes()
        self.assertEqual(recognize(wav), 'work')
        switcher = Switcher()
        self.assertEqual(switcher.request('чат переходим в ворк'), 'start_work')
        self.assertEqual(switcher.complete(False), 'work_unavailable')


if __name__ == '__main__':
    unittest.main()
