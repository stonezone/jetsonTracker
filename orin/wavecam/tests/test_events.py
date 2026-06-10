import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from test_control_api import DummyPipeline
from wavecam.events import EventRing
from wavecam.web import build_app


def test_ring_records_and_filters_by_since():
    r = EventRing(maxlen=4)
    r.record("lock", "acquired", t=100.0)
    r.record("owner", "vision_follow", t=101.0)
    assert [e["kind"] for e in r.since(0)] == ["lock", "owner"]
    assert [e["detail"] for e in r.since(100.5)] == ["vision_follow"]


def test_ring_drops_oldest():
    r = EventRing(maxlen=2)
    for i in range(3):
        r.record("k", str(i), t=float(i))
    assert [e["detail"] for e in r.since(0)] == ["1", "2"]


def test_since_filters_strictly_greater_than():
    r = EventRing(maxlen=4)
    r.record("lock", "acquired", t=100.0)
    # since(100.0) should NOT include the event at exactly t=100.0
    assert r.since(100.0) == []
    # since(99.9) should include it
    assert len(r.since(99.9)) == 1


def test_events_endpoint_contains_kill_record():
    pl = DummyPipeline()
    client = TestClient(build_app(pl))
    client.post("/api/v1/safety/kill")
    r = client.get("/api/v1/events?since=0")
    assert r.status_code == 200
    body = r.json()
    kinds = [e["kind"] for e in body["events"]]
    assert "kill" in kinds
