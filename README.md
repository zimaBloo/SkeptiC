# SkeptiC

SkeptiC is a panel of AI critics for innovation ideas. You give it an idea
that a person wrote; it tells you what is weak or risky about the idea and how
the idea could be better.

It is an MSc thesis project in early development. The panel runs from the
command line or from a small web interface.

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

The `skeptic` command runs the same panel and also writes the run as one
JSON file: the idea, each critic's verdict and timing, the combined verdict,
the checks, and where the run came from (git commit, prompt hash, model
builds). It goes next to the Inspect log unless you pass `--out`.

```bash
uv run skeptic run ideas/shelfsense.md
uv run skeptic export logs/<run>.eval   # the same file, from an earlier run
uv run skeptic schema                   # the file's format, as JSON Schema
```

## Frontier models

The panel can also run on hosted models from four labs, reached through
[OpenRouter](https://openrouter.ai) with one key:

| Critic | Model |
|---|---|
| Market | GPT-6.2 Sol (OpenAI) |
| Technical feasibility | Gemini 4 Carbon (Google) |
| Customer value | Claude Sonnet 5.5 (Anthropic) |
| Risk and regulation | Grok 4.7 (xAI) |

Claude Opus 5.5 writes the combined statement. Put the OpenRouter key in
`.env` as `OPENROUTER_API_KEY`, then:

```bash
uv run skeptic run ideas/shelfsense.md --lineup frontier
uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md -T lineup=frontier
```

The four critics run at the same time, so a run takes about a minute and a
half instead of several minutes. What differs from the local panel:

- The idea goes to the labs' servers. Use made-up or public ideas, such as
  the ones in `ideas/`.
- These models always reason before they answer. The panel asks for low
  reasoning effort and allows 16,000 tokens per reply, because reasoning
  tokens count against that limit. `--reasoning` and `--max-tokens` change
  them.
- Two runs on the same idea can differ. Only Grok uses the temperature
  setting; Claude, GPT and Gemini sample at a fixed temperature, and Claude
  takes no seed.
- Each model's calls go only to its own lab's endpoint, and only up to a
  price cap set in `src/skeptic/lineups.py`. If a lab raises a price above
  its cap, that model's calls fail until the cap is raised. A critic then
  gives no answer and the others go on; if it is Claude Opus 5.5, which
  writes the combined statement, the run fails after the critics answer.
- A run cost $0.13 to $0.16 in two test runs in November 2026, more than
  half of it for the combined statement; OpenRouter's Activity page shows
  the cost of each call. A prepaid balance with auto top-up off and a
  credit limit on the key cap what can be spent. Close to the end of the
  balance OpenRouter may refuse calls, because the four critics together
  set aside room for 16,000 tokens each.

## Web interface

The interface shows a run as it happens: the idea flows to each critic while
that critic works, then on to the combined verdict, and every finding links
back to the words of the idea it is about. The start page has a choice
between the local and the frontier models. It needs Node.js 22.22 or a later 22, 24, or 26 and later.

```bash
npm --prefix web install
npm --prefix web run build
uv run skeptic serve
```

Then open <http://127.0.0.1:8000>. To work on the interface itself, run
`uv run skeptic serve` and `npm --prefix web run dev` side by side and open
<http://localhost:5173>, which reloads on every change. Runs started from the
interface keep their files in `runs/` and their Inspect logs in `logs/`, and
git ignores both.

## Checks

GitHub runs these on every push to `main` and on every pull request.

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
npm --prefix web run typecheck
npm --prefix web test
```

## Licence

Not added yet.
