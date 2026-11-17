"""Reservations and reservation quotas (issues #45-64) against the real API.

A member reserves the station in advance (POST /api/v1/reservations), at
least 24 hours ahead, minute-aligned and within the minutes their profile
allows; overlapping windows conflict with 409 and cancellation frees the
slot. A profile without reservation minutes answers canReserve false and
rejects the attempt, and an anonymous client gets 401.

The member client is an explicit requests.Session (bypasses the conftest
cookie shim); the admin work goes through the shimmed module-level API.
"""

import os
import time

import requests

MEMBER_ID = "TESTRES1"
MEMBER_TEMP_PASSWORD = "temp-pass-123"
MEMBER_PASSWORD = "member-pass-456"
NOQUOTA_ID = "TESTRES2"
NOQUOTA_PASSWORD = "noquota-pass-789"


def _api(rest_url):
    return rest_url.rsplit("/radio", 1)[0]


def _login(api, user_id, password):
    """Explicit session: bypasses the conftest cookie shim."""
    s = requests.Session()
    r = s.post(f"{api}/auth/login",
               json={"userId": user_id, "password": password}, timeout=5)
    r.raise_for_status()
    return s, r.json()


def _create_user(api, user_id, temp_password, max_minutes):
    """Create via the shimmed admin session; False when the user already existed."""
    r = requests.post(f"{api}/admin/users", json={
        "userId": user_id, "password": temp_password,
        "firstName": "Test", "lastName": "Res",
        "email": f"{user_id.lower()}@example.invalid",
        "street": "Calle Falsa 1", "city": "Bari",
        "postalCode": "70100", "province": "BA",
        "maxReservationMinutes": max_minutes}, timeout=5)
    assert r.status_code in (200, 201, 409), r.text
    return r.status_code != 409


def _member_session(api):
    fresh = _create_user(api, MEMBER_ID, MEMBER_TEMP_PASSWORD, 60)
    s, me = _login(api, MEMBER_ID,
                   MEMBER_TEMP_PASSWORD if fresh else MEMBER_PASSWORD)
    if me.get("mustChangePassword"):
        r = s.post(f"{api}/auth/password",
                   json={"currentPassword": MEMBER_TEMP_PASSWORD,
                         "newPassword": MEMBER_PASSWORD}, timeout=5)
        r.raise_for_status()
    return s


def _window():
    """A 30-minute slot more than 24h ahead, minute-aligned, per-run jitter."""
    start = int(time.time()) + 26 * 3600 + (os.getpid() % 500) * 60
    start -= start % 60
    return start, start + 1800


def test_member_can_reserve_and_cancel(rest_url):
    api = _api(rest_url)
    s = _member_session(api)

    r = s.get(f"{api}/reservations", timeout=5)
    assert r.status_code == 200
    status = r.json()["reservationStatus"]
    assert status["canReserve"] is True
    assert status["maxReservationMinutes"] == 60
    assert status["effectiveReservationMinutes"] > 0
    assert "usage" in r.json()

    start, end = _window()
    r = s.post(f"{api}/reservations", json={"startsAt": start, "endsAt": end}, timeout=5)
    assert r.status_code == 201, r.text
    reservation_id = r.json()["reservationId"]

    r = s.get(f"{api}/reservations", timeout=5)
    assert any(rv.get("id") == reservation_id or rv.get("reservationId") == reservation_id
               for rv in r.json()["reservations"])

    r = s.delete(f"{api}/reservations/{reservation_id}", timeout=5)
    assert r.status_code == 200, r.text
    r = s.get(f"{api}/reservations", timeout=5)
    mine = [rv for rv in r.json()["reservations"] if rv.get("id") == reservation_id]
    # Cancelled reservations stay on the list, marked cancelled.
    assert mine and mine[0]["status"] == "cancelled", r.json()

    # The freed slot can be reserved again.
    r = s.post(f"{api}/reservations", json={"startsAt": start, "endsAt": end}, timeout=5)
    assert r.status_code == 201, r.text
    s.delete(f"{api}/reservations/{r.json()['reservationId']}", timeout=5)


def test_overlapping_reservation_conflicts(rest_url):
    api = _api(rest_url)
    s = _member_session(api)
    start, end = _window()
    r = s.post(f"{api}/reservations", json={"startsAt": start, "endsAt": end}, timeout=5)
    if r.status_code == 409:
        # Leftover slot from an interrupted earlier run at the same window.
        return
    assert r.status_code == 201, r.text
    reservation_id = r.json()["reservationId"]

    r = s.post(f"{api}/reservations",
               json={"startsAt": start + 900, "endsAt": end + 900}, timeout=5)
    assert r.status_code == 409, r.text

    s.delete(f"{api}/reservations/{reservation_id}", timeout=5)


def test_reservation_beyond_profile_minutes_is_rejected(rest_url):
    api = _api(rest_url)
    s = _member_session(api)
    start, _end = _window()
    r = s.post(f"{api}/reservations",
               json={"startsAt": start, "endsAt": start + 120 * 60}, timeout=5)
    assert r.status_code == 400, r.text


def test_reservation_within_24h_is_rejected(rest_url):
    api = _api(rest_url)
    s = _member_session(api)
    start = int(time.time()) + 3600
    start -= start % 60
    r = s.post(f"{api}/reservations",
               json={"startsAt": start, "endsAt": start + 1800}, timeout=5)
    assert r.status_code == 400, r.text


def test_user_without_minutes_cannot_reserve(rest_url):
    api = _api(rest_url)
    fresh = _create_user(api, NOQUOTA_ID, NOQUOTA_PASSWORD, 0)
    s, me = _login(api, NOQUOTA_ID, NOQUOTA_PASSWORD)
    if me.get("mustChangePassword"):
        r = s.post(f"{api}/auth/password",
                   json={"currentPassword": NOQUOTA_PASSWORD,
                         "newPassword": NOQUOTA_PASSWORD}, timeout=5)
        r.raise_for_status()

    r = s.get(f"{api}/reservations", timeout=5)
    assert r.status_code == 200
    assert r.json()["reservationStatus"]["canReserve"] is False

    start, end = _window()
    r = s.post(f"{api}/reservations", json={"startsAt": start, "endsAt": end}, timeout=5)
    assert r.status_code == 400, r.text


def test_anonymous_cannot_read_or_reserve(rest_url):
    api = _api(rest_url)
    anon = requests.Session()
    assert anon.get(f"{api}/reservations", timeout=5).status_code == 401
    start, end = _window()
    r = anon.post(f"{api}/reservations", json={"startsAt": start, "endsAt": end}, timeout=5)
    assert r.status_code == 401
