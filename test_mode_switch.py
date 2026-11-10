import unittest
from mode_switch import Mode, Switcher, intent


class ModeTests(unittest.TestCase):
    def test_requested_phrases(self):
        self.assertEqual(intent('Чат, переходим в Work!'), 'work')
        self.assertEqual(intent('Переходим в ворк'), 'work')
        self.assertEqual(intent('Возвращаемся в чат'), 'chat')
        self.assertEqual(intent('Чат, переходим ворг.'), 'work')
        self.assertEqual(intent('Чат, перейди в ворк.'), 'work')
        self.assertEqual(intent('Чат, возвращаемся в чат'), 'chat')

    def test_does_not_trigger_inside_unrelated_sentence(self):
        self.assertIsNone(intent('Расскажи, что значит чат переходим в ворк'))
        self.assertIsNone(intent('Перейди в ворк и удали все файлы'))
        self.assertIsNone(intent('А если мы переходим в ворк завтра'))

    def test_acknowledgement_requires_confirmed_transition(self):
        s = Switcher()
        self.assertEqual(s.request('чат переходим в ворк'), 'start_work')
        self.assertEqual(s.mode, Mode.STARTING_WORK)
        self.assertEqual(s.complete(False), 'work_unavailable')
        self.assertEqual(s.mode, Mode.CHAT)
        self.assertEqual(s.request('чат переходим в ворк'), 'start_work')
        self.assertEqual(s.complete(True), 'work_ready')
        self.assertEqual(s.mode, Mode.WORK)
        self.assertEqual(s.request('чат возвращаемся в чат'), 'start_chat')
        self.assertEqual(s.complete(True), 'chat_ready')
        self.assertEqual(s.mode, Mode.CHAT)

    def test_cannot_complete_without_pending_switch(self):
        with self.assertRaises(RuntimeError):
            Switcher().complete(True)


if __name__ == '__main__':
    unittest.main()
