import hashlib
import io
import plistlib
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
import wave
from unittest.mock import patch

import setup_mac as setup


class SetupTests(unittest.TestCase):
    def test_model_is_verified_and_never_overwrites(self):
        data=b'synthetic model'
        with tempfile.TemporaryDirectory() as d,patch.object(setup,'MODEL_SIZE',len(data)),\
             patch.object(setup,'MODEL_SHA',hashlib.sha256(data).hexdigest()):
            target=Path(d)/'model.bin'
            self.assertEqual(setup.prepare_model(target,opener=lambda *_,**__:io.BytesIO(data)),'downloaded')
            self.assertEqual(target.read_bytes(),data)
            self.assertEqual(setup.prepare_model(target),'already_present')
            target.write_bytes(b'other')
            with self.assertRaises(RuntimeError):setup.prepare_model(target)
            self.assertEqual(target.read_bytes(),b'other')

    def test_invalid_download_leaves_no_model(self):
        with tempfile.TemporaryDirectory() as d,patch.object(setup,'MODEL_SIZE',4),patch.object(setup,'MODEL_SHA','0'*64):
            target=Path(d)/'model.bin'
            with self.assertRaises(RuntimeError):setup.prepare_model(target,opener=lambda *_,**__:io.BytesIO(b'test'))
            self.assertFalse(target.exists());self.assertEqual(list(Path(d).iterdir()),[])

    def test_cue_is_generated_locally_and_existing_file_preserved(self):
        def run(args,**_):
            if args[0].endswith('/say'):Path(args[-2]).write_bytes(b'synthetic')
            else:
                with wave.open(args[-1],'wb') as audio:
                    audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(48000);audio.writeframes(bytes(480*4))
            return subprocess.CompletedProcess(args,0)
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'assets/cue.wav'
            self.assertEqual(setup.prepare_cue(target,runner=run),'created')
            before=target.read_bytes();self.assertEqual(setup.prepare_cue(target,runner=run),'already_present')
            self.assertEqual(target.read_bytes(),before)

    def test_existing_service_is_not_modified(self):
        calls=[]
        def run(args,**_):
            calls.append(args);return subprocess.CompletedProcess(args,0,stdout='state = running')
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):setup.install_autostart(root=Path(d),launch_agents=Path(d)/'agents',runner=run)
        self.assertEqual([x[1] for x in calls],['print'])

    def test_failed_bootstrap_never_stops_an_existing_job(self):
        calls=[]
        def run(args,**_):
            calls.append(args)
            if args[1]=='print':return subprocess.CompletedProcess(args,1,stdout='')
            if args[1]=='bootstrap':raise subprocess.CalledProcessError(1,args)
            return subprocess.CompletedProcess(args,0,stdout='')
        with tempfile.TemporaryDirectory() as d,patch.object(setup.shutil,'which',side_effect=lambda x:'/bin/'+x):
            root=Path(d)
            with self.assertRaises(subprocess.CalledProcessError):setup.install_autostart(root=root,launch_agents=root/'agents',runner=run)
            self.assertFalse((root/'agents'/(setup.LABEL+'.plist')).exists())
        self.assertNotIn('bootout',[x[1] for x in calls])

    def test_autostart_is_installed_only_after_confirmation_by_runner(self):
        calls=[]
        def run(args,**_):
            calls.append(args)
            if args[1]=='print':
                # First print checks that the label is free; second checks startup.
                count=sum(x[1]=='print' for x in calls)
                return subprocess.CompletedProcess(args,1 if count==1 else 0,stdout='' if count==1 else 'state = running')
            return subprocess.CompletedProcess(args,0,stdout='')
        with tempfile.TemporaryDirectory() as d,patch.object(setup.shutil,'which',side_effect=lambda x:'/bin/'+x):
            root=Path(d);target=setup.install_autostart(root=root,launch_agents=root/'agents',runner=run)
            self.assertTrue(target.is_file());self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertEqual([x[1] for x in calls],['print','bootstrap','print'])

    def test_remove_autostart_refuses_foreign_project(self):
        calls=[]
        def run(args,**_):calls.append(args);return subprocess.CompletedProcess(args,0,stdout='state = running')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);agents=root/'agents';agents.mkdir();target=agents/(setup.LABEL+'.plist')
            target.write_bytes(plistlib.dumps(setup.launch_config(root=root/'different',paths=['/bin'])))
            with self.assertRaises(RuntimeError):setup.remove_autostart(root=root,launch_agents=agents,runner=run)
            self.assertTrue(target.exists());self.assertEqual(calls,[])

    def test_remove_autostart_preserves_configuration(self):
        calls=[]
        def run(args,**_):calls.append(args);return subprocess.CompletedProcess(args,0,stdout='state = running')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);agents=root/'agents';agents.mkdir();target=agents/(setup.LABEL+'.plist')
            target.write_bytes(plistlib.dumps(setup.launch_config(root=root,paths=['/bin'])))
            setup.remove_autostart(root=root,launch_agents=agents,runner=run)
            self.assertFalse(target.exists())
        self.assertEqual([x[1] for x in calls],['print','bootout'])

    @unittest.skipUnless(shutil.which('go'), 'go unavailable')
    def test_build_relay_does_not_overwrite_other_binary(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'relay'
            source=Path(__file__).parent/'relay/main.go'
            shutil.copyfile(source,Path(d)/'main.go')
            self.assertEqual(setup.build_relay(target),'built')
            self.assertEqual(setup.build_relay(target),'already_present')
            target.write_bytes(b'other')
            with self.assertRaises(RuntimeError):setup.build_relay(target)
            self.assertEqual(target.read_bytes(),b'other')

    @unittest.skipUnless(shutil.which('git'), 'git unavailable')
    def test_clone_pin_and_preserve_existing_other_revision(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=root/'source';source.mkdir()
            subprocess.run(['git','init','-q',str(source)],check=True)
            subprocess.run(['git','-C',str(source),'config','user.name','Synthetic Test'],check=True)
            subprocess.run(['git','-C',str(source),'config','user.email','synthetic@example.invalid'],check=True)
            (source/'file').write_text('synthetic')
            subprocess.run(['git','-C',str(source),'add','file'],check=True)
            subprocess.run(['git','-C',str(source),'commit','-qm','test'],check=True)
            pin=subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
            with patch.object(setup,'UPSTREAM_URL',str(source)),patch.object(setup,'PIN',pin):
                cloned=setup.prepare_upstream(root/'clone');self.assertEqual((cloned/'file').read_text(),'synthetic')
                self.assertEqual(setup.prepare_upstream(cloned),cloned)
            with patch.object(setup,'PIN','0'*40):
                with self.assertRaises(RuntimeError):setup.prepare_upstream(cloned)
            self.assertEqual((cloned/'file').read_text(),'synthetic')


if __name__=='__main__':unittest.main()
