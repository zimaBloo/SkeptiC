<div align="center">

<img src="web/public/favicon.svg" width="72" alt="">

# SkeptiC

**Four AI critics read an innovation idea, each from one angle and blind to the others.<br>
One verdict comes back: what is weak, and how to fix it.**

<a href="https://github.com/zimaBloo/SkeptiC/actions/workflows/checks.yml"><img src="https://github.com/zimaBloo/SkeptiC/actions/workflows/checks.yml/badge.svg?branch=main" alt="Checks"></a>
<img src="https://img.shields.io/badge/python-3.14-111316?style=flat-square&logo=python&logoColor=ece8e1" alt="Python 3.14">
<img src="https://img.shields.io/badge/runs_logged_in-Inspect_AI-111316?style=flat-square" alt="Runs logged in Inspect AI">
<img src="https://img.shields.io/badge/web-React_19-111316?style=flat-square&logo=react&logoColor=ece8e1" alt="React 19">
<img src="https://img.shields.io/badge/status-MSc_thesis,_in_progress-d2a657?style=flat-square" alt="Status: MSc thesis, in progress">

[How a run works](#how-a-run-works) · [Quick start](#quick-start) · [The run record](#the-run-record) · [First results](#first-results) · [Development](#development)

</div>

<br>

> [!NOTE]
> Screenshot to come: a finished run in the web interface, with its time, tokens and cost in the caption.

SkeptiC is an MSc thesis project at the University of Helsinki, in the AI Engineering group. The thesis asks whether an AI critic of ideas can be trusted. So each point in a verdict quotes the words of the idea it is about, and each run keeps every prompt and model reply behind it.

## How a run works

> [!NOTE]
> Diagram to come: a flowchart of one run. The idea goes to the four critics, their answers go through the severity rules in code, and the combiner turns them into the verdict.

Every critic answers in the same form. Each issue quotes the idea, says what is wrong and how to fix it, and gets a severity: minor, major or fatal. The critics run on models from different labs and never see each other's answers.

Two rules are applied in code, where a model can't get them wrong. An issue the critic itself calls fixable can't be fatal, and the overall rating follows from the severities. The combiner then groups the serious issues into points by citing their ids (`market-2`, `risk-1`) and writes the statement. It rates nothing: a point's severity comes from the issues it cites, so the summary can't sound harsher or softer than the critics were.

Every reply is also checked, for example for a quote the idea doesn't contain or a field cut off mid-sentence. The checks flag problems in the log and never change an answer.

| Critic | Looks at | Local model | Frontier model |
|---|---|---|---|
| Market | demand, competitors, price | Qwen 3.5 9B | GPT-6.2 Sol |
| Technical feasibility | can it be built as described? | Gemma 4 12B | Gemini 4 Carbon |
| Customer value | would customers pay, and stay? | Ministral 3 8B | Claude Sonnet 5.5 |
| Risk and regulation | law, privacy, safety | Nemotron 3 Nano 4B | Grok 4.7 |
| Combiner | writes the verdict | Gemma 4 12B | Claude Opus 5.5 |

Local models run on your machine through [Ollama](https://ollama.com), so the idea stays private; a run takes several minutes. Frontier models run through [OpenRouter](https://openrouter.ai) with one key for all four labs. A run takes about a minute and a half and cost $0.13 to $0.16 in November 2026.

## What comes back

> The most serious problem is that LunchLoop has not shown that its €0.40 per-use fee covers the cost of collecting, washing, redistributing and replacing boxes. That case rests on an untested 95% return rate whose losses are not costed, and on a 30-restaurant target with no basis. [...]
>
> First, build the unit economics and run a pilot that measures real return rates and paid uses per restaurant. [...]

<sub>The start of the frontier panel's verdict on [LunchLoop](ideas/lunchloop.md), a made-up test idea (22 Nov 2026). Below the statement, the interface lists each point with the critics who raised it and the words of the idea it is about.</sub>

## Quick start

You need [uv](https://docs.astral.sh/uv/), Node.js (22.22+, 24 or 26+), and either Ollama or an OpenRouter key.

```bash
uv sync                        # installs Python 3.14 and the dependencies
cp .env.example .env           # put OPENROUTER_API_KEY here for frontier runs
npm --prefix web install
npm --prefix web run build
uv run skeptic serve           # then open http://127.0.0.1:8000
```

Write an idea or pick a sample, then choose local or frontier models.

<details>
<summary>Setting up the local models</summary>

```bash
ollama pull qwen3.5:9b-q4_K_M
ollama pull gemma4:12b-it-qat
ollama pull ministral-3:8b-instruct-2512-q4_K_M
ollama pull nemotron-3-nano:4b-q4_K_M
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
```

The combiner reads all four reviews at once, so the context window has to be at least 8192 tokens. The log records the window each model ran with and flags calls that came close to it.

</details>

<details>
<summary>What to know about frontier runs</summary>

- The idea goes to the labs' servers. Use made-up or public ideas, like the ones in `ideas/`.
- The models reason before they answer. The panel asks for low reasoning effort and allows 16,000 tokens per reply; `--reasoning` and `--max-tokens` change that.
- Two runs on the same idea can differ. Only Grok uses the temperature setting, and Claude takes no seed.
- Each model is called only through its own lab's endpoint, and only up to a price cap set in `src/skeptic/lineups.py`. If a lab raises its price above the cap, that critic gives no answer and the others go on. If it's the combiner, the run fails after the critics have answered.
- More than half of a run's cost is the combined statement. A prepaid balance with auto top-up off limits what can be spent.

</details>

## Command line

```bash
uv run skeptic run ideas/shelfsense.md                     # local models
uv run skeptic run ideas/shelfsense.md --lineup frontier   # frontier models
uv run skeptic export logs/<run>.eval                      # the record of an earlier run
uv run skeptic schema                                      # the record's format as JSON Schema
uv run inspect view                                        # every prompt and reply, in Inspect's viewer
```

The panel is an [Inspect](https://inspect.aisi.org.uk) task too. Each critic and the combiner is a model role, so any of them can run on another model:

```bash
uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md \
  --model-role risk=ollama/qwen3.5:9b-q4_K_M
```

## The run record

A run leaves two files. The Inspect log has every prompt and every reply. `record.json` has what a reader needs in one document, plus where the run came from: the git commit, a hash of the prompts, and the model builds that served it.

> [!NOTE]
> Diagram to come: a class diagram of `record.json`. A panel record holds four critic records and one combiner record. Each critic's verdict lists its issues, and each point in the combined verdict cites issues by id.

<details>
<summary>How the web interface follows a run</summary>

> [!NOTE]
> Diagram to come: a sequence diagram of the web app starting a run through the API and asking for its progress once a second until the verdict is in.

Runs keep their files in `runs/` and their Inspect logs in `logs/`. Git ignores both.

</details>

## First results

A pilot on 22 Nov 2026 put the frontier panel against Claude Opus 5.5 working alone, on 16 test ideas with answer keys, some sound and some with planted flaws.

| | Panel | One model, same checklist | One model, plain question |
|---|:-:|:-:|:-:|
| Planted fatal flaws named | 3 of 3 | 3 of 3 | 3 of 3 |
| Planted weaknesses found | 18 to 22 of 26 | 23 of 26 | 25 of 26 |
| Sound ideas called unworkable | 0 of 8 | 1 of 8 | 0 or 1 of 8 |
| Cost per verdict | $0.13 | $0.06 | $0.06 |
| Time per verdict | 79 s | 28 s | 36 s |

The panel didn't find more than one strong model, and it cost about twice as much. Traceability held: all 20 panel runs finished, and 312 of 313 critic quotes appear word for word in the idea. The samples are tiny, so none of these differences means much yet. Still untested: whether model diversity helps, and the private local lineup. More in the [pilot report](experiments/pilot-2026-11/report.md).

## Repository

```
src/skeptic/           panel, critics, combiner, checks, run record, CLI, web API
src/skeptic/prompts/   every prompt the models get
web/                   the web interface (React, TypeScript, Vite)
ideas/                 test ideas, some with planted flaws
experiments/           the pilot: ideas, answer keys, grading, results
tests/                 the panel end to end on mock models, offline
```

## Development

```bash
uv run skeptic serve           # the API on port 8000
npm --prefix web run dev       # the interface on http://localhost:5173, reloads on every change
```

GitHub runs these checks on every push to `main` and on every pull request:

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
npm --prefix web run typecheck
npm --prefix web test
```

<br>

<sub>MSc thesis, University of Helsinki, AI Engineering group. Licence not added yet.</sub>
