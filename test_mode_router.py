import threading
import time
import unittest

from mode_router import ModeRouter
from mode_switch import Mode


class Relay:
    def __init__(self, fail_work=False):self.controls=[];self.fail_work=fail_work
    def send_control(self,value):
        if value==b'W' and self.fail_work:raise OSError('offline')
        self.controls.append(value)


class Session:
    def __init__(self, fail=False, gate=None):
        self.ready=threading.Event();self.finished=threading.Event();self.return_to_chat=threading.Event()
        self.fail=fail;self.gate=gate;self.closed=False;self.input=[]
    def start(self):
        if self.gate:self.gate.wait(timeout=3)
        if self.fail:raise RuntimeError('offline')
        self.ready.set()
    def close(self):self.ready.clear();self.finished.set();self.closed=True
    def send_microphone(self,data):self.input.append(data)
    def next_speaker(self,timeout=.2):
        if timeout:time.sleep(min(timeout,.02))
        return None


class Player:
    def __init__(self,fail=False):self.fail=fail;self.closed=False;self.writes=[]
    def start(self):
        if self.fail:raise RuntimeError('offline')
    def alive(self):return not self.closed
    def write(self,data):self.writes.append(data)
    def close(self):self.closed=True


class RouterTests(unittest.TestCase):
    def test_switch_requires_ready_then_returns_to_chat(self):
        relay=Relay();session=Session();player=Player()
        r=ModeRouter(relay,lambda:session,lambda:player,cue=b'cue')
        self.assertTrue(r.start_work());self.assertEqual(r.mode,Mode.WORK)
        r.feed(b'pcm');self.assertEqual(session.input,[b'pcm'])
        r.return_chat();self.assertEqual(r.mode,Mode.CHAT)
        self.assertEqual(relay.controls[0],b'W');self.assertEqual(relay.controls[-1],b'N')
        self.assertTrue(session.closed);self.assertTrue(player.closed)

    def test_failed_start_never_mutes_chat(self):
        relay=Relay();session=Session(fail=True)
        r=ModeRouter(relay,lambda:session,lambda:Player())
        self.assertFalse(r.start_work());self.assertEqual(r.mode,Mode.CHAT)
        self.assertNotIn(b'W',relay.controls);self.assertTrue(session.closed)

    def test_broken_relay_closes_prepared_resources(self):
        relay=Relay(fail_work=True);session=Session();player=Player()
        r=ModeRouter(relay,lambda:session,lambda:player)
        self.assertFalse(r.start_work());self.assertEqual(r.mode,Mode.CHAT)
        self.assertTrue(session.closed);self.assertTrue(player.closed)

    def test_cue_is_written_in_bounded_chunks(self):
        relay=Relay();session=Session();player=Player();cue=bytes(48_000*4*2)
        r=ModeRouter(relay,lambda:session,lambda:player,cue=cue)
        self.assertTrue(r.start_work())
        deadline=time.monotonic()+2
        while sum(map(len,player.writes))<len(cue) and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(sum(map(len,player.writes)),len(cue))
        self.assertTrue(all(len(chunk)<=8192 for chunk in player.writes))
        r.close()

    def test_session_failure_reverts_to_chat(self):
        relay=Relay();session=Session();player=Player()
        r=ModeRouter(relay,lambda:session,lambda:player)
        self.assertTrue(r.start_work());session.ready.clear();session.finished.set()
        deadline=time.monotonic()+2
        while r.mode is not Mode.CHAT and time.monotonic()<deadline:time.sleep(.01)
        self.assertEqual(r.mode,Mode.CHAT);self.assertEqual(relay.controls[-1],b'N')
        r.close()

    def test_stop_while_connecting_never_activates_work(self):
        gate=threading.Event();relay=Relay();session=Session(gate=gate)
        r=ModeRouter(relay,lambda:session,lambda:Player())
        worker=threading.Thread(target=r.start_work);worker.start()
        while r.mode is not Mode.STARTING_WORK:time.sleep(.001)
        r.close();gate.set();worker.join(timeout=3)
        self.assertFalse(worker.is_alive());self.assertNotIn(b'W',relay.controls)
        self.assertTrue(session.closed);self.assertEqual(r.mode,Mode.CHAT)


if __name__=='__main__':unittest.main()
