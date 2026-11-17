"""rigctld (Hamlib TCP) contract with the authenticated web interface.

Since the login landed (#3) wfweb refuses to open the rigctld TCP port: the
Hamlib protocol has no authentication, so listening there would bypass the
web login. The server logs "rigctld disabled while the authenticated web
interface is enabled" and carries on without it.

These tests pin that contract: even when --rigctld-port is passed, nothing
listens on the port. They replace the pre-auth protocol tests, which failed
with ConnectionRefused by design. If rigctld returns one day (an
authenticated variant or an explicit no-auth mode), drop these and restore
real protocol coverage.
"""

import socket


def _port_listens(port, timeout=2.0):
    """True when something accepts a TCP connection on 127.0.0.1:port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def test_rigctld_port_stays_closed(wfweb_instance, rigctld_port):
    """The requested rigctld port accepts no connections while auth is on."""
    assert not _port_listens(rigctld_port)


def test_rigctld_port_stays_closed_7300(wfweb_7300_instance, rigctld_7300_port):
    """Same contract on the IC-7300 instance (its own requested port)."""
    assert not _port_listens(rigctld_7300_port)


def test_authenticated_session_does_not_open_rigctld(wfweb_instance, rigctld_port):
    """Holding a valid admin session changes nothing: no login, no port."""
    # wfweb_instance already carries an authenticated admin session via the
    # shared fixture, so reaching this line proves the session exists.
    assert not _port_listens(rigctld_port)


def test_server_log_records_rigctld_disabled(wfweb_instance):
    """The log pins the refusal as deliberate, not a silent bind failure."""
    proc, _web_port = wfweb_instance
    with open(proc.output_path, encoding="utf-8", errors="replace") as fh:
        assert "rigctld disabled" in fh.read()
