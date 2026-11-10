import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

import station_transport


class LocalStation:
    def args(self):return ['sh','-c']
    def run(self,command,data=None,check=True,timeout=30):
        return subprocess.run(['sh','-c',command],input=data,capture_output=True,check=check,timeout=timeout)
    def put(self,path,data,mode=0o600):
        Path(path).write_bytes(data);os.chmod(path,mode)


class StationTransportTests(unittest.TestCase):
    def test_relay_preserves_and_restores_fake_original_socket(self):
        root=Path(__file__).parent
        tmp_root='/private/tmp' if Path('/private/tmp').is_dir() else '/tmp'
        with tempfile.TemporaryDirectory(prefix='st-transport-',dir=tmp_root) as tmp:
            binary=Path(tmp)/'relay'
            env={**os.environ,'GO111MODULE':'off','GOCACHE':str(Path(tmp_root)/'station2-go-cache')}
            subprocess.run(['go','build','-o',str(binary),'.'],cwd=root/'relay',env=env,check=True)
            socket_path=str(Path(tmp)/'mic.sock')
            original=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);original.bind(socket_path)
            old=station_transport.SOCKET;station_transport.SOCKET=socket_path
            relay=station_transport.StationRelay(LocalStation(),binary)
            source=None
            try:
                relay.start();self.assertTrue(relay.alive())
                source=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);source.connect(socket_path)
                original.settimeout(2)
                relay.send_control(b'W');time.sleep(.05)
                source.send(b'\x11\x22');self.assertEqual(original.recv(2),b'\0\0')
                relay.send_control(b'N');time.sleep(.05)
                source.send(b'\x33\x44');self.assertEqual(original.recv(2),b'\x33\x44')
                relay.close()
                source.close();source=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);source.connect(socket_path)
                source.send(b'\x55\x66');self.assertEqual(original.recv(2),b'\x55\x66')
            finally:
                relay.close();station_transport.SOCKET=old
                if source:source.close()
                original.close()


if __name__=='__main__':unittest.main()
