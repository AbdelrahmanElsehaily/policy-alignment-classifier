"""Record natural conversations: a simulated customer (from a brief) talking to Lark.

    PAC_BACKEND=ollama PAC_MODEL=qwen3.5 uv run python scripts/record_conversations.py
    PAC_BACKEND=ollama PAC_MODEL=qwen3.5 uv run python scripts/record_conversations.py rec-pairing

Reads data/recordings/briefs.json and writes data/recordings/<id>.json: the brief, the models,
and every event with the session envelope it carried. No guardrail runs while recording, so the
trace is what the agent does on its own. Labels are written by hand afterwards, in
data/build_stress_cases.py, and recordings are never edited to fit them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from pac.bootstrap import build_agent, build_backend
from pac.settings import REPO_ROOT, Settings
from pac.simulation.application.customer import CustomerBrief, SimulatedCustomer
from pac.simulation.application.recording import record_conversation
from pac.simulation.domain.back_office import BackOffice
from pac.telemetry import configure_logging, log_context

logger = logging.getLogger("pac.scripts.record_conversations")

RECORDINGS = REPO_ROOT / "data" / "recordings"


async def record(brief: CustomerBrief, settings: Settings) -> Path:
    agent = build_agent(settings)
    customer = SimulatedCustomer(build_backend(settings, use_cache=False))
    office = BackOffice.seeded()
    emails = {c.customer_id: c.email for c in office.customers()}
    recording = await record_conversation(brief, customer, agent, office, emails)
    out = RECORDINGS / f"{brief.id}.json"
    payload = {
        "brief": {
            "id": brief.id,
            "brief": brief.brief,
            "verify_as": brief.verify_as,
            "verified_from_start": brief.verified_from_start,
            "language": brief.language,
        },
        "agent_model": agent.model,
        "customer_model": customer.model,
        "stopped": recording.stopped,
        "events": [
            {
                "session": session.model_dump(mode="json"),
                "event": event.model_dump(mode="json", exclude_none=True),
            }
            for session, event in recording.events
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


async def main(ids: list[str]) -> None:
    settings = Settings(live=True)
    briefs = [
        CustomerBrief(**b) for b in json.loads((RECORDINGS / "briefs.json").read_text("utf-8"))
    ]
    for brief in briefs:
        if ids and brief.id not in ids:
            continue
        with log_context(recording=brief.id):
            out = await record(brief, settings)
            raw = json.loads(out.read_text("utf-8"))
            logger.info(
                "recorded",
                extra={
                    "events": len(raw["events"]),
                    "stopped": raw["stopped"] or "-",
                    "path": str(out.relative_to(REPO_ROOT)),
                },
            )


if __name__ == "__main__":
    configure_logging(Settings().log_level, Settings().log_format)
    asyncio.run(main(sys.argv[1:]))
