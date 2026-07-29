// perspective.js — Frame Atlas V32
//
// Four-point perspective correction for crop mode: the maths for turning a
// tilted quadrilateral (a monitor, a poster, a projected image shot from an
// angle) back into a straight rectangle, plus the live preview that shows
// Ryan what the server is about to produce.
//
// THIS IS NOT PART OF THE DETECTION ENGINE. cropDetectV2.js decides where an
// axis-aligned rectangle goes and is untouched by any of this. Nothing here
// runs unless perspective mode is switched on, and perspective mode is never
// on by default.
//
// The corner points live in the image's natural (EXIF-upright) pixel space,
// exactly like cropDetectV2's boxes, and are converted to percentages only at
// the moment they're sent to the server — so the same four numbers mean the
// same selection whether the browser was looking at a 600px preview or a
// 6000px original.
//
// Corner order everywhere: [top-left, top-right, bottom-right, bottom-left].

export const CORNER_ORDER = ['tl', 'tr', 'br', 'bl'];

// Longest edge of the on-screen preview, in pixels. The preview is resampled
// in JS one pixel at a time, so this is a speed budget: it is redrawn on every
// pointermove while a corner is being dragged, and ~480px keeps that under a
// frame. The real output is computed server-side at full resolution.
const PREVIEW_MAX_EDGE = 480;

// Working copy of the source image the preview samples from. Full-resolution
// ImageData for a 6000x4000 photo is ~96MB, and we'd be re-reading it on every
// mouse move; a 1400px copy carries far more detail than a 480px preview can
// show and costs about 8MB.
const WORK_MAX_EDGE = 1400;

// One working copy per <img>, thrown away with the element.
const workCache = new WeakMap();

function getWorkPixels(img) {
  let work = workCache.get(img);
  if (work) return work;

  const nw = img.naturalWidth, nh = img.naturalHeight;
  const scale = Math.min(1, WORK_MAX_EDGE / Math.max(nw, nh));
  const w = Math.max(1, Math.round(nw * scale));
  const h = Math.max(1, Math.round(nh * scale));

  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  const imageData = ctx.getImageData(0, 0, w, h);

  work = {
    w, h,
    // scale maps natural pixels -> working-copy pixels
    scale: w / nw,
    scaleY: h / nh,
    // Read as 32-bit words so one array index copies a whole pixel.
    px: new Uint32Array(imageData.data.buffer),
  };
  workCache.set(img, work);
  return work;
}

/**
 * Solve the 8 coefficients of the homography that maps each point in
 * `fromPts` onto the matching point in `toPts`.
 *
 *   X = (a*x + b*y + c) / (g*x + h*y + 1)
 *   Y = (d*x + e*y + f) / (g*x + h*y + 1)
 *
 * Multiplying out gives two linear equations per corner; four corners make an
 * 8x8 system. This is the same solve app.py does server-side (there in plain
 * Python, here in plain JS) so the preview and the real output agree.
 *
 * Returns [a,b,c,d,e,f,g,h], or null if the four points are degenerate.
 */
export function solveHomography(fromPts, toPts) {
  const m = [];
  const rhs = [];
  for (let i = 0; i < 4; i++) {
    const [x, y] = fromPts[i];
    const [tx, ty] = toPts[i];
    m.push([x, y, 1, 0, 0, 0, -tx * x, -tx * y]); rhs.push(tx);
    m.push([0, 0, 0, x, y, 1, -ty * x, -ty * y]); rhs.push(ty);
  }

  const n = 8;
  const a = m.map((row, i) => [...row, rhs[i]]);

  for (let col = 0; col < n; col++) {
    // Partial pivoting. The handles are seeded from a rectangle, so exactly
    // axis-aligned corners — which put a zero on the diagonal — are the
    // COMMON case here, not an exotic one.
    let pivot = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(a[r][col]) > Math.abs(a[pivot][col])) pivot = r;
    }
    if (Math.abs(a[pivot][col]) < 1e-12) return null;
    [a[col], a[pivot]] = [a[pivot], a[col]];

    const inv = 1 / a[col][col];
    for (let r = col + 1; r < n; r++) {
      const factor = a[r][col] * inv;
      if (factor === 0) continue;
      for (let k = col; k <= n; k++) a[r][k] -= factor * a[col][k];
    }
  }

  const out = new Array(n).fill(0);
  for (let row = n - 1; row >= 0; row--) {
    let acc = a[row][n];
    for (let k = row + 1; k < n; k++) acc -= a[row][k] * out[k];
    out[row] = acc / a[row][row];
  }
  return out;
}

const dist = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);

/**
 * Pixel size of the de-skewed rectangle: each side is the AVERAGE of the two
 * OPPOSITE edges of the quad. Identical rule to perspective_output_size() in
 * app.py — sizing off a single edge would bake the perspective back in
 * (the near edge of an angled monitor is longer than the far one, so it would
 * stretch the whole result).
 */
