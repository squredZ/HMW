"""Bounded, in-memory conversation history for temporary visitor sessions."""
from collections import OrderedDict
from threading import Lock
from time import monotonic


class ConversationBusy(Exception):
    """A turn is already running or the conversation capacity is exhausted."""


class ConversationStore:
    def __init__(self, max_conversations: int = 1000, max_turns: int = 5, ttl_seconds: int = 8 * 3600):
        if min(max_conversations, max_turns, ttl_seconds) <= 0:
            raise ValueError('Conversation limits must be positive')
        self._max_conversations = max_conversations
        self._max_turns = max_turns
        self._ttl = ttl_seconds
        self._items: OrderedDict[tuple[str, ...], dict] = OrderedDict()
        self._lock = Lock()

    def begin(self, key: tuple[str, ...]) -> list[dict]:
        with self._lock:
            now = monotonic()
            for old_key, item in list(self._items.items()):
                if not item['busy'] and now - item['updated'] >= self._ttl:
                    del self._items[old_key]
            item = self._items.get(key)
            if item is None:
                while len(self._items) >= self._max_conversations:
                    victim = next((name for name, entry in self._items.items() if not entry['busy']), None)
                    if victim is None:
                        raise ConversationBusy()
                    del self._items[victim]
                item = {'turns':[], 'busy':False, 'updated':now}
                self._items[key] = item
            if item['busy']:
                raise ConversationBusy()
            item['busy'] = True
            item['updated'] = now
            self._items.move_to_end(key)
            return [turn.copy() for turn in item['turns']]

    def finish(self, key: tuple[str, ...], question: str, answer: str, sources: list[tuple[str, str]] | None = None) -> None:
        with self._lock:
            item = self._items[key]
            turn = {'question':question[:240], 'answer':answer[:4000]}
            if sources is not None:
                turn['sources'] = list(sources)
            item['turns'].append(turn)
            item['turns'] = item['turns'][-self._max_turns:]
            item['busy'] = False
            item['updated'] = monotonic()

    def abort(self, key: tuple[str, ...]) -> None:
        with self._lock:
            item = self._items.get(key)
            if item is not None:
                item['busy'] = False
                item['updated'] = monotonic()

    def clear(self, key: tuple[str, ...]) -> None:
        with self._lock:
            item = self._items.get(key)
            if item is not None and item['busy']:
                raise ConversationBusy()
            self._items.pop(key, None)
