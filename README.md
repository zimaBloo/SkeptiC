<div align="center">

<img src="web/public/favicon.svg" width="72" alt="">

# SkeptiC

**Four AI critics read an innovation idea, each from one angle and blind to the others.<br>
One verdict comes back: what is weak, and how to fix it.**

<a href="https://github.com/zimaBloo/SkeptiC/actions/workflows/checks.yml"><img src="https://github.com/zimaBloo/SkeptiC/actions/workflows/checks.yml/badge.svg?branch=main" alt="Checks"></a>
<img src="https://img.shields.io/badge/python-3.14-111316?style=flat-square&logo=python&logoColor=ece8e1" alt="Python 3.14">
<img src="https://img.shields.io/badge/runs_logged_in-Inspect_AI-111316?style=flat-square" alt="Runs logged in Inspect AI">
<img src="https://img.shields.io/badge/status-MSc_thesis,_in_progress-d2a657?style=flat-square" alt="Status: MSc thesis, in progress">

</div>

<br>

<img src=".github/assets/run.png" alt="The run page: the idea at the top, the four critics in a band, the verdict at the bottom, with a stream flowing to the risk critic">

<sub>A replay of a frontier run on [LunchLoop](ideas/lunchloop.md), a made-up test idea: three critics have answered, and Grok, the risk critic, is still writing. The whole run took 1:41 and cost $0.14.</sub>

SkeptiC is an MSc thesis project at the University of Helsinki, in the AI Engineering group. The thesis asks whether an AI critic of ideas can be trusted. So every issue a critic raises has to quote the idea, and every point in the verdict cites the issues behind it. Each run also keeps every prompt and model reply.

## How a run works

```mermaid
flowchart LR
    idea([Idea]) --> market[Market] & technical[Technical] & customer[Customer] & risk[Risk]
    market & technical & customer & risk --> rules["Checks and<br>severity rules (code)"]
    rules --> combiner[Combiner]
    combiner --> checks["Point severities<br>and checks (code)"]
    checks -. "at most one rewrite, if<br>the statement overstates" .-> combiner
    checks --> verdict([Verdict and record])
```

Each critic returns its issues as JSON, with the fields shown in the run record below: every issue quotes the idea and gets a severity (minor, major or fatal). The critics run on models from four labs.

Code then applies two rules a model could get wrong: an issue the critic calls fixable can't be fatal, and a critic's overall rating follows from its severities. The combiner groups the major and fatal issues into points by citing their ids (`market-2`, `risk-1`), notes where the critics disagree and writes the statement. It rates nothing: each point takes the highest severity of the issues it cites, so no point can be harsher or softer than the critics were.

Every reply is checked too, for example for a quote that isn't in the idea. The checks only flag problems in the log, with one exception: if the statement calls a problem fatal or not viable while no issue is rated fatal, the combiner is asked once to rewrite it.

| Critic | Looks at | Local model | Frontier model |
|---|---|---|---|
| Market | demand, competitors, price | Qwen 3.5 9B | GPT-6.2 Sol |
| Technical feasibility | can it be built as described? | Gemma 4 12B | Gemini 4 Carbon |
| Customer value | would customers pay, and stay? | Ministral 3 8B | Claude Sonnet 5.5 |
| Risk and regulation | law, privacy, safety | Nemotron 3 Nano 4B | Grok 4.7 |
| Combiner | writes the verdict | Gemma 4 12B | Claude Opus 5.5 |

