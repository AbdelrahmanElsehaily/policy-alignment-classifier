"""Monitoring data in and out: reference profiles as JSON files, and Prometheus text format."""

from __future__ import annotations

from pathlib import Path

from pac.monitoring.application.monitor import MonitoringSnapshot
from pac.monitoring.domain.drift import Profile


def save_profile(profile: Profile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=1) + "\n", encoding="utf-8")


def load_profile(path: Path) -> Profile | None:
    """The reference profile at ``path``, or ``None`` when there isn't one yet."""
    if not path.exists():
        return None
    return Profile.model_validate_json(path.read_text(encoding="utf-8"))


_STATUS_CODE = {"stable": 0, "moderate": 1, "major": 2, "insufficient": -1}


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def prometheus_text(snapshot: MonitoringSnapshot) -> str:
    """The snapshot in Prometheus' text exposition format (version 0.0.4)."""
    t = snapshot.totals
    lines = [
        "# HELP pac_classifications_total Events judged by the guardrail.",
        "# TYPE pac_classifications_total counter",
        *(
            f'pac_classifications_total{{verdict="{_label(v)}"}} {n}'
            for v, n in sorted(t.by_verdict.items())
        ),
        "# HELP pac_events_by_kind_total Events judged, by event kind.",
        "# TYPE pac_events_by_kind_total counter",
        *(
            f'pac_events_by_kind_total{{kind="{_label(k)}"}} {n}'
            for k, n in sorted(t.by_kind.items())
        ),
        "# HELP pac_classifier_errors_total Classifications that failed closed.",
        "# TYPE pac_classifier_errors_total counter",
        f"pac_classifier_errors_total {t.errors}",
        "# HELP pac_model_latency_seconds_sum Total model time across classifications.",
        "# TYPE pac_model_latency_seconds_sum counter",
        f"pac_model_latency_seconds_sum {t.latency_ms_sum / 1000:.3f}",
        "# HELP pac_monitor_window_events Events in the drift window.",
        "# TYPE pac_monitor_window_events gauge",
        f"pac_monitor_window_events {snapshot.window_events}",
    ]
    if snapshot.drift is not None:
        lines += [
            "# HELP pac_drift_psi Population Stability Index of the window against the reference.",
            "# TYPE pac_drift_psi gauge",
            *(
                f'pac_drift_psi{{feature="{f.feature}",group="{f.group}"}} {f.psi}'
                for f in snapshot.drift.features
                if f.psi is not None
            ),
            "# HELP pac_drift_status 0 stable, 1 moderate, 2 major, -1 not enough data.",
            "# TYPE pac_drift_status gauge",
            f"pac_drift_status {_STATUS_CODE[snapshot.drift.status]}",
        ]
    return "\n".join(lines) + "\n"
