import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure_mac


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.key=self.root/'test-key';self.key.write_text('synthetic');self.key.chmod(0o600)
        self.known=self.root/'known_hosts';self.known.write_text('synthetic')
        self.home=patch.object(configure_mac,'HOME',self.root/'state');self.home.start()
    def tearDown(self):self.home.stop();self.tmp.cleanup()

    def test_writes_only_after_success_and_never_overwrites(self):
        target=configure_mac.configure('station.local',2222,str(self.key),known_hosts=self.known,
            status_fn=lambda _: {'agent_connected':True,'phase':'idle'})
        self.assertEqual(target.stat().st_mode&0o777,0o600)
        config=json.loads(target.read_text());self.assertEqual(config['host'],'station.local')
        before=target.read_bytes()
        with self.assertRaises(RuntimeError):configure_mac.configure('other.local',2222,str(self.key),known_hosts=self.known)
        self.assertEqual(target.read_bytes(),before)

    def test_failed_check_or_insecure_key_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            configure_mac.configure('station.local',2222,str(self.key),known_hosts=self.known,
                status_fn=lambda _: {'agent_connected':False})
        self.assertFalse((self.root/'state/native/config.json').exists())
        self.key.chmod(0o644)
        with self.assertRaises(RuntimeError):
            configure_mac.configure('station.local',2222,str(self.key),known_hosts=self.known)

    def test_uninstalled_requires_verified_root_and_exact_model(self):
        absent=lambda _: {'agent_connected':False,'phase':'stopped'}
        with self.assertRaises(RuntimeError):
            configure_mac.configure('station.local',2222,str(self.key),known_hosts=self.known,
                status_fn=absent,allow_uninstalled=True,probe_fn=lambda _:False)
        self.assertFalse((self.root/'state/native/config.json').exists())
        target=configure_mac.configure('station.local',2222,str(self.key),known_hosts=self.known,
            status_fn=absent,allow_uninstalled=True,probe_fn=lambda _:True)
        self.assertFalse(json.loads(target.read_text())['installed'])


if __name__=='__main__':unittest.main()
