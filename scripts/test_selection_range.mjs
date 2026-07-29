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

import { rangeIdsBetween } from '../frontend/src/selectionRange.js';

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

console.log();
if (failures) {
  console.log(`${failures} CHECK(S) FAILED`);
  process.exit(1);
}
console.log('ALL SHIFT-CLICK RANGE TESTS PASSED');
