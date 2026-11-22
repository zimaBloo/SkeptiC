import { useEffect, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router";
import { ApiError, CRITICS, api, type IdeaSummary, type LineupInfo, type LineupName, type RunSummary } from "../api";
import { Chevron } from "../Chevron";
import { FlowCanvas } from "../flow/FlowCanvas";
import { Graticule } from "../flow/Graticule";
import { NodeIcon } from "../flow/NodeIcon";
import { PanelBand, Port } from "./RunPage";
import type { Intensities } from "../flow/field";
import { labelRoom, layout } from "../flow/geometry";
import { formatSeconds } from "../flow/timeline";
import { CRITIC_ANGLES, CRITIC_SHORT, CRITIC_TITLES, durationBetween, formatWhen, modelName } from "../format";
import { useSize } from "../hooks";

const OWN = "__own__";
const LINEUP_KEY = "skeptic.lineup";
const MIN_CHARS = 200;
const MAX_CHARS = 20000;
const MAX_TITLE = 200; // the server's limit

// At rest the field shows the panel's shape: the idea drifting towards every critic.
const RESTING: Intensities = Object.fromEntries(
  CRITICS.flatMap((c) => [
    [`in:${c}`, 0.3],
    [`out:${c}`, 0.16],
  ]),
);

/** The top of the page: what SkeptiC does, the idea and the models, and past runs. Only
 * the choices are out at first; the explanations and the list of past runs are folded
 * away until asked for. */
export function StartSection({ currentRunId }: { currentRunId?: string }) {
  const navigate = useNavigate();
  const [ideas, setIdeas] = useState<IdeaSummary[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // A visitor's own idea comes first; the samples are there for trying the panel out.
  const [choice, setChoice] = useState<string>(OWN);
  const [lineups, setLineups] = useState<LineupInfo[] | null>(null);
  const [lineup, setLineup] = useState<LineupName>(readLineup);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [loads, setLoads] = useState(0);
  const [samplesOpen, setSamplesOpen] = useState(false);
  const samplesRef = useRef<HTMLElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.ideas(), api.runs(), api.lineups()])
      .then(([i, r, m]) => {
        if (cancelled) return;
        setIdeas(i);
        setRuns(r);
        setLineups(m);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [loads]);

  const activeRun = runs?.find((r) => r.status === "running") ?? null;

  // Ask again while the server is unreachable, and while a run is going, so the
  // page notices when it comes back or when the run ends.
  useEffect(() => {
    if (!loadError && !activeRun) return;
    const timer = window.setTimeout(() => setLoads((n) => n + 1), 3000);
    return () => window.clearTimeout(timer);
  }, [loadError, activeRun, loads]);

  useEffect(() => {
    try {
      window.localStorage.setItem(LINEUP_KEY, lineup);
    } catch {
      // Only a convenience: the choice is not remembered.
    }
  }, [lineup]);

  const chosenLineup = lineups?.find((m) => m.name === lineup) ?? null;
  const chosenIdea = ideas?.find((i) => i.id === choice) ?? null;

  /** Fold the samples away again after a choice, back on their summary. */
  const closeSamples = () => {
    setSamplesOpen(false);
    samplesRef.current?.focus();
  };
  // A pointer choice folds the samples away; the arrow keys only move through them
  // (their clicks have no detail), and Enter or Escape folds them.
  const onSampleClick = (event: MouseEvent) => {
    if (event.detail > 0) closeSamples();
  };
  const onSamplesKey = (event: KeyboardEvent<HTMLElement>) => {
    if (!samplesOpen) return;
    if (event.key === "Escape" || (event.key === "Enter" && event.target instanceof HTMLInputElement)) {
      // Enter on a choice would otherwise submit the form.
      event.preventDefault();
      closeSamples();
    }
  };
  const writeOwn = () => {
    setChoice(OWN);
    window.requestAnimationFrame(() => titleRef.current?.focus());
  };
  const ownLength = text.trim().length;
  const ownReady = choice !== OWN || (title.trim().length > 0 && ownLength >= MIN_CHARS && ownLength <= MAX_CHARS);
  const ready = choice !== "" && ownReady && activeRun === null && chosenLineup?.ready === true;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const { id } =
        choice === OWN
          ? await api.startRun({ title: title.trim(), text: text.trim(), lineup })
          : await api.startRun({ idea_id: choice, lineup });
      navigate(`/runs/${id}`);
    } catch (e) {
      setSubmitError(
        e instanceof ApiError && e.status === 409
          ? "Another run is still going. The panel takes one idea at a time."
          : e instanceof Error
            ? e.message
            : String(e),
      );
      setSubmitting(false);
    }
  };

  return (
    <section className="start" id="start" aria-label="Start a run">
      <section className="start-intro">
        <h1>Put an idea in front of four critics.</h1>
        <p className="start-lede">
          Each reads it from one angle, blind to the others. One statement comes back: what to fix.
        </p>
        <details className="disclosure">
          <summary>
            How it works
            <Chevron />
          </summary>
          <div className="disclosure-body how-it-works">
            <p>Four critics read the idea, each from one angle:</p>
            <ul className="angle-list">
              {CRITICS.map((critic) => (
                <li key={critic} data-critic={critic}>
                  <span className="critic-dot" />
                  <span className="angle-title">{CRITIC_TITLES[critic]}</span>
                  <span className="angle-text">{CRITIC_ANGLES[critic]}</span>
                </li>
              ))}
            </ul>
            <p>
              Each critic runs on a model from a different family and never sees the others' answers. A
              last step combines what they found into one statement: what is weak or risky, and how to
              make the idea better. Every run is logged, so each point traces back to the critic that
              raised it.
            </p>
          </div>
        </details>

        {loadError ? (
          <p className="form-error" role="alert">
            Cannot reach the panel's server ({loadError}). Start it with{" "}
            <code>uv run skeptic serve</code>; this page tries again every few seconds.
          </p>
        ) : (
          <form className="start-form" onSubmit={submit}>
            <div className="field idea-field">
              {choice === OWN || !chosenIdea ? (
                <div className="own-idea">
                  <label>
                    <span>Title</span>
                    <input
                      ref={titleRef}
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      maxLength={MAX_TITLE}
                      placeholder="A short name for the idea"
                      disabled={submitting}
                    />
                  </label>
                  <label>
                    <span>Description</span>
                    <textarea
                      value={text}
                      onChange={(e) => setText(e.target.value)}
                      rows={6}
                      placeholder="Problem, solution, customers, business model, market, plan: a page of plain text works best."
                      disabled={submitting}
                    />
                  </label>
                  <p className="muted mono">
                    {ownLength.toLocaleString()} / {MIN_CHARS} to {MAX_CHARS.toLocaleString()} characters
                  </p>
                </div>
              ) : (
                <>
                  <span className="field-label">Sample idea</span>
                  <div className="sample-chosen">
                    <span className="sample-chosen-text">
                      <span className="sample-chosen-title">{chosenIdea.title}</span>
                      <span className="sample-chosen-meta mono">sample · {ideaMeta(chosenIdea)}</span>
                    </span>
                    <button type="button" className="text-button" onClick={writeOwn} disabled={submitting}>
                      Write your own
                    </button>
                  </div>
                </>
              )}
              {ideas && ideas.length > 0 && (
                <details
                  className="disclosure samples"
                  open={samplesOpen}
                  onToggle={(e) => setSamplesOpen(e.currentTarget.open)}
                  onKeyDown={onSamplesKey}
                >
                  <summary ref={samplesRef}>
                    {choice === OWN ? "Or try a sample idea" : "Other sample ideas"}
                    <Chevron />
                  </summary>
                  <fieldset className="idea-choices disclosure-body" disabled={submitting}>
                    <legend className="visually-hidden">Sample ideas</legend>
                    {ideas.map((idea) => (
                      <label key={idea.id} className="idea-choice" onClick={onSampleClick}>
                        <input
                          type="radio"
                          name="idea"
                          value={idea.id}
                          checked={choice === idea.id}
                          onChange={() => setChoice(idea.id)}
                        />
                        <span className="idea-choice-title">{idea.title}</span>
                        <span className="idea-choice-meta mono">{ideaMeta(idea)}</span>
                      </label>
                    ))}
                  </fieldset>
                </details>
              )}
            </div>

            <fieldset className="field" disabled={submitting}>
              <legend className="field-label">Models</legend>
              {lineups === null ? (
                <p className="muted">Loading…</p>
              ) : (
                <div className="segmented">
                  {lineups.map((option) => (
                    <label key={option.name} className="segment">
                      <input
                        type="radio"
                        name="lineup"
                        value={option.name}
                        checked={lineup === option.name}
                        onChange={() => setLineup(option.name)}
                      />
                      <span>{option.title}</span>
                    </label>
                  ))}
                </div>
              )}
              {chosenLineup && <p className="field-hint">{chosenLineup.description}</p>}
              {lineups && lineups.length > 0 && (
                <details className="disclosure">
                  <summary>
                    Which models run
                    <Chevron />
                  </summary>
                  <div className="disclosure-body">
                    <LineupTable lineups={lineups} />
                  </div>
                </details>
              )}
            </fieldset>
            {chosenLineup && !chosenLineup.ready && chosenLineup.missing_key && (
              <p className="form-note">
                The {chosenLineup.title.toLowerCase()} models need an API key. Put{" "}
                <code>{chosenLineup.missing_key}=…</code> in the <code>.env</code> file next to{" "}
                <code>pyproject.toml</code>; this page picks it up on its next load.
              </p>
            )}

            <div className="start-actions">
              <button type="submit" className="primary-button" disabled={!ready || submitting}>
                {submitting ? "Starting…" : "Run the panel"}
              </button>
              {activeRun && (
                <p className="muted">
                  A run is going: <Link to={`/runs/${activeRun.id}`}>{activeRun.idea_title}</Link>
                </p>
              )}
              {submitError && (
                <p className="form-error" role="alert">
                  {submitError}
                </p>
              )}
            </div>
          </form>
        )}
      </section>

      {runs && runs.length > 0 && (
        <details className="past-runs disclosure">
          <summary>
            Past runs <span className="past-runs-count mono">{runs.length}</span>
            <Chevron />
          </summary>
          <ul className="disclosure-body">
            {runs.slice(0, 10).map((run) => (
              <li key={run.id}>
                <Link to={`/runs/${run.id}`} aria-current={run.id === currentRunId ? "page" : undefined}>
                  {run.idea_title}
                </Link>
                <span className="mono">
                  {run.lineup === "local" ? "" : `${run.lineup} · `}
                  {formatWhen(run.started_at)}
                </span>
                <span className="mono" data-status={run.status}>
                  {run.status === "success"
                    ? formatSeconds(durationBetween(run.started_at, run.finished_at))
                    : run.status}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function ideaMeta(idea: IdeaSummary): string {
  return `${idea.chars.toLocaleString()} characters`;
}

/** Which model takes each step in each lineup, side by side. */
function LineupTable({ lineups }: { lineups: LineupInfo[] }) {
  const steps = [...CRITICS, "combiner"] as const;
  return (
    <table className="lineup-table">
      <thead>
        <tr>
          <th scope="col">
            <span className="visually-hidden">Step</span>
          </th>
          {lineups.map((option) => (
            <th key={option.name} scope="col">
              {option.title}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {steps.map((step) => (
          <tr key={step} data-critic={step === "combiner" ? undefined : step}>
            <th scope="row">
              {step === "combiner" ? (
                <>
                  <span className="critic-dot is-blank" /> Verdict
                </>
              ) : (
                <>
                  <span className="critic-dot" /> {CRITIC_TITLES[step]}
                </>
              )}
            </th>
            {lineups.map((option) => (
              <td key={option.name} className="mono">
                {modelName(option.models[step])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function readLineup(): LineupName {
  try {
    return window.localStorage.getItem(LINEUP_KEY) === "frontier" ? "frontier" : "local";
  } catch {
    return "local";
  }
}

/** The panel's shape at rest, before a run is chosen: the idea above, four critics,
 * the verdict below, with the streams drifting faintly between them. */
export function RestingStage({ reduce }: { reduce: boolean }) {
  const [ref, size] = useSize<HTMLDivElement>();
  const l = size.width > 0 ? layout(size.width, size.height) : null;
  return (
    <div className="run-stage is-resting" ref={ref}>
      <Graticule />
      <FlowCanvas className="run-field" intensities={RESTING} still={reduce} count={900} />
      {l && (
        <div className="node-labels" aria-hidden="true">
          <PanelBand layout={l} />
          <Port id="idea" capsule={l.inlet} state="done" title="Your idea" meta="written by a person" />
          {CRITICS.map((critic) => (
            <div
              key={critic}
              className="node node--critic"
              data-critic={critic}
              style={
                {
                  left: l.critics[critic].x,
                  top: l.critics[critic].y,
                  "--room": `${labelRoom(l, critic)}px`,
                } as CSSProperties
              }
            >
              <span className="node-ring" data-state="waiting">
                <NodeIcon id={critic} />
              </span>
              <div className={`node-text ${l.compact ? "node-text--below" : "node-text--right"}`} data-quiet>
                <span className="node-title">{l.compact ? CRITIC_SHORT[critic] : CRITIC_TITLES[critic]}</span>
              </div>
            </div>
          ))}
          <Port id="combiner" capsule={l.outlet} state="waiting" title="Verdict" meta="one statement: what to fix" />
        </div>
      )}
      <p className="status-line">Choose an idea above and run the panel. The streams show which critic is working.</p>
    </div>
  );
}
