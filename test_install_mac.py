import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure_mac
import alisa_cli.runtime as runtime
import alisa_cli.ssh as ssh
import install_mac
import mac_native_install


class Result:
    def __init__(self,code):self.returncode=code


class InstallerTests(unittest.TestCase):
    def test_cancel_before_download_changes_nothing(self):
        with patch.object(install_mac.sys,'platform','darwin'),patch('builtins.input',return_value='НЕТ'),\
             patch.object(install_mac,'prepare_local') as prepare,patch('sys.stdout',new_callable=io.StringIO):
            install_mac.main();prepare.assert_not_called()

    def test_existing_client_and_login_are_not_replaced(self):
        class Station:
            def __init__(self,_):pass
            def run(self,*_,**__):return Result(0)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'native').mkdir();(root/'native/config.json').write_text('{}')
            with patch.object(install_mac.sys,'platform','darwin'),patch('builtins.input',side_effect=['ДА','НЕТ']),\
                 patch.object(install_mac,'prepare_local',return_value={'upstream':d}),\
                 patch.object(configure_mac,'HOME',root),patch('alisa_cli.state.load',return_value={}),\
                 patch.object(runtime,'status',return_value={'agent_connected':True}),patch.object(mac_native_install,'start_preserving') as start,\
                 patch.object(ssh,'Station',Station),patch.object(mac_native_install,'run_install') as install,\
                 patch.object(install_mac.subprocess,'run') as browser,patch('sys.stdout',new_callable=io.StringIO):
                install_mac.main();install.assert_not_called();browser.assert_not_called();start.assert_not_called()

    def test_unknown_firmware_requires_explicit_words_before_writes(self):
        class Station:
            def __init__(self,_):pass
            def run(self,*_,**__):return Result(1)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'native').mkdir();(root/'native/config.json').write_text('{}')
            with patch.object(install_mac.sys,'platform','darwin'),patch('builtins.input',side_effect=['ДА','НЕТ']),\
                 patch.object(install_mac,'prepare_local',return_value={'upstream':d}),\
                 patch.object(configure_mac,'HOME',root),patch('alisa_cli.state.load',return_value={}),\
                 patch.object(ssh,'Station',Station),patch.object(mac_native_install,'require_fresh_install'),patch.object(mac_native_install,'run_install',
                     return_value={'preflight':{'firmware_abi_supported':False}}) as install,\
                 patch('sys.stdout',new_callable=io.StringIO):
                install_mac.main()
                self.assertEqual(install.call_count,1)
                self.assertEqual(install.call_args.kwargs,{'apply':False})

    def test_fresh_flow_requires_each_confirmation(self):
        logged_in={'value':False}
        class Station:
            def __init__(self,_):pass
            def run(self,command,**_):
                return Result(0 if command.startswith('test -s') and logged_in['value'] else 1)
        def login(*_,**__):logged_in['value']=True
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'native').mkdir();(root/'native/config.json').write_text('{}')
            with patch.object(install_mac.sys,'platform','darwin'),patch('builtins.input',side_effect=['ДА']*5),\
                 patch.object(install_mac,'prepare_local',return_value={'upstream':d}),\
                 patch.object(configure_mac,'HOME',root),patch('alisa_cli.state.load',return_value={}),\
                 patch.object(ssh,'Station',Station),patch.object(runtime,'status',return_value={'agent_connected':False}),\
                 patch.object(mac_native_install,'require_fresh_install'),patch.object(mac_native_install,'start_preserving') as start,\
                 patch.object(mac_native_install,'run_install',return_value={'preflight':{'firmware_abi_supported':True}}) as install,\
                 patch.object(install_mac.subprocess,'run',side_effect=login),patch.object(install_mac,'install_autostart') as autostart,\
                 patch('sys.stdout',new_callable=io.StringIO):
                install_mac.main()
                self.assertEqual([c.kwargs['apply'] for c in install.call_args_list],[False,True])
                start.assert_called_once();autostart.assert_called_once()


if __name__=='__main__':unittest.main()
