"""The owner's choices on the pages (4 Nov): kept in the lab, the same from every device, the last one saved wins."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo.api import make_router
from octo.db import config, connect
from octo.phone_api import ANYWHERE, phone_rule


def test_folds_are_kept_in_the_lab(tmp_path):
    db = lambda: connect(tmp_path / "t.db")  # noqa: E731
    app = FastAPI()
    app.include_router(make_router(db=db))
    c = TestClient(app)
    assert c.get("/api/octo/prefs").json() == {}
    assert c.put("/api/octo/prefs/octo:fold:week", json={"value": False}).json() == {"octo:fold:week": False}
    c.put("/api/octo/prefs/octo.calendar.show", json={"value": "fillers"})
    c.put("/api/octo/prefs/octo:fold:week", json={"value": True})  # another device, later: it wins
    assert c.get("/api/octo/prefs").json() == {"octo:fold:week": True, "octo.calendar.show": "fillers"}
    assert config(db(), "ui_prefs") == {"octo:fold:week": True, "octo.calendar.show": "fillers"}  # so export has them

    assert c.put('/api/octo/prefs/a"b', json={"value": True}).status_code == 422  # never into the JSON path
    assert c.put("/api/octo/prefs/x", json={"value": "y" * 65}).status_code == 422
    assert c.put("/api/octo/prefs/x", json={"value": {"a": 1}}).status_code == 422
    assert c.put("/api/octo/prefs/x", content="true", headers={"content-type": "text/plain"}).status_code == 415


def test_the_phone_keeps_them_too():
    assert phone_rule("GET", "/api/octo/prefs") == ANYWHERE
    assert phone_rule("PUT", "/api/octo/prefs/octo:fold:week") == ANYWHERE
