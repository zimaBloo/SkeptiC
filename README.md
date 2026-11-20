# SkeptiC

SkeptiC is a panel of AI critics for innovation ideas. You give it an idea
that a person wrote; it tells you what is weak or risky about the idea and how
the idea could be better.

It is an MSc thesis project in early development, so this page describes the
design. Nothing is ready to use yet.

Each critic looks at the idea from one angle (market, technical feasibility,
customer value, or risk and regulation) and runs on a model from a different
family. The critics never see each other's answers. A last step combines
their verdicts and shows where they agree and where they don't. Every run is
logged in full, which means any verdict can be checked against the prompts
and model outputs behind it.

## Setup

Copy `.env.example` to `.env` and add the API keys you need. Git ignores
`.env`, so the keys stay out of the repository.

## Licence

Not added yet.