Local models run on your own machine through [Ollama](https://ollama.com), so the idea stays private. They take turns, and a run took about nine minutes on the development Mac. Frontier models run side by side through [OpenRouter](https://openrouter.ai), with one key for all four labs. A run takes one to two minutes and cost $0.11 to $0.16 in November 2026.

## What comes back

> The most serious problem is that LunchLoop has not shown that its €0.40 per-use fee covers the cost of collecting, washing, redistributing and replacing boxes. That case rests on an untested 95% return rate whose losses are not costed, and on a 30-restaurant target with no basis. [...]
>
> First, build the unit economics and run a pilot that measures real return rates and paid uses per restaurant. [...]

<sub>The start of the verdict from the run in the screenshot.</sub>

## Quick start

You need [uv](https://docs.astral.sh/uv/), Node.js (22.22+, 24 or 26+) and either Ollama or an OpenRouter key.

```bash
uv sync                        # installs Python 3.14 and the dependencies
cp .env.example .env           # add OPENROUTER_API_KEY for frontier runs
npm --prefix web install
npm --prefix web run build
uv run skeptic serve           # then open http://127.0.0.1:8000
```

<details>
<summary>Setting up the local models</summary>

```bash
ollama pull qwen3.5:9b-q4_K_M
ollama pull gemma4:12b-it-qat
ollama pull ministral-3:8b-instruct-2512-q4_K_M
ollama pull nemotron-3-nano:4b-q4_K_M
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
```

The combiner reads all four answers at once, so Ollama needs a context window of at least 8192 tokens.

</details>

<details>
<summary>Before a frontier run</summary>

- The idea leaves your machine, so use made-up or public ideas, like the ones in `ideas/`.
- The panel asks for low reasoning effort and allows 16,000 tokens per reply. Each call reserves that much, so a low prepaid balance can get calls refused; with auto top-up off, the balance caps what can be spent.
- Each model is called only through its own lab, under a price cap in `src/skeptic/lineups.py`. Above the cap a critic gives no answer and the others go on; if it's the combiner, the run fails.
- Two runs on the same idea can differ.

</details>

<details>
<summary>Command line and Inspect</summary>

```bash
uv run skeptic run ideas/shelfsense.md                     # local models
uv run skeptic run ideas/shelfsense.md --lineup frontier   # frontier models
uv run skeptic export logs/<run>.eval                      # the record of an earlier run
uv run skeptic schema                                      # the record's format as JSON Schema
uv run inspect view                                        # every prompt and reply, in Inspect's viewer
```

The panel is also an [Inspect](https://inspect.aisi.org.uk) task, with each critic and the combiner as a model role, so any of them can run on another model:

```bash
uv run inspect eval skeptic/skeptic_panel -T idea=ideas/shelfsense.md \
  --model-role risk=ollama/qwen3.5:9b-q4_K_M
```

That writes only the Inspect log; `skeptic export` turns it into a record.

</details>

## The run record

Each run leaves an Inspect log and the JSON record below, which for local models also names the exact model build. The command line writes the record next to its log in `logs/`; the web app writes it to `runs/<id>/record.json`.

```text
record.json
├─ idea, status, git commit, prompts hash
├─ critics (4): model, summary, rating, checks, time, tokens, cost
│  └─ issues: quote, what is wrong, why, how to fix, fixable?, severity
└─ combiner: model, statement, disagreements, checks, time, tokens, cost
   └─ points: text, critics, severity, the issue ids it cites
```

## First results

A small test on 22 Nov 2026 compared the frontier panel with Claude Opus 5.5 reviewing alone: once with the panel's rules in a single prompt, and once just asked what is weak about the idea. The test ideas were made up, with answer keys written in advance. Two AI graders checked every review; where they disagreed, the table shows both counts.

| | Panel | Opus alone, with the panel's rules | Opus alone, asked plainly |
|---|:-:|:-:|:-:|
| **3 ideas with one planted fatal flaw** | | | |
| Found the flaw | 3 | 3 | 3 |
| Said the idea can't work | 1 | 2 | 3 |
| **13 planted weaknesses in 3 ideas, each reviewed twice (26 chances)** | | | |
| Fully found | 18 or 22 | 23 | 25 |
| **8 reviews of sound ideas** | | | |
| Wrongly said the idea can't work | 0 | 1 | 0 or 1 |
| **Quotes from the idea** | | | |
| Word for word | 312 of 313 | 124 of 124 | not checked |
| **Per review** | | | |
| Cost | $0.13 | $0.06 | $0.06 |
| Time | 79 s | 28 s | 36 s |

So the panel did no better than Opus alone, at twice the cost. It was also the least ready to call a fatally flawed idea unworkable, but in two of the three the critics who saw the flaw called it fixable, as the rules allow. The test is tiny, so none of these differences is conclusive, and model diversity and the local models are still untested. The [pilot report](experiments/pilot-2026-11/report.md) has the details.

## Development

```
src/skeptic/           the panel, the single-model baselines, checks, run record, CLI, web API
src/skeptic/prompts/   every prompt the panel and the baselines send
web/                   the web interface (React, TypeScript, Vite)
ideas/                 sample ideas and their answer keys
experiments/           the pilot: ideas, answer keys, grading, results
tests/                 offline tests, including the panel end to end on mock models
```

```bash
uv run skeptic serve           # the API on port 8000
npm --prefix web run dev       # the interface on http://localhost:5173, reloads on every change
```

<details>
<summary>Tests GitHub runs on every push and pull request</summary>

```bash
uv run ruff check
uv run ruff format --check
uv run pyright
uv run pytest
npm --prefix web run typecheck
npm --prefix web test
```

GitHub also checks that the web app builds.

</details>

<br>

<sub>No licence yet.</sub>
