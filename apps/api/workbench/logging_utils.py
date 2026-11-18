"""Metadata-only structured logs for the API and background worker."""
import json
import logging
import logging.handlers
import os
import stat
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path


class _PrivateRotatingFileHandler(logging.handlers.RotatingFileHandler):
    def _open(self):
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError('invalid log file')
            if os.name != 'nt':
                os.fchmod(fd, 0o600)
            return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(fd)
            raise


def configure_logging(component: str, directory: Path | None = None) -> None:
    """Write isolated, size-bounded logs to stderr and one file per process."""
    if component not in ('api', 'worker'):
        raise ValueError('invalid log component')
    logger = logging.getLogger('workbench')
    if not any(getattr(handler, '_workbench_handler', False) for handler in logger.handlers):
        path = directory or Path(os.environ.get('HMW_LOG_DIR', Path(__file__).resolve().parents[1] / 'workbench-logs'))
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (os.name != 'nt' and info.st_mode & 0o077):
            raise PermissionError('log directory must be private')
        handlers = [logging.StreamHandler(sys.stderr),
                    _PrivateRotatingFileHandler(path / f'{component}-{os.getpid()}.jsonl',
                                                maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')]
        for handler in handlers:
            handler.setFormatter(logging.Formatter('%(message)s'))
            handler._workbench_handler = True
            logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(logger: logging.Logger, level: int, component: str, event: str, **fields) -> None:
    payload = {'timestamp':datetime.now(timezone.utc).isoformat(), 'level':logging.getLevelName(level),
               'component':component, 'event':event, **fields}
    logger.log(level, json.dumps(payload, ensure_ascii=False))


def safe_exception_frames(exc: BaseException) -> list[dict]:
    """Keep source locations for diagnosis without exception messages or locals."""
    return [{'file':Path(frame.filename).name, 'line':frame.lineno, 'function':frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)[-12:]]
