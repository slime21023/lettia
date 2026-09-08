import base64
import binascii
import hashlib
import hmac
import json
from typing import cast

from lettia.context import Context, validate_json_value
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response
from lettia.state import SESSION, SessionData


def _sign(data: bytes, secret: bytes) -> str:
    signature = hmac.new(secret, data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(data + b"." + signature).decode("ascii")


def _unsign(cookie_value: str, secret: bytes) -> bytes | None:
    try:
        raw_bytes = base64.urlsafe_b64decode(cookie_value.encode("ascii"))
        if b"." not in raw_bytes:
            return None
        data, signature = raw_bytes.rsplit(b".", 1)
        expected_sig = hmac.new(secret, data, hashlib.sha256).digest()
        if hmac.compare_digest(signature, expected_sig):
            return data
    except (binascii.Error, UnicodeError, ValueError):
        return None
    return None


def session(
    secret_key: str,
    cookie_name: str = "session",
    max_age: int = 14 * 86400,
    same_site: str = "lax",
    https_only: bool = False,
) -> Middleware:
    secret_bytes = secret_key.encode("utf-8")

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            cookie_val = ctx.cookie(cookie_name)
            session_data: SessionData = {}

            if cookie_val:
                raw_json = _unsign(cookie_val, secret_bytes)
                if raw_json:
                    try:
                        decoded_session = json.loads(raw_json.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        session_data = {}
                    else:
                        decoded_value: object = decoded_session
                        if isinstance(decoded_value, dict):
                            raw_mapping = cast(dict[object, object], decoded_value)
                            validated = validate_json_value(raw_mapping)
                            if isinstance(validated, dict):
                                session_data = validated

            ctx.state.set(SESSION, session_data)
            initial_session_str = json.dumps(session_data, sort_keys=True)

            res = await next_handler(ctx)
            resp = res

            current_session = ctx.state.require(SESSION)
            current_session_str = json.dumps(current_session, sort_keys=True)

            # If session was modified, set updated signed cookie
            if current_session_str != initial_session_str:
                if current_session:
                    json_bytes = current_session_str.encode("utf-8")
                    signed_val = _sign(json_bytes, secret_bytes)
                    resp.set_cookie(
                        cookie_name,
                        signed_val,
                        max_age=max_age,
                        httponly=True,
                        secure=https_only,
                        samesite=same_site,
                    )
                else:
                    # Clear cookie if empty session
                    resp.set_cookie(cookie_name, "", max_age=0, httponly=True)

            return resp

        return handler

    return middleware
