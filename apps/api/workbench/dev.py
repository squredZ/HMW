"""Local-only entrypoint. Never use this identity provider in production."""
import os
from workbench.app import create_app

username = os.environ.get('HMW_DEV_USERNAME')
password = os.environ.get('HMW_DEV_PASSWORD')
if not username or not password:
    raise RuntimeError('Set HMW_DEV_USERNAME and HMW_DEV_PASSWORD for the local demo')
app = create_app(os.environ.get('HMW_DATABASE_URL', 'sqlite:///./workbench-dev.db'), dev_users={username: password})
