import hashlib
import hmac
import re
import secrets

try:
    from js import Object, Uint8Array, crypto
    from pyodide.ffi import to_js
    ON_WORKER = True
except ImportError:
    ON_WORKER = False

ITERATIONS = 100000


async def derive(password, salt):
    if not ON_WORKER:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS, 32)
    raw = Uint8Array.new(to_js(list(password.encode("utf-8"))))
    key = await crypto.subtle.importKey("raw", raw, "PBKDF2", False, to_js(["deriveBits"]))
    params = to_js(
        {"name": "PBKDF2", "salt": Uint8Array.new(to_js(list(salt))), "iterations": ITERATIONS, "hash": "SHA-256"},
        dict_converter=Object.fromEntries,
    )
    bits = await crypto.subtle.deriveBits(params, key, 256)
    return bytes(Uint8Array.new(bits).to_py())


async def hash_password(password):
    salt = secrets.token_bytes(16)
    return salt.hex() + ":" + (await derive(password, salt)).hex()


async def verify_password(password, stored):
    parts = (stored or "").split(":")
    if len(parts) != 2:
        return False
    try:
        salt = bytes.fromhex(parts[0])
        expected = bytes.fromhex(parts[1])
    except ValueError:
        return False
    return hmac.compare_digest(await derive(password, salt), expected)


def new_token():
    return secrets.token_hex(24)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def valid_sid(value):
    return bool(value) and re.fullmatch(r"[a-f0-9]+", value) is not None
