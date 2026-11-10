"""Prepare local dependencies and optional user login startup on macOS."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave

ROOT=Path(__file__).resolve().parent
UPSTREAM_URL='https://github.com/rison1337/Alisa-ChatGPT-voice-assistant.git'
PIN='6ba87295b96bb533e89a9cbf9515c0322c6ae5c3'
MODEL_URL='https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base-q5_1.bin'
MODEL_SHA='422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898'
MODEL_SIZE=59707625
LABEL='org.stationcodex.voice'


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def prepare_model(target,*,opener=urllib.request.urlopen):
    target=Path(target)
    if target.exists():
        if target.is_file() and digest(target)==MODEL_SHA:return 'already_present'
        raise RuntimeError('Существующий файл модели не совпадает; он не перезаписан.')
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.download-',dir=target.parent)
    temporary=Path(name)
    try:
        total=0;h=hashlib.sha256()
        with os.fdopen(fd,'wb') as output,opener(MODEL_URL,timeout=60) as response:
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>MODEL_SIZE:raise RuntimeError('Размер модели не совпадает.')
                h.update(chunk);output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if total!=MODEL_SIZE or h.hexdigest()!=MODEL_SHA:raise RuntimeError('Контрольная сумма модели не совпадает.')
        os.link(temporary,target)  # Exclusive: never replace another file.
        return 'downloaded'
    finally:temporary.unlink(missing_ok=True)


def prepare_upstream(path):
    path=Path(path).expanduser()
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        path.mkdir(mode=0o700)  # Exclusive; a failed clone is never silently deleted.
        subprocess.run(['git','clone','--no-checkout',UPSTREAM_URL,str(path)],check=True)
        subprocess.run(['git','-C',str(path),'checkout','--detach',PIN],check=True)
    revision=subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    if revision!=PIN:raise RuntimeError('Папка Alisa Voice содержит другую версию и не изменялась.')
    return path.resolve()


def build_relay(target):
    target=Path(target)
    fd,name=tempfile.mkstemp(prefix='.build-',dir=target.parent);os.close(fd)
    temporary=Path(name)
    try:
        env={**os.environ,'GOOS':'linux','GOARCH':'arm64','CGO_ENABLED':'0','GO111MODULE':'off'}
        subprocess.run(['go','build','-o',str(temporary),'.'],cwd=target.parent,env=env,check=True)
        os.chmod(temporary,0o700)
        if target.exists():
            if target.is_file() and digest(temporary)==digest(target):return 'already_present'
            raise RuntimeError('Существующая сборка relay отличается; она не перезаписана.')
        os.link(temporary,target)
        return 'built'
    finally:temporary.unlink(missing_ok=True)


def prepare_cue(target,*,runner=subprocess.run):
    target=Path(target)
    def valid(path):
        try:
            with wave.open(str(path),'rb') as audio:
                return (audio.getnchannels(),audio.getframerate(),audio.getsampwidth())==(2,48000,2) and 0<audio.getnframes()<=48000*12
        except (OSError,wave.Error,EOFError):return False
    if target.exists():
        if valid(target):return 'already_present'
        raise RuntimeError('Существующее голосовое подтверждение не подходит; оно не перезаписано.')
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.cue-',dir=target.parent) as tmp:
        source=Path(tmp)/'cue.aiff';generated=Path(tmp)/'cue.wav'
        voice=os.environ.get('STATION_SYSTEM_VOICE','Milena')
        runner(['/usr/bin/say','-v',voice,'-o',str(source),'Да, я перешёл в рабочий режим. Слушаю.'],check=True)
        runner(['/usr/bin/afconvert','-f','WAVE','-d','LEI16@48000','-c','2',str(source),str(generated)],check=True)
        if not valid(generated):raise RuntimeError('Не удалось подготовить подтверждение с нужным форматом звука.')
        os.link(generated,target)
    return 'created'


def launch_config(*,root=ROOT,python=sys.executable,paths=None,environment=None):
    env=dict(environment or {})
    env.update({'PATH':':'.join(paths or []),'PYTHONUNBUFFERED':'1'})
    return {'Label':LABEL,'ProgramArguments':[python,str(root/'voice_service.py')],
        'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':15,
        'EnvironmentVariables':env,'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null',
        'ProcessType':'Background'}


def install_autostart(*,root=ROOT,launch_agents=None,runner=subprocess.run):
    agent_dir=Path(launch_agents or Path.home()/'Library/LaunchAgents')
    target=agent_dir/(LABEL+'.plist')
    if target.exists():raise RuntimeError('Автозапуск с таким именем уже существует и не изменён.')
    domain='gui/'+str(os.getuid())
    existing=runner(['/bin/launchctl','print',domain+'/'+LABEL],capture_output=True,text=True,check=False)
    if existing.returncode==0:raise RuntimeError('Служба с таким именем уже загружена и не изменена.')
    tools=[shutil.which('codex'),shutil.which('whisper-cli')]
    if not all(tools):raise RuntimeError('В PATH должны быть доступны codex и whisper-cli.')
    paths=list(dict.fromkeys([str(Path(x).parent) for x in tools]+['/opt/homebrew/bin','/usr/local/bin','/usr/bin','/bin','/usr/sbin','/sbin']))
    env={k:os.environ[k] for k in ('ALISA_HOME','STATION_ALISA_REPO','STATION_WORK_CWD','STATION_WHISPER_CLI') if os.environ.get(k)}
    config=launch_config(root=root,paths=paths,environment=env)
    agent_dir.mkdir(parents=True,exist_ok=True)
    lock_fd=os.open(Path(root)/'voice-service.lock',os.O_RDWR|os.O_CREAT,0o600)
    try:
        fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        raise RuntimeError('Сначала остановите ручной запуск дополнения.') from None
    except OSError:
        os.close(lock_fd)
        raise
    try:fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except Exception:os.close(lock_fd);raise
    inode=os.fstat(fd).st_ino
    loaded=False
    try:
        with os.fdopen(fd,'wb') as stream:plistlib.dump(config,stream);stream.flush();os.fsync(stream.fileno())
        os.close(lock_fd);lock_fd=None
        runner(['/bin/launchctl','bootstrap',domain,str(target)],check=True);loaded=True
        # A loaded plist is not proof of a running process.
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            state=runner(['/bin/launchctl','print',domain+'/'+LABEL],capture_output=True,text=True,check=False)
            if state.returncode==0 and 'state = running' in state.stdout:break
            time.sleep(.5)
        else:raise RuntimeError('Автозапуск не подтвердил запуск процесса.')
    except Exception:
        if lock_fd is not None:os.close(lock_fd)
        if loaded:
            try:runner(['/bin/launchctl','bootout',domain,str(target)],check=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            except Exception:pass
        try:
            if target.lstat().st_ino==inode:target.unlink()
        except OSError:pass
        raise
    return target


def remove_autostart(*,root=ROOT,launch_agents=None,runner=subprocess.run):
    target=Path(launch_agents or Path.home()/'Library/LaunchAgents')/(LABEL+'.plist')
    if not target.is_file() or target.is_symlink():raise RuntimeError('Файл автозапуска не найден; ничего не изменено.')
    before=target.stat()
    with target.open('rb') as stream:config=plistlib.load(stream)
    arguments=config.get('ProgramArguments')
    if config.get('Label')!=LABEL or not isinstance(arguments,list) or len(arguments)<2 or arguments[-1]!=str(Path(root)/'voice_service.py'):
        raise RuntimeError('Автозапуск принадлежит другой установке и не изменён.')
    domain='gui/'+str(os.getuid())
    state=runner(['/bin/launchctl','print',domain+'/'+LABEL],capture_output=True,text=True,check=False)
    if state.returncode==0:
        runner(['/bin/launchctl','bootout',domain,str(target)],check=True)
    after=target.stat()
    if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
        raise RuntimeError('Файл автозапуска изменился; он сохранён.')
    target.unlink()
    return target


def prepare_local(*,root=ROOT):
    missing=[name for name in ('git','go','ssh','whisper-cli','codex') if not shutil.which(name)]
    if missing:raise RuntimeError('Не найдены программы: '+', '.join(missing))
    if not Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').is_file():
        raise RuntimeError('Google Chrome не найден в /Applications.')
    upstream=prepare_upstream(os.environ.get('STATION_ALISA_REPO',str(Path(root).parent/'alisa-voice')))
    model=prepare_model(Path(root)/'models/ggml-base-q5_1.bin')
    binary=build_relay(Path(root)/'relay/station-relay')
    cue=prepare_cue(Path(root)/'assets/work_ready.wav')
    return {'upstream':str(upstream),'model':model,'binary':binary,'cue':cue}


def main():
    p=argparse.ArgumentParser(description='Подготовка зависимостей для Станции 2 на Mac')
    p.add_argument('action',choices=['prepare','autostart','remove-autostart'])
    args=p.parse_args()
    if sys.platform!='darwin':raise SystemExit('Требуется macOS.')
    try:
        if args.action=='prepare':
            result=prepare_local()
            print('Исходный клиент:',result['upstream'])
            print('Модель:',result['model'])
            print('Сборка:',result['binary'])
            print('Голосовое подтверждение:',result['cue'])
            print('Зависимости подготовлены. Колонка не изменялась.')
        elif args.action=='autostart':
            print('Автозапуск будет включён для текущего пользователя Mac.')
            if input('Продолжить? Напишите ДА: ').strip()!='ДА':print('Отменено.');return
            print('Автозапуск установлен:',install_autostart())
        else:
            print('Будет отключён только автозапуск этого дополнения; вход и файлы сохранятся.')
            if input('Продолжить? Напишите ДА: ').strip()!='ДА':print('Отменено.');return
            remove_autostart();print('Автозапуск отключён.')
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print('Подготовка не завершена:',str(e),file=sys.stderr);raise SystemExit(1)


if __name__=='__main__':main()
