"""Проверка сигнализации WebRTC без микрофона и без платного API.

Временный браузер генерирует предложение; app-server запускает временный чат.
Данные SDP, содержимое чатов и авторизации никогда не печатаются.
"""
import base64
import json
import os
from pathlib import Path
import re
import select
import subprocess
import tempfile
import time

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROOT = Path(__file__).resolve().parent


def create_offer():
    with tempfile.TemporaryDirectory(prefix="station-rtc-") as tmp:
        directory = Path(tmp)
        page = directory / "offer.html"
        page.write_text("""<!doctype html><pre id="result">pending</pre><script>
const pc=new RTCPeerConnection({iceServers:[]});
pc.createDataChannel('oai-events');
pc.addTransceiver('audio',{direction:'sendrecv'});
pc.createOffer().then(o=>pc.setLocalDescription(o)).then(()=>{
document.querySelector('#result').textContent=btoa(pc.localDescription.sdp)
}).catch(e=>document.querySelector('#result').textContent='error:'+e.name);
</script>""")
        result = subprocess.run([CHROME, "--headless=new", "--no-first-run",
            "--disable-background-networking", "--disable-extensions", "--disable-gpu",
            "--user-data-dir=" + str(directory / "profile"), "--virtual-time-budget=4000",
            "--dump-dom", page.as_uri()], capture_output=True, text=True, timeout=25)
        match = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
        if result.returncode or not match or match.group(1) == "pending" or match.group(1).startswith("error:"):
            raise RuntimeError("Не удалось подготовить локальное соединение WebRTC")
        return base64.b64decode(match.group(1)).decode("utf-8")


class Client:
    def __init__(self):
        self.proc = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.buffer = b""
        self.events = []

    def send(self, payload):
        self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        self.proc.stdin.flush()

    def next(self, timeout):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
            if not select.select([self.proc.stdout], [], [], max(0, until - time.monotonic()))[0]:
                break
            data = os.read(self.proc.stdout.fileno(), 131072)
            if not data:
                break
            self.buffer += data
        return None

    def request(self, ident, method, params, timeout=25):
        self.send({"id": ident, "method": method, "params": params})
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            message = self.next(until - time.monotonic())
            if message is None:
                break
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: ошибка {message['error'].get('code')}")
                return message.get("result", {})
            if message.get("method", "").startswith("thread/realtime/"):
                self.events.append(message)
        raise RuntimeError(f"{method}: нет ответа")

    def close(self):
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def main():
    offer = create_offer()
    print("Предложение WebRTC подготовлено", flush=True)
    client = Client()
    thread_id = None
    started = False
    try:
        client.request(1, "initialize", {"clientInfo": {"name": "station-voice-test", "version": "1.0"},
            "capabilities": {"experimentalApi": True}})
        client.send({"method": "initialized", "params": {}})
        account = client.request(2, "account/read", {"refreshToken": False}).get("account") or {}
        if account.get("type") != "chatgpt":
            raise RuntimeError("Проверка остановлена: вход не через ChatGPT")
        thread = client.request(3, "thread/start", {"ephemeral": True, "sandbox": "read-only",
            "approvalPolicy": "on-request", "cwd": str(ROOT)})
        thread_id = thread["thread"]["id"]
        client.request(4, "thread/realtime/start", {"threadId": thread_id,
            "outputModality": "audio", "version": "v3", "transport": {"type": "webrtc", "sdp": offer}}, timeout=30)
        started = True
        until = time.monotonic() + 25
        while time.monotonic() < until:
            event = client.events.pop(0) if client.events else client.next(until - time.monotonic())
            if event is None:
                break
            method = event.get("method", "")
            if method == "thread/realtime/sdp":
                print("Сервер вернул ответ WebRTC; воспроизведение аудио ещё не проверено", flush=True)
                return
            if method == "thread/realtime/error":
                message = str(event.get("params", {}).get("message", ""))
                message = re.sub(r"https?://\S+", "[url]", message)
                message = re.sub(r"(?i)(token|secret|key|authorization|bearer)\s*[:= ]\s*\S+", r"\1=[hidden]", message)
                print("Ошибка голосового соединения:", message[:500], flush=True)
                return
        print("Сервер не вернул ответ за время проверки", flush=True)
    finally:
        if started:
            try:
                client.request(5, "thread/realtime/stop", {"threadId": thread_id}, timeout=4)
            except Exception:
                pass
        client.close()


if __name__ == "__main__":
    main()
