"""Create a separate private Mac connection configuration; no device changes."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys

ROOT=Path(__file__).resolve().parent
os.environ.setdefault('ALISA_HOME',str(Path.home()/'.local/state/station2-codex-voice'))
repo=Path(os.environ.get('STATION_ALISA_REPO',str(ROOT.parent/'alisa-voice'))).expanduser()
sys.path.insert(0,str(repo))

from alisa_cli.runtime import status
from alisa_cli.ssh import Station
from alisa_cli.state import HOME,defaults,validate


def verify_root_station(config):
    result=Station(config).run("id -u; tr '\\000' '\\n' </proc/device-tree/compatible",timeout=15)
    lines=result.stdout.decode('utf-8',errors='replace').splitlines()
    models={'yandex,station-midi','yandexmidi','station-midi'}
    return bool(lines and lines[0].strip()=='0' and any(line.strip() in models for line in lines[1:]))


def configure(host,port,identity,*,status_fn=status,known_hosts=None,allow_uninstalled=False,probe_fn=verify_root_station):
    target=HOME/'native/config.json'
    if target.exists():raise RuntimeError('Настройки уже существуют. Они не изменены.')
    key=Path(identity).expanduser().resolve(strict=True)
    st=key.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)&0o077:
        raise RuntimeError('SSH-ключ должен быть обычным файлом с доступом только владельцу.')
    known=Path(known_hosts) if known_hosts else Path.home()/'.ssh/known_hosts'
    if not known.is_file():raise RuntimeError('Сначала подтвердите ключ колонки в обычном SSH-клиенте.')
    config=defaults('native')
    config.update({'host':host,'port':port,'user':'root','identity':str(key),
                   'known_hosts':str(known),'ssh_explicit':True,'installed':False})
    validate(config)
    result=status_fn(config)
    installed=bool(result.get('agent_connected') and result.get('phase') in ('idle','active'))
    if not installed:
        if not allow_uninstalled or not probe_fn(config):
            raise RuntimeError('Нужна Станция 2 с проверенным root и SSH. Существующая настройка не изменена.')
    config['installed']=installed
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    own_inode=os.fstat(fd).st_ino
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(config,stream,ensure_ascii=False,indent=2)
            stream.flush();os.fsync(stream.fileno())
    except Exception:
        try:
            if target.lstat().st_ino==own_inode:target.unlink()
        except OSError:pass
        raise
    return target


def main():
    p=argparse.ArgumentParser(description='Настроить отдельное безопасное SSH-подключение на Mac')
    p.add_argument('--host',required=True,help='локальный адрес колонки')
    p.add_argument('--port',type=int,default=2222,help='SSH-порт колонки')
    p.add_argument('--identity',required=True,help='путь к уже существующему закрытому SSH-ключу')
    p.add_argument('--allow-uninstalled',action='store_true',help='проверить root и модель, если Alisa Voice ещё не установлен')
    args=p.parse_args()
    try:
        target=configure(args.host,args.port,args.identity,allow_uninstalled=args.allow_uninstalled)
        print('Соединение проверено, настройки сохранены локально:',target)
    except (OSError,ValueError,RuntimeError) as e:
        print('Настройка не завершена:',str(e),file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':main()
