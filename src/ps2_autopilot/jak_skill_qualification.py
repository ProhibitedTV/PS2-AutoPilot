from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .jak_lab import check_manifest, grade_trace


DEFAULT_REQUIRED_RUNS = 3


def _parse_trial(value: str) -> tuple[str, Path]:
    challenge, separator, trace = str(value).partition("=")
    challenge = challenge.strip()
    trace = trace.strip()
    if not separator or not challenge or not trace:
        raise ValueError("trial must use CHALLENGE_ID=TRACE_PATH")
    return challenge, Path(trace)


def _canonical(path: Path) -> str:
    try:
        return str(path.expanduser().resolve(strict=False))
    except OSError:
        return str(path.expanduser().absolute())


def evaluate_qualification(
    manifest_path: Path,
    trials: Iterable[tuple[str, Path]],
    *,
    required_runs: int = DEFAULT_REQUIRED_RUNS,
) -> dict[str, Any]:
    """Re-grade independent raw traces and require a perfect per-skill repeatability gate.

    Qualification intentionally consumes raw trace paths rather than previously saved
    grade JSON. If the episode contract later changes, the same traces are evaluated
    against the current predicates/time budget instead of inheriting a stale pass.
    A trace may appear only once across the suite so one lucky run cannot satisfy more
    than one attempt or challenge by repeated command-line arguments.
    """

    required_runs = max(1, int(required_runs))
    readiness = check_manifest(manifest_path)
    episodes = {
        item["challenge_id"]: item
        for item in readiness["episodes"]
        if bool(item.get("required"))
    }
    if not episodes:
        raise ValueError("lab manifest has no required episodes")

    grouped: dict[str, list[Path]] = {challenge_id: [] for challenge_id in episodes}
    seen_traces: dict[str, str] = {}
    unknown: list[str] = []
    duplicate_traces: list[dict[str, str]] = []

    for challenge_id, trace in trials:
        if challenge_id not in episodes:
            unknown.append(challenge_id)
            continue
        canonical = _canonical(trace)
        previous = seen_traces.get(canonical)
        if previous is not None:
            duplicate_traces.append(
                {
                    "trace": str(trace),
                    "first_challenge": previous,
                    "duplicate_challenge": challenge_id,
                }
            )
            continue
        seen_traces[canonical] = challenge_id
        grouped[challenge_id].append(trace)

    if unknown:
        raise ValueError("trial references unknown/non-required challenge(s): " + ", ".join(sorted(set(unknown))))

    rows: list[dict[str, Any]] = []
    qualified_count = 0
    for challenge_id, episode in episodes.items():
        traces = grouped[challenge_id]
        blockers: list[str] = []
        grades: list[dict[str, Any]] = []

        if not episode["ready"]:
            blockers.extend(f"episode-not-ready:{reason}" for reason in episode["blockers"])

        missing = [str(path) for path in traces if not path.exists()]
        if missing:
            blockers.append("missing-trace-files")

        for trace in traces:
            if not trace.exists():
                continue
            grades.append(grade_trace(manifest_path, challenge_id, trace))

        passing_runs = sum(1 for grade in grades if grade["passed"])
        supplied_runs = len(traces)
        if supplied_runs < required_runs:
            blockers.append("insufficient-independent-runs")
        if grades and not all(grade["passed"] for grade in grades):
            blockers.append("one-or-more-runs-failed")
        if supplied_runs > 0 and len(grades) != supplied_runs:
            blockers.append("one-or-more-traces-unreadable")

        qualified = bool(
            not blockers
            and supplied_runs >= required_runs
            and passing_runs == supplied_runs
        )
        qualified_count += int(qualified)
        rows.append(
            {
                "challenge_id": challenge_id,
                "qualified": qualified,
                "required_runs": required_runs,
                "supplied_runs": supplied_runs,
                "passing_runs": passing_runs,
                "blockers": sorted(set(blockers)),
                "traces": [str(path) for path in traces],
                "grades": grades,
            }
        )

    if duplicate_traces:
        # Keep the affected runs out of their second challenge/attempt above and make
        # the entire suite non-qualifying. Duplicate evidence is a suite-integrity
        # problem even when every remaining per-skill row happens to pass.
        suite_integrity = False
    else:
        suite_integrity = True

    required_total = len(rows)
    qualified = bool(
        suite_integrity
        and required_total > 0
        and qualified_count == required_total
    )
    return {
        "schema": "jak-skill-qualification-v1",
        "manifest": str(manifest_path),
        "required_runs_per_skill": required_runs,
        "qualified": qualified,
        "required_skills": required_total,
        "qualified_skills": qualified_count,
        "suite_integrity": suite_integrity,
        "duplicate_traces": duplicate_traces,
        "skills": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-skill-qualification",
        description=(
            "Strictly qualify required Jak lab skills from repeated independent retained traces."
        ),
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--trial",
        action="append",
        default=[],
        metavar="CHALLENGE_ID=TRACE_PATH",
        help="independent retained verbose trace for one required challenge; repeat as needed",
    )
    parser.add_argument("--required-runs", type=int, default=DEFAULT_REQUIRED_RUNS)
    args = parser.parse_args(argv)

    try:
        parsed = [_parse_trial(value) for value in args.trial]
        report = evaluate_qualification(
            args.manifest,
            parsed,
            required_runs=args.required_runs,
        )
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
