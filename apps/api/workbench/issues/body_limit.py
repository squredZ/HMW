"""Bound the visitor issue request before JSON parsing, including chunked bodies."""
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class IssueBodyLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = 256 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or scope['method'] != 'POST' or scope['path'] != '/api/guest/issues':
            await self.app(scope, receive, send)
            return
        chunks = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(chunk) > self.max_bytes - len(chunks):
                await JSONResponse({'detail':'Request too large'}, status_code=413)(scope, receive, send)
                return
            chunks.extend(chunk)
            if not message.get('more_body', False):
                break
        body = bytes(chunks)
        delivered = False
        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type':'http.request', 'body':body, 'more_body':False}
        await self.app(scope, replay, send)
