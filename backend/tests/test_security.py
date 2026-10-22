"""The engine must only answer its own dashboard on this machine."""

from fastapi.testclient import TestClient

from autoshorts.api.app import app
from autoshorts.api.security import COOKIE, HEADER, host_ok, token
from autoshorts.publish.hosting import media_app, sign


def client(host: str = "localhost:3000") -> TestClient:
    return TestClient(app, base_url=f"http://{host}")


def test_host_check_blocks_dns_rebinding():
    assert host_ok("localhost:3000") and host_ok("127.0.0.1:3000") and host_ok("[::1]:3000") and host_ok("app.localhost")
    assert not host_ok("evil.example.com") and not host_ok("localhost.evil.com:3000")
    r = client("attacker.example.com").get("/api/ping")
    assert r.status_code == 400


def test_api_requires_the_token():
    c = client()
    assert c.get("/api/summary").status_code == 401
    assert c.put("/api/settings", json={"llm_server_url": "http://evil"}).status_code == 401
    assert c.get("/api/summary", headers={HEADER: "wrong"}).status_code == 401
    assert c.get("/api/summary", headers={HEADER: token()}).status_code == 200


def test_open_paths_stay_open():
    c = client()
    assert c.get("/api/ping").status_code == 200
    # OAuth redirects arrive from the provider in a normal browser; they validate their own state.
    r = c.get("/api/oauth/instagram/callback?state=nope", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_dashboard_page_sets_the_cookie_and_the_cookie_works():
    c = client()
    page = c.get("/")
    assert page.status_code == 200
    cookie = page.cookies.get(COOKIE) or c.cookies.get(COOKIE)
    assert cookie == token()
    assert "samesite=strict" in page.headers.get("set-cookie", "").lower()
    assert c.get("/api/summary").status_code == 200           # the client now sends the cookie


def test_tunnel_server_serves_only_signed_media():
    c = TestClient(media_app(), base_url="https://random.trycloudflare.com")
    assert c.get("/api/settings").status_code == 404
    assert c.get("/").status_code == 404
    assert c.get("/public/media/1.0.bad.mp4").status_code == 403
    assert c.get(f"/public/media/{sign(999999)}.mp4").status_code == 404   # valid signature, no such clip


def test_errors_do_not_leak_internals():
    from autoshorts.api import app as app_mod

    def boom():
        raise RuntimeError("secret path /Users/me/.ssh/id_rsa")

    app_mod.app.add_api_route("/api/_boom_test", boom)
    app_mod.app.router.routes.insert(0, app_mod.app.router.routes.pop())   # ahead of the dashboard catch-all

    r = TestClient(app_mod.app, base_url="http://localhost", raise_server_exceptions=False).get(
        "/api/_boom_test", headers={HEADER: token()})
    assert r.status_code == 500 and "id_rsa" not in r.text and "ref" in r.json()["detail"]
