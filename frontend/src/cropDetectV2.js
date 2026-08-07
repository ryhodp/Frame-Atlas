// cropDetectV2.js — Frame Atlas, candidate replacement for cropDetect.js's
// detection core.
//
// WHY THIS EXISTS
// The v34 engine scores 2/14 on Ryan's "Test Photos/To Crop" set — it both
// over-crops (cutting through faces and skies) and under-crops (leaving the
// Instagram like/comment row in) on the same images. Its failure mode is
// structural, not a bad constant: it decides "is this line chrome?" from
// BRIGHTNESS (near-black or near-white) plus a one-sided trimmed std. That
// breaks in two directions at once:
//
//   · Dark artwork on a black IG background is dark everywhere, so brightness
//     can't find the seam — and low-texture dark photo columns get mistaken
//     for pillarbox, which is how IMG_1068 lost its left and right thirds.
//   · A one-sided trim (drop the brightest 12%) protects a BLACK bar carrying
//     white glyphs but not a WHITE bar carrying dark ones — nine scrollbar
//     pixels were enough to keep a dead-flat 255 border from being stripped.
//
// WHAT THIS DOES INSTEAD
// One statistic, applied uniformly: the MEDIAN ABSOLUTE DEVIATION of each
// line's luminance. MAD asks "how far is a typical pixel from the middle
// pixel?", so it is unmoved by a minority of outliers at EITHER end. That
// single property is what makes chrome separable:
//
//   · A letterbox bar, a white mat, or the flat black gutter around an IG
//     post reads MAD 0 — dead flat.
//   · An icon row (heart/comment/share) is mostly flat background with a few
//     glyph pixels, so it ALSO reads MAD 0 and gets peeled with the rest of
//     the chrome. This is the case brightness-plus-std kept leaving behind.
//   · Real photo content — even a very dark, low-contrast frame — carries
//     grain and gradient across the whole line, so MAD is >= 1 and usually
//     much higher. Measured on the test set: dark grass/sky edges that the
//     old engine cropped away as "flat" score MAD 5-10.
//
// Brightness is never consulted. Chrome is whatever is FLAT, whatever colour
// it happens to be, which is why one code path now covers black bars, white
// mats, grey app backgrounds and IG chrome alike.
//
// DELIBERATELY CONSERVATIVE
// Peeling stops at the first non-flat line — no gap-bridging, no "confirm N
// lines of content" lookahead. A subtitle burned into a letterbox bar will
// therefore stop the scan early and leave some bar behind. That is the
// intended trade: an under-crop is one Tighten press away from correct, while
// an over-crop overwrites the Drive file with no undo (see crop_image() in
// app.py) and permanently destroys picture. When this engine is unsure it
// keeps pixels.
//
// Coordinates in and out are the image's natural (EXIF-upright) pixel space.

// iOS Safari refuses canvases over ~16.7M pixels; stay under it. Same cap and
// same rationale as cropDetect.js.
const MAX_CANVAS_PIXELS = 16000000;

// A line counts as chrome at MAD <= FLAT_MAD_BASE. Zero means "at least half
// the pixels are identical to the median" — strictly dead flat.
//
// Swept against the 14-image set (cropsweep.html): 0 is right and anything
// looser over-crops. At MAD <= 1 the left edge of IMG_1068 and IMG_1081 —
// genuine dark grass and sky, which the old engine also ate — scores exactly
// 1 and gets peeled, taking 60px and 149px of picture with it. Real bars did
// not need the slack: every letterbox, mat and IG gutter in the set, JPEG and
// PNG alike, is dead flat at 0. Compression ringing sits ON the seam, not
// across a whole line, and MAD ignores it.
//
// The threshold only ever loosens from here, via `level` (Redetect).
const FLAT_MAD_BASE = 0;

// Never peel more than this fraction off any single edge. A real bar past
// ~48% would mean the "photo" is a minority of its own frame; far more likely
// the image is genuinely flat (a solid-colour graphic) and peeling would eat
// it. Also what stops a blank image collapsing to nothing.
const MAX_EDGE_PEEL = 0.48;

let _offscreen = null;
function getOffscreen() {
  if (!_offscreen) _offscreen = document.createElement('canvas');
  return _offscreen;
}

