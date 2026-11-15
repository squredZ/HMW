"""Explicit local development account configuration, not production identity."""
import json
from collections.abc import Mapping


def dev_users_from_env(env: Mapping[str, str]) -> dict[str, str]:
    users_json = env.get('HMW_DEV_USERS_JSON')
    username = env.get('HMW_DEV_USERNAME')
    password = env.get('HMW_DEV_PASSWORD')
    if users_json:
        if username or password:
            raise ValueError('Use only one development account configuration')
        try:
            users = json.loads(users_json)
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid development account configuration') from exc
        if not isinstance(users, dict) or not users or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in users.items()):
            raise ValueError('Development accounts require nonempty usernames and passwords')
        if len(users) != 1:
            raise ValueError('Exactly one development owner account is required')
        return users
    if not username or not password:
        raise ValueError('Set HMW_DEV_USERNAME and HMW_DEV_PASSWORD or HMW_DEV_USERS_JSON')
    return {username: password}
