"""Private, bounded PCM transport for an isolated local WebRTC voice client.

The server binds only to IPv4 loopback. Access requires an unguessable per-run
token and, for streaming and writes, a matching browser Origin. It never saves
audio or request bodies. It does not itself open a microphone or station.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Full, Queue
import json
import secrets
import threading


class PCMBridge:
    def __init__(self, page, capacity=80):
        self.token = secrets.token_urlsafe(32)
        self.page = page
        self.microphone = Queue(maxsize=capacity)
        self.speaker = Queue(maxsize=capacity)
        self.offers = Queue(maxsize=1)
        self.answers = Queue(maxsize=1)
        self.events = Queue(maxsize=100)
        self.stopped = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *_):
                pass

            def route(self):
                parts = self.path.split('?', 1)[0].split('/')
                if len(parts) < 3 or not secrets.compare_digest(parts[1], outer.token):
                    return None
                if self.client_address[0] != '127.0.0.1' or self.headers.get('Host') != outer.host:
                    return None
                return '/'.join(parts[2:])

            def valid_origin(self):
                origin = self.headers.get('Origin')
                if origin:
                    return origin == 'http://' + outer.host
                return self.command == 'GET' and self.headers.get('Sec-Fetch-Site') == 'same-origin'

            def reply(self, code, payload=b'', content_type='application/octet-stream'):
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def do_GET(self):
                route = self.route()
                if route is None:
                    self.reply(403); return
                if route == '':
                    self.reply(200, outer.page.encode(), 'text/html; charset=utf-8'); return
                if route != 'microphone' or not self.valid_origin():
                    self.reply(403); return
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                try:
                    while not outer.stopped.is_set():
                        try:
                            frame = outer.microphone.get(timeout=0.5)
                        except Empty:
                            continue
                        self.wfile.write(('%x\r\n' % len(frame)).encode() + frame + b'\r\n')
                        self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n')
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

            def do_POST(self):
                route = self.route()
                if route is None or not self.valid_origin():
                    self.reply(403); return
                try:
                    size = int(self.headers.get('Content-Length', '-1'))
                    if size < 0 or size > 131072:
                        self.reply(413); return
                    body = self.rfile.read(size)
                    if len(body) != size:
                        self.reply(400); return
                    if route == 'offer':
                        offer = json.loads(body)['sdp']
                        outer.offers.put(offer, timeout=1)
                        answer = outer.answers.get(timeout=35)
                        self.reply(200, json.dumps({'sdp': answer}).encode(), 'application/json')
                    elif route == 'event':
                        event = json.loads(body)
                        outer.put_latest(outer.events, event)
                        self.reply(200, b'{}', 'application/json')
                    elif route == 'speaker':
                        if len(body) % 4 or len(body) > 32768:
                            self.reply(400); return
                        outer.put_latest(outer.speaker, body)
                        self.reply(200, b'{}', 'application/json')
                    else:
                        self.reply(404)
                except (ValueError, KeyError, Empty, Full, BrokenPipeError, ConnectionResetError, OSError):
                    try: self.reply(500)
                    except OSError: pass

        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                pass

        self.server = QuietServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @staticmethod
    def put_latest(queue, value):
        try:
            queue.put_nowait(value)
        except Full:
            try: queue.get_nowait()
            except Empty: pass
            try: queue.put_nowait(value)
            except Full: pass

    def send_microphone(self, frame):
        if not isinstance(frame, bytes) or not frame or len(frame) % 2 or len(frame) > 4096:
            raise ValueError('invalid microphone frame')
        self.put_latest(self.microphone, frame)

    def start(self):
        self.thread.start()
        return 'http://' + self.host + '/' + self.token + '/'

    def close(self):
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
