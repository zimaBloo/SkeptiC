import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import type { CriticRecord, PanelRecord } from "../api";
import { formatSeconds } from "../flow/timeline";
import { CRITIC_TITLES, durationBetween, formatWhen, modelName } from "../format";
import { useRun } from "../hooks";
import { IdeaText, type Mark } from "../IdeaText";
import { findQuote } from "../quotes";

export function DetailsPage() {
  const { id } = useParams();
  const { hash } = useLocation();
  const { run, error, missing } = useRun(id);
  const linked = issueFromHash(hash);
  // The issue the reader came for (or has focused) stays lit; hovering another one
  // lights that instead, and letting go returns to it.
  const [pinned, setPinned] = useState<string | null>(linked);
  const [hovered, setHovered] = useState<string | null>(null);
  const active = hovered ?? pinned;
  const record = run?.record ?? null;
  const ideaText = record?.idea ?? run?.idea_text ?? "";

  // Hover follows the pointer, but not while the page scrolls to a linked issue: the
  // browser reports whatever slides under a resting pointer as hovered. Hover counts
  // again once the pointer really moves.
  const hoverArmed = useRef(!linked);
  useEffect(() => {
    if (hoverArmed.current) return;
    const onMove = (event: PointerEvent) => {
      if (Math.abs(event.movementX) + Math.abs(event.movementY) < 2) return;
      hoverArmed.current = true;
      window.removeEventListener("pointermove", onMove);
    };
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, []);
  const onHover = (markId: string | null) => {
    if (hoverArmed.current) setHovered(markId);
  };

  // Arriving from a point on the verdict: bring its issue, and the words it quotes, into view.
  const scrolled = useRef<string | null>(null);
  useEffect(() => {
    if (!linked || !record || scrolled.current === linked) return;
    const issue = document.getElementById(`issue-${linked}`);
    if (!issue) return;
    scrolled.current = linked;
    setPinned(linked);
    const behavior = window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
    issue.scrollIntoView({ block: "center", behavior });
    issue.focus({ preventScroll: true });
    // The idea column scrolls on its own on wide screens. Scroll it directly: a second
    // scrollIntoView would also move the window and cancel the scroll to the issue.
    const column = document.querySelector<HTMLElement>(".details-idea");
    const mark = column?.querySelector<HTMLElement>(`mark[data-mark="${CSS.escape(linked)}"]`);
    if (column && mark && column.scrollHeight > column.clientHeight) {
      const offset = mark.getBoundingClientRect().top - column.getBoundingClientRect().top;
      column.scrollTo({ top: column.scrollTop + offset - column.clientHeight / 3, behavior });
    }
  }, [linked, record]);

  const marks = useMemo<Mark[]>(() => {
    if (!record) return [];
    return record.critics.flatMap((critic) =>
      (critic.verdict?.issues ?? []).flatMap((issue, index) => {
        const span = findQuote(ideaText, issue.quote);
        return span ? [{ id: `${critic.critic}-${index + 1}`, critic: critic.critic, ...span }] : [];
      }),
    );
  }, [record, ideaText]);

  if (missing) {
    return (
      <main className="details">
        <p className="form-error" role="alert">
          There is no run called {id}. <Link to="/">Start a new run</Link>
        </p>
      </main>
    );
  }
  if (error && !run) return <main className="details"><p className="form-error">Cannot load the run: {error}</p></main>;
  if (!run) return <main className="details"><p className="muted">Loading…</p></main>;

  return (
    <main className="details page-enter">
      <header className="details-head">
        <h1>{run.idea_title}</h1>
        <p className="details-meta mono">
          {formatWhen(record?.started_at ?? null)}
          {record?.started_at && record.finished_at
            ? ` · ${formatSeconds(durationBetween(record.started_at, record.finished_at))}`
            : ""}
          {` · ${run.status}`}
        </p>
        <p className="details-back">
          <Link to={`/runs/${run.id}`}>Back to the verdict</Link>
        </p>
      </header>

      <div className="details-grid">
        <section className="details-idea" aria-label="The idea">
          <IdeaText text={ideaText} marks={marks} active={active} onMarkEnter={onHover} title={run.idea_title} />
        </section>

        <section className="details-critics" aria-label="What each critic found">
          {record ? (
            record.critics.map((critic) => (
              <CriticSection
                key={critic.critic}
                critic={critic}
                found={new Set(marks.map((m) => m.id))}
                active={active}
                onActive={setPinned}
                onHover={onHover}
              />
            ))
          ) : (
            <p className="muted">{run.status === "running" ? "The panel is still running." : "This run left no record."}</p>
          )}
        </section>
      </div>

      {record && <Provenance record={record} runId={run.id} />}
    </main>
  );
}

/** The issue id in a URL hash; a hand-edited hash with a broken %-escape is read as typed. */
function issueFromHash(hash: string): string | null {
  const raw = hash.slice(1);
  try {
    return decodeURIComponent(raw) || null;
  } catch {
    return raw || null;
  }
}

function CriticSection(props: {
  critic: CriticRecord;
  found: Set<string>;
  active: string | null;
  onActive: (id: string | null) => void;
  onHover: (id: string | null) => void;
}) {
  const { critic, found, active, onActive, onHover } = props;
  const verdict = critic.verdict;
  const ownRating = critic.model_overall && verdict && critic.model_overall !== verdict.overall;
  return (
    <article className="critic" data-critic={critic.critic}>
      <header className="critic-head">
        <h2>
          <span className="critic-dot" />
          {CRITIC_TITLES[critic.critic]}
        </h2>
        <p className="critic-meta mono">
          {modelName(critic.model)} · {formatSeconds(critic.timing.seconds)}
          {verdict && (
            <>
              {" · "}
              <span className="critic-rating">{verdict.overall}</span>
              {ownRating && <span className="muted"> (the critic said {critic.model_overall})</span>}
            </>
          )}
        </p>
      </header>
      {!verdict ? (
        <p className="critic-error">No usable answer: {critic.error}</p>
      ) : (
        <>
          <p className="critic-summary">{verdict.summary}</p>
          <ol className="issues">
            {verdict.issues.map((issue, index) => {
              const id = `${critic.critic}-${index + 1}`;
              const capped = critic.capped.includes(index);
              return (
                <li
                  key={id}
                  id={`issue-${id}`}
                  className={`issue${active === id ? " is-active" : ""}`}
                  tabIndex={0}
                  onMouseEnter={() => onHover(id)}
                  onMouseLeave={() => onHover(null)}
                  onFocus={() => onActive(id)}
                  onBlur={() => onActive(null)}
                >
                  <p className="issue-head">
                    <span className={`tag mono${issue.severity === "fatal" ? " tag--fatal" : ""}`}>{issue.severity}</span>
                    <span className="issue-text">{issue.issue}</span>
                  </p>
                  <p className="issue-why">{issue.why_it_matters}</p>
                  <p className="issue-fix">
                    <span className="issue-fix-label">To improve:</span> {issue.how_to_improve}
                  </p>
                  {(capped || !found.has(id)) && (
                    <p className="issue-notes mono">
                      {capped && <span>lowered from fatal: the critic called it fixable</span>}
                      {!found.has(id) && <span>its quote is not word for word in the idea</span>}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </article>
  );
}

/** The run's generation settings in a line, e.g. "frontier lineup · reasoning low · ...". */
function settingsLine(record: PanelRecord): string {
  const s = record.settings;
  const parts: string[] = [];
  if (typeof s.lineup === "string") parts.push(`${s.lineup} lineup`);
  if (typeof s.reasoning === "string") parts.push(`reasoning ${s.reasoning}`);
  else if (typeof s.thinking === "boolean") parts.push(s.thinking ? "thinking on" : "thinking off");
  if (typeof s.max_tokens === "number") parts.push(`replies up to ${s.max_tokens} tokens`);
  if (typeof s.temperature === "number") parts.push(`temperature ${s.temperature}`);
  if (typeof s.seed === "number") parts.push(`seed ${s.seed}`);
  if (typeof record.critics_parallel === "boolean") {
    parts.push(record.critics_parallel ? "critics at the same time" : "critics one after another");
  }
  return parts.length ? parts.join(" · ") : "not recorded";
}

function Provenance({ record, runId }: { record: PanelRecord; runId: string }) {
  const models = [
    ...record.critics.map((c) => [CRITIC_TITLES[c.critic], c.model, c.served] as const),
    ["Combining step", record.combiner.model ?? "", record.combiner.served] as const,
  ];
  const usesOllama = models.some(([, model]) => model.startsWith("ollama/"));
  const rows: [string, string][] = [
    ["Run", runId],
    ["Inspect log", record.log_file],
    ["Code", `${record.git_commit ?? "unknown"}${record.git_dirty ? " with uncommitted changes" : ""}`],
    ["Prompts", record.prompts_sha256 ? `sha256 ${record.prompts_sha256.slice(0, 12)}` : "unknown"],
    ["Inspect AI", record.inspect_version ?? "unknown"],
    ["Ollama", record.ollama_version ?? (usesOllama ? "unknown (the version lookup failed)" : "not used")],
    ["Settings", settingsLine(record)],
  ];
  const s = record.scores;
  const checks = [
    `${s.quotes ?? 0} quotes, ${s.quotes_not_verbatim ?? 0} not word for word`,
    `${s.fatals_capped ?? 0} lowered from fatal`,
    `${s.combiner_flags ?? 0} combining-step flags`,
    s.statement_repaired ? "statement rewritten once" : "statement as first written",
  ];
  return (
    <footer className="provenance">
      <h2>Where this run came from</h2>
      <dl>
        {rows.map(([term, value]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd className="mono">{value}</dd>
          </div>
        ))}
        {models.map(([role, model, served]) => (
          <div key={role}>
            <dt>{role}</dt>
            <dd className="mono">
              {model}
              {served?.digest ? ` · ${served.digest.slice(0, 12)}` : ""}
              {served?.context_length ? ` · ${served.context_length} tokens` : ""}
            </dd>
          </div>
        ))}
        <div>
          <dt>Checks</dt>
          <dd className="mono">{checks.join(" · ")}</dd>
        </div>
      </dl>
    </footer>
  );
}
