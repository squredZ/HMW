"""Local-only entrypoint. Never use this identity provider in production."""
import os
from pathlib import Path
from workbench.app import create_app
from workbench.dev_config import dev_users_from_env
from workbench.questions.agent import load_agent

app = create_app(os.environ.get('HMW_DATABASE_URL', 'sqlite:///./workbench-dev.db'), dev_users=dev_users_from_env(os.environ))

config_path = os.environ.get('HMW_CONFIG_PATH')
app.state.answer_agent = load_agent(Path(config_path) if config_path else None)
