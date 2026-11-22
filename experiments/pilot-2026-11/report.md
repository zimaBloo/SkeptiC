# Pilot: does a panel of critics beat one strong model? (22 Nov 2026)

A small pilot, not evidence. It was reviewed by two independent checkers (one recomputed
every number from the raw files, one attacked the interpretation); their corrections are
applied below.

## Setup

- **Panel:** SkeptiC frontier lineup. Critics: GPT-6.2 Sol, Gemini 4 Carbon, Claude Sonnet
  5.5, Grok 4.7; Claude Opus 5.5 combines. Graded on its final verdict (statement, points,
  improvements).
- **One model, same checklist:** Claude Opus 5.5 with the four critics' focuses, rules,
  severity scale and answer format in one prompt.
- **One model, plain question:** Claude Opus 5.5 asked "What is weak or risky about this
  idea, and how could it be better?"
- **Texts (16):** ShelfSense, LunchLoop, ShiftSwap (13 planted weaknesses); PumpWatch (sound
  control); 6 new pairs of a sound idea and a twin with one planted fatal flaw. An
  independent check (a Claude model) accepted 3 of the 6 twins (MealMate, MathQuest,
  NorthFix) and all 6 sound versions. The four older ideas ran twice, the new texts once.
- **Grading:** two graders (GPT-6.1 Terra, Gemini 4 Hydrogen) against the answer keys. The
  setups' formats reveal which setup wrote a review, so blinding is nominal.

## Results (counted in outputs; two graders each)

| | Panel | One model, checklist | One model, plain |
|---|---|---|---|
| Planted fatal flaw named (3 checked twins) | 3 of 3 | 3 of 3 | 3 of 3 |
| ...and called unworkable | 1 of 3 | 2 of 3 | 3 of 3 |
| Sound idea called unworkable (8 outputs) | 0 | 1 | 1 by one grader |
| Planted weaknesses found (26 output x weakness pairs) | 18 or 22, by grader | 23 | 25 |
| Words per review (mean) | 782 | 742 | 1,034 |
| Critic or judge quotes not in the idea | 1 of 313 | 0 of 124 | not checked |
| Cost per verdict | $0.131 | $0.060 | $0.064 |
| Time per verdict | 79 s | 28 s | 36 s |

Without ShelfSense (the idea used to tune the prompts), weaknesses found: panel 13 to 16 of
18, checklist 15, plain 17.

## What can be said

1. **The panel did not find more than one strong model.** All three setups named every
   planted fatal flaw, but those flaws are simple arithmetic or legal facts, so detection
   was at ceiling. On the 13 planted weaknesses the plain answer was marked found in 25 of
   26, the checklist in 23, the panel in 18 or 22 depending on the grader. The plain answer
   is also about 40% longer, and the grading counts recall only, not wrong or unneeded
   points.
2. **No setup separates fatal from fixable flaws well.** The panel's final verdict labels
   almost every point "major" (minor issues are dropped by design), and for MealMate and
   NorthFix it gave the flawed and the sound twin near-identical verdicts. Its critics
   marked those flaws fixable within the idea, which the rubric allows (cut costs per meal;
   shrink NorthFix to one or two mechanics), so the answer key's "fatal" for these two is
   itself contestable. The checklist model, with the same rubric, called NorthFix fatal
   but not MealMate. The false-alarm difference is one output.
3. **The panel costs about twice as much and takes two to three times as long.**
4. **Traceability works.** The pipeline ran cleanly (20 of 20 panel runs, every critic
   answered), and the critics' quotes were almost all word for word. That shows the
   verdicts can be traced to the idea's words, not that they are right.
5. **Not tested here:** whether model diversity helps, and the local, private lineup.

## Limits

- Tiny samples: 3 outputs per setup for the twins, 8 for the sound ideas; two graders double
  the count without adding information; no difference is statistically meaningful.
- The new ideas, the independent check and the single judge are all Claude models.
- Grading measures recall only; "called unworkable" is read from explicit labels for the
  panel and the checklist but inferred from tone for the plain answer.
- Sound ideas were often rated "weak" (the checklist on 6 of 8), which the unworkable-only
  measure hides.
- Not cost-matched: the panel spends about twice as much per verdict.
- The panel was graded on its final verdict only; its critics may have found more.

## What it means for the thesis

A neutral-to-negative pilot result is a legitimate finding and matches parts of the
literature (ensembles not beating the best single model). The panel's case rests on
traceability and on properties still to test: model diversity, the private local lineup,
and severity calibration. The next tests: a same-cost comparison, grading the critics'
issues as well as the final verdict, precision (wrong or unneeded points), more and
human-written test ideas with agreed fatal flaws, and the local lineup.
