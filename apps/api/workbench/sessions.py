"""Bounded, process-local sessions used only by the development identity provider."""
from collections import OrderedDict
from datetime import datetime
from threading import Lock


class SessionStore:
    def __init__(self, max_sessions: int = 1000):
        if max_sessions < 1:
            raise ValueError('max_sessions must be positive')
        self._max_sessions = max_sessions
        self._sessions: OrderedDict[str, tuple[str, str, datetime]] = OrderedDict()
        self._lock = Lock()

    def add(self, token: str, user: str, csrf: str, expires: datetime, now: datetime) -> None:
        with self._lock:
            for key, value in list(self._sessions.items()):
                if value[2] <= now:
                    del self._sessions[key]
            self._sessions.pop(token, None)
            while len(self._sessions) >= self._max_sessions:
                self._sessions.popitem(last=False)
            self._sessions[token] = (user, csrf, expires)

    def get(self, token: str, now: datetime) -> tuple[str, str, datetime] | None:
        with self._lock:
            session = self._sessions.get(token)
            if session and session[2] <= now:
                del self._sessions[token]
                return None
            return session

    def remove(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)
