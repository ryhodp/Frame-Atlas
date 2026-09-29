/**
 * Frame Atlas — local test for V32 shift-click range selection.
 *
 * CLAUDE.md's verification notes say browser automation can't reliably fire
 * this kind of interaction, so the range maths lives in a plain function and
 * gets tested here instead of by driving a page.
 *
 * Usage (from the frame-atlas folder):
 *     node scripts/test_selection_range.mjs
 */

import { rangeIdsBetween, idsInDragRect } from '../frontend/src/selectionRange.js';

let failures = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label} — ${ok ? 'OK' : `FAIL  got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`}`);
  if (!ok) failures += 1;
}

// A results list in server order. Ids are deliberately NOT sequential — the
// grid's order is whatever the search returned, not id order.
const images = [{ id: 30 }, { id: 12 }, { id: 7 }, { id: 44 }, { id: 5 }, { id: 91 }];

console.log('Shift-click range selection:');
check('a forward run includes both ends',
  rangeIdsBetween(images, 12, 5), [12, 7, 44, 5]);
check('a backward run gives the same photos',
  rangeIdsBetween(images, 5, 12), [12, 7, 44, 5]);
check('the whole list end to end',
  rangeIdsBetween(images, 30, 91), [30, 12, 7, 44, 5, 91]);
check('two neighbours',
  rangeIdsBetween(images, 7, 44), [7, 44]);
check('no anchor yet (first click of the session) means no range',
  rangeIdsBetween(images, null, 44), []);
check('shift-clicking the anchor itself means no range',
  rangeIdsBetween(images, 44, 44), []);
check('an anchor no longer in the results means no range',
  rangeIdsBetween(images, 999, 44), []);
check('a target not in the results means no range',
  rangeIdsBetween(images, 12, 999), []);
check('an empty grid is handled',
  rangeIdsBetween([], 1, 2), []);

// The run must follow the array, not the numeric value of the ids — this is
// the whole reason it's computed from position.
check('range follows result order, not id order',
  rangeIdsBetween(images, 30, 7), [30, 12, 7]);

// ── Day 46 (V94): box-drag selection — idsInDragRect ────────────────────────
// Browser automation can't reliably perform the drag itself, so the "which
// tiles does the rectangle touch?" maths is tested here.
const box = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h });
// A 3x2 grid of 100x100 tiles with 10px gaps: ids 1 2 3 on top, 4 5 6 below.
const tiles = [[1, box(0, 0, 100, 100)], [2, box(110, 0, 100, 100)], [3, box(220, 0, 100, 100)],
               [4, box(0, 110, 100, 100)], [5, box(110, 110, 100, 100)], [6, box(220, 110, 100, 100)]];
const sorted = (set) => [...set].sort((a, b) => a - b);

check('a drag over the top-left tile selects just it',
  sorted(idsInDragRect(new Set(), box(10, 10, 20, 20), tiles)), [1]);
check('a drag across the gap between two tiles selects both',
  sorted(idsInDragRect(new Set(), box(50, 50, 100, 20), tiles)), [1, 2]);
check('a drag covering everything selects all six',
  sorted(idsInDragRect(new Set(), box(-5, -5, 400, 300), tiles)), [1, 2, 3, 4, 5, 6]);
check('a drag inside the gap only selects nothing',
  sorted(idsInDragRect(new Set(), box(101, 0, 8, 200), tiles)), []);
check('touching a tile edge exactly does NOT count (strict overlap, as before)',
  sorted(idsInDragRect(new Set(), box(100, 0, 10, 50), tiles)), []);
check('one pixel past the edge does count',
  sorted(idsInDragRect(new Set(), box(99, 0, 12, 50), tiles)), [1, 2]);
// A zero-size rectangle can't happen in the app (a drag only starts after the
// mouse moves DRAG_THRESHOLD = 4px), but the maths is pinned anyway: a point
// strictly inside a tile counts as touching it, a point in a gap touches nothing.
check('a zero-size rectangle inside a tile selects that tile',
  sorted(idsInDragRect(new Set(), box(50, 50, 0, 0), tiles)), [1]);
check('a zero-size rectangle in a gap selects nothing',
  sorted(idsInDragRect(new Set(), box(105, 50, 0, 0), tiles)), []);
check('a drag only ever ADDS to what was already selected',
  sorted(idsInDragRect(new Set([6]), box(10, 10, 20, 20), tiles)), [1, 6]);
const base = new Set([6]);
idsInDragRect(base, box(10, 10, 20, 20), tiles);
check('the starting selection itself is never modified (a NEW set is returned)', sorted(base), [6]);
check('no tiles on screen is handled', sorted(idsInDragRect(new Set([2]), box(0, 0, 50, 50), [])), [2]);

// Identical to the pre-Day-46 inline code (copied verbatim from Home.jsx's
// onGridMouseMove) on thousands of random rectangles and tile layouts.
function oldInline(baseSelected, left, top, width, height, tileRects) {
  const rectRight = left + width;
  const rectBottom = top + height;
  const next = new Set(baseSelected);
  tileRects.forEach((r, id) => {
    const intersects = r.left < rectRight && r.right > left && r.top < rectBottom && r.bottom > top;
    if (intersects) next.add(id);
  });
  return next;
}
let seed = 46;
const rnd = (n) => { seed = (seed * 1103515245 + 12345) % 2147483648; return Math.floor(seed / 2147483648 * n); };
let mismatches = 0;
for (let i = 0; i < 5000; i++) {
  const tileMap = new Map();
  for (let t = 0; t < rnd(12); t++) tileMap.set(t + 1, box(rnd(400), rnd(400), rnd(120), rnd(120)));
  const baseSel = new Set([...tileMap.keys()].filter(() => rnd(4) === 0));
  const [l, t, w, h] = [rnd(400), rnd(400), rnd(200), rnd(200)];
  const a = oldInline(baseSel, l, t, w, h, tileMap);
  const b = idsInDragRect(baseSel, { left: l, top: t, right: l + w, bottom: t + h }, [...tileMap]);
  if (JSON.stringify([...a]) !== JSON.stringify([...b])) mismatches++;
}
check('5,000 random drags: same photos, same order as the old inline code', mismatches, 0);

console.log();
if (failures) {
  console.log(`${failures} CHECK(S) FAILED`);
  process.exit(1);
}
console.log('ALL SHIFT-CLICK RANGE + BOX-DRAG TESTS PASSED');
