"""Usage quota enforcement (issues #45-64): PTT release at the daily limit.

A member's connected time counts against the usage profile an admin assigns
them. When the daily allowance runs out mid-transmission the server must
release the PTT, notify the client with usageLimitDenied, close the socket
and refuse the next connection; the quota then shows as exhausted over REST.

The member's WebSocket and REST calls use an explicit session, bypassing the
conftest admin-cookie shim; the admin work (profile, user, usage reset) goes
through the shimmed module-level API.
"""

import json
import ssl
import time

import pytest
import requests

websockets = pytest.importorskip("websockets")
from websockets.sync.client import connect

QUOTA_ID = "TESTQUOTA"
QUOTA_TEMP_PASSWORD = "temp-pass-123"
QUOTA_PASSWORD = "quota-pass-456"
PROFILE_NAME = "Cuota de prueba 1 min"


def _api(rest_url):
    return rest_url.rsplit("/radio", 1)[0]


def _login(api, user_id, password):
    s = requests.Session()
    r = s.post(f"{api}/auth/login",
               json={"userId": user_id, "password": password}, timeout=5)
    r.raise_for_status()
    return s, r.json()


def _profile_id(api):
    r = requests.get(f"{api}/admin/usage-profiles", timeout=5)
    r.raise_for_status()
    for p in r.json()["profiles"]:
        if p.get("name") == PROFILE_NAME:
            return p.get("profileId", p.get("id"))
    r = requests.post(f"{api}/admin/usage-profiles",
                      json={"name": PROFILE_NAME, "dailyLimitMinutes": 1}, timeout=5)
    assert r.status_code == 201, r.text
    return r.json()["profileId"]


def _quota_session(api):
    """Fresh quota user with a 1-minute daily profile and a reset allowance."""
    profile_id = _profile_id(api)
    r = requests.post(f"{api}/admin/users", json={
        "userId": QUOTA_ID, "password": QUOTA_TEMP_PASSWORD,
        "firstName": "Test", "lastName": "Quota",
        "email": "testquota@example.invalid",
        "street": "Calle Falsa 1", "city": "Bari",
        "postalCode": "70100", "province": "BA",
        "maxReservationMinutes": 0, "usageProfileId": profile_id}, timeout=5)
    assert r.status_code in (200, 201, 409), r.text
    fresh = r.status_code != 409

    # Reused databases carry spent allowance from earlier runs: reset the day.
    requests.post(f"{api}/admin/users/{QUOTA_ID}/usage-reset",
                  json={"period": "daily"}, timeout=5).raise_for_status()

    s, me = _login(api, QUOTA_ID,
                   QUOTA_TEMP_PASSWORD if fresh else QUOTA_PASSWORD)
    if me.get("mustChangePassword"):
        r = s.post(f"{api}/auth/password",
                   json={"currentPassword": QUOTA_TEMP_PASSWORD,
                         "newPassword": QUOTA_PASSWORD}, timeout=5)
        r.raise_for_status()
    return s


def _ws_as(session, web_port):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    token = session.cookies.get("wfweb_session")
    return connect(f"wss://127.0.0.1:{web_port}", ssl=ctx, open_timeout=10,
                   additional_headers={"Cookie": f"wfweb_session={token}"})


def test_ptt_is_released_when_the_daily_quota_runs_out(rest_url, wfweb_instance, poll):
    api = _api(rest_url)
    _proc, web_port = wfweb_instance
    s = _quota_session(api)

    r = s.get(f"{api}/reservations", timeout=5)
    usage = r.json()["usage"]
    assert usage["allowed"] is True
    assert usage["profileName"] == PROFILE_NAME

    ws = _ws_as(s, web_port)
    try:
        ws.send(json.dumps({"cmd": "setPTT", "value": True}))
        poll("ptt", lambda d: d.get("transmitting") is True, timeout=5.0)

        # One minute of allowance: the server checks every second, so the
        # denial must land well inside 90 s.
        notice = None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and notice is None:
            try:
                msg = json.loads(ws.recv(timeout=90))
            except Exception:
                break  # closed by the server after the denial
            if msg.get("type") == "usageLimitDenied":
                notice = msg
        assert notice is not None, "no usageLimitDenied within 90 s of PTT"
    finally:
        try:
            ws.close()
        except Exception:
            pass

    poll("ptt", lambda d: d.get("transmitting") is False, timeout=5.0)

    r = s.get(f"{api}/reservations", timeout=5)
    assert r.json()["usage"]["allowed"] is False


def test_exhausted_user_cannot_reconnect(rest_url, wfweb_instance):
    """The quota from the previous test is spent: the new socket gets an
    immediate usageLimitDenied and is closed by the server."""
    api = _api(rest_url)
    _proc, web_port = wfweb_instance
    s, _me = _login(api, QUOTA_ID, QUOTA_PASSWORD)
    ws = _ws_as(s, web_port)
    try:
        msg = json.loads(ws.recv(timeout=10))
        assert msg.get("type") == "usageLimitDenied", msg
    finally:
        try:
            ws.close()
        except Exception:
            pass
