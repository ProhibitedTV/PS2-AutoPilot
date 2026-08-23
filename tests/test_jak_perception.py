import numpy as np

from ps2_autopilot.jak_knowledge import JakControlMode, control_mode_for_template
from ps2_autopilot.jak_perception import JakSceneMemory, merge_progress, parse_progress_ocr


def test_progress_ocr_extracts_collectible_totals():
    progress = parse_progress_ocr(
        "POWER CELLS 21 | PRECURSOR ORBS 437 | SCOUT FLIES 18"
    )
    assert progress.power_cells == 21
    assert progress.precursor_orbs == 437
    assert progress.scout_flies == 18


def test_progress_merge_rejects_transient_decrease():
    old = parse_progress_ocr("POWER CELLS 20 | ORBS 150 | FLIES 14")
    noisy = parse_progress_ocr("POWER CELLS 2 | ORBS 15 | FLIES 4")
    merged = merge_progress(old, noisy)
    assert merged.power_cells == 20
    assert merged.precursor_orbs == 150
    assert merged.scout_flies == 14


def test_control_modes_are_template_specific():
    assert control_mode_for_template("jak_gameplay_geyser") == JakControlMode.ON_FOOT
    assert control_mode_for_template("jak_fire_canyon_zoomer") == JakControlMode.ZOOMER
    assert control_mode_for_template("jak_flut_flut") == JakControlMode.FLUT_FLUT
    assert control_mode_for_template("jak_cannon") == JakControlMode.CANNON
    assert control_mode_for_template("jak_fishing") == JakControlMode.FISHING


def test_scene_memory_reports_repeat_view_as_loop_candidate():
    memory = JakSceneMemory(sample_seconds=0.25, history=12)
    base = np.zeros((120, 160, 3), dtype=np.uint8)
    base[:, :80] = 40
    base[:, 80:] = 210

    for i in range(7):
        frame = base.copy()
        # Vary recent history so the final base frame is compared against an older
        # matching view rather than only the immediate predecessor.
        if 1 <= i <= 5:
            frame[20:80, 20 + i * 5:60 + i * 5] = 90 + i * 10
        metrics = memory.observe(frame, None, i * 0.30)

    assert metrics.sampled is True
    assert metrics.loop_similarity > 0.90
