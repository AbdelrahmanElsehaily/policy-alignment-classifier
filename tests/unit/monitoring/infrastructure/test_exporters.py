"""Reference profiles on disk, and the Prometheus text format."""

from __future__ import annotations

from pathlib import Path

from pac.monitoring.application.monitor import Monitor
from pac.monitoring.domain.drift import Profile
from pac.monitoring.domain.features import Observation
from pac.monitoring.infrastructure.exporters import load_profile, prometheus_text, save_profile


def test_profiles_round_trip(tmp_path: Path) -> None:
    profile = Profile.of([Observation(values={"kind": ("user_input",)})] * 3)
    path = tmp_path / "monitoring" / "reference.json"
    save_profile(profile, path)
    assert load_profile(path) == profile
    assert load_profile(tmp_path / "missing.json") is None


def test_prometheus_text() -> None:
    reference = Profile.of([Observation(values={"kind": ("user_input",)})] * 3)
    text = prometheus_text(Monitor(reference, min_events=1).snapshot())
    assert "# TYPE pac_classifications_total counter" in text
    assert "pac_classifier_errors_total 0" in text
    assert "pac_drift_status" in text
    assert text.endswith("\n")
