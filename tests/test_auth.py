"""Role-based access tests for the authenticated web interface.

Covers the three roles the suite must distinguish, against the real login
flow on a test instance with a temporary database (seeded admin EA7URS):

  - anonymous client: every radio and admin endpoint answers 401;
  - normal (non-admin) user: radio access allowed, admin endpoints denied 403;
  - administrator: admin endpoints allowed.

Anonymous and normal-user clients are explicit ``requests.Session`` objects,
which bypass the conftest cookie shim (that shim only wraps the module-level
``requests.get`` convenience API used by the legacy tests).
"""

import requests

ADMIN_ID = "EA7URS"
ADMIN_PASSWORD = "wfweb-test-pass"  # set by conftest._login_test_instance
NORMAL_ID = "TESTUSER1"
NORMAL_TEMP_PASSWORD = "temp-pass-123"
NORMAL_PASSWORD = "normal-pass-456"


def _api(rest_url):
    """http://127.0.0.1:<rest>/api/v1 base, derived from the radio base."""
    return rest_url.rsplit("/radio", 1)[0]


def _login(api_base, user_id, password):
    """Log in with an explicit session (bypasses the shim); return (session, json)."""
    s = requests.Session()
    r = s.post(f"{api_base}/auth/login",
               json={"userId": user_id, "password": password}, timeout=5)
    r.raise_for_status()
    return s, r.json()


def test_anonymous_radio_read_is_unauthorized(rest_url):
    r = requests.Session().get(f"{rest_url}/info", timeout=5)
    assert r.status_code == 401


def test_anonymous_radio_write_is_unauthorized(rest_url):
    r = requests.Session().post(f"{rest_url}/frequency",
                                json={"hz": 7074000}, timeout=5)
    assert r.status_code == 401


def test_anonymous_admin_list_is_unauthorized(rest_url):
    r = requests.Session().get(f"{_api(rest_url)}/admin/users", timeout=5)
    assert r.status_code == 401


def test_admin_can_list_users(rest_url):
    # Module-level requests: the shim attaches the admin session cookie.
    r = requests.get(f"{_api(rest_url)}/admin/users", timeout=5)
    assert r.status_code == 200
    assert any(u["userId"] == ADMIN_ID for u in r.json()["users"])


def test_normal_user_denied_admin_allowed_radio(rest_url):
    api = _api(rest_url)
    # The admin session (via the shim) creates the normal user; a reused
    # database from an earlier run answers 409, which is fine.
    r = requests.post(f"{api}/admin/users",
                      json={"userId": NORMAL_ID, "password": NORMAL_TEMP_PASSWORD,
                            "firstName": "Test", "lastName": "User", "email": "testuser1@example.invalid", "street": "Calle Falsa 1", "city": "Bari", "postalCode": "70100", "province": "BA"}, timeout=5)
    assert r.status_code in (200, 409), r.text

    # New users must replace their temporary password before radio access.
    s, me = _login(api, NORMAL_ID, NORMAL_TEMP_PASSWORD if r.status_code == 200
                   else NORMAL_PASSWORD)
    if me.get("mustChangePassword"):
        r2 = s.post(f"{api}/auth/password",
                    json={"currentPassword": NORMAL_TEMP_PASSWORD,
                          "newPassword": NORMAL_PASSWORD}, timeout=5)
        r2.raise_for_status()
    assert not me.get("isAdmin")

    r = s.get(f"{api}/admin/users", timeout=5)
    assert r.status_code == 403

    r = s.get(f"{rest_url}/info", timeout=5)
    assert r.status_code == 200


def test_wrong_password_is_rejected(rest_url):
    r = requests.Session().post(f"{_api(rest_url)}/auth/login",
                                json={"userId": ADMIN_ID,
                                      "password": "not-the-password"}, timeout=5)
    assert r.status_code == 401
