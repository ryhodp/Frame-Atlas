import { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import ImageDetail from '../components/ImageDetail';
import DuplicateReview from '../components/DuplicateReview';
import UploadButton from '../components/UploadButton';
import SelectModeHeader from '../components/SelectModeHeader';
import TagModeBar from '../components/TagModeBar';
import TagRemovalPreview from '../components/TagRemovalPreview';
import CropModal from '../components/CropModal';
import { useAuth } from '../AuthContext';
import { useSync } from '../SyncContext';
import { rangeIdsBetween } from '../selectionRange';
import { useIsMobile, MOBILE_BREAKPOINT } from '../hooks/useIsMobile';
import { useSearch } from '../hooks/useSearch';
import { SearchInput, SearchError, AutocompleteDropdown } from '../components/SearchBox';
import BookmarksMenu from '../components/BookmarksMenu';
import ColorFilter from '../components/ColorFilter';
import FilterChips from '../components/FilterChips';
import { PAGE_BG, accentSimilar, accentVioletLighter, black, onPrimary, onSurfaceFaint, onSurfaceMuted, onSurfaceWarm, onTertiary, outlineVariant, overlayViolet, primary, primaryDim, success, surfaceContainerDark, surfaceContainerHover, surfaceContainerLow, surfaceContainerLowest, surfaceContainerMuted, tertiary, warning, white, withAlpha } from '../theme';

const PER_PAGE = 60;

// Search bar on-screen pieces (Day 45): components/SearchBox.jsx,
// BookmarksMenu.jsx, ColorFilter.jsx, FilterChips.jsx. The colour-slider
// helpers (PROM_MIN, posToProm, promLabel, …) moved with ColorFilter.

export default function Home() {
  const { isAdmin } = useAuth();
  const sync = useSync();
  const isMobile = useIsMobile();
  const [images, setImages] = useState([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(null);
  const [winW, setWinW] = useState(window.innerWidth);
  const [setupStatus, setSetupStatus] = useState(null); // V17: empty-library checklist

  const [showDuplicates, setShowDuplicates] = useState(false);
  // null | { scanning: true, phase, processed, total } | { groups: [...] }
  const [duplicateScanStatus, setDuplicateScanStatus] = useState(null);
  const duplicateScanPollRef = useRef(null);

  // ── Find Similar mode ────────────────────────────────────────────────────
  const [similarTo, setSimilarTo] = useState(null); // {id, filename} or null
  const [similarNotice, setSimilarNotice] = useState(null); // dismissible banner text

  // ── Search (Day 44): every filter, the search box + autocomplete +
  //    describe-it, and bookmarks live in hooks/useSearch.js. Destructured
  //    under their original names so everything below reads as before.
  //    Starting a new filter leaves Find Similar mode only if it's active (a
  //    stale notice survives); Clear all always wipes it.
  const search = useSearch({
    onBeforeFilter: () => { if (similarTo) { setSimilarTo(null); setSimilarNotice(null); } },
    onClearAll: () => { setSimilarTo(null); setSimilarNotice(null); },
  });
  const { chips, setChips, nlChips, setNlChips, noteChips, color, setColor, film, setFilm, ar, hasFilters, buildFilterParams, setSearchText, clearAll } = search;

  // ── Select Mode (was "Tag Mode"): bulk-select images to tag, crop, or deck ──
  const [tagMode, setTagMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [tagDrawerOpen, setTagDrawerOpen] = useState(false);
  const [selectingAll, setSelectingAll] = useState(false);
  const [selectMsg, setSelectMsg] = useState('');

  // ── V32: library-wide tag cleanup — the chip whose tag is being removed
  //         from every result of the current search, or null ─────────────────
  const [removingTag, setRemovingTag] = useState(null);
  const [tagRemovalMsg, setTagRemovalMsg] = useState('');

  // ── V18: crop review modal — array of images to crop, or null ──────────────
  const [cropImages, setCropImages] = useState(null);
  const [dragRect, setDragRect] = useState(null); // {left, top, width, height} in viewport coords, or null
  const tileRefs = useRef(new Map()); // image id -> tile DOM node
  const dragStateRef = useRef(null); // { startX, startY, dragging, baseSelected }
  const rangeAnchorRef = useRef(null); // last tile clicked — the far end of a shift-click range
  const justDraggedRef = useRef(false); // true for the brief window between mouseup-after-drag and the resulting click

  // ── V48: drop photos anywhere on the page, not just onto the Upload
  //         button's own panel — delegates to the same upload flow ──────────
  const uploadButtonRef = useRef(null);
  const [pageDragOver, setPageDragOver] = useState(false);
  const pageDragDepthRef = useRef(0); // dragenter/dragleave fire on every child too; only the count hitting 0 means "actually left"

  const searchRequestId = useRef(0);
  const pageRef = useRef(0);
  const fetchingRef = useRef(false);
  const sentinelRef = useRef(null);

  // ── V14: shuffled home feed ────────────────────────────────────────────────
  // One seed per visit: every reload gets a fresh shuffle, but scrolling within
  // a visit paginates through the same fixed order (no repeats or gaps).
  const shuffleSeedRef = useRef(String(Date.now()));
  const viewObserverRef = useRef(null);   // watches tiles entering the viewport
  const seenIdsRef = useRef(new Set());   // every id already queued this visit
  const pendingViewsRef = useRef(new Set()); // queued but not yet sent to the server

  // V17: brand-new friend with an empty library → fetch what the setup
  // checklist needs (folder connected? key saved?). Only fires in the
  // truly-empty case, never during normal browsing or filtering.
  useEffect(() => {
    if (isAdmin || loading || images.length > 0 || hasFilters) return;
    fetch('/api/account/setup-status')
      .then(r => r.json())
      .then(setSetupStatus)
      .catch(() => {});
  }, [isAdmin, loading, images.length, hasFilters]);

  // ── Fetch one page of results; append=true keeps existing images ───────────
  const fetchPage = useCallback(async (pageNum, append) => {
    // Only appends need the in-flight guard — that's what stops infinite
    // scroll double-loading a page. A page-0 reset must never be dropped:
    // it's usually the color sliders committing, and silently keeping the
    // previous results would make the live match count lie. Concurrent
    // resets are handled by the request id instead, so the last one wins.
    if (append && fetchingRef.current) return;
    fetchingRef.current = true;
    const reqId = ++searchRequestId.current;
    setLoading(true);
    try {
      const params = buildFilterParams();
      // No filters → default browse view → ask the server for this visit's shuffle
      if (!chips.length && !nlChips.length && !noteChips.length && !color && !film && !ar) {
        params.set('seed', shuffleSeedRef.current);
      }
      params.set('page', pageNum);
      params.set('per', PER_PAGE);
      const res = await fetch(`/api/search?${params}`);
      const data = await res.json();
      if (reqId !== searchRequestId.current) return;  // a newer search superseded this one
      setImages(prev => append ? [...prev, ...(data.images || [])] : (data.images || []));
      setTotal(data.total || 0);
      setHasMore(!!data.has_more);
      pageRef.current = pageNum;
    } catch (e) {
      console.error('Search failed', e);
    } finally {
      if (reqId === searchRequestId.current) {
        setLoading(false);
        fetchingRef.current = false;
      }
    }
  }, [buildFilterParams, chips, nlChips, noteChips, color, film, ar]);

  // Filters changed → reset to page 0 (skip while in Find Similar mode)
  useEffect(() => {
    if (similarTo) return;
    fetchPage(0, false);
  }, [fetchPage, similarTo]);

  // ── Find Similar: fetch similar images for a given image, replacing the grid ─
  const fetchSimilar = useCallback(async (image) => {
    setSimilarNotice(null);
    setLoading(true);
    try {
      const res = await fetch(`/api/images/${image.id}/similar?limit=60`);
      if (res.status === 404) {
        const data = await res.json().catch(() => ({}));
        if (data.error === 'no_embedding') {
          setSimilarNotice("This image hasn't been fingerprinted yet — new uploads get fingerprints the next time the fingerprint script runs.");
        } else {
          setSimilarNotice("Couldn't find similar images for this one.");
        }
        setSimilarTo(null);
        setLoading(false);
        return;
      }
      const data = await res.json();
      setImages(data.images || []);
      setTotal((data.images || []).length);
      setHasMore(false);
      setSimilarTo(data.source || { id: image.id, filename: image.filename });
    } catch (e) {
      console.error('Find similar failed', e);
      setSimilarNotice("Couldn't load similar images — check your connection and try again.");
      setSimilarTo(null);
    }
    setLoading(false);
  }, []);

  // ── Entry point: called from the detail panel's "Find Similar" button ──────
  const handleFindSimilar = (image) => {
    // Clear all other filters — similar mode is exclusive
    setChips([]);
    setNlChips([]);
    setColor(null);
    setFilm(null);
    setSearchText('');
    setSelectedImage(null);
    // Set similarTo synchronously (same render as the filter clears above) so the
    // filters effect's `if (similarTo) return;` guard sees it immediately — otherwise
    // the effect fires an unwanted /api/search before fetchSimilar's async result lands,
    // and that stray request can overwrite the similar results with the default grid.
    setSimilarTo({ id: image.id, filename: image.filename });
    fetchSimilar(image);
  };

  const clearSimilar = () => {
    setSimilarTo(null);
    setSimilarNotice(null);
    // fetchPage will re-run via the filters effect once similarTo clears
  };

  // ── Infinite scroll: load next page when the sentinel nears the viewport ───
  // Fallback/safety net for the midpoint prefetch below — if that one ever
  // misses (e.g. a tile ref not yet attached), this still guarantees more
  // images load once the user actually reaches the bottom.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && hasMore && !fetchingRef.current) {
        fetchPage(pageRef.current + 1, true);
      }
    }, { rootMargin: '800px' });
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, fetchPage]);

  // ── Infinite scroll (prefetch): start loading the NEXT page once the user
  // has scrolled halfway through the page that was loaded LAST — not just
  // when they hit the very bottom. With PER_PAGE=60: after the first page
  // loads (60 images), the trigger sits at image 30; once a second page
  // lands (120 total), it moves to image 90; and so on. Each fetch always
  // sits half a page behind the current end, so there's no pause waiting
  // for the next batch while scrolling steadily. Watches the actual tile at
  // that index via the existing tileRefs map (same one the view-tracking
  // observer uses) rather than adding a new DOM sentinel.
  useEffect(() => {
    if (!hasMore || images.length === 0) return;
    const midIndex = Math.max(0, images.length - Math.floor(PER_PAGE / 2));
    const midImage = images[midIndex];
    if (!midImage) return;
    const node = tileRefs.current.get(midImage.id);
    if (!node) return;

    const observer = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && hasMore && !fetchingRef.current) {
        fetchPage(pageRef.current + 1, true);
      }
    }, { rootMargin: '200px' });
    observer.observe(node);
    return () => observer.disconnect();
  }, [images, hasMore, fetchPage]);

  // ── V14: mark tiles as "seen" once at least half of one is on screen ───────
  useEffect(() => {
    const obs = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const id = Number(entry.target.dataset.imageId);
        if (id && !seenIdsRef.current.has(id)) {
          seenIdsRef.current.add(id);
          pendingViewsRef.current.add(id);
        }
        obs.unobserve(entry.target); // each tile only needs to be counted once
      }
    }, { threshold: 0.5 });
    viewObserverRef.current = obs;
    return () => obs.disconnect();
  }, []);

  // ── V14: send the seen-image batch when the user leaves ────────────────────
  // Flushing only on exit (not mid-scroll) keeps this visit's shuffled order
  // stable — the server ordering never shifts under an open page.
  const flushViews = useCallback(() => {
    const pending = pendingViewsRef.current;
    if (!pending.size) return;
    const ids = [...pending];
    pending.clear();
    try {
      // keepalive lets the request finish even as the tab closes
      fetch('/api/views/log', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: ids }),
        keepalive: true
      }).catch(() => {});
    } catch { /* view logging is best-effort — never break the page over it */ }
  }, []);

  useEffect(() => {
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') flushViews();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      flushViews(); // also fires when navigating to another page in the app
    };
  }, [flushViews]);

  // ── Track window width for responsive column count ─────────────────────────
  useEffect(() => {
    const onResize = () => setWinW(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

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
      if (!tagMode || selectedIds.size === 0 || cropImages) return;
      if (e.key === 't' || e.key === 'T') {
        e.preventDefault();
        openTagDrawer();
      } else if (e.key === 'c' || e.key === 'C') {
        e.preventDefault();
        const sel = images.filter(i => selectedIds.has(i.id));
        if (sel.length) setCropImages(sel);
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        handleBulkDeleteClick();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tagMode, selectedIds, images, cropImages]);

  // ── Detail-panel callbacks: keep grid in sync with edits ────────────────────
  const handleImageUpdated = (id, patch) => {
    setImages(prev => prev.map(img => img.id === id ? { ...img, ...patch } : img));
    setSelectedImage(prev => (prev && prev.id === id) ? { ...prev, ...patch } : prev);
  };

  const handleImageDeleted = (id) => {
    setImages(prev => prev.filter(img => img.id !== id));
    setTotal(t => Math.max(0, t - 1));
    setSelectedImage(prev => (prev && prev.id === id) ? null : prev);
  };

  // Quick-favorite star on the grid tile itself — no need to open the detail panel
  const toggleFavorite = async (img, e) => {
    e.stopPropagation();
    try {
      const res = await fetch(`/api/images/${img.id}/favorite`, { method: 'POST' });
      const data = await res.json();
      handleImageUpdated(img.id, { is_favorite: data.is_favorite });
    } catch (err) {
      console.error('Toggle favorite failed', err);
    }
  };

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

  // Apply a bulk patch to any currently-loaded images that were part of the bulk op
  const handleBulkTagsChanged = (ids, patchFn) => {
    const idSet = new Set(ids);
    setImages(prev => prev.map(img => idSet.has(img.id) ? patchFn(img) : img));
    setSelectedImage(prev => (prev && idSet.has(prev.id)) ? patchFn(prev) : prev);
  };

  // A bulk tag/filmography edit just landed on the server. handleBulkTagsChanged
  // only patched each photo's own fields in local state — it never re-checks
  // whether a photo still belongs in the currently active search filter (e.g.
  // untagging "car" while filtered to car should drop that photo from the
  // grid). Re-running the current search is what the server already does
  // correctly, so route through it instead of re-implementing every filter
  // (chips/nlChips/color/film/ar) client-side.
  const handleBulkMutated = () => {
    if (hasFilters) fetchPage(0, false);
  };

  // A bulk delete in Select Mode already tells us exactly which ids are gone
  // (unlike a tag edit, there's no need to re-run the search — a deleted
  // photo can't match any filter).
  const handleBulkDeleted = (ids) => {
    const idSet = new Set(ids);
    setImages(prev => prev.filter(img => !idSet.has(img.id)));
    setTotal(t => Math.max(0, t - ids.length));
    setSelectedImage(prev => (prev && idSet.has(prev.id)) ? null : prev);
  };

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
    handleBulkDeleted(ids);
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
          fetchPage(0, false); // Re-sync on error
        }
      } catch (e) {
        console.error('Bulk delete failed', e);
        fetchPage(0, false); // Re-sync on error
      }
    })();
  };

  // ── Background duplicate scanner ────────────────────────────────────────────
  // The scan itself runs server-side (fingerprints → palettes → Drive
  // reconcile → the actual O(images²) comparison, which is the slow part on
  // a large library). This polls /api/duplicates/scan-progress every 600ms
  // for a real percentage instead of one request that silently blocks until
  // everything is done — that's what read as "keeps waiting and waiting"
  // with no sign it was actually working.
  const pollDuplicateProgress = () => {
    fetch('/api/duplicates/scan-progress')
      .then(res => res.json())
      .then(p => {
        if (p.phase === 'done') {
          if (p.error) {
            console.error('Duplicate scan failed', p.error);
            setDuplicateScanStatus(null);
          } else {
            setDuplicateScanStatus({ groups: p.groups || [] });
          }
          return;
        }
        setDuplicateScanStatus({
          scanning: true, phase: p.phase, processed: p.processed || 0, total: p.total || 0
        });
        duplicateScanPollRef.current = setTimeout(pollDuplicateProgress, 600);
      })
      .catch(e => {
        console.error('Duplicate scan progress poll failed', e);
        setDuplicateScanStatus(null);
      });
  };

  const startDuplicateScan = async () => {
    setDuplicateScanStatus({ scanning: true, phase: null, processed: 0, total: 0 });
    try {
      const res = await fetch('/api/duplicates/scan', { method: 'POST' });
      if (!res.ok) {
        setDuplicateScanStatus(null);
        return;
      }
      // Whether this call started a fresh scan or found one already running
      // (already_running: true), either way the right next step is the same:
      // start polling for progress.
      pollDuplicateProgress();
    } catch (e) {
      console.error('Duplicate scan failed', e);
      setDuplicateScanStatus(null);
    }
  };

  // Stop polling if the page unmounts mid-scan
  useEffect(() => () => clearTimeout(duplicateScanPollRef.current), []);

  const DUP_PHASE_LABELS = {
    fingerprints: 'Checking fingerprints',
    palettes: 'Checking colors',
    reconcile: 'Syncing with Drive',
    comparing: 'Comparing photos',
  };

  // Dragenter/dragleave fire on every child element the cursor crosses, not
  // just once for the whole page — a depth counter is what tells "moved to a
  // child" apart from "actually left the window" (only the latter should
  // hide the overlay). Gated to admin + a real file drag so it never
  // intercepts, say, a tag chip being dragged around Select Mode.
  const isFileDrag = (e) => isAdmin && !!e.dataTransfer?.types?.includes('Files');

  const handlePageDragEnter = (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    pageDragDepthRef.current += 1;
    setPageDragOver(true);
  };
  const handlePageDragOver = (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
  };
  const handlePageDragLeave = (e) => {
    if (!isFileDrag(e)) return;
    pageDragDepthRef.current = Math.max(0, pageDragDepthRef.current - 1);
    if (pageDragDepthRef.current === 0) setPageDragOver(false);
  };
  const handlePageDrop = (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    pageDragDepthRef.current = 0;
    setPageDragOver(false);
    uploadButtonRef.current?.acceptFiles(e.dataTransfer.files);
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
    const next = new Set(st.baseSelected);
    tileRefs.current.forEach((node, id) => {
      if (!node) return;
      const r = node.getBoundingClientRect();
      const intersects = r.left < rectRight && r.right > left && r.top < rectBottom && r.bottom > top;
      if (intersects) next.add(id);
    });
    setSelectedIds(next);
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

  // ── True masonry: distribute images into columns, shortest-first ────────────
  // Every image keeps its full aspect ratio — nothing is cropped.
  // Placement is greedy in order, so appending a page never reshuffles
  // images that are already on screen. colWidth is the density slider's
  // target column width — smaller means more, denser columns. The initial
  // default is picked from the screen width so phones start at ~2 columns
  // and tablets at ~3, without touching how the slider itself works —
  // once mounted colWidth is fully manual again, same as desktop today.
  const [colWidth, setColWidth] = useState(() => {
    const w = window.innerWidth;
    const offset = w < MOBILE_BREAKPOINT ? 24 : 280;
    const contentW = w - offset;
    if (w < MOBILE_BREAKPOINT) return Math.max(140, contentW / 2);
    if (w < 1100) return Math.max(160, contentW / 3);
    return 320;
  });
  // Sidebar only reserves real width on tablet/desktop — on mobile it's an
  // overlay drawer, so the grid gets the full window width to itself.
  // Add the drawer width (280px) when it's open.
  const sidebarOffset = isMobile ? 24 : 280;
  const drawerOffset = tagDrawerOpen ? 280 : 0;
  const colCount = Math.max(2, Math.min(7, Math.floor((winW - sidebarOffset - drawerOffset) / colWidth)));
  const columns = (() => {
    const cols = Array.from({ length: colCount }, () => ({ items: [], h: 0 }));
    for (const img of images) {
      const shortest = cols.reduce((a, b) => (a.h <= b.h ? a : b));
      shortest.items.push(img);
      shortest.h += 1 / (img.ar_float || 1.78); // height at unit width
    }
    return cols.map(c => c.items);
  })();

  return (
    <div
      onDragEnter={handlePageDragEnter}
      onDragOver={handlePageDragOver}
      onDragLeave={handlePageDragLeave}
      onDrop={handlePageDrop}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        background: PAGE_BG,
        color: onSurfaceWarm,
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
        position: 'relative'
      }}>

      {/* Whole-page drop target hint — admin only, appears the moment a file
          drag enters anywhere on Home, not just over the Upload button's own
          panel. Drops delegate to that same panel's upload flow. */}
      {pageDragOver && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 500,
          background: withAlpha(surfaceContainerLowest,0.82),
          border: `3px dashed ${primaryDim}`,
          margin: '10px',
          borderRadius: '16px',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: '10px', pointerEvents: 'none'
        }}>
          <div style={{ fontSize: '32px' }}>⬆</div>
          <div style={{ fontSize: '16px', fontWeight: 600, color: onSurfaceWarm }}>Drop to upload</div>
          <div style={{ fontSize: '12.5px', color: onSurfaceMuted }}>Photos go straight into your Drive folder and start tagging automatically</div>
        </div>
      )}

      {/* Main content column — margin-right makes room for the Edit Tags
          drawer so the grid actually narrows and reflows into fewer, wider
          columns as it opens, instead of the drawer just landing on top of
          whatever was already there. colCount above is computed against
          this same drawerOffset, so the column count and the space they
          have to fill always agree. */}
      <div style={{
        display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0,
        marginRight: `${drawerOffset}px`,
        transition: 'margin-right 0.2s ease'
      }}>

      {/* V49: UploadProgressBadge and the always-present strip it sat in are
          gone. Background tagging is now reported by the Sync button's own
          inline progress plus a completion toast (SyncContext), so this was a
          second indicator for the same job — and it reserved a bordered
          24px-tall row on every page load whether or not it had anything to
          say. See SyncContext.jsx for the stale-"complete" guard that kept
          the badge stuck open showing an empty gear. */}

      {/* ── Select Mode Header (only when tagMode is on) ──────────────────────── */}
      {tagMode && (
        <SelectModeHeader
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onSelectAllResults={handleSelectAllResults}
          onExit={toggleTagMode}
          onEditTags={openTagDrawer}
          onCrop={() => {
            const sel = images.filter(i => selectedIds.has(i.id));
            if (sel.length) setCropImages(sel);
          }}
          onDelete={handleBulkDeleteClick}
          selectingAll={selectingAll}
          selectMsg={selectMsg}
          totalResults={similarTo ? images.length : total}
          images={images}
          everythingLoaded={everythingLoaded}
          allLoadedAndSelected={allLoadedAndSelected}
        />
      )}

      {/* ── Search bar ─────────────────────────────────────────────────────── */}
      <div
        data-search-area
        style={{
          padding: isMobile ? '12px 14px' : '16px 20px',
          borderBottom: `1px solid ${withAlpha(white,0.065)}`,
          position: 'relative',
          zIndex: 40
        }}
      >
        <div style={{ display: 'flex', gap: isMobile ? '6px' : '8px', alignItems: 'center' }}>
          <SearchInput search={search} />

          {/* V18: Select Mode is for everyone — friends bulk-crop their own
              images and add to decks. V75: the bar's tag + filmography panels
              are open to everyone too, scoped server-side to your own photos. */}
          <button
            onClick={toggleTagMode}
            title="Select Mode — bulk-select images to crop, tag, or add to a deck (press V)"
            style={{
              height: isMobile ? '38px' : '46px', width: isMobile ? '38px' : '46px', flexShrink: 0,
              background: tagMode ? withAlpha(tertiary,0.14) : surfaceContainerDark,
              border: `1px solid ${tagMode ? withAlpha(tertiary,0.6) : withAlpha(white,0.12)}`,
              borderRadius: '10px',
              cursor: 'pointer',
              color: tagMode ? tertiary : onSurfaceMuted,
              fontSize: '16px'
            }}
          >
            ✓
          </button>

          {/* Upload and Duplicate review still edit the admin's own library */}
          {isAdmin && (
            <>
              <UploadButton ref={uploadButtonRef} onUploaded={() => fetchPage(0, false)} />

              <button
                onClick={startDuplicateScan}
                disabled={!!duplicateScanStatus?.scanning}
                title={duplicateScanStatus?.scanning
                  ? `${DUP_PHASE_LABELS[duplicateScanStatus.phase] || 'Starting…'}${duplicateScanStatus.total ? ` (${duplicateScanStatus.processed}/${duplicateScanStatus.total})` : ''}`
                  : 'Find duplicate images (runs in background)'}
                style={{
                  display: 'flex', alignItems: 'center', gap: '7px',
                  height: isMobile ? '38px' : '46px', flexShrink: 0,
                  padding: duplicateScanStatus?.scanning ? '0 12px' : 0,
                  width: duplicateScanStatus?.scanning ? 'auto' : (isMobile ? '38px' : '46px'),
                  justifyContent: 'center',
                  background: duplicateScanStatus?.scanning ? withAlpha(primary,0.14) : surfaceContainerDark,
                  border: `1px solid ${duplicateScanStatus?.scanning ? withAlpha(primary,0.5) : withAlpha(white,0.12)}`,
                  borderRadius: '10px',
                  cursor: duplicateScanStatus?.scanning ? 'default' : 'pointer',
                  color: duplicateScanStatus?.scanning ? primary : onSurfaceMuted,
                  fontSize: '15px',
                  opacity: duplicateScanStatus?.scanning ? 0.9 : 1,
                  transition: 'width 0.2s ease'
                }}
              >
                {duplicateScanStatus?.scanning ? (
                  <>
                    <span style={{
                      width: '12px', height: '12px', flexShrink: 0,
                      border: `2px solid ${withAlpha(primary,0.3)}`, borderTopColor: primary,
                      borderRadius: '50%', display: 'inline-block',
                      animation: 'spin 0.7s linear infinite'
                    }} />
                    <span style={{ fontSize: '11px', whiteSpace: 'nowrap' }}>
                      {DUP_PHASE_LABELS[duplicateScanStatus.phase] || 'Starting…'}
                      {duplicateScanStatus.total > 0 ? ` ${duplicateScanStatus.processed}/${duplicateScanStatus.total}` : ''}
                    </span>
                  </>
                ) : '⧉'}
              </button>

              <button
                onClick={sync.startSync}
                disabled={sync.running}
                title={
                  sync.syncing ? 'Syncing from Google Drive…'
                  : sync.tagging ? 'Tagging new photos…'
                  : 'Sync photos from Google Drive (runs in background)'
                }
                style={{
                  display: 'flex', alignItems: 'center', gap: '7px',
                  height: isMobile ? '38px' : '46px', flexShrink: 0,
                  padding: sync.running ? '0 12px' : 0,
                  width: sync.running ? 'auto' : (isMobile ? '38px' : '46px'),
                  justifyContent: 'center',
                  background: sync.running ? withAlpha(primary,0.14) : surfaceContainerDark,
                  border: `1px solid ${sync.running ? withAlpha(primary,0.5) : withAlpha(white,0.12)}`,
                  borderRadius: '10px',
                  cursor: sync.running ? 'default' : 'pointer',
                  color: sync.running ? primary : onSurfaceMuted,
                  fontSize: '15px',
                  opacity: sync.running ? 0.9 : 1,
                  transition: 'width 0.2s ease'
                }}
              >
                {sync.running ? (
                  <span style={{
                    width: '12px', height: '12px', flexShrink: 0,
                    border: `2px solid ${withAlpha(primary,0.3)}`, borderTopColor: primary,
                    borderRadius: '50%', display: 'inline-block',
                    animation: 'spin 0.7s linear infinite'
                  }} />
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M21 2v6h-6M3 12a9 9 0 0115-6.7L21 8M3 22v-6h6M21 12a9 9 0 01-15 6.7L3 16"/>
                  </svg>
                )}
                {sync.running && (
                  <span style={{ fontSize: '11px', whiteSpace: 'nowrap' }}>
                    {sync.syncing
                      ? (sync.syncTotal > 0 ? `Syncing ${sync.syncProcessed}/${sync.syncTotal}` : 'Syncing…')
                      : (sync.tagTotal > 0 ? `Tagging ${sync.tagDone}/${sync.tagTotal}` : 'Tagging…')}
                  </span>
                )}
              </button>
            </>
          )}

          <BookmarksMenu search={search} isMobile={isMobile} />

          <SearchError search={search} />
        </div>

        <AutocompleteDropdown search={search} />

        <ColorFilter search={search} isMobile={isMobile} total={total} loading={loading} />

        <FilterChips search={search} similarTo={similarTo} onClearSimilar={clearSimilar} />

        {/* Find Similar notice — e.g. image has no fingerprint yet */}
        {similarNotice && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '10px',
            marginTop: '12px', padding: '9px 13px',
            background: withAlpha(accentSimilar,0.08),
            border: `1px solid ${withAlpha(accentSimilar,0.28)}`,
            borderRadius: '8px',
            fontSize: '12px', color: accentVioletLighter
          }}>
            <span style={{ flex: 1 }}>{similarNotice}</span>
            <button
              onClick={() => setSimilarNotice(null)}
              style={{
                background: 'none', border: 'none', color: accentVioletLighter,
                cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
              }}
              onMouseEnter={e => e.currentTarget.style.opacity = '1'}
              onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
            >×</button>
          </div>
        )}
      </div>

      {/* ── Result count bar ────────────────────────────────────────────────── */}
      <div style={{
        padding: isMobile ? '10px 14px' : '10px 20px',
        borderBottom: `1px solid ${withAlpha(white,0.065)}`,
        display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', rowGap: '8px'
      }}>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '12px', color: onSurfaceMuted
        }}>
          <span style={{ color: onSurfaceWarm, fontWeight: 500 }}>{total}</span> images
          {images.length > 0 && images.length < total && (
            <span style={{ color: onSurfaceFaint }}> · {images.length} loaded</span>
          )}
          {similarTo && (
            <span style={{ color: onSurfaceFaint }}> · showing similar matches</span>
          )}
          {!similarTo && hasFilters && (
            <span style={{ color: onSurfaceFaint }}>
              {' '}· {chips.length + nlChips.length + noteChips.length + (color ? 1 : 0) + (film ? 1 : 0)} filter{(chips.length + nlChips.length + noteChips.length + (color ? 1 : 0) + (film ? 1 : 0)) > 1 ? 's' : ''} active
            </span>
          )}
        </span>
        {loading && (
          <span style={{
            width: '12px', height: '12px',
            border: `2px solid ${withAlpha(primaryDim,0.2)}`,
            borderTopColor: primaryDim,
            borderRadius: '50%',
            display: 'inline-block',
            animation: 'spin 0.7s linear infinite'
          }} />
        )}

        {/* V32: clean a bad tag out of the whole library without clicking each
            photo. Only offered for exact-tag chips — a describe-it search
            matches photos that were never tagged the word you typed, so there
            would be nothing there to remove. Amber, and worded around the
            tag, so it can't be mistaken for the red Delete in Select Mode:
            this takes a label off, that moves the picture out of the library. */}
        {isAdmin && !similarTo && total > 0 && chips.map(chip => (
          <button
            key={`cleanup-${chip}`}
            onClick={() => setRemovingTag(chip)}
            title={`Take the tag “${chip}” off every photo in these results. The photos themselves are not touched.`}
            style={{
              background: withAlpha(primary,0.10),
              border: `1px solid ${withAlpha(primary,0.35)}`,
              color: warning, borderRadius: '7px', padding: '5px 11px',
              cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit'
            }}
          >
            Remove tag “{chip}” from all {total}…
          </button>
        ))}

        {tagRemovalMsg && (
          <span style={{ fontSize: '11.5px', color: tertiary }}>{tagRemovalMsg}</span>
        )}

        <div style={{ flex: 1 }} />

        {/* Grid density — smaller column target width = more, smaller tiles */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }} title="Grid density">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={onSurfaceFaint} strokeWidth="2">
            <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" />
          </svg>
          <input
            type="range"
            min={140}
            max={420}
            step={10}
            value={colWidth}
            onChange={e => setColWidth(Number(e.target.value))}
            style={{ width: '90px', accentColor: primaryDim }}
          />
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={onSurfaceFaint} strokeWidth="2">
            <rect x="3" y="3" width="8" height="8" /><rect x="13" y="3" width="8" height="8" />
            <rect x="3" y="13" width="8" height="8" /><rect x="13" y="13" width="8" height="8" />
          </svg>
        </div>
      </div>

      {/* ── Image grid ──────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px' }}>

        {/* V17: setup checklist — a friend's library before their first sync */}
        {!loading && images.length === 0 && !hasFilters && !similarTo && !isAdmin && (
          <div style={{
            height: '70%', display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: '18px'
          }}>
            <div style={{ textAlign: 'center' }}>
              <h2 style={{ fontSize: '20px', fontWeight: 700, color: onSurfaceWarm, margin: '0 0 6px' }}>
                Welcome to Frame Atlas
              </h2>
              <p style={{ fontSize: '13px', color: onSurfaceMuted, margin: 0 }}>
                Three steps and your own reference library is live:
              </p>
            </div>
            <div style={{
              width: 'min(440px, 90%)', background: surfaceContainerLow,
              border: `1px solid ${outlineVariant}`, borderRadius: '14px', padding: '10px 8px'
            }}>
              {[
                {
                  done: !!setupStatus?.folder_connected,
                  label: 'Connect your Google Drive folder',
                  sub: setupStatus?.folder_connected ? `📁 ${setupStatus.folder_name}` : 'Share it with the robot email, paste the link'
                },
                {
                  done: false, // library is empty here by definition
                  label: 'Sync your images',
                  sub: setupStatus?.folder_connected ? 'One click — pulls everything in the folder' : 'Unlocks after step 1'
                },
                {
                  done: !!setupStatus?.has_gemini_key,
                  label: 'Add your AI key',
                  sub: 'Optional — auto-tags photos so you can search by mood, light, color',
                  optional: true
                },
              ].map((step, i) => (
                <Link key={i} to="/account" style={{
                  display: 'flex', alignItems: 'center', gap: '14px', padding: '12px 14px',
                  textDecoration: 'none', borderRadius: '10px',
                  borderBottom: i < 2 ? `1px solid ${withAlpha(white,0.05)}` : 'none'
                }}>
                  <div style={{
                    width: '26px', height: '26px', borderRadius: '50%', flexShrink: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '13px', fontWeight: 700,
                    background: step.done ? withAlpha(success,0.15) : withAlpha(primaryDim,0.1),
                    border: `1px solid ${step.done ? withAlpha(success,0.5) : withAlpha(primaryDim,0.35)}`,
                    color: step.done ? success : primaryDim
                  }}>
                    {step.done ? '✓' : i + 1}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '13.5px', fontWeight: 600, color: step.done ? success : onSurfaceWarm }}>
                      {step.label}
                      {step.optional && <span style={{ color: onSurfaceFaint, fontWeight: 400 }}> (optional)</span>}
                    </div>
                    <div style={{ fontSize: '11.5px', color: onSurfaceFaint, marginTop: '2px' }}>{step.sub}</div>
                  </div>
                  <span style={{ color: onSurfaceFaint, fontSize: '14px' }}>→</span>
                </Link>
              ))}
            </div>
            <Link to="/account" style={{
              background: primary, color: onPrimary, borderRadius: '8px',
              padding: '10px 20px', fontSize: '13.5px', fontWeight: 600, textDecoration: 'none'
            }}>
              Set up my library
            </Link>
          </div>
        )}

        {/* Empty state */}
        {!loading && images.length === 0 && (hasFilters || similarTo || isAdmin) && (
          <div style={{
            height: '60%', display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center',
            gap: '10px', color: onSurfaceFaint
          }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.5" opacity="0.4">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <p style={{ fontSize: '14px', color: onSurfaceMuted }}>
              {similarTo
                ? 'No similar images found'
                : hasFilters ? 'No images match this filter' : 'No images yet — run a sync first'}
            </p>
            {similarTo ? (
              <button
                onClick={clearSimilar}
                style={{
                  fontSize: '12px', color: warning, background: 'none',
                  border: `1px solid ${withAlpha(primaryDim,0.3)}`,
                  borderRadius: '7px', padding: '7px 14px',
                  cursor: 'pointer', fontFamily: 'inherit'
                }}
              >
                Back to browsing
              </button>
            ) : hasFilters && (
              <button
                onClick={clearAll}
                style={{
                  fontSize: '12px', color: warning, background: 'none',
                  border: `1px solid ${withAlpha(primaryDim,0.3)}`,
                  borderRadius: '7px', padding: '7px 14px',
                  cursor: 'pointer', fontFamily: 'inherit'
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        )}

        {/* Masonry columns — full aspect ratio, no cropping */}
        <div
          onMouseDown={onGridMouseDown}
          onMouseMove={onGridMouseMove}
          onMouseUp={onGridMouseUp}
          style={{
            display: 'flex', gap: '10px', alignItems: 'flex-start',
            userSelect: tagMode ? 'none' : 'auto'
          }}
        >
          {columns.map((col, ci) => (
            <div key={ci} style={{
              flex: 1, minWidth: 0,
              display: 'flex', flexDirection: 'column', gap: '10px'
            }}>
              {col.map(img => {
                const isSelected = tagMode && selectedIds.has(img.id);
                return (
                <div
                  key={img.id}
                  data-image-id={img.id}
                  ref={node => {
                    if (node) {
                      tileRefs.current.set(img.id, node);
                      viewObserverRef.current?.observe(node); // V14: count as seen once visible
                    } else {
                      tileRefs.current.delete(img.id);
                    }
                  }}
                  onClick={(e) => {
                    if (tagMode) {
                      // Don't toggle if this click was the tail end of a drag
                      if (justDraggedRef.current) return;
                      toggleTileSelection(img.id, e.shiftKey);
                    } else {
                      setSelectedImage(img);
                    }
                  }}
                  style={{
                    position: 'relative',
                    width: '100%',
                    aspectRatio: `${img.ar_float || 1.78}`,
                    background: surfaceContainerMuted,
                    borderRadius: '6px',
                    overflow: 'hidden',
                    cursor: 'pointer',
                    border: isSelected ? `2px solid ${tertiary}` : `1px solid ${withAlpha(white,0.04)}`,
                    transition: 'transform 0.15s ease'
                  }}
                  onMouseEnter={e => {
                    e.currentTarget.style.transform = 'scale(1.01)';
                    const star = e.currentTarget.querySelector('[data-quickfav]');
                    if (star && !img.is_favorite) star.style.opacity = '1';
                  }}
                  onMouseLeave={e => {
                    e.currentTarget.style.transform = 'scale(1)';
                    const star = e.currentTarget.querySelector('[data-quickfav]');
                    if (star && !img.is_favorite) star.style.opacity = '0';
                  }}
                >
                  {/* Thumbnail — box matches the image's true ratio, so nothing crops */}
                  {img.thumbnail && (
                    <img
                      src={img.thumbnail}
                      alt={img.filename}
                      style={{
                        position: 'absolute', inset: 0,
                        width: '100%', height: '100%',
                        objectFit: 'cover'
                      }}
                      loading="lazy"
                    />
                  )}

                  {/* Gradient overlay */}
                  <div style={{
                    position: 'absolute', inset: 0,
                    background: `linear-gradient(180deg, ${withAlpha(black,0)} 40%, ${withAlpha(black,0.78)} 100%)`,
                    pointerEvents: 'none'
                  }} />

                  {/* Quick-favorite star — always visible (gold) once favorited; otherwise
                      a translucent gray star that only shows up on hover (opacity toggled
                      imperatively above, same pattern as the tile's own scale-on-hover).
                      On mobile there's no hover, so it stays dimly visible instead of hidden —
                      otherwise it'd be undiscoverable on touch. Hit area is enlarged on mobile
                      to meet a comfortable tap-target size without growing the visible glyph.
                      Hidden entirely in Tag Mode so it doesn't fight tile-selection clicks. */}
                  {!tagMode && (
                    <button
                      data-quickfav
                      onClick={(e) => toggleFavorite(img, e)}
                      title={img.is_favorite ? 'Unfavorite' : 'Favorite'}
                      style={{
                        position: 'absolute', top: '0px', right: '0px',
                        background: 'none', border: 'none', cursor: 'pointer',
                        padding: isMobile ? '11px' : '4px', lineHeight: 1, zIndex: 2,
                        fontSize: img.is_favorite ? '13px' : '14px',
                        color: img.is_favorite ? warning : withAlpha(onSurfaceWarm,0.65),
                        opacity: img.is_favorite ? 1 : (isMobile ? 0.55 : 0),
                        transition: 'opacity 120ms ease',
                        filter: `drop-shadow(0 1px 2px ${withAlpha(black,0.7)})`
                      }}
                    >★</button>
                  )}
                  {tagMode && img.is_favorite && (
                    <span style={{
                      position: 'absolute', top: '6px', right: '7px',
                      color: warning, fontSize: '13px',
                      filter: `drop-shadow(0 1px 2px ${withAlpha(black,0.7)})`
                    }}>★</span>
                  )}
                  {/* Similarity badge — only shown while browsing "Find Similar" results */}
                  {similarTo && typeof img.similarity === 'number' && (
                    <span style={{
                      position: 'absolute', bottom: '7px', right: '7px',
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: '9px', color: accentVioletLighter,
                      background: withAlpha(overlayViolet,0.55),
                      border: `1px solid ${withAlpha(accentSimilar,0.35)}`,
                      padding: '2px 6px', borderRadius: '4px'
                    }}>
                      {Math.round(img.similarity * 100)}%
                    </span>
                  )}

                  {/* Tag Mode selection checkmark — top-right, offset clear of the star */}
                  {isSelected && (
                    <span style={{
                      position: 'absolute', top: '6px', right: '28px',
                      width: '18px', height: '18px', borderRadius: '50%',
                      background: tertiary,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      boxShadow: `0 1px 3px ${withAlpha(black,0.5)}`
                    }}>
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                        stroke={onTertiary} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    </span>
                  )}
                </div>
                );
              })}
            </div>
          ))}
        </div>

        {/* Drag-select rectangle overlay — viewport-fixed, matches drag coords */}
        {dragRect && (
          <div style={{
            position: 'fixed',
            left: dragRect.left, top: dragRect.top,
            width: dragRect.width, height: dragRect.height,
            background: withAlpha(tertiary,0.14),
            border: `1px solid ${tertiary}`,
            pointerEvents: 'none',
            zIndex: 500
          }} />
        )}

        {/* Infinite-scroll sentinel — when this nears the viewport, load more */}
        <div ref={sentinelRef} style={{ height: '1px' }} />

        {hasMore && (
          <div style={{
            padding: '20px', textAlign: 'center',
            fontSize: '12px', color: onSurfaceFaint,
            fontFamily: "'JetBrains Mono', monospace"
          }}>
            loading more…
          </div>
        )}

        <div style={{ height: '30px' }} />
      </div>

      </div>

      {/* Detail panel */}
      {selectedImage && (
        <ImageDetail
          image={selectedImage}
          onClose={() => setSelectedImage(null)}
          onUpdated={handleImageUpdated}
          onDeleted={handleImageDeleted}
          onSearchFilm={(query) => {
            if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
            setFilm(query);
            setSelectedImage(null); // close panel so the filtered grid is visible
          }}
          onFindSimilar={handleFindSimilar}
          onCrop={(img) => setCropImages([img])}
        />
      )}

      {/* V18: crop review modal — auto-detects letterbox/chrome, applies on approve */}
      {cropImages && (
        <CropModal
          images={cropImages}
          onClose={(started) => {
            setCropImages(null);
            // V38: a crop batch that actually started leaves Select Mode
            // stuck on (selection intact, Exit not pressed) — clear it the
            // moment cropping begins, same as bulk delete's instant-clear
            // pattern. A cancelled/empty review must leave the selection alone.
            if (started && tagMode) toggleTagMode();
          }}
          onImageCropped={(id, patch) => handleImageUpdated(id, patch)}
        />
      )}

      {/* V32: preview-then-remove a tag across every result of this search */}
      {removingTag && (
        <TagRemovalPreview
          value={removingTag}
          filterParams={buildFilterParams().toString()}
          onClose={() => setRemovingTag(null)}
          onRemoved={(removed, failedBatches) => {
            setRemovingTag(null);
            setTagRemovalMsg(
              failedBatches > 0
                ? `Removed the tag from ${removed} photos — ${failedBatches} batch${failedBatches === 1 ? '' : 'es'} didn't go through, try again.`
                : `Removed the tag from ${removed} photo${removed === 1 ? '' : 's'}.`
            );
            setTimeout(() => setTagRemovalMsg(''), 6000);
            // Re-run the search rather than patching state here: photos that
            // just lost the very tag we're filtered by no longer belong on
            // screen, and the server already knows how to work that out.
            handleBulkMutated();
          }}
        />
      )}

      {/* Duplicate review modal — show when results are ready and user clicks to view */}
      {showDuplicates && duplicateScanStatus && typeof duplicateScanStatus === 'object' && (
        <DuplicateReview
          initialGroups={duplicateScanStatus.groups}
          onClose={() => {
            setShowDuplicates(false);
            setDuplicateScanStatus(null);
          }}
          onImageDeleted={handleImageDeleted}
          onResync={() => fetchPage(0, false)}
        />
      )}

      {/* Toast-style notification when duplicates are found — click to review */}
      {duplicateScanStatus && typeof duplicateScanStatus === 'object' && !showDuplicates && duplicateScanStatus.groups && duplicateScanStatus.groups.length > 0 && (
        <div style={{
          position: 'fixed',
          bottom: '20px',
          right: '20px',
          background: surfaceContainerLow,
          border: `1px solid ${outlineVariant}`,
          borderRadius: '10px',
          padding: '12px 16px',
          cursor: 'pointer',
          fontSize: '13px',
          color: onSurfaceWarm,
          zIndex: 1000,
          maxWidth: '320px',
          boxShadow: `0 12px 32px ${withAlpha(black,0.45)}`
        }}
        onClick={() => setShowDuplicates(true)}
        onMouseEnter={e => e.currentTarget.style.background = surfaceContainerHover}
        onMouseLeave={e => e.currentTarget.style.background = surfaceContainerLow}
        >
          <span style={{ fontWeight: 600, display: 'block', marginBottom: '4px' }}>
            ⧉ {duplicateScanStatus.groups.length} duplicate group{duplicateScanStatus.groups.length === 1 ? '' : 's'} found
          </span>
          <span style={{ fontSize: '11px', color: onSurfaceFaint }}>Click to review</span>
        </div>
      )}

      {/* Tag Mode drawer — right sidebar when tagMode is on and drawer is open */}
      {tagMode && (
        <TagModeBar
          images={images}
          totalResults={similarTo ? images.length : total}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onSelectAllResults={selectAllResults}
          onExit={toggleTagMode}
          onBulkChanged={handleBulkTagsChanged}
          onBulkMutated={handleBulkMutated}
          onBulkDeleted={handleBulkDeleted}
          onResync={() => fetchPage(0, false)}
          onCrop={() => {
            const sel = images.filter(i => selectedIds.has(i.id));
            if (sel.length) setCropImages(sel);
          }}
          isOpen={tagDrawerOpen}
          onClose={closeTagDrawer}
        />
      )}

      <style>{`
        @keyframes fapop {
          from { opacity: 0; transform: translateY(4px) scale(0.99); }
          to   { opacity: 1; transform: none; }
        }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
