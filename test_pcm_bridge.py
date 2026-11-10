import http.client
import unittest
from urllib.parse import urlsplit

from pcm_bridge import PCMBridge


class PCMBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bridge = PCMBridge('<!doctype html>test', capacity=2)
        self.url = urlsplit(self.bridge.start())
        self.connection = http.client.HTTPConnection(self.url.hostname, self.url.port, timeout=3)

    def tearDown(self):
        self.connection.close()
        self.bridge.close()

    def request(self, method, path, body=None, headers=None):
        self.connection.request(method, path, body=body, headers=headers or {})
        return self.connection.getresponse()

    def test_wrong_token_and_origin_are_rejected(self):
        response = self.request('GET', '/wrong/'); self.assertEqual(response.status, 403); response.read()
        response = self.request('POST', self.url.path+'speaker', b'\0'*8,
                                {'Origin':'http://untrusted.example'})
        self.assertEqual(response.status, 403); response.read()
        self.assertTrue(self.bridge.speaker.empty())

    def test_microphone_is_streamed_only_to_same_origin(self):
        frame = b'\x01\x02' * 320
        self.bridge.send_microphone(frame)
        response = self.request('GET', self.url.path+'microphone',
                                headers={'Sec-Fetch-Site':'same-origin'})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(len(frame)), frame)
        response.close()

    def test_speaker_validates_pcm_and_queue_is_bounded(self):
        origin = {'Origin':'http://'+self.bridge.host}
        response = self.request('POST', self.url.path+'speaker', b'\0'*3, origin)
        self.assertEqual(response.status, 400); response.read()
        for value in (1, 2, 3):
            response = self.request('POST', self.url.path+'speaker', bytes([value])*8, origin)
            self.assertEqual(response.status, 200); response.read()
        self.assertEqual(self.bridge.speaker.get_nowait(), b'\x02'*8)
        self.assertEqual(self.bridge.speaker.get_nowait(), b'\x03'*8)


if __name__ == '__main__':
    unittest.main()
