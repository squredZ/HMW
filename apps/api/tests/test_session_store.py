from datetime import datetime, timedelta, timezone
from workbench.sessions import SessionStore


def test_expired_sessions_removed_and_capacity_bounded():
    now = datetime(2026, 11, 15, tzinfo=timezone.utc)
    store = SessionStore(max_sessions=2)
    store.add('one', 'user', 'csrf', now + timedelta(seconds=1), now)
    store.add('two', 'user', 'csrf', now + timedelta(seconds=20), now)
    store.add('three', 'user', 'csrf', now + timedelta(seconds=30), now + timedelta(seconds=2))
    assert store.get('one', now + timedelta(seconds=2)) is None
    assert store.get('two', now + timedelta(seconds=2))[0] == 'user'
    store.add('four', 'user', 'csrf', now + timedelta(seconds=40), now + timedelta(seconds=3))
    assert store.get('two', now + timedelta(seconds=3)) is None
    assert store.get('three', now + timedelta(seconds=3))[0] == 'user'
    assert store.get('four', now + timedelta(seconds=3))[0] == 'user'