// Draw the image into the offscreen canvas, downscaling only past the pixel
// cap, and flatten to one luminance byte per pixel. Returns the scale used so
// boxes can be mapped back to natural coordinates.
function rasterizeLuma(img) {
  const natW = img.naturalWidth, natH = img.naturalHeight;
  const scale = Math.min(1, Math.sqrt(MAX_CANVAS_PIXELS / (natW * natH)));
  const W = Math.max(1, Math.round(natW * scale));
  const H = Math.max(1, Math.round(natH * scale));
  const canvas = getOffscreen();
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, W, H);
  const data = ctx.getImageData(0, 0, W, H).data;
  const lum = new Uint8Array(W * H);
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    lum[p] = (0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]) | 0;
  }
  return { lum, W, H, scale };
}

// Median absolute deviation of `n` luminance bytes, via two 256-bin counting
// passes. O(n) with no sort and no allocation per call — this runs once per
// row and once per column of a multi-megapixel image, so the constant matters.
const _hist = new Int32Array(256);
const _hist2 = new Int32Array(256);
function madOf(buf, n) {
  _hist.fill(0);
  for (let i = 0; i < n; i++) _hist[buf[i]]++;
  const half = n >> 1;
  let c = 0, med = 0;
  for (let v = 0; v < 256; v++) { c += _hist[v]; if (c > half) { med = v; break; } }
  _hist2.fill(0);
  for (let i = 0; i < n; i++) _hist2[Math.abs(buf[i] - med) | 0]++;
  c = 0;
  for (let v = 0; v < 256; v++) { c += _hist2[v]; if (c > half) return v; }
  return 0;
}

// Peel flat lines inward from both ends of one axis.
//
// `madAt(i)` measures line i ACROSS THE CURRENT OPPOSITE-AXIS WINDOW, which is
// what makes the multi-pass call order below matter: measuring a column over
// the full height includes the letterbox rows, and a column that is black in
// the letterbox and merely dark in the picture can average out to looking
// flat. Restricting to the content rows first removes that contamination.
function peelAxis(size, madAt, flatMad) {
  const limit = Math.floor(size * MAX_EDGE_PEEL);
  let start = 0;
  while (start < limit && madAt(start) <= flatMad) start++;
  let end = size;
  while (end > size - limit && end > start + 1 && madAt(end - 1) <= flatMad) end--;
  return [start, end];
}

// ── PUBLIC: detectCrop ────────────────────────────────────────────────────────
// Same contract as cropDetect.js's detectCrop: returns { box } in natural
// pixel coordinates, or null when there is nothing worth cropping (the
// caller falls back to a full-image box).
//
// (A per-edge "confidence" score used to ride along here too, but it was
// removed: it measured local contrast at the boundary, which reads as "low"
// on a dark/moody frame regardless of whether the crop itself is correct —
// exactly the material this app is full of. Rather than ship a number that
// was actively misleading on Ryan's most common content, it's gone.)
//
// `level` is the Redetect strictness dial. Unlike the v34 engine — where
// higher levels demanded a DARKER, flatter bar and so shrank the crop — flat
// is already the floor here, so each level instead widens what counts as flat
// (MAD <= 1 + level). Because this engine's bias is to under-crop, pressing
// Redetect is the way to get past a slightly-textured bar (film grain over a
// letterbox, a gradient mat) that a strict MAD 1 refuses to peel.
export function detectCrop(img, level = 0) {
  return detectCropAtFlatMad(img, FLAT_MAD_BASE + Math.max(0, level));
}

