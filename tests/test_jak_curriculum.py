from __future__ import annotations

import json
from pathlib import Path

import pytest

from ps2_autopilot.jak_curriculum import (
    DEFAULT_CHALLENGES,
    KNOWN_ATOMIC_SKILLS,
    check_manifest,
    default_manifest,
    parse_manifest,
)


def write_manifest(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_default_curriculum_covers_v22_atomic_skill_set():
    manifest = default_manifest()
    challenges = manifest["challenges"]
    ids = [item["id"] for item in challenges]
    skills = {
        item["atomic_skill"]
        for item in challenges
        if item["kind"] == "atomic_skill"
    }

    assert len(ids) == len(set(ids))
    assert skills == KNOWN_ATOMIC_SKILLS
    assert len(DEFAULT_CHALLENGES) >= len(KNOWN_ATOMIC_SKILLS)


def test_blank_template_is_valid_but_not_ready(tmp_path):
    path = write_manifest(tmp_path / "curriculum.json", default_manifest())

    report = check_manifest(path)

    assert report["ready"] is False
    assert report["required_ready"] == 0
    assert report["missing_required"] == report["required_total"]
    assert all(
        item["status"] == "savestate-not-configured"
        for item in report["challenges"]
    )


def test_relative_real_savestate_paths_make_curriculum_ready(tmp_path):
    raw = default_manifest()
    states = tmp_path / "states"
    states.mkdir()
    for challenge in raw["challenges"]:
        state = states / f"{challenge['id']}.p2s"
        state.write_bytes(b"real-capture-placeholder-for-test")
        challenge["savestate_path"] = f"states/{state.name}"

    path = write_manifest(tmp_path / "curriculum.json", raw)
    report = check_manifest(path)

    assert report["ready"] is True
    assert report["required_ready"] == report["required_total"]
    assert all(item["status"] == "ready" for item in report["challenges"])


def test_missing_configured_savestate_is_reported_separately(tmp_path):
    raw = default_manifest()
    raw["challenges"][0]["savestate_path"] = "states/not-there.p2s"
    path = write_manifest(tmp_path / "curriculum.json", raw)

    report = check_manifest(path)

    first = report["challenges"][0]
    assert first["ready"] is False
    assert first["status"] == "savestate-file-missing"


def test_duplicate_challenge_ids_are_rejected():
    raw = default_manifest()
    raw["challenges"][1]["id"] = raw["challenges"][0]["id"]

    with pytest.raises(ValueError, match="duplicate challenge id"):
        parse_manifest(raw)


def test_unknown_atomic_skill_is_rejected():
    raw = default_manifest()
    raw["challenges"][0]["atomic_skill"] = "magic_jump"

    with pytest.raises(ValueError, match="atomic_skill must be one of"):
        parse_manifest(raw)