export function quadOutputSize(corners) {
  const [tl, tr, br, bl] = corners;
  return {
    w: Math.round((dist(tl, tr) + dist(bl, br)) / 2),
    h: Math.round((dist(tl, bl) + dist(tr, br)) / 2),
  };
}

/** Four corners at the untouched image bounds — nothing to correct. */
export function isIdentityQuad(corners, img) {
  if (!corners || !img) return true;
  const W = img.naturalWidth, H = img.naturalHeight;
  const want = [[0, 0], [W, 0], [W, H], [0, H]];
  // Half a percent of each dimension, matching the server's tolerance.
  const tx = W * 0.005, ty = H * 0.005;
  return corners.every((p, i) =>
    Math.abs(p.x - want[i][0]) <= tx && Math.abs(p.y - want[i][1]) <= ty);
}

/**
 * Is this a simple (non-crossing) convex quadrilateral, in the given order?
 *
 * Same test the server runs, for the same reason: a rectangle photographed
 * from ANY angle is always convex, so a crossed ("bow-tie") or dented outline
 * is not a perspective view of anything. The server refuses these outright —
 * running the check here too means Ryan sees the outline turn red while he's
 * dragging instead of getting a rejection after he hits Apply.
 */
export function isConvexQuad(corners) {
  if (!corners || corners.length !== 4) return false;
  let sign = 0;
  for (let i = 0; i < 4; i++) {
    const a = corners[i], b = corners[(i + 1) % 4], c = corners[(i + 2) % 4];
    const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
    if (cross === 0) return false;
    const s = cross > 0 ? 1 : -1;
    if (sign === 0) sign = s;
    else if (s !== sign) return false;
  }
  return true;
}

/** Seed four corners from an axis-aligned box (or the whole image). */
export function cornersFromBox(box, img) {
  const b = box || { x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight };
  return [
    { x: b.x,       y: b.y },
    { x: b.x + b.w, y: b.y },
    { x: b.x + b.w, y: b.y + b.h },
    { x: b.x,       y: b.y + b.h },
  ];
}

/** Corners in natural pixels -> the percentages the API expects. */
export function cornersToPercent(corners, img) {
  const W = img.naturalWidth, H = img.naturalHeight;
  // Clamped to 0-100 because the server REJECTS out-of-range corners rather
  // than clamping them (clamping would silently change the shape). The drag
  // handler already keeps them in bounds; this only guards floating-point
  // dust like 100.00000000000001.
  return corners.map(p => ({
    x: Math.min(100, Math.max(0, (p.x / W) * 100)),
    y: Math.min(100, Math.max(0, (p.y / H) * 100)),
  }));
}

/**
 * Draw the de-skewed result into `canvas`, so the CROPPED RESULT panel shows
 * the actual perspective correction rather than a rectangle that ignores it.
 *
 * Nearest-neighbour on purpose: this is a preview redrawn on every
 * pointermove. The server uses bicubic on the full-resolution original.
 */
export function drawPerspectivePreview(canvas, img, corners) {
  const out = quadOutputSize(corners);
  if (!(out.w > 0 && out.h > 0)) return false;

  const fit = Math.min(1, PREVIEW_MAX_EDGE / Math.max(out.w, out.h));
  const dw = Math.max(1, Math.round(out.w * fit));
  const dh = Math.max(1, Math.round(out.h * fit));

  const work = getWorkPixels(img);

  // Same direction as Pillow: walk the OUTPUT pixels and ask where each one
  // came from. So the homography maps the destination rectangle onto the quad,
  // pre-scaled into working-copy coordinates.
  const dst = [[0, 0], [dw, 0], [dw, dh], [0, dh]];
  const src = corners.map(p => [p.x * work.scale, p.y * work.scaleY]);
  const co = solveHomography(dst, src);
  if (!co) return false;
  const [a, b, c, d, e, f, g, h] = co;

  canvas.width = dw;
  canvas.height = dh;
  const ctx = canvas.getContext('2d');
  const outData = ctx.createImageData(dw, dh);
  const outPx = new Uint32Array(outData.data.buffer);

  for (let y = 0; y < dh; y++) {
    for (let x = 0; x < dw; x++) {
      const w = g * x + h * y + 1;
      const sx = (a * x + b * y + c) / w;
      const sy = (d * x + e * y + f) / w;
      const ix = sx | 0, iy = sy | 0;
      // Outside the source reads as transparent. The quad is constrained to
      // the image bounds, so in practice this only catches the last row/column
      // of rounding at the very edge.
      outPx[y * dw + x] =
        (ix >= 0 && iy >= 0 && ix < work.w && iy < work.h)
          ? work.px[iy * work.w + ix]
          : 0;
    }
  }

  ctx.putImageData(outData, 0, 0);
  return true;
}
