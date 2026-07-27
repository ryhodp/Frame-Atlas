const PHI = 1.6180339887;

export const OVERLAY_MODES = ['off', 'thirds', 'phi', 'spiral', 'diagonal', 'cross'];
export const OVERLAY_LABELS = {
  off: 'Off',
  thirds: 'Rule of Thirds',
  phi: 'Golden Ratio',
  spiral: 'Golden Spiral',
  diagonal: 'Diagonal Method',
  cross: 'Center Cross',
};
// Only these two have a directional "which corner does it lead from" — the
// rotate control is hidden for the symmetric grids (thirds/phi/cross).
export const OVERLAY_ROTATABLE = { spiral: true, diagonal: true };

// ── Geometry helpers (all in the same px space as width/height) ────────────

function inscribedGoldenRect(W, H) {
  let gw, gh;
  if (W / H > PHI) { gh = H; gw = H * PHI; }
  else { gw = W; gh = W / PHI; }
  return { x: (W - gw) / 2, y: (H - gh) / 2, w: gw, h: gh };
}

// Peel the largest square off the rect's long side, rotating which side is
// cut each time. Because the rect is a true golden rectangle, every
// remainder is itself golden (rotated 90°), so this never degenerates
// regardless of the photo's own aspect ratio.
function peelSquares(rect, startSide, dir) {
  const cycle = ['left', 'top', 'right', 'bottom'];
  const idx = cycle.indexOf(startSide);
  let r = { ...rect };
  const squares = [];
  for (let i = 0; i < 10; i++) {
    if (r.w < 0.6 || r.h < 0.6) break;
    const side = cycle[((idx + dir * i) % 4 + 4) % 4];
    let sq, rem, s;
    if (side === 'left' || side === 'right') {
      s = r.h;
      if (s >= r.w - 0.01) { squares.push({ ...r, cut: side }); break; }
      if (side === 'left') { sq = { x: r.x, y: r.y, w: s, h: r.h }; rem = { x: r.x + s, y: r.y, w: r.w - s, h: r.h }; }
      else { sq = { x: r.x + r.w - s, y: r.y, w: s, h: r.h }; rem = { x: r.x, y: r.y, w: r.w - s, h: r.h }; }
    } else {
      s = r.w;
      if (s >= r.h - 0.01) { squares.push({ ...r, cut: side }); break; }
      if (side === 'top') { sq = { x: r.x, y: r.y, w: r.w, h: s }; rem = { x: r.x, y: r.y + s, w: r.w, h: r.h - s }; }
      else { sq = { x: r.x, y: r.y + r.h - s, w: r.w, h: s }; rem = { x: r.x, y: r.y, w: r.w, h: r.h - s }; }
    }
    sq.cut = side;
    squares.push(sq);
    r = rem;
  }
  return squares;
}

function cornersOf(sq) {
  return { TL: [sq.x, sq.y], TR: [sq.x + sq.w, sq.y], BL: [sq.x, sq.y + sq.h], BR: [sq.x + sq.w, sq.y + sq.h] };
}
const DIAG_OF = { TL: 'BR', BR: 'TL', TR: 'BL', BL: 'TR' };
// The pair of corners lying on the edge shared with whatever square comes next.
const NEAR = { left: ['TR', 'BR'], right: ['TL', 'BL'], top: ['BL', 'BR'], bottom: ['TL', 'TR'] };

function samePt(a, b) { return Math.abs(a[0] - b[0]) < 0.05 && Math.abs(a[1] - b[1]) < 0.05; }

// One continuous SVG path — a quarter-circle arc inscribed in each square,
// connected corner-to-corner so the curve spirals smoothly inward.
function buildSpiralPath(squares) {
  const cs = squares.map(cornersOf);
  const n = squares.length;
  if (n === 0) return '';
  const exitName = new Array(n).fill(null);
  for (let i = 0; i < n - 1; i++) {
    const near = NEAR[squares[i].cut];
    for (const name of near) {
      if (Object.values(cs[i + 1]).some(p => samePt(p, cs[i][name]))) { exitName[i] = name; break; }
    }
  }
  if (exitName[n - 1] == null) exitName[n - 1] = NEAR[squares[n - 1].cut][0];

  let d = '';
  let entryPt = null;
  for (let i = 0; i < n; i++) {
    const near = NEAR[squares[i].cut];
    const exit = exitName[i];
    const entryName = DIAG_OF[exit];
    const center = near.find(nm => nm !== exit);
    const p0 = entryPt || cs[i][entryName];
    const p1 = cs[i][exit];
    const c = cs[i][center];
    const r = Math.min(squares[i].w, squares[i].h);
    const a0 = Math.atan2(p0[1] - c[1], p0[0] - c[0]);
    const a1 = Math.atan2(p1[1] - c[1], p1[0] - c[0]);
    let diff = a1 - a0;
    while (diff <= -Math.PI) diff += 2 * Math.PI;
    while (diff > Math.PI) diff -= 2 * Math.PI;
    const sweep = diff > 0 ? 1 : 0;
    if (i === 0) d += `M ${p0[0]} ${p0[1]} `;
    d += `A ${r} ${r} 0 0 ${sweep} ${p1[0]} ${p1[1]} `;
    entryPt = p1;
  }
  return d;
}

