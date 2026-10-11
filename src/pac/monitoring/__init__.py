"""Monitoring context: measure what the guardrail sees and says, and alert on drift.

In production the classifier meets traffic nobody labelled. Accuracy can't be measured there,
but the shape of the traffic and of the verdicts can: if either moves away from what the
classifier was evaluated on, its eval numbers stop describing it. This context profiles every
judged event, compares a rolling window with a reference profile (Population Stability Index),
and raises alerts. It depends only on the guardrail's domain model.
"""
