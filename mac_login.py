"""Explicit one-time ChatGPT login in a new private Chrome profile on macOS.

The owner signs in manually. The existing Chrome profile is never opened.
The verified session is installed only if the Station has no existing session.
No credentials or browser responses are printed or committed to this project.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import select
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent
os.environ.setdefault('ALISA_HOME',str(Path.home()/'.local/state/station2-codex-voice'))
repo=Path(os.environ.get('STATION_ALISA_REPO',str(ROOT.parent/'alisa-voice'))).expanduser()
sys.path.insert(0,str(repo))
from alisa_cli.ssh import Station
from alisa_cli.state import HOME,load
from configure_mac import verify_root_station
from mac_native_install import verify_upstream

CHROME='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
NATIVE='/data/opt/alisa-standalone'
TOOLS='/data/opt/alisa-cli'


class ChromePipe:
    def __init__(self,profile):
        read_child,write_parent=os.pipe();read_parent,write_child=os.pipe()
        wrapper='''import os, sys
incoming = int(sys.argv[1])
outgoing = int(sys.argv[2])
os.dup2(incoming, 3)
os.dup2(outgoing, 4)
for descriptor in {incoming, outgoing} - {3, 4}:
    os.close(descriptor)
os.execv(sys.argv[3], sys.argv[3:])
'''
        try:
            self.process=subprocess.Popen([sys.executable,'-c',wrapper,str(read_child),str(write_child),
                CHROME,'--user-data-dir='+str(profile),'--remote-debugging-pipe','--no-first-run',
                '--no-default-browser-check','https://chatgpt.com/'],pass_fds=(read_child,write_child),
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        except Exception:
            for fd in (read_child,write_parent,read_parent,write_child):os.close(fd)
            raise
        os.close(read_child);os.close(write_child)
        self.read_fd=read_parent;self.write_fd=write_parent;self.buffer=b'';self.counter=0

    def request(self,method,params=None,session=None,timeout=25):
        self.counter+=1;number=self.counter
        message={'id':number,'method':method,'params':params or {}}
        if session:message['sessionId']=session
        data=json.dumps(message).encode()+b'\0'
        while data:
            count=os.write(self.write_fd,data)
            if count<=0:raise RuntimeError('Окно входа закрыто.')
            data=data[count:]
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            while b'\0' in self.buffer:
                raw,self.buffer=self.buffer.split(b'\0',1)
                response=json.loads(raw)
                if response.get('id')==number:
                    if 'error' in response:raise RuntimeError('Окно входа не подтвердило запрос.')
                    return response.get('result',{})
            if not select.select([self.read_fd],[],[],max(0,until-time.monotonic()))[0]:break
            chunk=os.read(self.read_fd,65536)
            if not chunk:break
            self.buffer+=chunk
            if len(self.buffer)>4*1024*1024:raise RuntimeError('Ответ окна входа слишком велик.')
        raise RuntimeError('Окно входа не ответило вовремя.')

    def capture(self):
        targets=self.request('Target.getTargets').get('targetInfos',[])
        page=next((x for x in targets if x.get('type')=='page' and x.get('url','').startswith('https://chatgpt.com/')),None)
        if not page:raise RuntimeError('Откройте ChatGPT и завершите вход в отдельном окне Chrome.')
        session=self.request('Target.attachToTarget',{'targetId':page['targetId'],'flatten':True})['sessionId']
        result=self.request('Runtime.evaluate',{'expression':"location.origin==='https://chatgpt.com'?fetch('/api/auth/session',{credentials:'include'}).then(r=>r.json()):null",
                                               'awaitPromise':True,'returnByValue':True},session)
        data=result.get('result',{}).get('value') or {}
        if not isinstance(data,dict) or not data.get('accessToken'):
            raise RuntimeError('Вход ChatGPT ещё не завершён.')
        cookies=self.request('Network.getCookies',{'urls':['https://chatgpt.com/']},session).get('cookies',[])
        selected={c['name']:c['value'] for c in cookies if c.get('name','').startswith(
            ('__Secure-next-auth.session-token','__Secure-authjs.session-token')) or c.get('name') in ('oai-did','cf_clearance')}
        if not any('session-token' in key for key in selected):raise RuntimeError('Не удалось проверить сессию входа.')
        agent=self.request('Runtime.evaluate',{'expression':'navigator.userAgent','returnByValue':True},session)
        account=data.get('account') if isinstance(data.get('account'),dict) else {}
        return {'access_token':data['accessToken'],'expires':data.get('expires'),
            'account_id':account.get('id'),'cookies':selected,
            'user_agent':agent.get('result',{}).get('value')}

    def close(self):
        try:self.request('Browser.close',timeout=3)
        except Exception:pass
        for fd in (self.write_fd,self.read_fd):
            try:os.close(fd)
            except OSError:pass
        try:self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()


def no_existing_session(station):
    result=station.run('test -s '+NATIVE+'/state/web-session.json',check=False,timeout=10)
    if result.returncode==0:raise RuntimeError('На колонке уже есть вход; он не будет заменён.')
    if result.returncode!=1:raise RuntimeError('Не удалось безопасно проверить существующий вход.')


def install_verified(station,payload,*,token=None):
    token=token or secrets.token_hex(16)
    if len(token)!=32 or any(x not in '0123456789abcdef' for x in token):raise ValueError('invalid temporary identifier')
    directory=TOOLS+'/mac-auth-'+token
    target=NATIVE+'/state/web-session.json'
    no_existing_session(station)
    station.run('umask 077; mkdir -m 700 '+directory,timeout=10)
    try:
        station.put(directory+'/web-session.json',json.dumps(payload).encode(),mode=0o600)
        checked=station.run(TOOLS+'/native-call.sh refresh '+directory,check=False,timeout=60)
        if checked.returncode:raise RuntimeError('Колонка не смогла подтвердить новый вход; предыдущая сессия не изменена.')
        # A fresh installation need not have started the service yet, so its
        # state directory may not exist. Never change an existing directory.
        state=NATIVE+'/state'
        station.run('test -d '+NATIVE+' && (test -d '+state+' || mkdir -m 700 '+state+' || test -d '+state+')',timeout=10)
        # Atomic no-overwrite: a concurrent login cannot be replaced.
        linked=station.run('ln '+directory+'/web-session.json '+target,check=False,timeout=10)
        if linked.returncode:raise RuntimeError('Вход не сохранён: существующая сессия или ошибка на колонке.')
    finally:
        station.run('rm -f '+directory+'/web-session.json '+directory+'/web-session.json.new; rmdir '+directory+' 2>/dev/null || true',check=False,timeout=10)


def main():
    parser=argparse.ArgumentParser(description='Вход ChatGPT через отдельное окно Chrome на Mac')
    parser.parse_args()
    if sys.platform!='darwin':raise SystemExit('Требуется macOS.')
    if not Path(CHROME).is_file():raise SystemExit('Google Chrome не найден в /Applications.')
    verify_upstream();config=load('native')
    if not verify_root_station(config):raise SystemExit('Не подтверждены модель Станции 2 и root-доступ.')
    station=Station(config);no_existing_session(station)
    state_dir=HOME/'native';state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
    print('Откроется новое отдельное окно Chrome. Войдите в свой ChatGPT на сайте.')
    print('После подтверждения действующая сессия будет сохранена на колонке. Доступ к ней даёт доступ к аккаунту.')
    with tempfile.TemporaryDirectory(prefix='mac-login-',dir=state_dir) as tmp:
        profile=Path(tmp);profile.chmod(0o700);browser=ChromePipe(profile)
        try:
            input('Когда вход завершён и открыт ChatGPT, нажмите Enter здесь: ')
            payload=browser.capture()
            if input('Передать проверяемый вход на колонку? Напишите ДА: ').strip()!='ДА':
                print('Отменено.');return
            install_verified(station,payload)
            print('Вход проверен и сохранён на колонке.')
        finally:browser.close()


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print('Вход не завершён:',str(e),file=sys.stderr);raise SystemExit(1)
