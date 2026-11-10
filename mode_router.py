"""Fail-safe transition coordinator. Dependencies are supplied by the caller."""
import threading
import time

from mode_switch import Mode


class ModeRouter:
    def __init__(self, relay, make_session, make_player, cue=b'', report=None):
        self.relay=relay;self.make_session=make_session;self.make_player=make_player;self.cue=cue
        self.lock=threading.RLock();self.mode=Mode.CHAT;self.session=None;self.player=None
        self.stopping=threading.Event();self.work_running=threading.Event();self.generation=0
        self.heartbeat=None;self.output=None
        self.report=report or (lambda _:None)

    def start_work(self):
        with self.lock:
            if self.mode is not Mode.CHAT or self.stopping.is_set():return False
            self.mode=Mode.STARTING_WORK;self.generation+=1;generation=self.generation
        session=None;player=None
        try:
            session=self.make_session();session.start()
            if self.stopping.is_set():raise RuntimeError('stopped')
            player=self.make_player();player.start()
            with self.lock:
                if self.stopping.is_set() or self.generation!=generation:raise RuntimeError('cancelled')
                self.session=session;self.player=player
                # The lease is asserted only after the voice service and player are ready.
                self.relay.send_control(b'W')
                self.work_running.set();self.mode=Mode.WORK
                self.heartbeat=threading.Thread(target=self._heartbeat,args=(generation,),daemon=True)
                self.output=threading.Thread(target=self._output,args=(generation,),daemon=True)
                self.heartbeat.start();self.output.start()
            self.report('work_ready')
            return True
        except Exception:
            self.work_running.clear()
            try:self.relay.send_control(b'N')
            except Exception:pass
            if player:
                try:player.close()
                except Exception:pass
            if session:
                try:session.close()
                except Exception:pass
            with self.lock:
                if self.generation==generation:
                    self.session=None;self.player=None;self.mode=Mode.CHAT
            self.report('work_unavailable')
            return False

    def _heartbeat(self,generation):
        while self.work_running.wait(.4):
            with self.lock:
                session=self.session;player=self.player
                alive=(self.generation==generation and self.mode is Mode.WORK and
                       session is not None and session.ready.is_set() and player is not None and player.alive())
            if not alive:break
            try:self.relay.send_control(b'W')
            except Exception:break
            time.sleep(.4)
        with self.lock: should_return=self.generation==generation and self.mode is Mode.WORK
        if should_return:self.return_chat()

    def _output(self,generation):
        try:
            with self.lock: session=self.session;player=self.player
            # No stale audio that was queued before the transition is played.
            while session.next_speaker(timeout=0) is not None:pass
            # Keep each write short: the player consumes PCM at real-time speed.
            for offset in range(0,len(self.cue),8192):
                if not self.work_running.is_set():break
                player.write(self.cue[offset:offset+8192])
            while self.work_running.is_set() and not self.stopping.is_set():
                with self.lock:
                    if self.generation!=generation or self.mode is not Mode.WORK:break
                if session.return_to_chat.is_set() or session.finished.is_set():break
                chunk=session.next_speaker(timeout=.2)
                if chunk:player.write(chunk)
        except Exception:pass
        with self.lock: should_return=self.generation==generation and self.mode is Mode.WORK
        if should_return:self.return_chat()

    def feed(self,pcm):
        with self.lock: session=self.session if self.mode is Mode.WORK else None
        if session and session.ready.is_set():session.send_microphone(pcm)

    def return_chat(self):
        with self.lock:
            was_work=self.mode is Mode.WORK
            self.generation+=1
            session,self.session=self.session,None
            player,self.player=self.player,None
            self.work_running.clear();self.mode=Mode.CHAT
        try:self.relay.send_control(b'N')
        except Exception:pass  # The short lease also expires on a broken connection.
        if player:
            try:player.close()
            except Exception:pass
        if session:
            try:session.close()
            except Exception:pass
        if was_work:self.report('chat_ready')

    def close(self):
        self.stopping.set();self.return_chat()
