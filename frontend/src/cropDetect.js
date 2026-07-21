// cropDetect.js — Frame Atlas V18
//
// The letterbox/screenshot-chrome detection engine, lifted VERBATIM from
// CropStudio v34 (Ryan's standalone tool, tuned across 34 versions against
// real IG screenshots and dark flat-lay photography). Do not "clean up" or
// re-derive any threshold in here — every constant is load-bearing and most
// carry a comment explaining which real image broke without them.
//
// The only changes from CropStudio v34:
//   1. The offscreen <canvas> is created here instead of grabbed from the
//      CropStudio page's DOM (getOffscreen below).
//   2. detectCrop/tightenBox cap the analysis canvas at ~16M pixels (iOS
//      Safari's canvas limit) and scale the resulting box back up — a no-op
//      for every image CropStudio was ever tuned on (phone screenshots are
//      well under the cap); it only kicks in for very large camera files.
//   3. tightenCrop's pixel-scanning core is exposed as a pure function
//      (tightenBox) instead of mutating CropStudio's review-screen state.
//
// All coordinates in and out are in the image's natural (EXIF-upright)
// pixel space: { x, y, w, h }.

const PADDING = 12;

// iOS Safari refuses canvases over ~16.7M pixels; stay under it.
const MAX_CANVAS_PIXELS = 16000000;

let _offscreen = null;
function getOffscreen() {
  if (!_offscreen) _offscreen = document.createElement('canvas');
  return _offscreen;
}

// Draw the image into the offscreen canvas, downscaling only if it exceeds
// the canvas pixel cap. Returns pixel data plus the scale used, so callers
// can map detected boxes back to natural coordinates.
function rasterize(img) {
  const natW = img.naturalWidth, natH = img.naturalHeight;
  const scale = Math.min(1, Math.sqrt(MAX_CANVAS_PIXELS / (natW * natH)));
  const W = Math.max(1, Math.round(natW * scale));
  const H = Math.max(1, Math.round(natH * scale));
  const canvas = getOffscreen();
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, W, H);
  const data = ctx.getImageData(0, 0, W, H).data;
  return { data, W, H, scale };
}

// ── AVATAR EXCLUSION (Track 1 support) ──
// Username/profile rows often include a small circular avatar photo right
// next to the text. That avatar has real photographic texture, which can
// make the row look too "busy" to pass the landmark signature (extreme
// luminance + few clusters + low edge density) even though the rest of the
// row — text plus flat background — is a textbook landmark row. This scans
// the left margin of the top landmark zone for a small squarish/circular
// high-texture blob and, if found, returns its column extent so landmark
// evaluation can ignore those columns. The avatar gets treated as part of
// the chrome being cut out, not as evidence the row is real photo content.
function detectAvatarZone(data, W, H) {
  const EDGE_THRESH = 10;
  // Uses a taller scan zone than the landmark pass. An avatar can be as
  // wide as ~130px, and a circle that wide needs matching vertical room —
  // capping this at the same 250px landmark zone would truncate the row
  // band right as the avatar is still in view, undersizing its measured
  // height and making it look like a wide, flat sliver instead of a circle.
  const zoneH = Math.min(400, Math.floor(H * 0.3));
  const searchW = Math.min(200, Math.floor(W * 0.2));
  if (searchW < 20 || zoneH < 20) return null;

  const texDensity = new Float32Array(zoneH);
  for (let y = 0; y < zoneH; y++) {
    let edges = 0;
    let prevL = 0.299*data[(y*W)*4]+0.587*data[(y*W)*4+1]+0.114*data[(y*W)*4+2];
    for (let x = 1; x < searchW; x++) {
      const i = (y*W+x)*4;
      const l = 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      if (Math.abs(l - prevL) > EDGE_THRESH) edges++;
      prevL = l;
    }
    texDensity[y] = edges / (searchW - 1);
  }

  // Group rows with elevated left-margin texture into bands, bridging tiny
  // gaps (a couple of flat pixel rows inside a photo is normal).
  const TEX_THRESH = 0.15, GAP = 3;
  const bands = [];
  let bs = -1, prevY = -1;
  for (let y = 0; y < zoneH; y++) {
    if (texDensity[y] > TEX_THRESH) {
      if (bs === -1) bs = y;
      prevY = y;
    } else if (bs !== -1 && y - prevY > GAP) {
      bands.push([bs, prevY]);
      bs = -1;
    }
  }
  if (bs !== -1) bands.push([bs, prevY]);

  // Avatars are small (roughly 20–130px tall) — pick the largest band in
  // that range as the avatar candidate. Bands outside this range are either
  // noise or too large to plausibly be an avatar thumbnail.
  let best = null;
  for (const [s, e] of bands) {
    const h = e - s + 1;
    if (h >= 20 && h <= 130 && (!best || h > (best[1]-best[0]+1))) best = [s, e];
  }
  if (!best) return null;
  const [rowStart, rowEnd] = best;
  const midY = Math.floor((rowStart + rowEnd) / 2);

  // Find the avatar's right edge. Scan every window across the search
  // width and track the rightmost one that still shows real texture — NOT
  // the first flat-looking window encountered. A photo thumbnail can have
  // small locally-flat patches inside it (a dark background behind a leaf,
  // a shadow), and bailing at the first flat window would badly undersize
  // the detected avatar.
  const SUSTAIN = 15, WIN_THRESH = 0.06;
  function rightmostTexturedEdge(y) {
    let lastEnd = -1;
    for (let x = 0; x < searchW - SUSTAIN; x++) {
      let prevL = 0.299*data[(y*W+x)*4]+0.587*data[(y*W+x)*4+1]+0.114*data[(y*W+x)*4+2];
      let edges = 0;
      for (let k = 1; k < SUSTAIN; k++) {
        const i = (y*W+(x+k))*4;
        const l = 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
        if (Math.abs(l - prevL) > EDGE_THRESH) edges++;
      }
      if (edges / (SUSTAIN - 1) > WIN_THRESH) lastEnd = x + SUSTAIN;
    }
    return lastEnd;
  }
  // Check a couple of rows within the band, not just the midpoint — a
  // single row can clip a circle's edge unevenly near its top/bottom.
  const sampleRows = [rowStart + Math.floor((rowEnd-rowStart)*0.3), midY, rowStart + Math.floor((rowEnd-rowStart)*0.7)];
  let colEnd = -1;
  for (const sy of sampleRows) colEnd = Math.max(colEnd, rightmostTexturedEdge(sy));
  if (colEnd === -1) return null; // no real texture found — likely a false-positive band
  colEnd = Math.min(searchW, colEnd + 6); // small buffer past the detected edge

  // Sanity check: a real avatar is roughly circular/square. Reject blobs
  // that are much wider than tall or vice versa — likely not an avatar.
  const bandH = rowEnd - rowStart + 1;
  const ratio = colEnd / bandH;
  if (colEnd < 20 || ratio < 0.4 || ratio > 2.6) return null;

  return { rowStart, rowEnd, colEnd };
}

