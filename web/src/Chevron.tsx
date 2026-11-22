/** The mark on a disclosure or a picker: points down while closed and turns over when
 * open (the turn is in CSS). */
export function Chevron() {
  return (
    <svg className="chevron" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
      <path d="M2.75 4.5L6 7.75L9.25 4.5" />
    </svg>
  );
}
