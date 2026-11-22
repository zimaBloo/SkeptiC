/** A faint latitude and longitude grid behind the panel, curved like a globe seen from
 * a little above its equator, as under the Wind Map and earth.nullschool: the map the
 * streams move across. Static, hairline, and quiet enough never to compete with them. */

const PARALLELS = [-4, -3, -2, -1, 0, 1, 2, 3, 4];
const MERIDIANS = [-6, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6];

function parallel(i: number): string {
  const y = 500 + i * 112;
  // Seen from above, every parallel bows downwards, the northern ones more.
  const bow = 34 - i * 5;
  return `M -40 ${y} Q 500 ${y + bow * 2} 1040 ${y}`;
}

function meridian(k: number): string {
  // Meridians gather towards a pole above the stage and swell past the middle.
  const top = 500 + k * 46;
  const swell = 500 + k * 118;
  const bottom = 500 + k * 96;
  return `M ${top} -40 C ${swell} 300 ${swell} 700 ${bottom} 1040`;
}

export function Graticule() {
  return (
    <svg className="graticule" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
      {PARALLELS.map((i) => (
        <path key={`p${i}`} d={parallel(i)} className={i === 0 ? "is-main" : undefined} />
      ))}
      {MERIDIANS.map((k) => (
        <path key={`m${k}`} d={meridian(k)} className={k === 0 ? "is-main" : undefined} />
      ))}
    </svg>
  );
}
