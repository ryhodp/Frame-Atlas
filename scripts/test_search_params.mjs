/**
 * Frame Atlas — local test for Day 44 (V92): frontend/src/searchParams.js.
 *
 * These are the parts of Home's search that can be silently wrong — a filter
 * quietly missing from the server request, or a bookmark restoring different
 * filters than it saved, still renders a normal-looking grid — so they're
 * plain functions tested here rather than by driving a page (same reasoning
 * as test_selection_range.mjs).
 *
 * Besides hand-written cases, the new functions are checked against a
 * VERBATIM copy of the pre-Day-44 inline logic from Home.jsx on thousands of
 * random filter states: the move must not change a single server request.
 *
 * Usage (from the frame-atlas folder):
 *     node scripts/test_search_params.mjs
 */

import {
  DEFAULT_PROM, DEFAULT_EXACT,
  hasActiveFilters, filterParams, bookmarkState, filtersFromBookmark,
} from '../frontend/src/searchParams.js';

let failures = 0;
const check = (label, cond, detail = '') => {
  console.log(`${cond ? '  ok  ' : '  FAIL'} ${label}${cond ? '' : '  ' + detail}`);
  if (!cond) failures++;
};

const EMPTY = { chips: [], nlChips: [], noteChips: [], color: null, film: null, ar: null,
                prom: DEFAULT_PROM, exact: DEFAULT_EXACT, promApplied: DEFAULT_PROM, exactApplied: DEFAULT_EXACT };

// ── Reference: the pre-Day-44 logic, copied verbatim from Home.jsx ─────────
function oldBuildFilterParams({ chips, nlChips, noteChips, color, promApplied, exactApplied, film, ar }) {
  const params = new URLSearchParams();
  if (chips.length) params.set('chips', chips.join(','));
  if (nlChips.length) params.set('nl', JSON.stringify(nlChips.map(n => n.tags)));
  if (noteChips.length) params.set('notes', JSON.stringify(noteChips));
  if (color) {
    params.set('color', color);
    params.set('prom', promApplied);
    params.set('exact', exactApplied);
  }
  if (film) params.set('film', film);
  if (ar) params.set('ar', ar);
  return params;
}
const oldHasFilters = ({ chips, nlChips, noteChips, color, film, ar }) =>
  chips.length > 0 || nlChips.length > 0 || noteChips.length > 0 || !!color || !!film || !!ar;
const oldSaveBody = ({ chips, nlChips, noteChips, color, film, ar, prom, exact }) =>
  ({ chips, nlChips, noteChips, color, film, ar, prom, exact });
function oldApply(state) {   // what applyBookmark() set each piece of state to
  return {
    chips: state.chips || [], nlChips: state.nlChips || [], noteChips: state.noteChips || [],
    color: state.color || null, film: state.film || null, ar: state.ar || null,
    prom: state.prom ?? DEFAULT_PROM, exact: state.exact ?? DEFAULT_EXACT,
  };
}

console.log('1. hasActiveFilters');
check('nothing set -> false', hasActiveFilters(EMPTY) === false);
for (const [k, v] of [['chips', ['night']], ['nlChips', [{ phrase: 'x', tags: ['a'] }]], ['noteChips', ['bounce']],
                      ['color', '#E08840'], ['film', 'Heat'], ['ar', '2.39:1']]) {
  check(`only ${k} set -> true`, hasActiveFilters({ ...EMPTY, [k]: v }) === true);
}
check('moving the colour sliders alone is NOT a filter',
      hasActiveFilters({ ...EMPTY, prom: 40, exact: 90, promApplied: 40, exactApplied: 90 }) === false);

console.log('2. filterParams');
check('empty -> no params', filterParams(EMPTY).toString() === '');
const full = { ...EMPTY, chips: ['night', 'lonely'], nlChips: [{ phrase: 'sad drive', tags: ['lonely', 'low-key'] }],
               noteChips: ['ceiling bounce'], color: '#E08840', prom: 99, exact: 1, promApplied: 12.5, exactApplied: 80,
               film: 'Michael Mann', ar: '2.39:1' };