// ── TAB BAR DETECTION (dedicated signature) ──
// A persistent bottom tab bar (home/search/camera/heart/profile) produces
// far more clusters per row than any icon-row landmark can safely tolerate
// — up to ~27, well past the cap of 16. Raising the cap further isn't a
// safe fix: v28 tried a looser bottom rule with no cluster gate and it
// collapsed a dark photo (IMG_2573) to a 143px crop, because large dark
// photo regions satisfy "extreme luminance" just as easily as real chrome.
// The cluster count is doing real work telling chrome apart from Ryan's
// dark, textured flat-lay style — so instead of loosening it, this looks
// for the tab bar's own physical structure directly: several evenly-spaced
// icon glyphs sitting on an otherwise solid-color bar that touches the
// image's true bottom edge. Runs independently of, and before, the normal
// landmark bottom-band pass — its only job is to mark the tab bar itself
// as chrome so the existing icon-row/edge-anchor logic can then correctly
// chain up to the real edge through it, the same way it already does for
// any other confirmed chrome.
//
// NOTE: built from the documented root cause in the v28 handoff, not fresh
// pixel analysis of an actual tab-bar source file — no raw source image was
// available this round. Thresholds below are reasoned estimates; if a real
// tab bar still isn't caught, the fastest path to tightening this is a
// source screenshot to test against directly, per the project's usual
// diagnose-before-fixing approach.
function detectTabBar(data, W, H) {
  const SEARCH_H = Math.min(240, Math.floor(H * 0.22));
  if (SEARCH_H < 30) return null;
  const y0 = H - SEARCH_H;
  const EDGE_THRESH = 10;

  // Per-column vertical edge count within the search window — icon glyphs
  // produce lots of vertical luminance transitions in their column range;
  // flat background between icons has almost none.
  const colEdges = new Int32Array(W);
  for (let x = 0; x < W; x++) {
    let edges = 0;
    let prevL = 0.299*data[(y0*W+x)*4]+0.587*data[(y0*W+x)*4+1]+0.114*data[(y0*W+x)*4+2];
    for (let y = y0 + 1; y < H; y++) {
      const i = (y*W+x)*4;
      const l = 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      if (Math.abs(l - prevL) > EDGE_THRESH) edges++;
      prevL = l;
    }
    colEdges[x] = edges;
  }

  // Group columns with elevated edge activity into icon-glyph regions,
  // bridging tiny 2-3px gaps (anti-aliasing between adjacent strokes).
  const COL_EDGE_THRESH = Math.max(4, Math.floor(SEARCH_H * 0.04));
  const COL_GAP = 4;
  const groups = [];
  let gs = -1, prevX = -1;
  for (let x = 0; x < W; x++) {
    if (colEdges[x] > COL_EDGE_THRESH) {
      if (gs === -1) gs = x;
      prevX = x;
    } else if (gs !== -1 && x - prevX > COL_GAP) {
      groups.push([gs, prevX]);
      gs = -1;
    }
  }
  if (gs !== -1) groups.push([gs, prevX]);

  // Real icon glyphs: not slivers (anti-aliasing noise) and not huge (a
  // whole photo edge bleeding into the window). 2%-22% of width covers
  // everything from a small glyph to a wide icon at low resolution.
  const minGlyphW = W * 0.02, maxGlyphW = W * 0.22;
  const iconGroups = groups.filter(([s,e]) => (e - s) >= minGlyphW && (e - s) <= maxGlyphW);
  if (iconGroups.length < 3 || iconGroups.length > 7) return null;

  // Evenly-spaced check: gaps between consecutive icon centers shouldn't
  // vary wildly. Real tab bars space icons uniformly; a coincidental
  // cluster of photo detail rarely does.
  const centers = iconGroups.map(([s,e]) => (s+e)/2).sort((a,b)=>a-b);
  const gaps = [];
  for (let i = 1; i < centers.length; i++) gaps.push(centers[i]-centers[i-1]);
  const meanGap = gaps.reduce((a,b)=>a+b,0) / gaps.length;
  const evenlySpaced = gaps.every(g => g > meanGap * 0.5 && g < meanGap * 1.7);
  if (!evenlySpaced) return null;

  // Background check: sample columns OUTSIDE any icon group and confirm
  // they're a solid, extreme-luminance, low-texture bar — not just "low
  // edge count columns" that happen to sit inside real photo content.
  function inIconGroup(x) { return iconGroups.some(([s,e]) => x >= s-2 && x <= e+2); }
  let bgSum = 0, bgCount = 0, bgSqSum = 0;
  for (let x = 0; x < W; x += 3) {
    if (inIconGroup(x)) continue;
    for (let y = y0; y < H; y += 3) {
      const i = (y*W+x)*4;
      const l = 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      bgSum += l; bgSqSum += l*l; bgCount++;
    }
  }
  if (bgCount < 20) return null;
  const bgMean = bgSum / bgCount;
  const bgStd = Math.sqrt(Math.max(0, bgSqSum/bgCount - bgMean*bgMean));
  if (!(bgMean > 200 || bgMean < 55) || bgStd >= 30) return null;

  // Confirms the band actually touches the image's physical bottom edge —
  // the same "real chrome connects to the frame" principle already used
  // for hard bounds — rather than floating mid-photo.
  let lastSum = 0, lastCount = 0;
  for (let x = 0; x < W; x += 5) {
    const i = ((H-1)*W+x)*4;
    lastSum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; lastCount++;
  }
  if (Math.abs((lastSum/lastCount) - bgMean) > 40) return null;

  // Find the actual top of the bar (may be shorter than the full search
  // window) by walking upward from the bottom edge while background rows
  // (icon columns excluded) keep matching the same solid-color signature.
  let top = H - 1;
  for (let y = H - 1; y >= y0; y--) {
    let sum = 0, count = 0;
    for (let x = 0; x < W; x += 3) {
      if (inIconGroup(x)) continue;
      const i = (y*W+x)*4;
      sum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      count++;
    }
    const rowMean = count ? sum / count : bgMean;
    if (Math.abs(rowMean - bgMean) > 45) break;
    top = y;
  }
  return top;
}

// ── NAV BAR DETECTION (color-consistency signature) ──
// Some header bars sit at a medium luminance — neither dark enough for the
// dark-chrome brightness test (<40) nor light enough for the light-chrome
// test (>210), and inconsistent enough row-to-row that even the landmark
// pass's extreme-luminance gate (>215 or <85) only catches scattered single
// rows instead of a connected band (confirmed on a real IG hashtag-page
// header: a blue nav bar whose rows swing between ~71 and ~114 depending on
// how much status-bar/title text sits in them). Every threshold this tool
// currently has is built around comparing luminance to a fixed cutoff, so a
// bar like that falls through all of them regardless of level/redetect.
//
// This looks for the same thing a person would: a real header bar is a
// fairly consistent COLOR (not necessarily consistent luminance alone) that
// starts right at the image's true top edge, with small icon/text
// deviations layered on top — as opposed to real photo content, which
// rarely holds one exact color for dozens of consecutive rows immediately
// from row 0 AND also contains that kind of small-deviation texture (a
// perfectly flat, zero-deviation band is treated as more likely to be a
// plain photo backdrop and is deliberately rejected below).
//
// IMPORTANT: only evaluates the SINGLE band starting at row 0, on purpose.
// An earlier version of this walked into consecutive bands (to also catch
// a secondary avatar/username strip right after the nav bar) — verified
// against a real dark low-key photo that this walks straight through the
// photo's own near-black background (which is >90% one dominant quantized
// color near the top and only fades out ~500 rows in) and would have cropped
// out ~500px of real photo content. That's a bigger failure than the bug
// this is meant to fix, so it's deliberately NOT done. This only recovers
// the nav bar itself; a secondary strip right after it (confirmed on the
// same hashtag-page source image, ~130px of white avatar/username area) is
// left for the existing landmark pass to catch on its own, which it does
// reliably in the common case — the residual miss only shows up when that
// pass can't chain back to an edge, i.e. exactly this rarer combination.
//
// NOTE: validated against one real medium-blue-header source image this
// session, not a broad regression set.
function detectNavBar(data, W, H) {
  const SEARCH_H = Math.min(500, Math.floor(H * 0.4));
  if (SEARCH_H < 20) return null;

  function rowModeColor(y) {
    const buckets = new Map();
    for (let x = 0; x < W; x++) {
      const i = (y*W+x)*4;
      const key = (data[i]>>4)*256 + (data[i+1]>>4)*16 + (data[i+2]>>4);
      buckets.set(key, (buckets.get(key)||0) + 1);
    }
    let bestKey = -1, bestCount = -1;
    for (const [k, c] of buckets) if (c > bestCount) { bestCount = c; bestKey = k; }
    return {
      r: Math.floor(bestKey/256)*16 + 8,
      g: Math.floor((bestKey%256)/16)*16 + 8,
      b: (bestKey%16)*16 + 8,
      frac: bestCount / W
    };
  }

  const row0 = rowModeColor(0);
  if (row0.frac < 0.5) return null;

  const COLOR_TOL = 28 * 1.8;
  function fracMatching(y) {
    let match = 0, total = 0;
    for (let x = 0; x < W; x += 2) {
      const i = (y*W+x)*4;
      const dr = data[i]-row0.r, dg = data[i+1]-row0.g, db = data[i+2]-row0.b;
      if (Math.sqrt(dr*dr+dg*dg+db*db) < COLOR_TOL) match++;
      total++;
    }
    return match / total;
  }

  let bottom = 0, missStreak = 0, hasDeviation = false;
  for (let y = 0; y < SEARCH_H; y++) {
    const f = fracMatching(y);
    if (f > 0.4) {
      bottom = y; missStreak = 0;
      if (f < 0.9) hasDeviation = true;
    } else {
      missStreak++;
      if (missStreak > 15) break;
    }
  }

  const bandLen = bottom + 1;
  if (bandLen < 20 || bandLen > 550) return null;
  if (!hasDeviation) return null;
  return bottom;
}

