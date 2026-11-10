"""macOS preflight and opt-in fresh native installation on an existing root device."""
import argparse
import copy
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
os.environ.setdefault('ALISA_HOME',str(Path.home()/'.local/state/station2-codex-voice'))
REPO=Path(os.environ.get('STATION_ALISA_REPO',str(ROOT.parent/'alisa-voice'))).expanduser().resolve()
sys.path.insert(0,str(REPO))

from alisa_cli import doctor,install
from alisa_cli.runtime import NATIVE,TOOLS,status
from alisa_cli.ssh import Station
from alisa_cli.state import load

PIN='6ba87295b96bb533e89a9cbf9515c0322c6ae5c3'
CHROME=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


def require_fresh_install(config):
    # Refuse even a partial prior install: shared VPN and profile files must not
    # be overwritten by a fresh install with default preferences.
    check=Station(config).run('test -e '+NATIVE+' || test -e '+TOOLS,check=False)
    if check.returncode==0:raise RuntimeError('Обнаружена существующая установка или её файлы; они не изменены.')
    if check.returncode!=1:raise RuntimeError('Не удалось проверить существующие файлы на колонке.')


def start_preserving(config):
    station=Station(config)
    command=('if test -f '+TOOLS+'/vpn-required; then '+TOOLS+'/vpn.sh ready || exit 31; fi; '
             'for p in $(pidof quasar_launcher2 2>/dev/null || true); do '
             'tr "\\000" " " < /proc/$p/cmdline; done')
    check=station.run(command,check=False,timeout=15)
    if check.returncode:raise RuntimeError('Защищённая сеть не готова; запуск остановлен.')
    if b'/data/opt/alice-voice/' in check.stdout:raise RuntimeError('Обнаружен другой аудиомост; он не изменён.')
    station.run(NATIVE+'/run.sh start',timeout=60)
    for _ in range(40):
        result=status(config)
        if result.get('agent_connected'):return result
        import time
        time.sleep(.5)
    raise RuntimeError('Запуск не подтверждён; проверьте состояние колонки.')


def verify_upstream(repo=REPO):
    revision=subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    if revision!=PIN:raise RuntimeError('Исходный Alisa Voice не соответствует закреплённой версии.')
    checked=['alisa_cli','assets/native','engines/native/tools/deploy.py','station']
    for args in (['diff','--quiet','HEAD','--',*checked],['diff','--cached','--quiet','HEAD','--',*checked]):
        if subprocess.run(['git','-C',str(repo),*args],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
            raise RuntimeError('У закреплённого исходного проекта изменены файлы установщика.')


def mac_report(config,*,inspect_fn=doctor.inspect,chrome=CHROME,platform=sys.platform):
    if platform!='darwin':raise RuntimeError('Этот установщик предназначен только для macOS.')
    if not chrome.is_file() or not os.access(chrome,os.X_OK):raise RuntimeError('Google Chrome не найден в /Applications.')
    report=copy.deepcopy(inspect_fn(config))
    # Upstream checks for a Linux executable named chromium. On macOS this
    # project uses the separately verified Chrome app for its login flow.
    report['host_missing']=[x for x in report['host_missing'] if x!='chromium']
    report['ok']=bool(report.get('root') and report.get('model')=='yandexmidi' and
        report.get('mode_supported') and report.get('firmware_files_present') and
        report.get('architecture') in doctor.ARCHITECTURES and
        report.get('free_data_kib') is not None and report['free_data_kib']>12000 and
        not report['host_missing'] and not report['station_missing'])
    if report.get('model')!='yandexmidi':raise RuntimeError('Поддерживается только Яндекс Станция 2 (yandexmidi).')
    return doctor.require(config,report=report)


def run_install(config,*,apply=False,accept_untested=False):
    verify_upstream()
    report=mac_report(config)
    if apply and not report.get('firmware_abi_supported') and not accept_untested:
        raise RuntimeError('Прошивка не входит в проверенный список. Установка не начата.')
    if apply:
        require_fresh_install(config)
    original=install.require
    try:
        install.require=lambda c:mac_report(c)
        return install.install(config,activate=False,dry_run=not apply)
    finally:
        install.require=original


def main():
    parser=argparse.ArgumentParser(description='Проверка и установка Alisa Voice с Mac на Станцию 2 с готовым root/SSH')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--apply',action='store_true',help='установить после отдельного подтверждения')
    group.add_argument('--start',action='store_true',help='запустить уже установленный клиент после входа')
    parser.add_argument('--accept-untested-firmware',action='store_true',help='подтвердить риск неподтверждённой прошивки')
    args=parser.parse_args();config=load('native')
    try:
        if args.start:
            verify_upstream();result=start_preserving(config);print('Команда запуска выполнена; проверьте голос на колонке.')
        else:
            if args.apply:
                preview=run_install(config,apply=False)
                preflight=preview.get('preflight',{})
                if not preflight.get('firmware_abi_supported') and not args.accept_untested_firmware:
                    raise RuntimeError('Прошивка не входит в проверенный список; сначала изучите риск и инструкции.')
                require_fresh_install(config)
                print('Предварительно проверено файлов пакета:',preview.get('verified_files','не определено'))
                print('Установка меняет компоненты на колонке. Существующая установка не перезаписывается.')
                if input('Чтобы продолжить, напишите ДА: ').strip()!='ДА':print('Отменено.');return
            result=run_install(config,apply=args.apply,accept_untested=args.accept_untested_firmware)
            print('Установка завершена.' if args.apply else 'Проверка завершена; изменения на колонке не вносились.')
            if not args.apply:print('Проверено файлов пакета:',result.get('verified_files','не определено'))
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print('Операция не завершена:',str(e),file=sys.stderr);raise SystemExit(1)


if __name__=='__main__':main()
