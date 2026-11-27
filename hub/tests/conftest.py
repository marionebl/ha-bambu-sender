import pytest

from hubfakes import Hub


@pytest.fixture
def hub(tmp_path):
    h = Hub(tmp_path)
    yield h
    h.server.shutdown()
    h.bridge.shutdown()