// ── PURE BLACK BAR DETECTION ──
// Detects actual black bars (letterbox/pillarbox) by looking for regions where
// ALL pixels are pure or near-pure black: low luminance AND minimal RGB variation.
// This catches bars that would otherwise be mistaken for legitimate dark content
// (shadows, night sky, dark machinery) which CAN vary slightly in tone.
// Returns { topY, bottomY, leftX, rightX } edges of detected bars, or empty object.
function detectPureBlackBars(data, W, H) {
  const COLOR_VAR_THRESH = 50;    // R/G/B must be within 50 of each other (very permissive)
  const PURE_BLACK_LUMA = 90;     // luminance threshold for "dark" (0-90 range, catches most dark bars)
  const MIN_BAR_HEIGHT = 6;       // minimum consecutive rows to count as a bar (not noise)
  const EDGE_SCAN_DEPTH = 0.40;   // scan up to 40% from each edge

  function isPixelPureBlack(i) {
    const r = data[i];
    const g = data[i+1];
    const b = data[i+2];
    const lum = 0.299*r + 0.587*g + 0.114*b;
    const colorVar = Math.max(r, g, b) - Math.min(r, g, b);
    return lum < PURE_BLACK_LUMA && colorVar < COLOR_VAR_THRESH;
  }

  // Count pure-black pixels per row
  const pureBlackFraction = new Float32Array(H);
  for (let y = 0; y < H; y++) {
    let blackCount = 0;
    for (let x = 0; x < W; x++) {
      if (isPixelPureBlack((y*W+x)*4)) blackCount++;
    }
    pureBlackFraction[y] = blackCount / W;
  }

  // Scan from edges inward, finding contiguous regions of >50% dark-ish
  const PURITY_THRESH = 0.50;
  const maxScanH = Math.floor(H * EDGE_SCAN_DEPTH);
  const result = {};

  // Scan from top
  let topBarEnd = -1;
  for (let y = 0; y < Math.min(maxScanH, H); y++) {
    if (pureBlackFraction[y] > PURITY_THRESH) {
      topBarEnd = y;
    } else if (topBarEnd >= 0) {
      // Allow small gaps (compression artifacts, faint text)
      let consecutiveNonBlack = 0;
      for (let k = y; k < Math.min(y + 3, H); k++) {
        if (pureBlackFraction[k] <= PURITY_THRESH) consecutiveNonBlack++;
      }
      if (consecutiveNonBlack >= 3) break; // gap is too large, bar ended
    }
  }
  if (topBarEnd >= 0 && topBarEnd + 1 >= MIN_BAR_HEIGHT) {
    result.topY = topBarEnd + 1;
  }

  // Scan from bottom
  let bottomBarStart = H;
  for (let y = H-1; y >= Math.max(0, H - maxScanH); y--) {
    if (pureBlackFraction[y] > PURITY_THRESH) {
      bottomBarStart = y;
    } else if (bottomBarStart < H) {
      let consecutiveNonBlack = 0;
      for (let k = y; k > Math.max(y - 3, -1); k--) {
        if (pureBlackFraction[k] <= PURITY_THRESH) consecutiveNonBlack++;
      }
      if (consecutiveNonBlack >= 3) break;
    }
  }
  if (bottomBarStart < H && H - bottomBarStart >= MIN_BAR_HEIGHT) {
    result.bottomY = bottomBarStart;
  }

  // Same logic for columns (left/right bars)
  const maxScanW = Math.floor(W * EDGE_SCAN_DEPTH);
  const pureBlackFractionCol = new Float32Array(W);
  for (let x = 0; x < W; x++) {
    let blackCount = 0;
    for (let y = 0; y < H; y++) {
      if (isPixelPureBlack((y*W+x)*4)) blackCount++;
    }
    pureBlackFractionCol[x] = blackCount / H;
  }

  let leftBarEnd = -1;
  for (let x = 0; x < Math.min(maxScanW, W); x++) {
    if (pureBlackFractionCol[x] > PURITY_THRESH) {
      leftBarEnd = x;
    } else if (leftBarEnd >= 0) {
      let consecutiveNonBlack = 0;
      for (let k = x; k < Math.min(x + 3, W); k++) {
        if (pureBlackFractionCol[k] <= PURITY_THRESH) consecutiveNonBlack++;
      }
      if (consecutiveNonBlack >= 3) break;
    }
  }
  if (leftBarEnd >= 0 && leftBarEnd + 1 >= MIN_BAR_HEIGHT) {
    result.leftX = leftBarEnd + 1;
  }

  let rightBarStart = W;
  for (let x = W-1; x >= Math.max(0, W - maxScanW); x--) {
    if (pureBlackFractionCol[x] > PURITY_THRESH) {
      rightBarStart = x;
    } else if (rightBarStart < W) {
      let consecutiveNonBlack = 0;
      for (let k = x; k > Math.max(x - 3, -1); k--) {
        if (pureBlackFractionCol[k] <= PURITY_THRESH) consecutiveNonBlack++;
      }
      if (consecutiveNonBlack >= 3) break;
    }
  }
  if (rightBarStart < W && W - rightBarStart >= MIN_BAR_HEIGHT) {
    result.rightX = rightBarStart;
  }

  return result;
}

