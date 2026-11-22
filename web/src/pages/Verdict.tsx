import { useState } from "react";
import { Link } from "react-router";
import type { IssueRef, PanelPoint, PanelRecord } from "../api";
import { CRITIC_TITLES, SEVERITY_ORDER } from "../format";
import { useRevealGuard } from "../hooks";

const POINTS_SHOWN = 5;

/** The panel's statement, then the problems behind it. */
export function Verdict({ record, runId }: { record: PanelRecord; runId: string }) {
  const [all, setAll] = useState(false);
  const ref = useRevealGuard<HTMLElement>(all);
  const combined = record.combiner.combined;
  if (!combined) return null;
  const { lede, rest } = splitLede(combined.statement);
  const points = [...combined.points]
    .filter((p) => p.issues.length > 0)
    .sort(byWeight);
  const shown = all ? points : points.slice(0, POINTS_SHOWN);

  return (
    <section ref={ref} className="verdict" aria-labelledby="verdict-lede">
      <p id="verdict-lede" className="verdict-lede">
        {lede}
      </p>
      {rest.map((paragraph) => (
        <p key={paragraph.slice(0, 32)} className="verdict-rest">
          {paragraph}
        </p>
      ))}

      <ol className="points">
        {shown.map((point, i) => {
          const lead = leadIssue(point.issues);
          // The first five follow the statement in; the rest arrive when asked for.
          const delay = i < POINTS_SHOWN ? 1150 + i * 70 : (i - POINTS_SHOWN) * 40;
          return (
            <li key={i} className="point" style={{ animationDelay: `${delay}ms` }}>
              <Link className="point-link" to={`/runs/${runId}/details${lead ? `#${lead.id}` : ""}`}>
                <span className="point-critics" aria-hidden="true">
                  {point.critics.map((critic) => (
                    <span key={critic} className="critic-dot" data-critic={critic} />
                  ))}
                </span>
                <span className="point-text">
                  <span className="visually-hidden">Raised by {listCritics(point)}: </span>
                  {point.point}
                </span>
                <span className="point-tags mono">
                  {point.severity === "fatal" && <span className="tag tag--fatal">fatal</span>}
                  {point.severity_split && <span className="tag">critics split on severity</span>}
                </span>
              </Link>
            </li>
          );
        })}
      </ol>
      {!all && points.length > shown.length && (
        <p className="points-more">
          <button type="button" className="text-button" onClick={() => setAll(true)}>
            {points.length - shown.length} more {points.length - shown.length === 1 ? "point" : "points"}
          </button>
        </p>
      )}

      {combined.disagreements.length > 0 && (
        <div className="disagreements">
          <h2>Where the critics disagree</h2>
          {combined.disagreements.map((d, i) => (
            <div key={i} className="disagreement">
              <p className="disagreement-point">{d.point}</p>
              <ul>
                {d.positions.map((position) => (
                  <li key={position.critic} data-critic={position.critic}>
                    <span className="critic-dot" />
                    <span>
                      <strong>{CRITIC_TITLES[position.critic]}:</strong> {position.claim}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** The issue a point's link opens on the details page: its most severe, then its first. */
function leadIssue(issues: IssueRef[]): IssueRef | undefined {
  return [...issues].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity])[0];
}

function listCritics(point: PanelPoint): string {
  const names = point.critics.map((c) => CRITIC_TITLES[c]);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : (names[0] ?? "");
}

/** Fatal first, then problems more critics raised, then major before minor. */
function byWeight(a: PanelPoint, b: PanelPoint): number {
  if (a.severity === "fatal" || b.severity === "fatal") {
    const diff = SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
    if (diff) return diff;
  }
  if (b.critics.length !== a.critics.length) return b.critics.length - a.critics.length;
  return SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity];
}

// A full stop after one of these does not end a sentence: common abbreviations and
// dotted initialisms (U.S., U.K.). A lone capital does end one ("Series A.").
const ABBREVIATION = /(?:\b(?:e\.g|i\.e|vs|etc|approx|ca|cf|incl|esp|Mr|Mrs|Ms|Dr|Inc|Ltd|Co|St)|\b(?:[A-Z]\.)+[A-Z])$/;

/** The statement's first sentence as the lede; everything after it as reading text. */
export function splitLede(statement: string): { lede: string; rest: string[] } {
  const paragraphs = statement.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  const first = paragraphs[0] ?? "";
  // A sentence ends at . ! or ?, possibly followed by a closing quote or bracket,
  // then white space and more text.
  const ends = /[.!?]['"’”)\]]*(?=\s+\S)/g;
  for (const match of first.matchAll(ends)) {
    const at = match.index ?? 0;
    if (first[at] === "." && ABBREVIATION.test(first.slice(0, at))) continue;
    const cut = at + match[0].length;
    return { lede: first.slice(0, cut), rest: [first.slice(cut).trim(), ...paragraphs.slice(1)] };
  }
  return { lede: first, rest: paragraphs.slice(1) };
}
