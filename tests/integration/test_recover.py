import pytest

from lettia import App
from lettia.context import Context
from lettia.middleware import recover
from lettia.testing import TestClient


@pytest.mark.contract("MW-RECOVER")
def test_recover_preserves_http_exception_status() -> None:
    app = App()
    app.use(recover())

    def missing(ctx: Context) -> str:
        ctx.abort(404, "missing")

    app.add_route("GET", "/missing", missing)
    response = TestClient(app).get("/missing")
    assert response.status_code == 404
    assert response.text == "missing"