// ── CROP DETECTION ──
// level: strictness for what counts as "chrome." 0 = default. Higher levels
// require stronger evidence (darker/lighter, lower texture) before stripping
// anything, used by the Redetect button to recover undersized crops.
function detectCrop(img, level = 0) {
  const { data, W, H, scale } = rasterize(img);

  // Per-row luminance + std dev
  const rowLum = new Float32Array(H);
  const rowStd = new Float32Array(H);
  for (let y = 0; y < H; y++) {
    let sum = 0;
    for (let x = 0; x < W; x++) { const i=(y*W+x)*4; sum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; }
    rowLum[y] = sum / W;
  }
  for (let y = 0; y < H; y++) {
    let v = 0;
    for (let x = 0; x < W; x++) { const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; v+=(l-rowLum[y])**2; }
    rowStd[y] = Math.sqrt(v / W);
  }

  // Per-row edge/texture density — fraction of horizontally-adjacent pixel
  // pairs with a meaningful luminance jump. Real photo content (even dark,
  // low-contrast patches like shadow or night sky) almost always has fine
  // grain/texture; truly flat UI backgrounds and letterbox bars don't. This
  // catches cases plain brightness/std can miss: a row that's dim with only
  // a few small bright accents can still look "low contrast" overall while
  // having real local detail.
  const EDGE_THRESH = 10;
  const rowEdgeDensity = new Float32Array(H);
  for (let y = 0; y < H; y++) {
    let edges = 0;
    let prevL = 0.299*data[y*W*4]+0.587*data[y*W*4+1]+0.114*data[y*W*4+2];
    for (let x = 1; x < W; x++) {
      const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      if (Math.abs(l - prevL) > EDGE_THRESH) edges++;
      prevL = l;
    }
    rowEdgeDensity[y] = edges / (W - 1);
  }

  // UI type — use the median row luminance across the WHOLE image, not just
  // the top strip. A solid colored header bar (e.g. a blue "Photo" nav bar)
  // can have a medium luminance that's neither clearly light nor clearly
  // dark, which throws off a top-strip-only sample. The median across all
  // rows reflects the dominant tone of the bulk of the screenshot (usually
  // the app's actual background, light or dark) and isn't skewed by one
  // accent-colored bar that's a small fraction of the total image.
  //
  // v28: this whole-image median still mis-classifies dark/moody photos
  // (photo pixels dominate pixel count) as "dark UI" even when the actual
  // app chrome is bright white. The interior keeps using this global value
  // (out of scope here — interior fragmentation on very dark photos is a
  // separate, still-open issue). But the top/bottom EDGE ZONES — where
  // chrome actually lives — are now classified independently using their
  // own local median, so real light chrome doesn't get missed just because
  // the photo itself is dark. See ZONE_SIZE below for why the window is
  // wider than the old fixed 250px cap.
  const sortedRowLum = Array.from(rowLum).sort((a, b) => a - b);
  const medianLum = sortedRowLum[Math.floor(H / 2)];
  const isLightUI = medianLum > 128;

  // v28: enlarged from a flat 250px cap. Real trailing IG chrome (icon row +
  // likes + multi-line hashtag caption + date + tab bar) commonly runs past
  // 250px, which left it entirely outside both the old classification zone
  // and the landmark scan zone. Scales down safely on shorter images.
  const ZONE_SIZE = Math.min(600, Math.floor(H * 0.40));
  // v29: classification uses a NARROWER, edge-anchored sub-window than the
  // full scan zone. ZONE_SIZE (up to 600px) is sized so the landmark scan
  // can reach trailing multi-line captions — but using that same wide
  // window to compute the light/dark median dilutes it with real dark
  // photo content once the zone reaches that deep (dark, low-key flat-lay
  // photography especially). That can flip a genuinely light header or
  // margin to a "dark UI" read, which hides it from the brightness-based
  // chrome test entirely (it only looks for near-black rows, never
  // near-white, under a dark classification). Real chrome backgrounds are
  // almost always concentrated right at the edge, so a tighter window stays
  // representative of the chrome itself instead of the photo beyond it.
  // LANDMARK_ZONE (below) is intentionally left wide/unchanged.
  const CLASSIFY_ZONE = Math.min(200, ZONE_SIZE);
  const topZoneMedian = median(rowLum.slice(0, CLASSIFY_ZONE));
  const bottomZoneMedian = median(rowLum.slice(H - CLASSIFY_ZONE));
  const isLightUITop = topZoneMedian > 128;
  const isLightUIBottom = bottomZoneMedian > 128;
  function zoneUI(y) {
    if (y < ZONE_SIZE) return isLightUITop;
    if (y >= H - ZONE_SIZE) return isLightUIBottom;
    return isLightUI;
  }
  function median(arr) {
    const s = Array.from(arr).sort((a, b) => a - b);
    return s[Math.floor(s.length / 2)];
  }

  // Chrome candidate + pure-black fast-path
  // Two tiers: "soft" chrome-like rows (run-length capped, so we don't eat
  // large smooth photo regions like sky/snow) and "strict uniform" rows
  // (near-flat, near-zero texture — these are blank UI margins/caption space
  // and almost never appear inside real photo content, so no run-length cap).
  // Thresholds tighten at higher `level` so Redetect can recover crops that
  // were too aggressive the first time.
  const softLumPad = level * 5, softStdPad = level * 5;
  const uniLumPad = level * 5, uniStdPad = level * 3;
  function isChromeCandidateRow(y) {
    if (rowLum[y] < 2 && rowStd[y] < 2) return true;
    const primary = zoneUI(y)
      ? (rowLum[y] > (210 + softLumPad) && rowStd[y] < Math.max(5, 35 - softStdPad))
      : (rowLum[y] < Math.max(5, 40 - softLumPad) && rowStd[y] < Math.max(5, 25 - softStdPad));
    if (primary) return true;
    // v31: see isStrictUniformRow below for the full rationale -- same
    // proximity-bounded opposite-branch fallback, needed HERE too since
    // this is the actual gate that decides which rows form a run at all.
    // isStrictUniformRow only ever runs on rows this function already let
    // through, so fixing it alone (without this) would have had zero effect.
    const PROXIMITY = 300;
    const nearEdge = y < PROXIMITY || y >= H - PROXIMITY;
    if (!nearEdge) return false;
    return zoneUI(y)
      ? (rowLum[y] < Math.max(5, 40 - softLumPad) && rowStd[y] < Math.max(5, 25 - softStdPad))
      : (rowLum[y] > (210 + softLumPad) && rowStd[y] < Math.max(5, 35 - softStdPad));
  }
  function isStrictUniformRow(y) {
    const primary = zoneUI(y)
      ? (rowLum[y] > (210 + uniLumPad) && rowStd[y] < Math.max(3, 10 - uniStdPad))
      : (rowLum[y] < Math.max(3, 40 - uniLumPad) && rowStd[y] < Math.max(3, 10 - uniStdPad));
    if (primary) return true;
    // v31: within a bounded distance of the true top/bottom edge, also allow
    // the OPPOSITE branch. A header is often a sequence of differently-toned
    // bands stacked together (e.g. a dark-blue nav bar directly followed by
    // a white avatar/username strip) — zoneUI() picks ONE classification for
    // the whole edge zone from a median, so whichever band is taller wins
    // and the other band's own rows can fail the chrome test outright (a
    // "dark" verdict only ever checks for near-black, never near-white).
    // Confirmed on two real source images this way: a blue-nav+white-avatar
    // IG header, and a separate white UI margin sitting below dark photo
    // content near the bottom edge.
    //
    // Deliberately NOT applied image-wide: real dark, low-key photo content
    // starting immediately after a light header (confirmed on a separate
    // real source image) can be just as flat/uniform as genuine chrome, and
    // without a boundary between "header" and "photo", the two fuse into
    // one run and swallow real photo content. Restricting this to a fixed
    // distance from the physical edge keeps it useful for multi-band
    // headers/footers while stopping it from reaching into photo interior.
    const PROXIMITY = 300;
    const nearEdge = y < PROXIMITY || y >= H - PROXIMITY;
    if (!nearEdge) return false;
    return zoneUI(y)
      ? (rowLum[y] < Math.max(3, 40 - uniLumPad) && rowStd[y] < Math.max(3, 10 - uniStdPad))
      : (rowLum[y] > (210 + uniLumPad) && rowStd[y] < Math.max(3, 10 - uniStdPad));
  }
  function isPureBlackRow(y) { return rowLum[y] < 2 && rowStd[y] < 2; }

  const MAX_CHROME_RUN = Math.max(40, 130 - level * 20);
  // v30: a few-pixel-thin uniform patch (e.g. a small dark shadow pocket in
  // a low-key photo) can pass the same flat/uniform test a real chrome
  // block does, and just being marked as chrome is enough for it to act as
  // a hard split point later — silently cutting a big real content run in
  // half and discarding the smaller piece. Confirmed on a real source
  // image: a single 3px dark blip split one continuous 1400px-tall photo
  // into two runs, and the scorer kept only the larger one, losing ~300px
  // of real photo. Genuine chrome blocks in this dataset are consistently
  // well above this floor (the shortest confirmed real one is ~16px), so a
  // 12px minimum only screens out sub-pixel noise, not real UI.
  const MIN_FLAT_RUN = 12;
  const EDGE_DENSITY_THRESH = 0.025; // above this, treat the run as real texture, not flat chrome
  const rowRuns = [];
  let inRun = false, rStart = 0;
  for (let y = 0; y <= H; y++) {
    const cand = y < H && isChromeCandidateRow(y);
    if (cand && !inRun) { rStart = y; inRun = true; }
    else if (!cand && inRun) { rowRuns.push([rStart, y]); inRun = false; }
  }
  // v34: internal-jump run splitting. A run can merge two unrelated things
  // (e.g. a photo's dark vignette pixel-adjacent to a bright margin) with zero
  // gap between them. A real single-row luminance jump >100 never occurs inside
  // smooth photo gradients (validated: all legitimate runs max <=36; bug case
  // is 255), so it's a safe signal to split the run there. Split fragments are
  // held to stricter "must touch image edge" standard (same as pure-black
  // fast-path), not the general uniform/flat-run trust.
  const SPLIT_JUMP_THRESH = 100;
  function findInternalSplits(s, e) {
    const splits = [];
    for (let y = s + 1; y < e; y++) {
      if (Math.abs(rowLum[y] - rowLum[y - 1]) > SPLIT_JUMP_THRESH) splits.push(y);
    }
    return splits;
  }
  // Same signal used again: landmark-band bridging also assumes a contiguous
  // run of matching rows is one coherent thing. A hard jump means that
  // assumption is false here too, so no band-merging should bridge across one.
  const hardJumpRow = new Uint8Array(H);
  for (let y = 1; y < H; y++) {
    if (Math.abs(rowLum[y] - rowLum[y - 1]) > SPLIT_JUMP_THRESH) hardJumpRow[y] = 1;
  }

  // Detect pure-black bars (letterbox/pillarbox) BEFORE the main chrome logic.
  // These are high-confidence chrome regions and should be marked immediately
  // so they don't get confused with legitimate dark image content.
  const pureBlackBars = detectPureBlackBars(data, W, H);

  const isActualChrome = new Uint8Array(H);
  // Mark rows from detected pure-black bars as chrome immediately
  if (pureBlackBars.topY !== undefined) {
    for (let y = 0; y < pureBlackBars.topY; y++) isActualChrome[y] = 1;
  }
  if (pureBlackBars.bottomY !== undefined) {
    for (let y = pureBlackBars.bottomY; y < H; y++) isActualChrome[y] = 1;
  }
  for (const [s, e] of rowRuns) {
    const splits = findInternalSplits(s, e);
    const bounds = [s, ...splits, e];
    const segments = [];
    for (let i = 0; i < bounds.length - 1; i++) segments.push([bounds[i], bounds[i + 1]]);
    const wasSplit = segments.length > 1;
    for (const [ss, ee] of segments) {
      const rLen = ee - ss;
      const allBlack = Array.from({length: rLen}, (_, i) => isPureBlackRow(ss+i)).every(Boolean);
      const touchesEdge = (ss === 0) || (ee === H);
      if (wasSplit) {
        // A fragment born from a split is inherently suspect — it was part of
        // an ambiguous merged blob. Only trust if it touches the image's true
        // physical edge (stricter bar: same as pure-black fast-path).
        if (!touchesEdge) continue;
        const allUniform = Array.from({length: rLen}, (_, i) => isStrictUniformRow(ss+i)).every(Boolean);
        if (allBlack || allUniform) for (let ry = ss; ry < ee; ry++) isActualChrome[ry] = 1;
        continue;
      }
      const allUniform = Array.from({length: rLen}, (_, i) => isStrictUniformRow(ss+i)).every(Boolean);
      let avgEdgeDensity = 0;
      for (let ry = ss; ry < ee; ry++) avgEdgeDensity += rowEdgeDensity[ry];
      avgEdgeDensity /= rLen;
      const looksFlat = avgEdgeDensity < EDGE_DENSITY_THRESH;
      if (allBlack && touchesEdge) for (let ry = ss; ry < ee; ry++) isActualChrome[ry] = 1;
      else if (allUniform || (rLen >= MIN_FLAT_RUN && rLen <= MAX_CHROME_RUN && looksFlat))
        for (let ry = ss; ry < ee; ry++) isActualChrome[ry] = 1;
    }
  }

  // Landmark rows: icon rows (heart/comment/share) and username/avatar strips
  // look like a short band with a few small isolated high-contrast clusters on a
  // very bright or very dark background. This signature catches headers the
  // brightness pass misses — e.g. the blue "PHOTO" nav bar on old IG (medium
  // luminance, falls between the dark and light thresholds), or white username
  // strips on images classified as dark-UI.
  //
  // CRITICAL: the row's average luminance must be at an extreme — very bright
  // (>215, white UI background) or very dark (<85, dark status/nav bar). This
  // gate excludes medium-luminance photo content that can coincidentally produce
  // a sparse cluster count: colored skies, skin tones, lightsaber beams,
  // purple/grey clouds. Without it, the pass fires inside real photo content
  // and fragments it into wrong crop regions.
  const rowClusterCount = new Int16Array(H);
  for (let y = 0; y < H; y++) {
    let clusters = 0, inEdge = false;
    let prevL = 0.299*data[y*W*4]+0.587*data[y*W*4+1]+0.114*data[y*W*4+2];
    for (let x = 1; x < W; x++) {
      const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      const isEdge = Math.abs(l - prevL) > EDGE_THRESH;
      if (isEdge && !inEdge) clusters++;
      inEdge = isEdge;
      prevL = l;
    }
    rowClusterCount[y] = clusters;
  }
  // A profile-picture thumbnail sitting next to a username can make that
  // row look too textured/clustered to pass the landmark test on its own.
  // If one's detected, landmark evaluation ignores its columns entirely —
  // the avatar is chrome being cut out, not evidence of real photo content.
  const avatarZone = detectAvatarZone(data, W, H);
  const AVATAR_ROW_PAD = 4;
  // v33: action-row icon glyphs (heart/comment/share) sit at extreme
  // luminance same as any other landmark row, but their glyph edges push
  // cluster count to 15-23 on real screenshots - past the generic cap of 16.
  // Real dark-photo interior that coincidentally matches the landmark
  // luminance test stays low-cluster (0-2 observed), so raising the upper
  // bound doesn't create new risk for that failure class on its own - but
  // only do it within a proximity window of the true bottom edge, and keep
  // it tight (180px, vs. real icon rows measured 22-153px out). A wider
  // window was tried and tested against real screenshots first: it let
  // scattered dark-vignette-region matches further out bridge together with
  // the icon row into one oversized band that MAX_LANDMARK_BAND then
  // rejected, silently undoing the fix. 180px stays clear of that.
  const ACTION_ROW_CLUSTER_CAP = 30;
  const LANDMARK_PROXIMITY = 180;
  function landmarkClusterCap(y) {
    return (y >= H - LANDMARK_PROXIMITY) ? ACTION_ROW_CLUSTER_CAP : 16;
  }
  function isLandmarkRow(y) {
    // Try the row as-is first. Most rows are unaffected by the avatar and
    // this is what already worked before the avatar exclusion existed.
    const raw = { lum: rowLum[y], clusters: rowClusterCount[y], edgeDensity: rowEdgeDensity[y] };
    if (raw.lum > 215 || raw.lum < 85) {
      // v34: cluster floor for action-row fallback. A flat row (cl=0) cannot
      // be a real icon glyph — glyphs always produce at least one edge cluster.
      // This constraint only applies to the nearBottomEdge action-row fallback
      // path; general landmark detection still requires cl >= 2.
      const nearBottom = y >= H - LANDMARK_PROXIMITY;
      const minClusters = nearBottom ? 1 : 2;
      if (raw.clusters >= minClusters && raw.clusters <= landmarkClusterCap(y) && raw.edgeDensity < 0.2) return true;
    }
    // Only fall back to avatar-excluded stats as a rescue for a row that
    // failed on its own — this can only ADD a landmark match, never remove
    // one a row would have gotten anyway. That matters because the avatar
    // column-exclusion search can occasionally overshoot into real
    // adjacent content (e.g. the very start of a username label), and if
    // used unconditionally that overshoot can wipe out the cluster signal
    // that made a genuine landmark row detectable in the first place.
    if (avatarZone && y >= avatarZone.rowStart - AVATAR_ROW_PAD && y <= avatarZone.rowEnd + AVATAR_ROW_PAD) {
      const x0 = avatarZone.colEnd;
      if (x0 >= W - 5) return false;
      let sum = 0;
      for (let x = x0; x < W; x++) { const i=(y*W+x)*4; sum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; }
      const lum = sum / (W - x0);
      let clusters = 0, inEdge = false, edges = 0;
      let prevL = 0.299*data[(y*W+x0)*4]+0.587*data[(y*W+x0)*4+1]+0.114*data[(y*W+x0)*4+2];
      for (let x = x0 + 1; x < W; x++) {
        const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
        const isEdge = Math.abs(l - prevL) > EDGE_THRESH;
        if (isEdge && !inEdge) clusters++;
        if (isEdge) edges++;
        inEdge = isEdge;
        prevL = l;
      }
      const edgeDensity = edges / (W - x0 - 1);
      const extremeLum = lum > 215 || lum < 85;
      return extremeLum && clusters >= 2 && clusters <= 16 && edgeDensity < 0.2;
    }
    return false;
  }
  // Only scan the top/bottom edge zones — UI chrome always lives near image edges.
  // Photo content in the image interior is never scanned, even if it passes the
  // luminance and cluster criteria (e.g. snow against black sky near the top of
  // the photo area would be outside this zone).
  // v28: shares ZONE_SIZE with the classification fix above (was a separate
  // flat 250px cap) — the landmark scan needs to reach the same real chrome
  // the classification fix now correctly identifies as light/dark.
  const LANDMARK_ZONE = ZONE_SIZE;
  const LANDMARK_GAP = 10;       // bridge small row gaps within a real UI band
  const MIN_LANDMARK_BAND = 8;   // single coincidental rows are noise
  // v28: raised from 90. With the cluster cap raised above, an icon row can
  // now bridge with immediately-adjacent chrome (e.g. a tab bar right below
  // it, no caption in between) into one longer band. Safe to raise because
  // the edge-anchor check below (not this cap) is what actually protects
  // against false positives now — this cap is a secondary sanity check, not
  // the primary defense.
  const MAX_LANDMARK_BAND = 150;
  const lmMatches = [];
  for (let y = 0; y < H; y++) {
    if ((y < LANDMARK_ZONE || y >= H - LANDMARK_ZONE) && !isActualChrome[y] && isLandmarkRow(y))
      lmMatches.push(y);
  }
  const lmBands = [];
  if (lmMatches.length) {
    let bs = lmMatches[0], prev = lmMatches[0];
    for (let i = 1; i < lmMatches.length; i++) {
      // v34: internal hard jumps must also block landmark-band bridging.
      // The hardJumpRow signal partitions rows into separate regions; a run
      // that was split by that signal shouldn't bridge its fragments back
      // together in a separate landmark-band-grouping pass.
      let bridgeable = (lmMatches[i] - prev <= LANDMARK_GAP);
      if (bridgeable) {
        for (let y = prev + 1; y <= lmMatches[i]; y++) {
          if (hardJumpRow[y]) { bridgeable = false; break; }
        }
      }
      if (bridgeable) { prev = lmMatches[i]; }
      else { lmBands.push([bs, prev]); bs = lmMatches[i]; prev = lmMatches[i]; }
    }
    lmBands.push([bs, prev]);
  }
  // v33: a band only gets edge-anchor gap tolerance (below) if it actually
  // needed the relaxed cluster cap to qualify - i.e. it's a genuine
  // action-row candidate. Every other band (username rows, headers, and
  // critically the dark-photo-vignette false-positive documented in the
  // v32/v33 handoff) keeps the exact original zero-tolerance behavior,
  // unchanged from before this fix.
  function bandNeedsRelaxedCap(s, e) {
    for (let y = s; y <= e; y++) {
      if (rowClusterCount[y] > 16 && rowClusterCount[y] <= landmarkClusterCap(y)) return true;
    }
    return false;
  }
  // ── HARD LANDMARK BOUNDS (Track 1, extended in v28) ──
  // A landmark band in the top zone (username/profile row, plus anything
  // above it — status bar, colored nav bar) sets a hard floor: nothing
  // above it can ever end up in the crop, no matter what the luminance test
  // makes of the colors in between. A landmark band in the bottom zone
  // (icon row) sets a hard ceiling the same way. This is what lets an
  // ambiguous medium-luminance bar (e.g. an old-style blue IG nav bar) get
  // excluded correctly without the luminance test ever needing to classify
  // its color — position relative to a confirmed landmark decides it.
  // Luminance detection still refines the crop inward from these bounds;
  // it just can never push back outward past them.
  //
  // v27 trusted top-zone bands directly, on the theory that a screenshot's
  // header always precedes its photo. That held until the zone was widened
  // (v28, to reach multi-line captions) — the wider scan can now reach real
  // dark, low-texture photo content near the top (shadow, negative space)
  // that coincidentally matches the landmark signature, the same failure
  // mode already documented for the bottom zone. So v28 applies the SAME
  // edge-anchor safeguard to both edges, symmetrically: real UI chrome
  // connects unbroken to the image's physical edge (top OR bottom); a
  // landmark-shaped band that doesn't is floating inside the photo, not
  // bordering the frame, and is untrustworthy regardless of which edge
  // it's near.
  //
  // v28 also gates the SOFT chrome-marking on this same anchor check, not
  // just the hard-bound decision — an unanchored band left soft-marked as
  // chrome still fragments real photo content into pieces, and the run-
  // scorer can silently keep one fragment and discard the rest. Bands are
  // processed edge-inward, one at a time, so a band can only anchor through
  // chrome/bands already confirmed closer to the edge; processing stops at
  // the first unanchored band since nothing past it can connect either.
  // v33: previously required every single row between the band and the true
  // edge to already be isActualChrome, zero tolerance. A single 1px noise
  // row (unrelated to the icons themselves - just a stray luminance/std dip
  // in an otherwise flat white margin) was enough to break the chain
  // entirely, so a correctly-detected icon band still never earned
  // hard-bound trust. `tolerant` is only ever passed true for bands tagged
  // by bandNeedsRelaxedCap() above, so this can't change behavior for any
  // band this fix isn't targeting.
  const EDGE_ANCHOR_GAP_TOLERANCE = 3;
  function reachesBottomEdge(bandEnd, tolerant) {
    const limit = tolerant ? EDGE_ANCHOR_GAP_TOLERANCE : 0;
    let gap = 0;
    for (let y = bandEnd + 1; y < H; y++) {
      if (!isActualChrome[y]) { if (++gap > limit) return false; }
      else gap = 0;
    }
    return true;
  }
  function reachesTopEdge(bandStart, tolerant) {
    const limit = tolerant ? EDGE_ANCHOR_GAP_TOLERANCE : 0;
    let gap = 0;
    for (let y = 0; y < bandStart; y++) {
      if (!isActualChrome[y]) { if (++gap > limit) return false; }
      else gap = 0;
    }
    return true;
  }
  const topBands = lmBands.filter(([s]) => s < H / 2).sort((a, b) => a[0] - b[0]);
  const bottomBands = lmBands.filter(([s]) => s >= H / 2).sort((a, b) => b[1] - a[1]);
  let hardMinY = null, hardMaxY = null;

  // v29: run tab-bar detection BEFORE the bottom landmark loop below and
  // mark it as chrome immediately. The existing icon-row landmark band
  // (heart/comment/share) already passes its own test fine on its own —
  // what breaks it is reachesBottomEdge() failing because the tab bar
  // between it and the true edge was previously unmarked. Marking the tab
  // bar first lets that existing edge-anchor logic chain through it
  // unchanged, rather than needing a separate bespoke bound here.
  // v30: run nav-bar detection BEFORE the top landmark loop below, same
  // pattern as the tab-bar detector above. A medium-luminance header (e.g.
  // a dark-ish blue "MOST RECENT / #ART" nav bar) can leave scattered,
  // disconnected landmark matches that never chain back to row 0 on their
  // own. Marking the bar's own rows as chrome first gives the existing
  // reachesTopEdge() chain an unclaimed top edge to connect to, the same
  // way the tab-bar fix gives reachesBottomEdge() a connected bottom edge.
  const navBarBottom = detectNavBar(data, W, H);
  if (navBarBottom !== null) {
    for (let y = 0; y <= navBarBottom; y++) isActualChrome[y] = 1;
    hardMinY = navBarBottom + 1;
  }

  const tabBarTop = detectTabBar(data, W, H);
  if (tabBarTop !== null) {
    for (let y = tabBarTop; y < H; y++) isActualChrome[y] = 1;
    hardMaxY = tabBarTop - 1;
  }

  for (const [s, e] of topBands) {
    const bLen = e - s + 1;
    if (bLen < MIN_LANDMARK_BAND || bLen > MAX_LANDMARK_BAND) continue;
    if (!reachesTopEdge(s, bandNeedsRelaxedCap(s, e))) break;
    for (let ry = s; ry <= e; ry++) isActualChrome[ry] = 1;
    hardMinY = hardMinY === null ? e + 1 : Math.max(hardMinY, e + 1);
  }
  for (const [s, e] of bottomBands) {
    const bLen = e - s + 1;
    if (bLen < MIN_LANDMARK_BAND || bLen > MAX_LANDMARK_BAND) continue;
    if (!reachesBottomEdge(e, bandNeedsRelaxedCap(s, e))) break;
    for (let ry = s; ry <= e; ry++) isActualChrome[ry] = 1;
    hardMaxY = hardMaxY === null ? s - 1 : Math.min(hardMaxY, s - 1);
  }
  // A username-row landmark band only captures rows that pass the strict
  // cluster/edge signature — usually just the text's own height. But a
  // circular avatar next to that text is taller than the text line itself
  // and extends further down past the text's baseline, still as part of
  // the same header. If a landmark band ended inside (or above) the
  // avatar's own extent, the avatar's bottom edge is the real floor.
  if (avatarZone) {
    const avatarFloor = avatarZone.rowEnd + 1;
    hardMinY = hardMinY === null ? avatarFloor : Math.max(hardMinY, avatarFloor);
  }
  // Safety guard: if the bounds end up inverted (unusual image shape, or a
  // false-positive landmark), ignore both rather than forcing a nonsense
  // crop — falls back to the pre-Track-1 whole-image behavior for this image.
  if (hardMinY !== null && hardMaxY !== null && hardMinY >= hardMaxY) { hardMinY = null; hardMaxY = null; }
  if (hardMinY !== null) for (let y = 0; y < hardMinY; y++) isActualChrome[y] = 1;
  if (hardMaxY !== null) for (let y = hardMaxY + 1; y < H; y++) isActualChrome[y] = 1;

  // Kept as a secondary tie-breaker between whatever content blocks remain
  // after the landmark pass above already carved out header/icon rows —
  // still useful when a landmark row is too faint/short to fully qualify on
  // its own but a neighboring block boundary should still be preferred.
  function hasLandmarkRow(yFrom, yTo) {
    const lo = Math.max(0, Math.min(yFrom, yTo)), hi = Math.min(H, Math.max(yFrom, yTo));
    for (let y = lo; y < hi; y++) if (isLandmarkRow(y)) return true;
    return false;
  }
  const LANDMARK_BAND = 80, LANDMARK_BONUS = 120;

  // Largest content block — score by length, with a bonus for being
  // bordered by an icon-row/header-row landmark just outside it.
  const candidateRuns = [];
  let runStart = -1;
  for (let y = 0; y < H; y++) {
    if (!isActualChrome[y]) { if (runStart === -1) runStart = y; }
    else { if (runStart !== -1) { candidateRuns.push([runStart, y-1]); runStart = -1; } }
  }
  if (runStart !== -1) candidateRuns.push([runStart, H-1]);

  let bestStart = 0, bestEnd = H-1, bestScore = -1;
  for (const [s, e] of candidateRuns) {
    const len = e - s + 1;
    let score = len;
    if (hasLandmarkRow(Math.max(0, s - LANDMARK_BAND), s)) score += LANDMARK_BONUS;
    if (hasLandmarkRow(e + 1, Math.min(H, e + 1 + LANDMARK_BAND))) score += LANDMARK_BONUS;
    if (score > bestScore) { bestScore = score; bestStart = s; bestEnd = e; }
  }
  const bestLen = bestEnd - bestStart + 1;
  if (bestLen < 40) return null;

  // Column detection within photo row block
  const colLum = new Float32Array(W);
  const colStd = new Float32Array(W);
  const colEdgeDensity = new Float32Array(W);
  for (let x = 0; x < W; x++) {
    let sum = 0, count = bestEnd - bestStart + 1;
    for (let y = bestStart; y <= bestEnd; y++) { const i=(y*W+x)*4; sum+=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; }
    colLum[x] = sum / count;
    let v = 0;
    for (let y = bestStart; y <= bestEnd; y++) { const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; v+=(l-colLum[x])**2; }
    colStd[x] = Math.sqrt(v / count);
  }
  for (let x = 0; x < W; x++) {
    let edges = 0;
    let prevL = 0.299*data[(bestStart*W+x)*4]+0.587*data[(bestStart*W+x)*4+1]+0.114*data[(bestStart*W+x)*4+2];
    for (let y = bestStart + 1; y <= bestEnd; y++) {
      const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      if (Math.abs(l - prevL) > EDGE_THRESH) edges++;
      prevL = l;
    }
    colEdgeDensity[x] = edges / Math.max(1, (bestEnd - bestStart));
  }

  function isChromeCandidateCol(x) {
    if (colLum[x] < 2 && colStd[x] < 2) return true;
    if (isLightUI) return colLum[x] > (240 + softLumPad) && colStd[x] < Math.max(5, 20 - softStdPad);
    else           return colLum[x] < Math.max(5, 15 - softLumPad) && colStd[x] < Math.max(5, 20 - softStdPad);
  }
  function isStrictUniformCol(x) {
    if (isLightUI) return colLum[x] > (240 + uniLumPad) && colStd[x] < Math.max(3, 10 - uniStdPad);
    else            return colLum[x] < Math.max(3, 15 - uniLumPad) && colStd[x] < Math.max(3, 10 - uniStdPad);
  }
  function isPureBlackCol(x) { return colLum[x] < 2 && colStd[x] < 2; }

  const colRuns = [];
  let inColRun = false, cStart = 0;
  for (let x = 0; x <= W; x++) {
    const cand = x < W && isChromeCandidateCol(x);
    if (cand && !inColRun) { cStart = x; inColRun = true; }
    else if (!cand && inColRun) { colRuns.push([cStart, x]); inColRun = false; }
  }
  const isActualChromeCol = new Uint8Array(W);
  // Mark columns from detected pure-black bars as chrome immediately
  if (pureBlackBars.leftX !== undefined) {
    for (let x = 0; x < pureBlackBars.leftX; x++) isActualChromeCol[x] = 1;
  }
  if (pureBlackBars.rightX !== undefined) {
    for (let x = pureBlackBars.rightX; x < W; x++) isActualChromeCol[x] = 1;
  }
  for (const [s, e] of colRuns) {
    const cLen = e - s;
    const allBlack = Array.from({length: cLen}, (_, i) => isPureBlackCol(s+i)).every(Boolean);
    const allUniform = Array.from({length: cLen}, (_, i) => isStrictUniformCol(s+i)).every(Boolean);
    let avgEdgeDensity = 0;
    for (let cx = s; cx < e; cx++) avgEdgeDensity += colEdgeDensity[cx];
    avgEdgeDensity /= cLen;
    const looksFlat = avgEdgeDensity < EDGE_DENSITY_THRESH;
    if ((cLen <= MAX_CHROME_RUN && looksFlat) || allBlack || allUniform)
      for (let cx = s; cx < e; cx++) isActualChromeCol[cx] = 1;
  }

  let colStart = 0, colEnd = W-1;
  for (let x = 0; x < W; x++) { if (!isActualChromeCol[x]) { colStart = x; break; } }
  for (let x = W-1; x >= 0; x--) { if (!isActualChromeCol[x]) { colEnd = x; break; } }

  // Smart padding — stop at chrome boundaries
  function isChromeLum(l) {
    if (l < 2) return true;
    if (isLightUI) return l > 210;
    else return l < 40;
  }
  // v28: row padding walks right along the top/bottom edge of the selected
  // block — exactly where the zone-aware classification applies. Using the
  // global isChromeLum() here could let padding walk 1-2px back into real
  // chrome the zone fix just correctly identified (this was the "white
  // sliver at the crop edge" symptom). Column padding is unchanged/out of
  // scope — left/right chrome bars weren't part of what broke.
  function isChromeLumRow(l, y) {
    if (l < 2) return true;
    if (zoneUI(y)) return l > 210;
    else return l < 40;
  }
  // Padding respects both luminance-chrome and the isActualChrome array from
  // the brightness/landmark passes. Without the chrome-array check, padding
  // walks back through bright white UI rows (username strips on dark-UI images)
  // that the landmark pass marked as chrome but isChromeLum() misses because
  // the dark-UI threshold only looks for lum<40, not bright whites.
  let minY = bestStart;
  for (let s = 0; s < PADDING && minY > 0; s++) { if (!isChromeLumRow(rowLum[minY-1], minY-1) && !isActualChrome[minY-1]) minY--; else break; }
  let maxY = bestEnd + 1;
  for (let s = 0; s < PADDING && maxY < H; s++) { if (!isChromeLumRow(rowLum[maxY], maxY) && !isActualChrome[maxY]) maxY++; else break; }
  let minX = colStart;
  for (let s = 0; s < PADDING && minX > 0; s++) { if (!isChromeLum(colLum[minX-1])) minX--; else break; }
  let maxX = colEnd + 1;
  for (let s = 0; s < PADDING && maxX < W; s++) { if (!isChromeLum(colLum[maxX])) maxX++; else break; }

  const w = maxX - minX, h = maxY - minY;
  if (w >= W * 0.97 && h >= H * 0.97) return null;
  if (w < 40 || h < 40) return null;

  // Sanity check: pure UI (no photo content)
  // Brightness alone isn't enough — a real photo can be legitimately bright
  // (overexposed/high-key shots, white backgrounds, snow, bright interiors)
  // and shouldn't be thrown out just because most of its pixels are light.
  // What actually distinguishes blank UI from a bright photo is texture:
  // flat UI has near-zero local contrast, real photo content (even washed
  // out) still has grain/edges. So only reject as "pure UI" when the block
  // is BOTH mostly bright AND mostly flat (low edge density) — matching the
  // same texture signal used elsewhere in detection rather than brightness
  // in isolation.
  let brightPx = 0, totalPx = 0, sampleEdges = 0, sampleComparisons = 0;
  for (let cy = bestStart; cy <= bestEnd; cy += 4) {
    let prevL = null;
    for (let cx = minX; cx < maxX; cx += 4) {
      const i=(cy*W+cx)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2];
      if (l > 200) brightPx++; totalPx++;
      if (prevL !== null) { if (Math.abs(l - prevL) > EDGE_THRESH) sampleEdges++; sampleComparisons++; }
      prevL = l;
    }
  }
  const sanityEdgeDensity = sampleComparisons > 0 ? sampleEdges / sampleComparisons : 0;
  const SANITY_EDGE_THRESH = 0.02; // reject only if near-zero texture (blank UI); real bright photos have >=0.03
  if (brightPx / totalPx > 0.75 && sanityEdgeDensity < SANITY_EDGE_THRESH) return null;

  // ── CONFIDENCE SCORE ──
  // Factors: how cleanly do chrome rows separate from content rows?
  // 1. Edge sharpness: luminance jump at the crop boundaries
  // 2. Chrome run proportion: what % of stripped rows were short clean runs vs borderline
  // 3. Crop coverage: how much of the image was cropped (more = more confident the chrome was obvious)
  let score = 1.0;

  // Factor 1: edge sharpness (luminance delta at top and bottom boundary)
  const topDelta = Math.abs(rowLum[bestStart] - (bestStart > 0 ? rowLum[bestStart-1] : rowLum[bestStart]));
  const botDelta = Math.abs(rowLum[bestEnd] - (bestEnd < H-1 ? rowLum[bestEnd+1] : rowLum[bestEnd]));
  const edgeSharpness = Math.min(1, (topDelta + botDelta) / 200); // 0–1
  score *= (0.4 + 0.6 * edgeSharpness);

  // Factor 2: how much was cropped (if barely anything cropped, detection was uncertain)
  const cropFraction = 1 - (w * h) / (W * H);
  if (cropFraction < 0.05) score *= 0.5; // barely cropped anything
  else if (cropFraction > 0.2) score = Math.min(1, score * 1.2); // clear chrome strip

  // Factor 3: bright pixel ratio of the content area (lower = photo, not UI)
  const brightRatio = brightPx / totalPx;
  if (brightRatio > 0.5) score *= 0.6;
  else if (brightRatio < 0.25) score = Math.min(1, score * 1.1);

  // Clamp and label
  score = Math.max(0, Math.min(1, score));
  const confidence = score > 0.65 ? 'high' : score > 0.35 ? 'medium' : 'low';

  // Map back to natural coordinates if the analysis ran on a downscaled
  // canvas (only happens past the iOS canvas pixel cap — see rasterize).
  if (scale < 1) {
    const inv = 1 / scale;
    return {
      box: {
        x: Math.round(minX * inv), y: Math.round(minY * inv),
        w: Math.round(w * inv), h: Math.round(h * inv)
      },
      confidence
    };
  }
  return { box: { x: minX, y: minY, w, h }, confidence };
}