// Same detector with the flatness threshold supplied directly. Exported so the
// threshold can be swept against a labelled image set rather than guessed at;
// detectCrop above is the only caller product code should use.
export function detectCropAtFlatMad(img, flatMad) {
  const { lum, W, H, scale } = rasterizeLuma(img);
  if (W < 8 || H < 8) return null;
  const buf = new Uint8Array(Math.max(W, H));

  const rowMad = (y, x0, x1) => {
    const n = x1 - x0;
    for (let i = 0; i < n; i++) buf[i] = lum[y * W + x0 + i];
    return madOf(buf, n);
  };
  const colMad = (x, y0, y1) => {
    const n = y1 - y0;
    for (let i = 0; i < n; i++) buf[i] = lum[(y0 + i) * W + x];
    return madOf(buf, n);
  };

  // Pass 1 — rows across the full width, to find the horizontal bars.
  let [top, bottom] = peelAxis(H, y => rowMad(y, 0, W), flatMad);
  if (bottom - top < 8) return null;

  // Pass 2 — columns measured only within those content rows. Doing this
  // second is the point: it is what keeps a letterbox's black from making a
  // dark picture edge look like a pillarbox bar.
  let [left, right] = peelAxis(W, x => colMad(x, top, bottom), flatMad);
  if (right - left < 8) return null;

  // Pass 3 — rows again, now measured only within the content columns. Cheap,
  // and recovers bar rows that a chrome column was propping up.
  [top, bottom] = peelAxis(H, y => rowMad(y, left, right), flatMad);
  if (bottom - top < 8) return null;

  const x = left, y = top, w = right - left, h = bottom - top;

  // Nothing meaningful to cut — let the caller show the untouched frame.
  if (w >= W * 0.995 && h >= H * 0.995) return null;

  if (scale < 1) {
    const inv = 1 / scale;
    return {
      box: {
        x: Math.round(x * inv), y: Math.round(y * inv),
        w: Math.round(w * inv), h: Math.round(h * inv),
      },
    };
  }
  return { box: { x, y, w, h } };
}

// ── TIGHTEN ──────────────────────────────────────────────────────────────────
// Finishing pass: shave any flat residue detectCrop left behind at its edges.
//
// detectCrop peels only at MAD <= 0 and stops dead at the first imperfect
// line, so it reliably stops a few pixels short — a JPEG-noisy row of a white
// mat scores MAD 1, not 0. Tighten runs at MAD <= 1 to clear exactly that
// residue.
//
// THE TRAP, and why the two guards below are the whole design:
// MAD 1 does not only describe leftover mat. It also describes genuine dark
// picture — the grass and sky at the edges of IMG_1068 and IMG_1081 score
// exactly 1, and a plain MAD <= 1 peel eats 38-48px of real image off them.
// Loosening the threshold alone hands back the very over-crops this engine
// was built to stop.
//
// Brightness cannot referee it. Measured medians of the strips tighten wants
// to take: 254 (real white mat residue on IMG_1243), but 8, 10 and 19 for the
// dark picture edges — indistinguishable from a genuine black bar's residue.
// So "is it near-white or near-black?" is useless here.
//
// Two guards work, and both are needed:
//
//   1. ONLY TIGHTEN AN EDGE DETECTION ACTUALLY PEELED. detectCrop already
//      removed every dead-flat line, so a residue can only exist against an
//      edge where a bar was found. On IMG_1068 and IMG_5530 the sides were
//      never peeled — the picture runs to the frame — so a "residue" there is
//      fiction, and this alone blocks the 38px and 33px bites.
//   2. THE FLAT RUN MUST END ON ITS OWN, WITHIN A 2% CAP. This is the sharp
//      one. Real residue terminates: IMG_1243's leftover mat is exactly 6
//      rows and then the photograph starts. A dark picture edge does not — on
//      IMG_1081 the MAD <= 1 run keeps going for 48px and beyond, and simply
//      capping it just takes a smaller bite out of the picture (a 1% cap
//      turned a 48px bite into a 16px one, which is less wrong, not right).
//      So a run that reaches the cap without hitting real texture is read as
//      picture and trimmed by nothing at all.
//
// An explicit press of the Tighten button lifts both guards (all edges, and a
// cap that grows per level), because that is Ryan deliberately asking for
// more. Only the automatic pass is held to the strict rules.
const TIGHTEN_FLAT_MAD = 1;
const TIGHTEN_CAP_BASE = 0.02;
const TIGHTEN_CAP_PER_LEVEL = 0.05;
const TIGHTEN_MIN_SIDE = 30;

