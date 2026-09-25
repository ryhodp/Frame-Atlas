// searchParams.js — the pure, testable half of Home's search (Day 44 / V92).
//
// These are the parts of search that can be silently WRONG rather than
// visibly broken: a filter that quietly drops out of the server request, or a
// bookmark that restores slightly different filters than it saved, still
// renders a perfectly normal-looking grid. So they live here as plain
// functions with no React in them, pinned by scripts/test_search_params.mjs
// (run by CI on every push) — same reasoning as selectionRange.js (V32) and
// presentationOrder.js (V41).
//
// hooks/useSearch.js is the only caller.

// V24 color search. Keep these in step with DEFAULT_PROMINENCE /
// DEFAULT_EXACTNESS in backend/colors.py.
export const DEFAULT_PROM = 6;    // percent of frame
export const DEFAULT_EXACT = 60;  // 0 = any nearby hue, 100 = near-identical hue

// True when any filter is on. With none, the grid is the default shuffled
// browse view (the server gets a `seed` instead of filters).
export function hasActiveFilters({ chips, nlChips, noteChips, color, film, ar }) {
  return chips.length > 0 || nlChips.length > 0 || noteChips.length > 0 || !!color || !!film || !!ar;
}

// ── The active filter, as query params ─────────────────────────────────────
// One place builds this. The grid, the "select all N results" button and the
// tag-removal preview all ask the server the SAME question, and if each one
// assembled its own params they would drift — a select-all that grabs a
// different set of photos than the grid is showing would be worse than
// having no select-all at all.
//
// The colour knobs sent are the *Applied* values (the ones that trail the
// sliders by a beat), never the live slider positions.
export function filterParams({ chips, nlChips, noteChips, color, promApplied, exactApplied, film, ar }) {
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

// What a saved bookmark stores. Key order is part of the contract: it's the
// JSON the server keeps, byte-for-byte what Home sent before Day 44.
export function bookmarkState({ chips, nlChips, noteChips, color, film, ar, prom, exact }) {
  return { chips, nlChips, noteChips, color, film, ar, prom, exact };
}

// The filters a saved bookmark restores. Every field is optional because
// bookmarks from older versions lack newer ones: pre-V39 has no noteChips,
// pre-V24 has no prom/exact — those take today's defaults, so an old
// bookmark comes back tighter (and cleaner) than when it was saved.
export function filtersFromBookmark(state) {
  const s = state || {};
  return {
    chips: s.chips || [],
    nlChips: s.nlChips || [],
    noteChips: s.noteChips || [],
    color: s.color || null,
    film: s.film || null,
    ar: s.ar || null,
    prom: s.prom ?? DEFAULT_PROM,
    exact: s.exact ?? DEFAULT_EXACT,
  };
}
