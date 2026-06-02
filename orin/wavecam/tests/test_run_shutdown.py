from __future__ import annotations

import signal

from run import handle_shutdown_signal, shutdown_pipeline


class DummyPipeline:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append(("pipe_stop",))

    def join(self, timeout=None):
        self.calls.append(("pipe_join", timeout))


class DummyPtz:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append(("ptz_stop",))

    def close(self):
        self.calls.append(("ptz_close",))


def test_shutdown_pipeline_stops_joins_and_closes_ptz():
    pipe = DummyPipeline()
    ptz = DummyPtz()

    shutdown_pipeline(pipe, ptz, join_timeout=2.5)

    assert pipe.calls == [("pipe_stop",), ("pipe_join", 2.5)]
    assert ptz.calls == [("ptz_stop",), ("ptz_close",)]


def test_shutdown_signal_handler_runs_cleanup_once_and_exits():
    pipe = DummyPipeline()
    ptz = DummyPtz()
    state = {"handled": False}

    try:
        handle_shutdown_signal(signal.SIGTERM, pipe, ptz, state, force_exit=False)
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("shutdown handler did not exit")

    assert state["handled"] is True
    assert pipe.calls == [("pipe_stop",), ("pipe_join", 3.0)]
    assert ptz.calls == [("ptz_stop",), ("ptz_close",)]
