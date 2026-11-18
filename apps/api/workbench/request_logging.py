"""Request-level diagnostics without recording input data or credentials."""
import logging
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from workbench.logging_utils import log_event, safe_exception_frames

logger = logging.getLogger('workbench.request')


def install_request_logging(app: FastAPI) -> None:
    @app.middleware('http')
    async def log_request(request: Request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        start = monotonic()
        status = 500
        error_type = None
        frames = None
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers['X-Request-ID'] = request_id
            return response
        except Exception as exc:
            error_type = type(exc).__name__
            frames = safe_exception_frames(exc)
            return JSONResponse({'detail':'Internal server error'}, status_code=500,
                                headers={'X-Request-ID':request_id})
        finally:
            route = request.scope.get('route')
            event = {
                'request_id': request_id,
                'method': request.method,
                'route': getattr(route, 'path', '<unmatched>'),
                'status_code': status,
                'duration_ms': round((monotonic() - start) * 1000, 2),
            }
            if error_type:
                event['error_type'] = error_type
                event['frames'] = frames
            log_event(logger, logging.ERROR if status >= 500 else logging.INFO, 'api', 'http_request', **event)
