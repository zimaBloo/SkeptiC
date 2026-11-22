import { Fragment, type ReactNode } from "react";
import type { CriticName } from "./api";

export interface Mark {
  id: string;
  critic: CriticName;
  start: number;
  end: number;
}

interface Props {
  text: string;
  marks: Mark[];
  active: string | null;
  onMarkEnter?: (id: string | null) => void;
  /** A heading with this text is left out, because the page already shows it. */
  title?: string;
}

/** An idea's Markdown (headings, bold labels, paragraphs) with critics' quotes marked. */
export function IdeaText({ text, marks, active, onMarkEnter, title }: Props) {
  const blocks: { start: number; end: number }[] = [];
  const pattern = /\n\s*\n/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    blocks.push({ start: cursor, end: match.index ?? cursor });
    cursor = (match.index ?? cursor) + match[0].length;
  }
  blocks.push({ start: cursor, end: text.length });

  return (
    <div className="idea-text">
      {blocks.map(({ start, end }) => {
        const raw = text.slice(start, end);
        if (!raw.trim()) return null;
        const heading = /^#{1,6}[ \t]+/.exec(raw);
        if (heading) {
          // A heading is its own line; text straight under it is a paragraph.
          const newline = raw.indexOf("\n");
          const headEnd = newline >= 0 ? start + newline : end;
          const isTitle = title !== undefined && text.slice(start + heading[0].length, headEnd).trim() === title.trim();
          return (
            <Fragment key={start}>
              {!isTitle && (
                <h2 className="idea-heading">
                  {renderInline(text, start + heading[0].length, headEnd, marks, active, onMarkEnter)}
                </h2>
              )}
              {headEnd < end && text.slice(headEnd + 1, end).trim() && (
                <p>{renderInline(text, headEnd + 1, end, marks, active, onMarkEnter)}</p>
              )}
            </Fragment>
          );
        }
        return <p key={start}>{renderInline(text, start, end, marks, active, onMarkEnter)}</p>;
      })}
    </div>
  );
}

/** Text between start and end, with **bold** segments and quote marks applied. */
function renderInline(
  text: string,
  start: number,
  end: number,
  marks: Mark[],
  active: string | null,
  onMarkEnter?: (id: string | null) => void,
): ReactNode[] {
  // Cut points: every boundary of a bold run or a mark inside this block.
  const bold: [number, number][] = [];
  const boldPattern = /\*\*([^*]+)\*\*/g;
  const block = text.slice(start, end);
  for (const match of block.matchAll(boldPattern)) {
    const s = start + (match.index ?? 0);
    bold.push([s, s + match[0].length]);
  }
  // Ideas are often hard-wrapped, so a newline is a space, except before a list
  // item ("- ", "* ", "• ", "1. "), where it stays a line break.
  const breaks = new Set<number>();
  for (const match of block.matchAll(/\n(?=[ \t]*(?:[-*•]|\d+[.)])[ \t])/g)) {
    breaks.add(start + (match.index ?? 0));
  }
  const cuts = new Set([start, end]);
  for (const [s, e] of bold) cuts.add(s).add(e);
  for (const b of breaks) cuts.add(b).add(b + 1);
  for (const mark of marks) {
    if (mark.end <= start || mark.start >= end) continue;
    cuts.add(Math.max(start, mark.start)).add(Math.min(end, mark.end));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const nodes: ReactNode[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const s = points[i] as number;
    const e = points[i + 1] as number;
    const piece = text.slice(s, e).replace(/\*\*/g, "");
    if (!piece) continue;
    const isBold = bold.some(([bs, be]) => s >= bs && e <= be);
    const covering = marks.filter((m) => m.start <= s && m.end >= e);
    let node: ReactNode = breaks.has(s) ? <br /> : isBold ? <strong>{piece}</strong> : piece;
    if (covering.length) {
      const lead = covering.find((m) => m.id === active) ?? covering[0];
      if (lead) {
        node = (
          <mark
            data-critic={lead.critic}
            data-mark={lead.id}
            className={covering.some((m) => m.id === active) ? "is-active" : undefined}
            onMouseEnter={() => onMarkEnter?.(lead.id)}
            onMouseLeave={() => onMarkEnter?.(null)}
          >
            {node}
          </mark>
        );
      }
    }
    nodes.push(<Fragment key={s}>{node}</Fragment>);
  }
  return nodes;
}
