"""Explicit voice-mode transitions; no device or account access on import."""
import re
import unicodedata
from enum import Enum


class Mode(Enum):
    CHAT = 'chat'
    STARTING_WORK = 'starting_work'
    WORK = 'work'
    RETURNING_CHAT = 'returning_chat'


def intent(text):
    """Accept only an entire, explicitly directed mode-switch command."""
    if not isinstance(text, str) or len(text) > 200:
        return None
    normalized = unicodedata.normalize('NFKC', text).casefold().replace('ё', 'е')
    normalized = ' '.join(re.sub(r'[^a-zа-я0-9]+', ' ', normalized).split())
    words = {
        'переходим в ворк': 'work',
        'переходим в work': 'work',
        'переходим ворг': 'work',
        'переходим в ворг': 'work',
        'чат перейди в ворк': 'work',
        'чат переходим в ворк': 'work',
        'чат переходим ворг': 'work',
        'чат переходим в ворг': 'work',
        'чат переходим в work': 'work',
        'чат перейди в work': 'work',
        'чат вернись в чат': 'chat',
        'чат возвращаемся в чат': 'chat',
        'возвращаемся в чат': 'chat',
    }
    return words.get(normalized)


class Switcher:
    def __init__(self):
        self.mode = Mode.CHAT

    def request(self, text):
        action = intent(text)
        if action == 'work' and self.mode is Mode.CHAT:
            self.mode = Mode.STARTING_WORK
            return 'start_work'
        if action == 'chat' and self.mode is Mode.WORK:
            self.mode = Mode.RETURNING_CHAT
            return 'start_chat'
        return None

    def complete(self, success):
        if self.mode is Mode.STARTING_WORK:
            self.mode = Mode.WORK if success else Mode.CHAT
            return 'work_ready' if success else 'work_unavailable'
        if self.mode is Mode.RETURNING_CHAT:
            self.mode = Mode.CHAT if success else Mode.WORK
            return 'chat_ready' if success else 'chat_unavailable'
        raise RuntimeError('No mode change is pending')
