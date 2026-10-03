import base64
import binascii
import hashlib
import hmac
import json
import time

from lettia._json import validate_json_value
from lettia.context import Context
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response
from lettia.state import SESSION, SessionData


def _sign(data: bytes, secret: bytes) -> str:
    signature = hmac.new(secret, data, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(data + b"." + signature).decode("ascii")


def _unsign(cookie_value: str, secret: bytes) -> bytes | None:
    try:
        raw_bytes = base64.urlsafe_b64decode(cookie_value.encode("ascii"))
        signature_size = hashlib.sha256().digest_size
        delimiter_index = -(signature_size + 1)
        if len(raw_bytes) <= signature_size or raw_bytes[delimiter_index] != ord("."):
            return None
        data = raw_bytes[:delimiter_index]
        signature = raw_bytes[delimiter_index + 1 :]
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
    if max_age <= 0:
        raise ValueError("Session max_age must be positive")
    secret_bytes = secret_key.encode("utf-8")

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            cookie_val = ctx.cookie(cookie_name)
            session_data: SessionData = {}

            if cookie_val:
                raw_json = _unsign(cookie_val, secret_bytes)
                if raw_json:
                    try:
                        decoded: object = json.loads(raw_json.decode("utf-8"))
                        envelope = validate_json_value(decoded)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        session_data = {}
                    else:
                        if (
                            isinstance(envelope, dict)
                            and type(envelope.get("v")) is int
                            and envelope.get("v") == 1
                        ):
                            issued_at = envelope.get("iat")
                            data = envelope.get("data")
                            now = time.time()
                            if (
                                isinstance(issued_at, (int, float))
                                and not isinstance(issued_at, bool)
                                and now - max_age < issued_at <= now
                                and isinstance(data, dict)
                            ):
                                session_data = data

            ctx.state.set(SESSION, session_data)
            initial_session_str = json.dumps(session_data, sort_keys=True)

            resp = await next_handler(ctx)

            def finalize(response: Response) -> None:
                current_session = ctx.state.require(SESSION)
                if json.dumps(current_session, sort_keys=True) == initial_session_str:
                    return
                if current_session:
                    json_bytes = json.dumps(
                        {"v": 1, "iat": time.time(), "data": current_session},
                        sort_keys=True,
                    ).encode("utf-8")
                    signed_val = _sign(json_bytes, secret_bytes)
                else:
                    signed_val = ""
                cookie_age = max_age if current_session else 0

                response.set_cookie(
                    cookie_name,
                    signed_val,
                    max_age=cookie_age,
                    httponly=True,
                    secure=https_only,
                    samesite=same_site,
                )

            ctx._register_response_finalizer(resp, finalize)  # pyright: ignore[reportPrivateUsage]

            return resp

        return handler

    return middleware
