import time

import numpy as np

from ps2_autopilot.madden_ocr import MaddenOCR, OCRLine, OCRSnapshot


class FakeOCR(MaddenOCR):
    def __init__(self, *, delay=0.06, **kwargs):
        super().__init__(min_width=480, max_width=480, **kwargs)
        self.delay = delay

    def _infer(self, frame):
        time.sleep(self.delay)
        value = int(frame[0, 0, 0])
        line = OCRLine(str(value), 0.99, 0.5, 0.5, 0.1, 0.1)
        return OCRSnapshot((line,), str(value), True, None), self.delay * 1000.0


def frame(value):
    return np.full((12, 16, 3), value, dtype=np.uint8)


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_bootstrap_is_synchronous_then_recurring_read_is_nonblocking():
    ocr = FakeOCR(interval_seconds=0.20, async_enabled=True, bootstrap_sync=True, delay=0.06)
    try:
        started = time.perf_counter()
        first = ocr.read(frame(1), 10.0)
        bootstrap_elapsed = time.perf_counter() - started
        assert first.text == "1"
        assert bootstrap_elapsed >= 0.05

        started = time.perf_counter()
        stale = ocr.read(frame(2), 10.3)
        recurring_elapsed = time.perf_counter() - started
        assert stale.text == "1"
        assert recurring_elapsed < 0.04
        assert wait_until(lambda: ocr.read(frame(9), 10.31).text == "2")
        assert ocr.runs >= 2
    finally:
        ocr.close()


def test_worker_keeps_only_latest_pending_frame_and_reports_backpressure():
    ocr = FakeOCR(interval_seconds=0.20, async_enabled=True, bootstrap_sync=True, delay=0.12)
    try:
        assert ocr.read(frame(1), 20.0).text == "1"
        ocr.read(frame(2), 20.3)
        time.sleep(0.02)  # worker should now be busy on frame 2
        ocr.read(frame(3), 20.6)
        ocr.read(frame(4), 20.9)  # replaces pending frame 3

        assert ocr.dropped_frames >= 1
        assert wait_until(lambda: ocr.read(frame(9), 20.91).text == "4", timeout=1.5)
        metrics = ocr.telemetry(21.0)
        assert metrics["ocr_async_enabled"] is True
        assert metrics["ocr_submitted_frames"] >= 3
        assert metrics["ocr_dropped_frames"] >= 1
        assert metrics["ocr_result_age_ms"] is not None
    finally:
        ocr.close()


def test_async_ownership_downscales_1080p_before_queue_copy():
    ocr = MaddenOCR(
        enabled=False,
        min_width=960,
        max_width=1280,
        async_enabled=True,
        bootstrap_sync=False,
    )
    source = np.full((1080, 1920, 3), 37, dtype=np.uint8)

    owned = ocr._own_submit_frame(source)

    assert owned.shape == (720, 1280, 3)
    assert owned.nbytes == 1280 * 720 * 3
    assert source.nbytes == 1920 * 1080 * 3
    assert owned.nbytes / source.nbytes < 0.45
    assert ocr.submit_downscales == 1
    metrics = ocr.telemetry(1.0)
    assert metrics["ocr_submit_source_width"] == 1920
    assert metrics["ocr_submit_owned_width"] == 1280
    assert 55.0 <= metrics["ocr_submit_copy_reduction_pct"] <= 56.0

    # Resize must also establish ownership: later capture-buffer mutation cannot alter
    # the queued worker snapshot.
    source.fill(99)
    assert int(owned[0, 0, 0]) == 37


def test_native_size_async_ownership_still_copies_capture_buffer():
    ocr = MaddenOCR(
        enabled=False,
        min_width=480,
        max_width=1280,
        async_enabled=True,
        bootstrap_sync=False,
    )
    source = np.full((480, 640, 3), 12, dtype=np.uint8)

    owned = ocr._own_submit_frame(source)
    source.fill(77)

    assert owned.shape == source.shape
    assert int(owned[0, 0, 0]) == 12
    assert ocr.submit_downscales == 0
    assert ocr.telemetry(1.0)["ocr_submit_copy_reduction_pct"] == 0.0


def test_sync_mode_remains_available_for_debugging():
    ocr = FakeOCR(interval_seconds=0.20, async_enabled=False, bootstrap_sync=True, delay=0.01)
    try:
        assert ocr.read(frame(5), 30.0).text == "5"
        assert ocr.read(frame(6), 30.1).text == "5"
        assert ocr.read(frame(7), 30.3).text == "7"
        assert ocr.telemetry(30.3)["ocr_async_enabled"] is False
    finally:
        ocr.close()
