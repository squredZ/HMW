"""Bound multipart requests before and during parsing, including chunked bodies."""
from fastapi.responses import JSONResponse
from starlette.formparsers import MultiPartException
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class UploadLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get('path', '')
        if (scope['type'] != 'http' or scope['method'] != 'POST'
                or not path.startswith('/api/projects/') or not path.endswith('/imports')):
            await self.app(scope, receive, send)
            return
        headers = dict(scope['headers'])
        declared = headers.get(b'content-length')
        if declared is not None:
            try:
                size = int(declared)
            except ValueError:
                await JSONResponse({'detail':'Invalid Content-Length'}, status_code=400)(scope, receive, send)
                return
            if size < 0:
                await JSONResponse({'detail':'Invalid Content-Length'}, status_code=400)(scope, receive, send)
                return
            if size > self.max_body_bytes:
                await JSONResponse({'detail':'Upload exceeds request limit'}, status_code=413)(scope, receive, send)
                return

        consumed = 0
        exceeded = False

        async def bounded_receive() -> Message:
            nonlocal consumed, exceeded
            message = await receive()
            if message['type'] == 'http.request':
                consumed += len(message.get('body', b''))
                if consumed > self.max_body_bytes:
                    exceeded = True
                    # The multipart parser closes temporary files on this exception.
                    raise MultiPartException('Upload exceeds request limit')
            return message

        async def bounded_send(message: Message) -> None:
            if exceeded and message['type'] == 'http.response.start':
                message = {**message, 'status': 413}
            await send(message)

        await self.app(scope, bounded_receive, bounded_send)
