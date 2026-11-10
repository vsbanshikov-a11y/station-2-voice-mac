"""Interactive macOS setup for Station 2 with already established root and SSH."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
os.environ.setdefault('ALISA_HOME',str(Path.home()/'.local/state/station2-codex-voice'))
from setup_mac import prepare_local,install_autostart


def confirmed(question):
    return input(question+' Напишите ДА для продолжения: ').strip()=='ДА'


def main():
    if sys.platform!='darwin':raise RuntimeError('Этот установщик предназначен для macOS.')
    print('Станция 2 + ChatGPT/Codex на Mac: интерактивная установка.')
    print('Потребуются Станция 2 с уже работающими root/SSH, доверенный ключ хоста и SSH-ключ на Mac.')
    print('Root не устанавливается автоматически; инструкция: docs/ROOT.md.')
    if not confirmed('Исходный код и модель будут загружены; прошивка и DNS автоматически не меняются.'):
        print('Отменено.');return
    prepared=prepare_local();print('Зависимости подготовлены.')
    os.environ.setdefault('STATION_ALISA_REPO',prepared['upstream'])
    from configure_mac import configure,HOME
    from alisa_cli.state import load
    from alisa_cli.runtime import status,NATIVE
    from alisa_cli.ssh import Station
    from mac_native_install import run_install,start_preserving,require_fresh_install

    config_path=HOME/'native/config.json'
    if not config_path.exists():
        print('Укажите данные проверенного SSH-подключения. Пароль или содержимое ключа вводить не нужно.')
        host=input('Локальное имя или адрес Станции: ').strip()
        port_text=input('SSH-порт [2222]: ').strip()
        port=int(port_text or '2222')
        key=input('Путь к закрытому SSH-ключу на Mac: ').strip()
        configure(host,port,key,allow_uninstalled=True)
        print('Отдельное подключение сохранено локально.')
    else:print('Существующая локальная настройка сохранена без изменений.')
    config=load('native');station=Station(config)

    check=station.run('test -e '+NATIVE+'/bin/alisa-standalone',check=False,timeout=10)
    if check.returncode==1:
        preview=run_install(config,apply=False)
        require_fresh_install(config)
        unverified=not preview.get('preflight',{}).get('firmware_abi_supported')
        if unverified:
            print('Эта прошивка не входит в список проверенных исходным проектом. Работа не гарантируется.')
            if input('Для продолжения на свой риск напишите ПРИНИМАЮ РИСК: ').strip()!='ПРИНИМАЮ РИСК':
                print('Установка остановлена без изменений колонки.');return
        if not confirmed('Будут установлены компоненты Alisa Voice; существующая установка не заменяется.'):
            print('Отменено.');return
        run_install(config,apply=True,accept_untested=unverified);config=load('native')
        print('Нативные компоненты установлены.')
    elif check.returncode==0:
        print('Существующий Alisa Voice оставлен без изменений.')
    else:raise RuntimeError('Не удалось проверить наличие клиента на колонке.')

    check=station.run('test -s '+NATIVE+'/state/web-session.json',check=False,timeout=10)
    if check.returncode==1:
        print('Для голоса нужен отдельный вход ChatGPT на колонке.')
        if not confirmed('Откроется новое окно Chrome; после входа сессия будет передана на колонку.'):
            print('Остановлено до входа.');return
        subprocess.run([sys.executable,str(ROOT/'mac_login.py')],check=True)
        check=station.run('test -s '+NATIVE+'/state/web-session.json',check=False,timeout=10)
        if check.returncode!=0:raise RuntimeError('Вход не подтверждён; дальнейший запуск остановлен.')
    elif check.returncode==0:print('Существующий вход оставлен без изменений.')
    else:raise RuntimeError('Не удалось проверить существующий вход.')

    state=status(config)
    if not state.get('agent_connected'):
        if not confirmed('Запустить установленный голосовой клиент на колонке?'):
            print('Остановлено до запуска клиента.');return
        start_preserving(config);print('Команда запуска клиента выполнена.')
    else:print('Голосовой клиент колонки уже работает.')

    print('Осталось включить сервис переключения на Mac.')
    if confirmed('Включить автоматический запуск после входа на Mac?'):
        install_autostart();print('Автозапуск установлен.')
    else:print('Для ручного запуска из этой папки выполните: python3 voice_service.py')
    print('Скажите «Алиса, открой чат», затем «Переходим в ворк».')


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as e:
        print('Установка остановлена:',str(e),file=sys.stderr);raise SystemExit(1)