// `allowedEdges` restricts which sides may be trimmed — {left,top,right,bottom}
// booleans. Omitted (the manual Tighten button) means all four.
export function tightenBox(img, cropBox, level = 0, allowedEdges = null) {
  const { lum, W, H, scale } = rasterizeLuma(img);
  const flatMad = TIGHTEN_FLAT_MAD + Math.max(0, level);
  const cap = TIGHTEN_CAP_BASE + TIGHTEN_CAP_PER_LEVEL * Math.max(0, level);
  const may = e => !allowedEdges || allowedEdges[e];
  const buf = new Uint8Array(Math.max(W, H));

  // Map the natural-coordinate box into the analysis canvas's space.
  let x = Math.round(cropBox.x * scale), y = Math.round(cropBox.y * scale);
  let w = Math.round(cropBox.w * scale), h = Math.round(cropBox.h * scale);
  x = Math.max(0, Math.min(W - 1, x)); y = Math.max(0, Math.min(H - 1, y));
  w = Math.max(1, Math.min(W - x, w)); h = Math.max(1, Math.min(H - y, h));

  const rowMad = (ry, x0, x1) => {
    const n = x1 - x0;
    for (let i = 0; i < n; i++) buf[i] = lum[ry * W + x0 + i];
    return madOf(buf, n);
  };
  const colMad = (cx, y0, y1) => {
    const n = y1 - y0;
    for (let i = 0; i < n; i++) buf[i] = lum[(y0 + i) * W + cx];
    return madOf(buf, n);
  };

  // Count flat lines inward from one edge. No lookahead — same conservative
  // rule as detectCrop, stop at the first real line. Returns 0 if the run
  // reaches `limit` without ending: that is guard 2, the difference between a
  // residue that stops and a picture edge that keeps going.
  const countFlat = (limit, madAt) => {
    let n = 0;
    while (n < limit && madAt(n) <= flatMad) n++;
    return n >= limit ? 0 : n;
  };

  let changed = false;
  const capRows = Math.min(Math.floor(h * cap), Math.max(0, h - TIGHTEN_MIN_SIDE));
  const capCols = Math.min(Math.floor(w * cap), Math.max(0, w - TIGHTEN_MIN_SIDE));

  let t = may('top') ? countFlat(capRows, k => rowMad(y + k, x, x + w)) : 0;
  if (t) { y += t; h -= t; changed = true; }
  t = may('bottom') ? countFlat(Math.min(capRows, Math.max(0, h - TIGHTEN_MIN_SIDE)), k => rowMad(y + h - 1 - k, x, x + w)) : 0;
  if (t) { h -= t; changed = true; }
  t = may('left') ? countFlat(capCols, k => colMad(x + k, y, y + h)) : 0;
  if (t) { x += t; w -= t; changed = true; }
  t = may('right') ? countFlat(Math.min(capCols, Math.max(0, w - TIGHTEN_MIN_SIDE)), k => colMad(x + w - 1 - k, y, y + h)) : 0;
  if (t) { w -= t; changed = true; }

  if (!changed) return { box: { ...cropBox }, changed: false };
  const inv = 1 / scale;
  return {
    box: {
      x: Math.round(x * inv), y: Math.round(y * inv),
      w: Math.round(w * inv), h: Math.round(h * inv),
    },
    changed: true,
  };
}

// "This box wouldn't actually crop anything" — used to count approved-but-
// uncropped images as originals rather than sending a no-op to the server.
export function isFullImageBox(box, img) {
  return !box || (box.x === 0 && box.y === 0 && box.w === img.naturalWidth && box.h === img.naturalHeight);
}

// detectCrop followed by the automatic finishing pass. This is what the crop
// UI should call: detection alone deliberately stops short of the seam, and
// on its own would hand Ryan a box with a few pixels of mat still in it.
export function detectCropTightened(img, level = 0) {
  const det = detectCrop(img, level);
  if (!det) return null;
  const b = det.box;
  // Only the edges detection actually cut are eligible — see guard 1 above.
  const edges = {
    left:   b.x > 0,
    top:    b.y > 0,
    right:  b.x + b.w < img.naturalWidth,
    bottom: b.y + b.h < img.naturalHeight,
  };
  const t = tightenBox(img, b, 0, edges);
  return t.changed ? { box: t.box } : det;
}

export { FLAT_MAD_BASE, MAX_EDGE_PEEL, TIGHTEN_CAP_BASE };
