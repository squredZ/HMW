import pytest
from workbench.dev_config import dev_users_from_env


def test_accepts_multiple_explicit_development_accounts():
    assert dev_users_from_env({'HMW_DEV_USERS_JSON':'{"owner":"first","colleague":"second"}'}) == {'owner':'first','colleague':'second'}


def test_rejects_empty_or_ambiguous_account_configuration():
    with pytest.raises(ValueError):
        dev_users_from_env({'HMW_DEV_USERS_JSON':'{"owner":""}'})
    with pytest.raises(ValueError):
        dev_users_from_env({'HMW_DEV_USERS_JSON':'{"owner":"a"}', 'HMW_DEV_USERNAME':'owner','HMW_DEV_PASSWORD':'b'})
