// Synthetic regression test for cropDetect.js — no new deps. Stubs just
// enough of `document`/canvas for rasterize() to work: drawImage copies a
// pre-built pixel buffer straight through (no real scaling needed since
// every test image here is tiny, well under the iOS canvas cap), and
// getImageData hands that buffer back.
//
// Usage (from the frame-atlas folder):
//   node frontend/scripts/test_cropDetect_synthetic.mjs

global.document = {
  createElement() {
    const canvas = {
      width: 0, height: 0,
      getContext() {
        return {
          drawImage(img, dx, dy, w, h) { canvas._pixels = img.__pixels; },
          getImageData(x, y, w, h) { return { data: canvas._pixels }; },
        };
      },
    };
    return canvas;
  },
};

const { detectCrop } = await import('../src/cropDetect.js');

function makeImage(W, H, fillFn) {
  const pixels = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const [r, g, b] = fillFn(x, y);
      const i = (y * W + x) * 4;
      pixels[i] = r; pixels[i + 1] = g; pixels[i + 2] = b; pixels[i + 3] = 255;
    }
  }
  return { naturalWidth: W, naturalHeight: H, __pixels: pixels };
}

// Coarse checkerboard so "textured" rows have real std/edge density without
// risking a stray extreme-luminance dip (a sine-hash noise function tried
// first here occasionally produced one by pure chance, which coincidentally
// tripped the landmark/icon-row detector and made even the plain-bar
// regression case fail — a test-generator artifact, not a real detector bug;
// this pattern's per-row average is exactly `base` everywhere, so it can't
// recreate that by accident).
function block(x, y, base, amp) {
  const on = (Math.floor(x / 6) + Math.floor(y / 6)) % 2 === 0;
  return base + (on ? amp : -amp);
}

const checks = [];
function check(label, cond) {
  checks.push([label, !!cond]);
  console.log((cond ? '  ok  ' : 'FAIL  ') + label);
}

// ── KNOWN GAP (not fixed by this patch, documented for future work) ──
// Hazy negative space directly adjacent to a true black bar (no sharp break
// between them) still gets eaten along with the bar. Root cause traced via
// this repro: the true bar (std~0) and the haze (std~15, matching this
// file's own claim that real dark photo content runs std~10-30) BOTH pass
// isChromeCandidateRow's loose dark-zone test (lum<40 && std<25) on their
// own, so they merge into one contiguous, edge-touching candidate run
// *before* any run-level trust decision (touchesEdge, allUniform, etc.) ever
// runs — those checks can't un-merge something that was already merged one
// step earlier. Confirmed same result with haze std=3 and std=15. Fixing
// this for real means tightening isChromeCandidateRow's dark-zone std
// ceiling (currently 25) toward the ~10-12 range that would actually
// separate true-bar flatness from real dark-photo texture — NOT attempted
// here because that threshold is shared by every dark-UI chrome case in
// this file (dim toolbars, gradients, etc.), not just letterbox bars, and
// tightening it blind — without the real source image to test against —
// risks newly MISSING legitimate soft dark chrome elsewhere. Left as a
// known limitation rather than guessed at.
{
  const W = 120, H = 300;
  const img = makeImage(W, H, (x, y) => {
    if (y < 40 || y >= 260) { const v = 1; return [v, v, v]; } // true black bar
    if (y < 70 || y >= 230) { const v = block(x, y, 28, 15); return [v, v, v]; } // haze, std~15
    const v = block(x, y, 140, 90); return [v, v, v]; // textured subject
  });
  const result = detectCrop(img);
  if (result) {
    console.log(`  info  known-gap repro: box.y=${result.box.y} (bar ends at 40, haze ends at 70) — `
      + (result.box.y <= 45 ? 'unexpectedly fixed!' : 'still reproduces, as expected — see comment above'));
  }
}

// ── Test 2: a real true bar (touching the edge) is still detected ──
// Plain letterbox — top/bottom pure black bars, real textured photo between.
// This is the ordinary case the tool must keep getting right.
{
  const W = 120, H = 300;
  const img = makeImage(W, H, (x, y) => {
    if (y < 50 || y >= 250) { const v = 1; return [v, v, v]; }
    const v = block(x, y, 130, 90); return [v, v, v];
  });
  const result = detectCrop(img);
  check('plain bar test: detection succeeded', result);
  if (result) {
    const { y, h } = result.box;
    check(`plain bar test: top bar trimmed (y in [40,60]), got y=${y}`, y >= 40 && y <= 60);
    check(`plain bar test: bottom bar trimmed (bottom in [240,260]), got bottom=${y + h}`, (y + h) >= 240 && (y + h) <= 260);
  }
}

// ── Test 3: a flat dark bar with small bright glyphs still counts as flat ──
// Top bar (0-49) is near-black with three narrow bright vertical glyphs
// (icon stand-ins) at x=20-25, 55-60, 90-95. Real photo below (50-299).
{
  const W = 120, H = 300;
  const img = makeImage(W, H, (x, y) => {
    if (y < 50) {
      const onGlyph = (x >= 20 && x < 26) || (x >= 55 && x < 61) || (x >= 90 && x < 96);
      const v = onGlyph ? 235 : 2;
      return [v, v, v];
    }
    const v = block(x, y, 130, 90); return [v, v, v];
  });
  const result = detectCrop(img);
  check('glyph-bar test: detection succeeded', result);
  if (result) {
    const { y } = result.box;
    check(`glyph-bar test: bar with glyphs still trimmed (y in [35,55]), got y=${y}`, y >= 35 && y <= 55);
  }
}

const passed = checks.filter(([, ok]) => ok).length;
console.log(`\n${passed}/${checks.length} checks passed`);
if (passed !== checks.length) process.exit(1);