const p = filterParams(full);
check('chips comma-joined', p.get('chips') === 'night,lonely');
check('describe-it sends only the tag groups', p.get('nl') === '[["lonely","low-key"]]');
check('notes as a JSON array', p.get('notes') === '["ceiling bounce"]');
check('colour sends the APPLIED knobs, never the live slider', p.get('prom') === '12.5' && p.get('exact') === '80');
check('film + ratio', p.get('film') === 'Michael Mann' && p.get('ar') === '2.39:1');
check('param order is stable', [...p.keys()].join() === 'chips,nl,notes,color,prom,exact,film,ar');
check('no colour -> no prom/exact at all', !filterParams({ ...full, color: null }).has('prom'));

console.log('3. bookmarks');
check('saved state keeps the exact key order the server stores',
      JSON.stringify(bookmarkState(full)) === JSON.stringify({ chips: full.chips, nlChips: full.nlChips, noteChips: full.noteChips,
        color: full.color, film: full.film, ar: full.ar, prom: 99, exact: 1 }));
check('saves the LIVE slider values (what the user sees)', bookmarkState(full).prom === 99);
const restored = filtersFromBookmark(JSON.parse(JSON.stringify(bookmarkState(full))));
check('save -> restore round-trips every filter', JSON.stringify(restored) ===
      JSON.stringify({ chips: full.chips, nlChips: full.nlChips, noteChips: full.noteChips, color: full.color,
                       film: full.film, ar: full.ar, prom: 99, exact: 1 }));
const pre24 = filtersFromBookmark({ chips: ['night'], color: '#ff0000' });
check('pre-V24 bookmark (no knobs) takes the defaults', pre24.prom === DEFAULT_PROM && pre24.exact === DEFAULT_EXACT);
check('pre-V39 bookmark (no noteChips) -> []', Array.isArray(pre24.noteChips) && pre24.noteChips.length === 0);
check('a saved knob of 0 is kept, not replaced by the default (?? not ||)',
      filtersFromBookmark({ exact: 0 }).exact === 0);
check('empty state object -> all defaults', JSON.stringify(filtersFromBookmark({})) === JSON.stringify(oldApply({})));

console.log('4. identical to the pre-Day-44 logic on random states');
let seed = 44;
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const pick = (arr) => arr[Math.floor(rnd() * arr.length)];
const some = (arr) => arr.filter(() => rnd() < 0.3);
const TAGS = ['night', 'lonely', 'low-key', 'car', 'rain & fog', 'a,b', 'ünïcode', '"quoted"'];
let mismatches = 0;
for (let i = 0; i < 5000; i++) {
  const st = {
    chips: some(TAGS), noteChips: some(['bounce', 'T2.8 & ND', 'x=y']),
    nlChips: some(TAGS).map(t => ({ phrase: `p-${t}`, tags: some(TAGS) })),
    color: rnd() < 0.4 ? pick(['#E08840', '#000000', '#abcdef']) : null,
    film: rnd() < 0.3 ? pick(['Heat', 'Yasujiro Ozu', 'Her & Him']) : null,
    ar: rnd() < 0.3 ? pick(['2.39:1', '9:16', '1:1']) : null,
    prom: pick([0.5, 6, 12.4, 95]), exact: pick([0, 60, 100]),
    promApplied: pick([0.5, 6, 12.4, 95]), exactApplied: pick([0, 60, 100]),
  };
  if (filterParams(st).toString() !== oldBuildFilterParams(st).toString()) mismatches++;
  if (hasActiveFilters(st) !== oldHasFilters(st)) mismatches++;
  if (JSON.stringify(bookmarkState(st)) !== JSON.stringify(oldSaveBody(st))) mismatches++;
  const saved = JSON.parse(JSON.stringify(Object.fromEntries(
    Object.entries(oldSaveBody(st)).filter(() => rnd() < 0.8))));   // older bookmarks miss fields
  if (JSON.stringify(filtersFromBookmark(saved)) !== JSON.stringify(oldApply(saved))) mismatches++;
}
check('5,000 random states: query, has-filters, saved JSON and restore all match the old code', mismatches === 0, `${mismatches} mismatches`);

console.log();
if (failures) { console.log(`${failures} check(s) FAILED`); process.exit(1); }
console.log('All search-params checks passed.');
