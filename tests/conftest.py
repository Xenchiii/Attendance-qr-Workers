import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
sys.path.insert(0, HERE)

import dbx
import services
import web
from d1shim import Binding

SCHEMA = os.path.join(os.path.dirname(HERE), "schema.sql")


class Client:
    def __init__(self):
        self.jar = {}

    async def request(self, method, path, form=None):
        from urllib.parse import urlencode
        header = "; ".join(k + "=" + v for k, v in self.jar.items())
        body = urlencode(form) if form else ""
        result = await web.handle(method, "https://example.test" + path, header, body)
        for cookie in result.cookies:
            pair = cookie.split(";")[0]
            name, value = pair.split("=", 1)
            if "Max-Age=0" in cookie:
                self.jar.pop(name, None)
            else:
                self.jar[name] = value
        return result

    async def get(self, path):
        return await self.request("GET", path)

    async def post(self, path, **form):
        return await self.request("POST", path, form)

    async def login(self, role, email, password):
        result = await self.post("/login", role=role, email=email, password=password)
        assert result.status == 303
        return result


@pytest.fixture
def client():
    dbx.use(Binding(SCHEMA))
    services.reset_ready()
    return Client()


@pytest.fixture
def make_client():
    def factory():
        return Client()
    return factory
