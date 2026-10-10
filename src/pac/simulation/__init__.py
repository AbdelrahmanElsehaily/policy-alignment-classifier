"""Simulation context: a stand-in agent and back office that give the guardrail live traffic.

The guardrail is a man in the middle: in a real deployment the agent belongs to someone else.
For the live demo this context plays that agent ("Lark") and the systems behind its tools, so
a person can chat with it and watch the guardrail judge every event as it happens.

It depends only on the guardrail's published domain model and its LLM port, never on the
classifier itself: the conversation is wired to a classifier in the composition root.
"""
