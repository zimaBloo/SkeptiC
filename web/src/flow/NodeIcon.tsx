import type { ReactNode } from "react";
import type { NodeId } from "./geometry";

/** Each node's mark: a fine line glyph in the manner of a map's symbols, one stroke
 * weight and round ends throughout, drawn on a 24 px grid. Each says what its step
 * does: the idea a guiding star where everything starts; market a supply and a demand
 * curve meeting at a price; technical feasibility a drafting set square; customer
 * value a person; risk and regulation a balance; the verdict, the panel's written
 * statement, a quotation mark. */
export function NodeIcon({ id }: { id: NodeId }) {
  return (
    <svg className="node-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {GLYPHS[id]}
    </svg>
  );
}

const GLYPHS: Record<NodeId, ReactNode> = {
  idea: (
    <>
      <path d="M12 2.8C12.7 8.2 15.8 11.3 21.2 12C15.8 12.7 12.7 15.8 12 21.2C11.3 15.8 8.2 12.7 2.8 12C8.2 11.3 11.3 8.2 12 2.8Z" />
      <circle cx="12" cy="12" r="1.15" className="is-solid" />
    </>
  ),
  market: (
    <>
      <path d="M4 3.6V20.4H20.6" className="is-faint" />
      <path d="M6.6 6.2C9.8 6.8 12.4 9.4 14.2 12.4C15.7 14.9 17.6 16.8 20.2 17.6" />
      <path d="M6.6 17.6C9.4 16.8 11.4 14.7 13 12.3C14.6 9.8 16.8 7.2 20.2 6.2" />
      <circle cx="13.6" cy="11.4" r="1.75" className="is-solid" />
    </>
  ),
  technical: (
    <>
      <path d="M4.5 3.8V20H20.7L4.5 3.8Z" />
      <path d="M8.2 12.6V16.3H11.9L8.2 12.6Z" />
      <path d="M4.5 7.6H6.4M4.5 11.2H6.4M8.3 20V18.1M11.9 20V18.1M15.5 20V18.1" className="is-faint" />
    </>
  ),
  customer: (
    <>
      <circle cx="12" cy="8.2" r="3.6" />
      <path d="M4.8 20.4C5.6 16.1 8.4 13.6 12 13.6C15.6 13.6 18.4 16.1 19.2 20.4" />
    </>
  ),
  risk: (
    <>
      <path d="M12 4.2V19.6M8.4 19.6H15.6" />
      <path d="M4.6 7.4C7.2 6.4 9.6 6 12 6C14.4 6 16.8 6.4 19.4 7.4" />
      <path d="M4.6 7.4L2.8 12.6M4.6 7.4L6.4 12.6M19.4 7.4L17.6 12.6M19.4 7.4L21.2 12.6" className="is-faint" />
      <path d="M2.6 12.6C2.9 14.4 6.3 14.4 6.6 12.6ZM17.4 12.6C17.7 14.4 21.1 14.4 21.4 12.6Z" />
      <circle cx="12" cy="4.2" r="1.05" className="is-solid" />
    </>
  ),
  combiner: (
    <>
      <circle cx="7.9" cy="14.6" r="2.7" className="is-solid" />
      <path d="M5.3 14.4C5.2 10.4 6.8 7.6 10.2 6" />
      <circle cx="16.1" cy="14.6" r="2.7" className="is-solid" />
      <path d="M13.5 14.4C13.4 10.4 15 7.6 18.4 6" />
    </>
  ),
};
