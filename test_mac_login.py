import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import mac_login


class Result:
    def __init__(self,code=0):self.returncode=code


class Station:
    def __init__(self,*,existing=False,refresh_error=False,link_error=False):
        self.existing=existing;self.refresh_error=refresh_error;self.link_error=link_error
        self.commands=[];self.writes=[]
    def run(self,command,**_):
        self.commands.append(command)
        if command.startswith('test -s '):return Result(0 if self.existing else 1)
        if 'native-call.sh refresh' in command:return Result(1 if self.refresh_error else 0)
        if command.startswith('ln '):return Result(1 if self.link_error else 0)
        return Result()
    def put(self,path,data,mode=0):self.writes.append((path,data,mode))


class LoginTests(unittest.TestCase):
    def test_existing_session_is_never_overwritten(self):
        station=Station(existing=True)
        with self.assertRaises(RuntimeError):mac_login.install_verified(station,{'synthetic':True},token='a'*32)
        self.assertFalse(station.writes)
        self.assertFalse(any(c.startswith('ln ') for c in station.commands))

    def test_failed_verification_cleans_own_temporary_data(self):
        station=Station(refresh_error=True)
        with self.assertRaises(RuntimeError):mac_login.install_verified(station,{'synthetic':True},token='b'*32)
        self.assertFalse(any(c.startswith('ln ') for c in station.commands))
        self.assertTrue(any(c.startswith('rm -f ') for c in station.commands))

    def test_verified_session_is_linked_without_overwrite(self):
        station=Station();mac_login.install_verified(station,{'synthetic':True},token='c'*32)
        self.assertEqual(station.writes[0][2],0o600)
        self.assertTrue(any('mkdir -m 700 '+mac_login.NATIVE+'/state' in c for c in station.commands))
        self.assertTrue(any(c.startswith('ln ') and ' -f ' not in c for c in station.commands))
        self.assertTrue(any(c.startswith('rm -f ') for c in station.commands))

    def test_racing_session_is_not_replaced(self):
        station=Station(link_error=True)
        with self.assertRaises(RuntimeError):mac_login.install_verified(station,{'synthetic':True},token='d'*32)
        self.assertTrue(any(c.startswith('rm -f ') for c in station.commands))

    def test_browser_rejects_other_origins(self):
        browser=object.__new__(mac_login.ChromePipe)
        with patch.object(browser,'request',return_value={'targetInfos':[{'type':'page','url':'https://chatgpt.com.evil/'}]}):
            with self.assertRaises(RuntimeError):browser.capture()

    def test_private_browser_pipe_with_synthetic_responses(self):
        fake='''#!/usr/bin/env python3
import json,os
buf=b""
while True:
 data=os.read(3,65536)
 if not data:break
 buf+=data
 while b"\\0" in buf:
  raw,buf=buf.split(b"\\0",1);msg=json.loads(raw);m=msg["method"]
  if m=="Target.getTargets":r={"targetInfos":[{"type":"page","url":"https://chatgpt.com/","targetId":"test"}]}
  elif m=="Target.attachToTarget":r={"sessionId":"synthetic"}
  elif m=="Network.getCookies":r={"cookies":[{"name":"__Secure-authjs.session-token","value":"synthetic"},{"name":"other","value":"skip"}]}
  elif m=="Runtime.evaluate":r={"result":{"value":"test-agent" if "userAgent" in msg["params"]["expression"] else {"accessToken":"synthetic","account":{"id":"test"}}}}
  else:r={}
  os.write(4,json.dumps({"id":msg["id"],"result":r}).encode()+b"\\0")
  if m=="Browser.close":raise SystemExit
'''
        with tempfile.TemporaryDirectory() as d:
            executable=Path(d)/'fake-browser';executable.write_text(fake);executable.chmod(0o700)
            with patch.object(mac_login,'CHROME',str(executable)):
                browser=mac_login.ChromePipe(Path(d)/'profile')
                try:
                    payload=browser.capture()
                    self.assertEqual(payload['access_token'],'synthetic')
                    self.assertEqual(payload['cookies'],{'__Secure-authjs.session-token':'synthetic'})
                finally:browser.close()


if __name__=='__main__':unittest.main()
