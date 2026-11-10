"""Temporary ChatGPT-authenticated Codex voice session; no station access here."""
from pathlib import Path
import os
from queue import Empty
import subprocess
import tempfile
import threading
import time

from pcm_bridge import PCMBridge
from webrtc_probe import CHROME, Client, ROOT

INSTRUCTIONS = (
    'Отвечай по-русски. Выполняй только задачи пользователя. '
    'Не отправляй сообщения, не совершай платежи, не публикуй материалы и не '
    'выполняй необратимые действия без отдельного разрешения пользователя. '
    'Не раскрывай пароли, ключи и данные входа. Если требуется подтверждение, '
    'объясни это и не выполняй действие без него.'
)


class WorkSession:
    def __init__(self, *, cwd=None):
        default_cwd=Path.home()/'Documents/Codex'
        if not default_cwd.is_dir():default_cwd=ROOT
        self.cwd = Path(cwd or os.environ.get('STATION_WORK_CWD') or default_cwd).expanduser()
        self.bridge = None
        self.client = None
        self.chrome = None
        self.profile = None
        self.thread_id = None
        self.started = False
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.return_to_chat = threading.Event()
        self.input_heard = threading.Event()
        self.assistant_spoke = threading.Event()
        self.stop_requested = threading.Event()
        self.pump = None

    def start(self):
        try:
            self.bridge = PCMBridge((ROOT/'work_page.html').read_text())
            url = self.bridge.start()
            self.client = Client()
            self.client.request(1, 'initialize', {'clientInfo':{'name':'station-work-voice','version':'1.0'},
                                                'capabilities':{'experimentalApi':True}})
            self.client.send({'method':'initialized','params':{}})
            account = self.client.request(2, 'account/read', {'refreshToken':False}).get('account') or {}
            if account.get('type') != 'chatgpt':
                raise RuntimeError('chatgpt_sign_in_required')
            thread = self.client.request(3, 'thread/start', {'ephemeral':True,'sandbox':'workspace-write',
                'approvalPolicy':'on-request','approvalsReviewer':'user','cwd':str(self.cwd),
                'developerInstructions':INSTRUCTIONS})
            self.thread_id = thread['thread']['id']
            self.profile = tempfile.TemporaryDirectory(prefix='station-work-')
            self.chrome = subprocess.Popen([CHROME,'--headless=new','--no-first-run','--disable-background-networking',
                '--disable-extensions','--disable-gpu','--autoplay-policy=no-user-gesture-required',
                '--user-data-dir='+str(Path(self.profile.name)/'profile'),url],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            offer=self.bridge.offers.get(timeout=25)
            self.client.request(4,'thread/realtime/start',{'threadId':self.thread_id,'outputModality':'audio',
                'version':'v3','transport':{'type':'webrtc','sdp':offer}},timeout=30)
            self.started=True
            deadline=time.monotonic()+30; answer=None
            while time.monotonic()<deadline:
                event=self.client.events.pop(0) if self.client.events else self.client.next(deadline-time.monotonic())
                if event is None: break
                if event.get('method')=='thread/realtime/sdp': answer=event['params']['sdp']; break
                if event.get('method')=='thread/realtime/error': break
            if not answer: raise RuntimeError('voice_session_unavailable')
            self.bridge.answers.put(answer)
            channel=False;track=False;connected=False;deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                event=self.bridge.events.get(timeout=max(.01,deadline-time.monotonic()))
                kind=event.get('kind')
                if kind=='channel_open': channel=True
                if kind=='audio_track': track=True
                if kind=='connection' and event.get('state')=='connected': connected=True
                if kind in ('browser_error','microphone_error','speaker_error','server_error','session_closed'):
                    raise RuntimeError('voice_channel_unavailable')
                if channel and track and connected:break
            if not (channel and track and connected): raise RuntimeError('voice_channel_timeout')
            self.ready.set()
            self.pump=threading.Thread(target=self._pump,daemon=True)
            self.pump.start()
        except Exception:
            self.close()
            raise

    def _pump(self):
        try:
            while not self.stop_requested.is_set():
                event=self.client.events.pop(0) if self.client.events else self.client.next(.1)
                if event:
                    method=event.get('method','')
                    if event.get('id') is not None and method:
                        # The standalone voice client has no approval UI yet.
                        # Fail closed; never grant permissions automatically.
                        self.client.send({'id':event['id'],'error':{'code':-32000,
                            'message':'Confirmation unavailable in voice session'}})
                    if method=='thread/realtime/error':break
                try:
                    event=self.bridge.events.get_nowait()
                    kind=event.get('kind')
                    if kind=='input_heard':self.input_heard.set()
                    if kind=='assistant_spoke':self.assistant_spoke.set()
                    if kind=='switch_to_chat':self.return_to_chat.set()
                    if kind in ('browser_error','microphone_error','speaker_error','server_error','session_closed'):break
                    if kind=='connection' and event.get('state') in ('failed','closed','disconnected'):break
                except Empty:pass
                if self.chrome.poll() is not None:break
        finally:
            self.ready.clear()
            self.finished.set()

    def send_microphone(self, pcm):
        if self.ready.is_set(): self.bridge.send_microphone(pcm)

    def next_speaker(self, timeout=.2):
        if not self.bridge: return None
        try: return self.bridge.speaker.get(timeout=timeout)
        except Empty: return None

    def close(self):
        self.ready.clear();self.stop_requested.set()
        if self.pump and self.pump is not threading.current_thread():self.pump.join(timeout=3)
        if self.started and self.client:
            try:self.client.request(5,'thread/realtime/stop',{'threadId':self.thread_id},timeout=4)
            except Exception:pass
        if self.chrome and self.chrome.poll() is None:
            self.chrome.terminate()
            try:self.chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:self.chrome.kill();self.chrome.wait()
        if self.client:self.client.close()
        if self.bridge:self.bridge.close()
        if self.profile:self.profile.cleanup()
        self.started=False;self.finished.set()
