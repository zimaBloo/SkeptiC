# SkeptiC

SkeptiC is a panel of AI critics for innovation ideas. You give it an idea
that a person wrote; it tells you what is weak or risky about the idea and how
the idea could be better.

It is an MSc thesis project in early development. The critic panel runs from
the command line; there is no web interface yet.

Each critic looks at the idea from one angle (market, technical feasibility,
customer value, or risk and regulation) and runs on a model from a different
family. The critics never see each other's answers. A last step combines
their verdicts and shows where they agree and where they don't. Every run is
logged in full, which means any verdict can be checked against the prompts
and model outputs behind it.

## Setup

You need [uv](https://docs.astral.sh/uv/). It also installs the Python
version the project uses (3.14).

```bash
uv sync
```

Then copy `.env.example` to `.env` and add the API keys you need. Git ignores
`.env`, so the keys stay out of the repository.

## Running the panel

By default the panel runs on four local models through
[Ollama](https://ollama.com), one model family per critic:

| Critic | Model |
|---|---|
| Market | Qwen 3.5 9B |
| Technical feasibility | Gemma 4 12B |
| Customer value | Ministral 3 8B |
| Risk and regulation | Nemotron 3 Nano 4B |

Gemma also writes the combined statement. Pull the models once:

```bash
ollama pull qwen3.5:9b-q4_K_M
ollama pull gemma4:12b-it-qat
ollama pull ministral-3:8b-instruct-2512-q4_K_M
ollama pull nemotron-3-nano:4b-q4_K_M
```

Start Ollama with a context window of at least 8192 tokens. The combining
step reads all four reviews at once, and a smaller window cuts them off. The
log records the window each model ran with and flags any call that came close
to it.

```bash
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
```

Then run the panel on an idea and open the log:

```bash
uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md
uv run inspect view
```

The log holds every prompt and every model reply. Each critic and the
combiner is a model role, so any of them can run on a different model:

```bash
uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md \
  --model-role risk=ollama/qwen3.5:9b-q4_K_M
```

## Checks

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
```

## Licence

Not added yet.