// ── TIGHTEN (from CropStudio v34's tightenCrop, exposed as a pure function) ──
// Re-scans inward from the current crop edges and trims any remaining flat
// black/white border. Each level loosens the uniformity tolerance a bit, so
// repeated presses catch progressively subtler borders, converging on a
// tight crop without needing to re-run full detection from scratch.
export function tightenBox(img, cropBox, level) {
  const { data, W, H, scale } = rasterize(img);

  const stdThresh = 10 + level * 8;      // texture tolerance loosens each press
  const lumDarkThresh = 35 + level * 12; // "dark enough to be a black bar"
  const lumLightThresh = 220 - level * 12; // "light enough to be a white bar"

  function rowStats(y, x0, x1) {
    let sum = 0;
    for (let x = x0; x < x1; x++) { const i=(y*W+x)*4; sum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; }
    const mean = sum / (x1-x0);
    let v = 0;
    for (let x = x0; x < x1; x++) { const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; v += (l-mean)**2; }
    return { mean, std: Math.sqrt(v/(x1-x0)) };
  }
  function colStats(x, y0, y1) {
    let sum = 0;
    for (let y = y0; y < y1; y++) { const i=(y*W+x)*4; sum += 0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; }
    const mean = sum / (y1-y0);
    let v = 0;
    for (let y = y0; y < y1; y++) { const i=(y*W+x)*4; const l=0.299*data[i]+0.587*data[i+1]+0.114*data[i+2]; v += (l-mean)**2; }
    return { mean, std: Math.sqrt(v/(y1-y0)) };
  }
  function isUniform(s) {
    return s.std < stdThresh && (s.mean < lumDarkThresh || s.mean > lumLightThresh);
  }

  // Map the (natural-coordinate) box into the analysis canvas's space.
  let x = Math.round(cropBox.x * scale), y = Math.round(cropBox.y * scale);
  let w = Math.round(cropBox.w * scale), h = Math.round(cropBox.h * scale);
  x = Math.max(0, Math.min(W - 1, x)); y = Math.max(0, Math.min(H - 1, y));
  w = Math.max(1, Math.min(W - x, w)); h = Math.max(1, Math.min(H - y, h));

  const minSize = 30;
  const LOOKAHEAD = 12; // tolerate a thin transition zone (vignette, watermark) before giving up
  let changed = false;

  // Scans inward from one edge, trimming uniform border. If a non-uniform
  // pixel is hit, peeks up to LOOKAHEAD further for uniform border resuming
  // (e.g. past a thin gradient fade or small watermark) and jumps through
  // the gap rather than stopping dead at the first imperfect pixel.
  function scanTrim(maxCount, getStats) {
    let trimmed = 0;
    let i = 0;
    while (maxCount() - trimmed > minSize) {
      if (isUniform(getStats(i))) { i++; trimmed++; continue; }
      let found = -1;
      for (let k = 1; k <= LOOKAHEAD && maxCount() - trimmed - k > minSize; k++) {
        if (isUniform(getStats(i + k))) { found = k; break; }
      }
      if (found === -1) break;
      i += found + 1; trimmed += found + 1;
    }
    return trimmed;
  }

  let t;
  t = scanTrim(() => h, k => rowStats(y + k, x, x + w));         if (t) { y += t; h -= t; changed = true; }
  t = scanTrim(() => h, k => rowStats(y + h - 1 - k, x, x + w)); if (t) { h -= t; changed = true; }
  t = scanTrim(() => w, k => colStats(x + k, y, y + h));         if (t) { x += t; w -= t; changed = true; }
  t = scanTrim(() => w, k => colStats(x + w - 1 - k, y, y + h)); if (t) { w -= t; changed = true; }

  if (!changed) return { box: { ...cropBox }, changed: false };
  const inv = 1 / scale;
  return {
    box: {
      x: Math.round(x * inv), y: Math.round(y * inv),
      w: Math.round(w * inv), h: Math.round(h * inv)
    },
    changed: true
  };
}

// "This box wouldn't actually crop anything" — used to count approved-but-
// uncropped images as originals rather than sending a no-op to the server.
export function isFullImageBox(box, img) {
  return !box || (box.x === 0 && box.y === 0 && box.w === img.naturalWidth && box.h === img.naturalHeight);
}

export { detectCrop, PADDING };
