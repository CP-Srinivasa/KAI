// Dependency-free SVG sparkline. Replaces a recharts <LineChart> that was the
// ONLY recharts usage in the whole app — pulling its ~510 kB vendor chunk into
// the eager Dashboard first-paint just for a 92×40 px spark. This renders the
// same shape (auto-domain with ±5 padding, monotone-ish polyline, no dots) in a
// few bytes of SVG.

type Point = { x: number; y: number };

const VIEW_W = 92;
const VIEW_H = 40;
const PAD_TOP = 4;
const PAD_BOTTOM = 2;
// recharts used YAxis domain [dataMin - 5, dataMax + 5]; keep the same padding
// so the visual amplitude is unchanged.
const DOMAIN_PAD = 5;

// Pure: maps spark points to an SVG polyline `points` string, or null when
// there is nothing meaningful to draw (<2 points).
export function sparkPolyline(data: Point[]): string | null {
  if (!data || data.length < 2) return null;
  const ys = data.map((d) => d.y);
  const yMin = Math.min(...ys) - DOMAIN_PAD;
  const yMax = Math.max(...ys) + DOMAIN_PAD;
  const span = yMax - yMin;
  const drawTop = PAD_TOP;
  const drawBottom = VIEW_H - PAD_BOTTOM;
  const drawH = drawBottom - drawTop;
  const n = data.length;
  return data
    .map((d, i) => {
      const px = (i / (n - 1)) * VIEW_W;
      const frac = span === 0 ? 0.5 : (d.y - yMin) / span;
      const py = drawBottom - frac * drawH;
      return `${px.toFixed(2)},${py.toFixed(2)}`;
    })
    .join(" ");
}

export function Sparkline({ data, stroke }: { data: Point[]; stroke: string }) {
  const points = sparkPolyline(data);
  if (points === null) return null;
  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      width="100%"
      height="100%"
      aria-hidden
    >
      <polyline
        points={points}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
