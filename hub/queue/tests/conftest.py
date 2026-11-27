import os
import sys

# QUEUE_IMPL lets the suite run against a stub to show it fails without the implementation.
sys.path.insert(0, os.environ.get("QUEUE_IMPL") or os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from queue_engine import Queue  # noqa: E402
from queue_registry import Registry  # noqa: E402


class Fake:
    """Fake Home Assistant: states dict plus a call log; calls to fail can be scripted."""

    def __init__(self):
        self.states, self.calls, self.fail, self.trays, self.now = {}, [], {}, {}, 1000.0

    def get_state(self, eid):
        return self.states.get(eid)

    def call_service(self, domain, service, data):
        self.calls.append((domain, service, data))
        if (domain, service) in self.fail:
            raise RuntimeError(self.fail[(domain, service)])
        if domain == "homeassistant":
            self.states[data["entity_id"]] = "on" if service == "turn_on" else "off"


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def make(tmp_path, fake):
    reg = Registry(str(tmp_path))
    reg.put("S1", name="One", model="H2S", account="a", prefix="one", pause_entities=["automation.snap"])
    reg.put("S2", name="Two", model="P1S", account="b", prefix="two")
    reg.put("S3", name="Three", model="H2S", account="c", prefix="three")
    for p in ("one", "two", "three"):
        fake.states["sensor.%s_print_status" % p] = "idle"
    fake.states["automation.snap"] = "on"
    q = Queue(reg, str(tmp_path), fake.get_state, fake.call_service,
              lambda s: fake.trays.get(s, []), clock=lambda: fake.now)
    return q
