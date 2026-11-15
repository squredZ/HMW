"""Local-only entrypoint. Never use this identity provider in production."""
import os
from workbench.app import create_app
from workbench.dev_config import dev_users_from_env

app = create_app(os.environ.get('HMW_DATABASE_URL', 'sqlite:///./workbench-dev.db'), dev_users=dev_users_from_env(os.environ))
