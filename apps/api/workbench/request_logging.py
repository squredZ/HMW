"""Request-level diagnostics without recording input data or credentials."""
import json
import logging
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request

logger = logging.getLogger('workbench.request')


def install_request_logging(app: FastAPI) -> None:
    @app.middleware('http')
    async def log_request(request: Request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        start = monotonic()
        status = 500
        error_type = None
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers['X-Request-ID'] = request_id
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            route = request.scope.get('route')
            event = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'component': 'api',
                'event': 'http_request',
                'request_id': request_id,
                'method': request.method,
                'route': getattr(route, 'path', '<unmatched>'),
                'status_code': status,
                'duration_ms': round((monotonic() - start) * 1000, 2),
            }
            if error_type:
                event['error_type'] = error_type
            logger.log(logging.ERROR if status >= 500 else logging.INFO, json.dumps(event, ensure_ascii=False))
