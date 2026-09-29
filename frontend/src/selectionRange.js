/**
 * V32 — shift-click range selection for the image grid.
 *
 * Pulled out of Home.jsx as a plain function so it can be tested directly
 * (see scripts/test_selection_range.mjs). CLAUDE.md's verification notes are
 * explicit that browser automation can't drive this kind of interaction
 * reliably, so the logic has to be reachable from code.
 *
 * The run is counted along the order the SERVER returned images in — the
 * `images` array — not the on-screen column layout. The grid is masonry:
 * each photo drops into whichever column is shortest at the time, so screen
 * position isn't a stable order to count along, and two photos that look
 * adjacent may be far apart in the results.
 *
 * @param {Array<{id:number}>} images  the current result list, in server order
 * @param {number|null} anchorId       the last tile clicked (start of the run)
 * @param {number} targetId            the tile just shift-clicked (end of the run)
 * @returns {number[]} ids in the run, inclusive of both ends; empty if no
 *                     valid range exists (either end missing, or same tile).
 */
export function rangeIdsBetween(images, anchorId, targetId) {
  if (anchorId == null || anchorId === targetId) return [];
  const from = images.findIndex(i => i.id === anchorId);
  const to = images.findIndex(i => i.id === targetId);
  // An anchor that's no longer in the results (a filter changed under it, or
  // the photo was deleted) means there's no run to draw. Fall back to a
  // plain toggle rather than guessing at a start point.
  if (from === -1 || to === -1) return [];
  const [lo, hi] = from < to ? [from, to] : [to, from];
  return images.slice(lo, hi + 1).map(i => i.id);
}

/**
 * Day 46 (V94) — box-select hit test for Select Mode's drag rectangle.
 *
 * Pulled out of Home's mouse-move handler for the same reason as
 * rangeIdsBetween above: browser automation can't reliably perform a drag,
 * and an off-by-one here (a tile that merely touches the rectangle's edge
 * counting as selected, or a leftward/upward drag missing tiles) still
 * "looks like it works" on screen. Tested by scripts/test_selection_range.mjs.
 *
 * The overlap rule is STRICT, exactly as it was inline: a tile whose edge
 * only touches the rectangle's edge is not selected.
 *
 * @param {Set<number>} baseSelected  the selection when the drag started —
 *                                    a drag only ever ADDS to it
 * @param {{left:number, top:number, right:number, bottom:number}} rect
 *                                    the drag rectangle, viewport coords
 * @param {Array<[number, {left:number, top:number, right:number, bottom:number}]>} tiles
 *                                    [image id, tile's on-screen box] for each tile
 * @returns {Set<number>} a NEW set: baseSelected plus every overlapping tile
 */
export function idsInDragRect(baseSelected, rect, tiles) {
  const next = new Set(baseSelected);
  for (const [id, r] of tiles) {
    const intersects = r.left < rect.right && r.right > rect.left && r.top < rect.bottom && r.bottom > rect.top;
    if (intersects) next.add(id);
  }
  return next;
}
