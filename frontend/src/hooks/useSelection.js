import { useState, useEffect, useRef, useCallback } from 'react';
import { rangeIdsBetween, idsInDragRect } from '../selectionRange';

// useSelection — everything about Select Mode (was "Tag Mode"): which photos
// are selected and how you select them (Day 46 / V94).
//
// Owns: whether Select Mode is on, the selected-id Set, the tag drawer's
// open/closed state, "select all N results" (both the header's and the
// drawer's version), click / shift-click / box-drag selection, the keyboard
// shortcuts (V, T, C, Delete) and the Delete key's confirm-and-delete.
// Moved out of Home.jsx unchanged apart from the handoffs below — Home
// destructures every name this returns, so its JSX reads as before.
//
// What HAPPENS to photos stays in Home and arrives as callbacks. These are
// the handoffs two historical bugs lived in (V35: a stale selection after a
// bulk delete; V38: a selection surviving a crop), so each is explicit:
//   onBulkDeleted(ids) — the Delete key removed these photos; Home drops them
//                        from the grid (the selection is cleared right here)
//   onResync()         — a background delete failed; Home re-runs the search
//   onCropSelected(images) — the C key: Home opens the crop review window
//   cropOpen           — true while that window is open; every shortcut
//                        except V stays silent so its own T / Delete keys
//                        don't also reach the page underneath
// Leaving Select Mode after a crop actually STARTS is still Home's
// CropModal onClose calling toggleTagMode() (V38).
//
// The box-drag hit test is the pure idsInDragRect() in selectionRange.js,
// tested by scripts/test_selection_range.mjs alongside shift-click.
export function useSelection({
  images, total, similarTo, buildFilterParams,
  onBulkDeleted, onResync, onCropSelected, cropOpen,
}) {
  // ── Select Mode (was "Tag Mode"): bulk-select images to tag, crop, or deck ──
  const [tagMode, setTagMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [tagDrawerOpen, setTagDrawerOpen] = useState(false);
  const [selectingAll, setSelectingAll] = useState(false);
  const [selectMsg, setSelectMsg] = useState('');

  const [dragRect, setDragRect] = useState(null); // {left, top, width, height} in viewport coords, or null
  const tileRefs = useRef(new Map()); // image id -> tile DOM node
  const dragStateRef = useRef(null); // { startX, startY, dragging, baseSelected }
  const rangeAnchorRef = useRef(null); // last tile clicked — the far end of a shift-click range
  const justDraggedRef = useRef(false); // true for the brief window between mouseup-after-drag and the resulting click

  // ── Safety net: if the mouse is released outside the grid mid-drag, still end it ─
  useEffect(() => {
    if (!tagMode) return;
    const onUp = () => endDrag();
    window.addEventListener('mouseup', onUp);
    return () => window.removeEventListener('mouseup', onUp);
  }, [tagMode]);

  // ── Keyboard shortcuts: 'V' toggles Select Mode; with photos selected,
  //    'T' opens the tag drawer, 'C' crops, Delete/Backspace deletes ─────────
  useEffect(() => {
    const onKeyDown = (e) => {
      // Only trigger if user isn't typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'v' || e.key === 'V') {
        e.preventDefault();
        toggleTagMode();
        return;
      }
      // The Crop review modal binds its own 'T' (Tighten) and Backspace/Delete
      // (Skip photo) shortcuts with no stopPropagation — while it's open these
      // keys must NOT also reach the page underneath (T would fight over the
      // tag drawer, Delete would pop a bulk-delete confirm mid-review).
      if (!tagMode || selectedIds.size === 0 || cropOpen) return;
      if (e.key === 't' || e.key === 'T') {
        e.preventDefault();
        openTagDrawer();
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        const sel = images.filter(i => selectedIds.has(i.id));
        if (sel.length) onCropSelected(sel);
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        handleBulkDeleteClick();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tagMode, selectedIds, images, cropOpen]);

  // ── Tag Mode: toggling in/out, tile clicks, box-select drag ─────────────────
  const toggleTagMode = () => {
    setTagMode(v => {
      const next = !v;
      if (!next) {
        setSelectedIds(new Set()); // turning OFF clears selection
        setTagDrawerOpen(false); // Also close the drawer
      }
      return next;
    });
  };

  // Shift-click adds a whole run of photos at once (see selectionRange.js for
  // why the run follows server order, not screen position). Shift only ever
  // ADDS; it never unselects, so a mis-aimed shift-click can't quietly wipe a
  // selection you spent a minute building.
  const toggleTileSelection = (id, extendRange) => {
    if (extendRange) {
      const rangeIds = rangeIdsBetween(images, rangeAnchorRef.current, id);
      if (rangeIds.length) {
        setSelectedIds(prev => new Set([...prev, ...rangeIds]));
        rangeAnchorRef.current = id;
        return;
      }
    }
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    rangeAnchorRef.current = id;
  };

  // Select every image the current filter matches, not just the pages the
  // browser has scrolled far enough to load. Asking the server for the id
  // list is what makes this cheap and honest: a few kilobytes of numbers
  // instead of force-loading every remaining page of thumbnails, and it comes
  // from the same filter code the grid's own results do.
  const selectAllResults = useCallback(async () => {
    // Find Similar doesn't go through /api/search and always returns its whole
    // result set in one shot, so everything is already on screen.
    if (similarTo) {
      setSelectedIds(new Set(images.map(i => i.id)));
      return { ok: true, count: images.length };
    }
    try {
      const res = await fetch(`/api/search/ids?${buildFilterParams()}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'failed');
      const ids = data.ids || [];
      setSelectedIds(new Set(ids));
      return { ok: true, count: ids.length };
    } catch (e) {
      console.error('Select all results failed', e);
      // Deliberately leave the selection untouched rather than quietly
      // falling back to "the loaded ones" — silently selecting a smaller set
      // than asked for is the exact trap this feature exists to fix.
      return { ok: false, count: 0 };
    }
  }, [similarTo, images, buildFilterParams]);

  // ── Select all results wrapper for the header ─────────────────────────────
  const everythingLoaded = !total || images.length >= total;
  const allLoadedAndSelected = everythingLoaded && selectedIds.size > 0 && selectedIds.size >= images.length;

  const handleSelectAllResults = useCallback(async () => {
    if (selectingAll) return;
    setSelectMsg('');
    // Everything's already on screen — no round trip needed.
    if (everythingLoaded) {
      setSelectedIds(new Set(images.map(i => i.id)));
      return;
    }
    setSelectingAll(true);
    try {
      const res = await fetch(`/api/search/ids?${buildFilterParams()}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'failed');
      const ids = data.ids || [];
      setSelectedIds(new Set(ids));
    } catch (e) {
      console.error('Select all results failed', e);
      // Deliberately leave the selection untouched rather than quietly
      // falling back to "the loaded ones" — silently selecting a smaller set
      // than asked for is the exact trap this feature exists to fix.
      setSelectMsg("Couldn't reach the server — nothing selected.");
    }
    setSelectingAll(false);
  }, [similarTo, images, buildFilterParams, everythingLoaded, selectingAll]);

  const openTagDrawer = () => setTagDrawerOpen(true);
  const closeTagDrawer = () => setTagDrawerOpen(false);

  const handleBulkDeleteClick = () => {
    // Delete handler for the header's Delete button
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    // Ask for confirmation, then trigger the delete
    if (!window.confirm(`Delete ${ids.length} photo${ids.length === 1 ? '' : 's'}? They'll be moved to Drive's _Removed folder.`)) return;

    // Optimistically update UI
    onBulkDeleted(ids);
    setSelectedIds(new Set());

    // Delete in the background
    (async () => {
      try {
        const res = await fetch('/api/images/bulk-delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_ids: ids })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          onResync(); // Re-sync on error
        }
      } catch (e) {
        console.error('Bulk delete failed', e);
        onResync(); // Re-sync on error
      }
    })();
  };

  const DRAG_THRESHOLD = 4;

  const onGridMouseDown = (e) => {
    if (!tagMode) return;
    // Only left-click drags start a box-select
    if (e.button !== 0) return;
    dragStateRef.current = {
      startX: e.clientX, startY: e.clientY,
      dragging: false,
      baseSelected: new Set(selectedIds)
    };
  };

  const onGridMouseMove = (e) => {
    if (!tagMode || !dragStateRef.current) return;
    const st = dragStateRef.current;
    const dx = e.clientX - st.startX;
    const dy = e.clientY - st.startY;
    if (!st.dragging && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    st.dragging = true;

    const left = Math.min(st.startX, e.clientX);
    const top = Math.min(st.startY, e.clientY);
    const width = Math.abs(dx);
    const height = Math.abs(dy);
    setDragRect({ left, top, width, height });

    // Hit-test every tile against the drag rectangle (both in viewport coords)
    const rectRight = left + width;
    const rectBottom = top + height;
    const tiles = [];
    tileRefs.current.forEach((node, id) => {
      if (!node) return;
      tiles.push([id, node.getBoundingClientRect()]);
    });
    setSelectedIds(idsInDragRect(st.baseSelected, { left, top, right: rectRight, bottom: rectBottom }, tiles));
  };

  const endDrag = () => {
    // If a real drag happened, suppress the click that the browser fires right
    // after mouseup on the tile under the cursor (clear the flag on a timeout
    // so it doesn't linger and swallow the next legitimate click).
    if (dragStateRef.current?.dragging) {
      justDraggedRef.current = true;
      setTimeout(() => { justDraggedRef.current = false; }, 0);
    }
    dragStateRef.current = null;
    setDragRect(null);
  };

  const onGridMouseUp = () => {
    if (!tagMode) return;
    endDrag();
  };

  return {
    tagMode, selectedIds, setSelectedIds, tagDrawerOpen, selectingAll, selectMsg,
    dragRect, tileRefs, justDraggedRef,
    toggleTagMode, toggleTileSelection, selectAllResults, handleSelectAllResults,
    everythingLoaded, allLoadedAndSelected, openTagDrawer, closeTagDrawer,
    handleBulkDeleteClick, onGridMouseDown, onGridMouseMove, onGridMouseUp,
  };
}
