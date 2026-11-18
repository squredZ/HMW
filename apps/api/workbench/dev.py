"""Local-only entrypoint. Never use this identity provider in production."""
import os
import logging
from pathlib import Path
from workbench.app import create_app
from workbench.dev_config import dev_users_from_env
from workbench.questions.agent import load_agent
from workbench.logging_utils import configure_logging, log_event, safe_exception_frames

configure_logging('api')
logger = logging.getLogger('workbench.startup')
try:
    app = create_app(os.environ.get('HMW_DATABASE_URL', 'sqlite:///./workbench-dev.db'), dev_users=dev_users_from_env(os.environ))
    config_path = os.environ.get('HMW_CONFIG_PATH')
    app.state.answer_agent = load_agent(Path(config_path) if config_path else None)
except Exception as exc:
    log_event(logger, logging.ERROR, 'api', 'api_start_failed', error_type=type(exc).__name__, frames=safe_exception_frames(exc))
    raise SystemExit(1) from None
log_event(logger, logging.INFO, 'api', 'api_started', agent_enabled=app.state.answer_agent is not None)