// ── Line/arc rendering (dark halo + gold stroke so it reads on any image) ──

function Line({ x1, y1, x2, y2 }) {
  return (
    <>
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="black" strokeOpacity={0.4} strokeWidth={3} />
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#D9A441" strokeOpacity={0.9} strokeWidth={1.4} />
    </>
  );
}

function ArcPath({ d }) {
  return (
    <>
      <path d={d} fill="none" stroke="black" strokeOpacity={0.35} strokeWidth={3.5} />
      <path d={d} fill="none" stroke="#D9A441" strokeOpacity={0.95} strokeWidth={1.7} />
    </>
  );
}

function ThirdsLines({ W, H }) {
  return (
    <>
      <Line x1={W / 3} y1={0} x2={W / 3} y2={H} />
      <Line x1={2 * W / 3} y1={0} x2={2 * W / 3} y2={H} />
      <Line x1={0} y1={H / 3} x2={W} y2={H / 3} />
      <Line x1={0} y1={2 * H / 3} x2={W} y2={2 * H / 3} />
    </>
  );
}

function PhiLines({ W, H }) {
  const a = 0.382, b = 0.618;
  return (
    <>
      <Line x1={W * a} y1={0} x2={W * a} y2={H} />
      <Line x1={W * b} y1={0} x2={W * b} y2={H} />
      <Line x1={0} y1={H * a} x2={W} y2={H * a} />
      <Line x1={0} y1={H * b} x2={W} y2={H * b} />
    </>
  );
}

function CrossLines({ W, H }) {
  return (
    <>
      <Line x1={W / 2} y1={0} x2={W / 2} y2={H} />
      <Line x1={0} y1={H / 2} x2={W} y2={H / 2} />
    </>
  );
}

function SpiralLines({ W, H, orientation }) {
  const golden = inscribedGoldenRect(W, H);
  const wide = golden.w >= golden.h;
  const configs = wide
    ? [['left', 1], ['left', -1], ['right', 1], ['right', -1]]
    : [['top', 1], ['top', -1], ['bottom', 1], ['bottom', -1]];
  const [startSide, dir] = configs[((orientation % 4) + 4) % 4];
  const squares = peelSquares(golden, startSide, dir);
  return (
    <>
      {squares.map((sq, i) => (
        <rect key={i} x={sq.x} y={sq.y} width={sq.w} height={sq.h}
          fill="none" stroke="#D9A441" strokeOpacity={0.22} strokeWidth={1} />
      ))}
      <ArcPath d={buildSpiralPath(squares)} />
    </>
  );
}

function DiagonalLines({ W, H, orientation }) {
  const c = { tl: [0, 0], tr: [W, 0], bl: [0, H], br: [W, H] };
  let mainA, mainB, apex;
  const o = ((orientation % 4) + 4) % 4;
  if (o === 0) { mainA = 'tl'; mainB = 'br'; apex = 'tr'; }
  else if (o === 1) { mainA = 'tl'; mainB = 'br'; apex = 'bl'; }
  else if (o === 2) { mainA = 'tr'; mainB = 'bl'; apex = 'tl'; }
  else { mainA = 'tr'; mainB = 'bl'; apex = 'br'; }
  const [x1, y1] = c[mainA], [x2, y2] = c[mainB];
  const [px, py] = c[apex];
  const dx = x2 - x1, dy = y2 - y1;
  const t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy);
  const fx = x1 + t * dx, fy = y1 + t * dy;
  return (
    <>
      <Line x1={x1} y1={y1} x2={x2} y2={y2} />
      <Line x1={px} y1={py} x2={fx} y2={fy} />
    </>
  );
}

export default function CompositionOverlay({ mode, orientation, width, height }) {
  if (mode === 'off' || !width || !height) return null;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
    >
      {mode === 'thirds' && <ThirdsLines W={width} H={height} />}
      {mode === 'phi' && <PhiLines W={width} H={height} />}
      {mode === 'cross' && <CrossLines W={width} H={height} />}
      {mode === 'spiral' && <SpiralLines W={width} H={height} orientation={orientation} />}
      {mode === 'diagonal' && <DiagonalLines W={width} H={height} orientation={orientation} />}
    </svg>
  );
}
