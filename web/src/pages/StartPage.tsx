import { useEffect, useState, type CSSProperties, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { ApiError, CRITICS, api, type IdeaSummary, type RunSummary } from "../api";
import { FlowCanvas } from "../flow/FlowCanvas";
import type { Intensities } from "../flow/field";
import { labelRoom, labelSide, layout } from "../flow/geometry";
import { formatSeconds } from "../flow/timeline";
import { CRITIC_TITLES, durationBetween, formatWhen } from "../format";
import { useReducedMotion, useSize } from "../hooks";

const OWN = "__own__";
const MIN_CHARS = 200;
const MAX_CHARS = 20000;
const MAX_TITLE = 200; // the server's limit

// At rest the field shows the panel's shape: the idea drifting towards every critic.
const RESTING: Intensities = Object.fromEntries(
  CRITICS.flatMap((c) => [
    [`in:${c}`, 0.14],
    [`out:${c}`, 0.06],
  ]),
);

export function StartPage() {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const [ideas, setIdeas] = useState<IdeaSummary[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [choice, setChoice] = useState<string>("");
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [loads, setLoads] = useState(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.ideas(), api.runs()])
      .then(([i, r]) => {
        if (cancelled) return;
        setIdeas(i);
        setRuns(r);
        setLoadError(null);
        setChoice((current) => current || (i[0]?.id ?? OWN));
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

  const ownLength = text.trim().length;
  const ownReady = choice !== OWN || (title.trim().length > 0 && ownLength >= MIN_CHARS && ownLength <= MAX_CHARS);
  const ready = choice !== "" && ownReady && activeRun === null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const { id } =
        choice === OWN
          ? await api.startRun({ title: title.trim(), text: text.trim() })
          : await api.startRun({ idea_id: choice });
      navigate(`/runs/${id}`);
    } catch (e) {
      setSubmitError(
        e instanceof ApiError && e.status === 409
          ? "Another run is still going. The local models run one at a time."
          : e instanceof Error
            ? e.message
            : String(e),
      );
      setSubmitting(false);
    }
  };

  return (
    <main className="start page-enter">
      <section className="start-intro">
        <h1>Put an idea in front of four critics.</h1>
        <p className="start-lede">
          Each critic reads it from one angle, on a model from a different family, and never sees
          the others' answers. A last step combines what they found into one statement: what is
          weak or risky, and how to make the idea better.
        </p>

        {loadError ? (
          <p className="form-error" role="alert">
            Cannot reach the panel's server ({loadError}). Start it with{" "}
            <code>uv run skeptic serve</code>; this page tries again every few seconds.
          </p>
        ) : (
          <form className="start-form" onSubmit={submit}>
            <fieldset className="idea-choices" disabled={submitting}>
              <legend>Idea</legend>
              {ideas === null && <p className="muted">Loading ideas…</p>}
              {ideas?.map((idea) => (
                <label key={idea.id} className="idea-choice">
                  <input
                    type="radio"
                    name="idea"
                    value={idea.id}
                    checked={choice === idea.id}
                    onChange={() => setChoice(idea.id)}
                  />
                  <span className="idea-choice-title">{idea.title}</span>
                  <span className="idea-choice-meta mono">
                    {idea.sample ? "sample · " : ""}
                    {idea.chars.toLocaleString()} characters
                  </span>
                </label>
              ))}
              <label className="idea-choice">
                <input type="radio" name="idea" value={OWN} checked={choice === OWN} onChange={() => setChoice(OWN)} />
                <span className="idea-choice-title">Your own idea</span>
                <span className="idea-choice-meta mono">paste it in</span>
              </label>
            </fieldset>

            {choice === OWN && (
              <div className="own-idea">
                <label>
                  <span>Title</span>
                  <input
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    maxLength={MAX_TITLE}
                    placeholder="A short name for the idea"
                  />
                </label>
                <label>
                  <span>Idea</span>
                  <textarea
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    rows={9}
                    placeholder="Problem, solution, customers, business model, market, plan: a page of plain text works best."
                  />
                </label>
                <p className="muted mono">
                  {ownLength.toLocaleString()} / {MIN_CHARS} to {MAX_CHARS.toLocaleString()} characters
                </p>
              </div>
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

        {runs && runs.length > 0 && (
          <section className="past-runs" aria-labelledby="past-runs-title">
            <h2 id="past-runs-title">Past runs</h2>
            <ul>
              {runs.slice(0, 8).map((run) => (
                <li key={run.id}>
                  <Link to={`/runs/${run.id}`}>{run.idea_title}</Link>
                  <span className="mono">{formatWhen(run.started_at)}</span>
                  <span className="mono" data-status={run.status}>
                    {run.status === "success"
                      ? formatSeconds(durationBetween(run.started_at, run.finished_at))
                      : run.status}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </section>

      <PanelDiagram reduce={reduce} />
    </main>
  );
}

/** The panel's shape at rest: idea, four critics, verdict. */
function PanelDiagram({ reduce }: { reduce: boolean }) {
  const [ref, size] = useSize<HTMLDivElement>();
  const l = size.width > 0 ? layout(size.width, size.height) : null;
  return (
    <div className="start-diagram" ref={ref} aria-hidden="true">
      <FlowCanvas intensities={RESTING} still={reduce} count={700} />
      {l && (
        <div className="node-labels is-resting">
          <div className="node node--idea" style={{ left: l.idea.x, top: l.idea.y }}>
            <span className="node-ring" data-state="done" />
            <div className="node-text node-text--below" data-quiet>
              <span className="node-title">Idea</span>
            </div>
          </div>
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
              <span className="node-ring" data-state="waiting" />
              <div className={`node-text node-text--right is-${labelSide(l, critic)}`} data-quiet>
                <span className="node-title">{CRITIC_TITLES[critic]}</span>
              </div>
            </div>
          ))}
          <div className="node node--combiner" style={{ left: l.combiner.x, top: l.combiner.y }}>
            <span className="node-ring" data-state="waiting" />
            <div className={`node-text ${l.compact ? "node-text--above" : "node-text--below"}`} data-quiet>
              <span className="node-title">Verdict</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
