from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mac_native_install as target


def sample_report():
    return {'root':True,'model':'yandexmidi','plum':True,'mode_supported':True,
            'firmware_files_present':True,'firmware_abi_supported':True,
            'architecture':'aarch64','free_data_kib':100000,'host_missing':['chromium'],
            'station_missing':[],'ok':False}


class MacNativeTests(unittest.TestCase):
    def test_mac_chrome_substitutes_only_linux_browser_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome=Path(tmp)/'chrome';chrome.write_text('synthetic');chrome.chmod(0o700)
            report=target.mac_report({},inspect_fn=lambda _:sample_report(),chrome=chrome,platform='darwin')
            self.assertTrue(report['ok']);self.assertEqual(report['host_missing'],[])
            bad=sample_report();bad['station_missing']=['aplay']
            with self.assertRaises(RuntimeError):target.mac_report({},inspect_fn=lambda _:bad,chrome=chrome,platform='darwin')
            bad=sample_report();bad['model']='mini2'
            with self.assertRaises(RuntimeError):target.mac_report({},inspect_fn=lambda _:bad,chrome=chrome,platform='darwin')

    def test_existing_install_is_never_overwritten(self):
        report={'firmware_abi_supported':True}
        class S:
            def __init__(self,_):pass
            def run(self,*_,**__):return type('Result',(),{'returncode':0})()
        with patch.object(target,'verify_upstream'),patch.object(target,'mac_report',return_value=report),patch.object(target,'Station',S):
            with self.assertRaises(RuntimeError):target.run_install({},apply=True)

    def test_unknown_firmware_requires_explicit_opt_in(self):
        with patch.object(target,'verify_upstream'),patch.object(target,'mac_report',return_value={'firmware_abi_supported':False}):
            with self.assertRaises(RuntimeError):target.run_install({},apply=True)

    def test_installer_preflight_hook_is_restored_on_failure(self):
        original=target.install.require
        with patch.object(target,'verify_upstream'),patch.object(target,'mac_report',return_value={'firmware_abi_supported':True}),\
             patch.object(target.install,'install',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):target.run_install({},apply=False)
        self.assertIs(target.install.require,original)

    def test_vpn_failure_prevents_start_without_changing_settings(self):
        commands=[]
        class S:
            def __init__(self,_):pass
            def run(self,command,**_):
                commands.append(command)
                return type('Result',(),{'returncode':31,'stdout':b''})()
        with patch.object(target,'Station',S):
            with self.assertRaises(RuntimeError):target.start_preserving({})
        self.assertEqual(len(commands),1)
        self.assertNotIn('run.sh start',commands[0])
        self.assertNotIn('rm ',commands[0])

    def test_existing_other_audio_bridge_prevents_start(self):
        commands=[]
        class S:
            def __init__(self,_):pass
            def run(self,command,**_):
                commands.append(command)
                return type('Result',(),{'returncode':0,'stdout':b'/data/opt/alice-voice/client'})()
        with patch.object(target,'Station',S):
            with self.assertRaises(RuntimeError):target.start_preserving({})
        self.assertEqual(len(commands),1)


if __name__=='__main__':unittest.main()
