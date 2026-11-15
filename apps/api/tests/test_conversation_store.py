import pytest
from workbench.questions.conversation import ConversationStore, ConversationBusy


def test_conversations_are_isolated_and_bounded():
    store = ConversationStore(max_conversations=3, max_turns=2)
    key = ('session-a', 'project-a')
    history = store.begin(key)
    assert history == []
    store.finish(key, '问题一', '回答一')
    store.begin(key); store.finish(key, '问题二', '回答二')
    store.begin(key); store.finish(key, '问题三', '回答三')
    assert store.begin(key) == [{'question':'问题二','answer':'回答二'}, {'question':'问题三','answer':'回答三'}]
    store.abort(key)
    assert store.begin(('session-b', 'project-a')) == []
    store.abort(('session-b', 'project-a'))
    assert store.begin(('session-a', 'project-b')) == []
    store.abort(('session-a', 'project-b'))


def test_concurrent_turns_rejected_and_failed_turn_not_persisted():
    store = ConversationStore()
    key = ('session-a', 'project-a')
    store.begin(key)
    with pytest.raises(ConversationBusy):
        store.begin(key)
    store.abort(key)
    assert store.begin(key) == []
    store.finish(key, '新问题', '新回答')
    store.clear(key)
    assert store.begin(key) == []
    store.abort(key)
