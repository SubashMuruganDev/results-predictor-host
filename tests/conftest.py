"""All tests must be hermetic, including legacy parity tests."""
import socket
import pytest

@pytest.fixture(autouse=True)
def forbid_sockets(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Tests must not access the network')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)
