"""Synthesis context: synthetic, labelled traffic for the guardrail, and an adversarial search.

The eval set showed what helps and what hurts. Template-generated events taught prompt
optimisers template-specific rules; natural simulated conversations were what generalised;
different agent models produce very different traffic; reusing the eval world's ids leaks; and
labels written by one model family measure that family's consistency, not the truth. This
context holds the pure logic of a pipeline built around those lessons:

* ``domain``: the boundary spec of each rule, a separate synthetic world, scenario grids
  (personas, goals, flawed agents, tool faults), deterministic labelling functions for the
  mechanical rules, jury aggregation, near-duplicate detection, the coverage grid, the
  quality-diversity archive for adversarial search, and agreement and diversity metrics.
* ``application``: use cases that call models through the guardrail's ``LLMBackend`` port: the
  per-rule jury labeller, seed growth and review, calibration against existing gold labels,
  and the Rainbow-Teaming-style adversarial loop.
* ``infrastructure``: the call and cost ledger with budget caps, and file stores.

Like the other supporting contexts it sees only the guardrail's domain model and its LLM port.
The script ``scripts/synthesize.py`` composes it with ``simulation`` (recording conversations),
``monitoring`` (drift) and the classifier.
"""
