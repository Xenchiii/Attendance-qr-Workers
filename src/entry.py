from js import Headers, Object, Response as JsResponse
from pyodide.ffi import to_js
from workers import WorkerEntrypoint

import web


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        method = request.method
        body = await request.text() if method == "POST" else ""
        result = await web.handle(method, request.url, request.headers.get("cookie") or "", body, self.env)
        headers = Headers.new()
        for name, value in result.headers:
            headers.append(name, value)
        for cookie in result.cookies:
            headers.append("Set-Cookie", cookie)
        headers.append("Cache-Control", "no-store")
        options = to_js({"status": result.status, "headers": headers}, dict_converter=Object.fromEntries)
        return JsResponse.new(result.body, options)
