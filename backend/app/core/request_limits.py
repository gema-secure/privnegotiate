"""Bound OCR request bodies before JSON parsing, including chunked uploads."""
from starlette.responses import JSONResponse


class MenuBodyLimit:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/menu/parse-image":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length", b"0")
        if not length.isdigit() or int(length) > self.max_bytes:
            return await self.reject(scope, receive, send)
        chunks = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                return await self.reject(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)

    async def reject(self, scope, receive, send):
        response = JSONResponse(status_code=413, content={
            "code": "MENU_IMAGE_TOO_LARGE", "message": "图片超过大小限制，请压缩后重试。",
        })
        await response(scope, receive, send)
