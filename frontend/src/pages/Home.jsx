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

const PRESET_SWATCHES = [
  '#D9A441', '#E08840', '#B33A3A', '#C75B8B',
  '#7B5BC7', '#3A5BB3', '#2E8B8B', '#6FA3B8',
  '#4E7A3A', '#8A7A3A', '#E8DFC8', '#1A1A1E'
];

const PER_PAGE = 60;
const FILM_FIELD_LABELS = { title: 'Title', director: 'Director', dp: 'DP' };

// V24 color search. Keep these in step with DEFAULT_PROMINENCE /
// DEFAULT_EXACTNESS in backend/app.py.
const DEFAULT_PROM = 6;    // percent of frame
const DEFAULT_EXACT = 60;  // 0 = any nearby hue, 100 = near-identical hue

// V33: the slider runs 0.5%–95%, not 0.5%–40%. The old 40% ceiling was
// arbitrary — a real photo's biggest single color reaches 96% of the frame,
// and 16 of 19 of Ryan's reference shots have a color over 40%. Capping at 40
// meant "orange is the whole shot" was a question you literally could not ask.
// Log scale because most of the useful range sits low: a linear slider would
// bunch every meaningful setting into its first inch.
const PROM_MIN = 0.5, PROM_SPAN = 190;   // 0.5 * 190 = 95% at the top
const posToProm = (pos) => +(PROM_MIN * Math.pow(PROM_SPAN, pos / 100)).toFixed(1);
const promToPos = (p) => Math.round(100 * Math.log(p / PROM_MIN) / Math.log(PROM_SPAN));

// The slider asks "how much does this color OWN the frame", so it's labelled
// the way Ryan described it rather than as a bare percentage: low means a red
// shirt or red lipstick, high means a red backdrop. Above 50% the color is
// automatically the largest thing in frame — nothing else has room to beat it
// — which is why no separate "dominant color" toggle was needed.
const promLabel = (p) =>
  p < 3  ? 'a small accent' :
  p < 10 ? 'a noticeable part' :
  p < 25 ? 'a major element' :
  p < 50 ? 'most of the frame' :
           'fills the frame';

// V33: exactness now controls hue AND brightness together, because brown is
// not its own hue — brown IS dark orange. See EXACTNESS_TIGHT_VAL in app.py.
const exactLabel = (e) => (e < 25 ? 'very loose' : e < 50 ? 'loose' : e < 75 ? 'close' : 'exact');

export default function Home() {
  const { isAdmin } = useAuth();
  const sync = useSync();
  const isMobile = useIsMobile();
  const [chips, setChips] = useState([]);
  const [nlChips, setNlChips] = useState([]);        // [{phrase, tags[]}]
  const [noteChips, setNoteChips] = useState([]);    // V39: [phrase, phrase, ...] — on-set-notes search
  const [color, setColor] = useState(null);           // active hex or null
  // V24: color search knobs. `prom` = min % of the frame the color must cover,
  // `exact` = 0-100 hue strictness. The *Applied values are what actually get
  // searched — they trail the sliders by a beat so a drag fires one request,
  // not fifty.
  const [prom, setProm] = useState(DEFAULT_PROM);
  const [exact, setExact] = useState(DEFAULT_EXACT);
  const [promApplied, setPromApplied] = useState(DEFAULT_PROM);
  const [exactApplied, setExactApplied] = useState(DEFAULT_EXACT);
  const [film, setFilm] = useState(null);             // film/director/DP text filter
  const [ar, setAr] = useState(null);                 // V15: aspect-ratio bucket, e.g. "2.39:1"
  const [searchText, setSearchText] = useState('');
  const [autocomplete, setAutocomplete] = useState([]);
  const [showAuto, setShowAuto] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [interpreting, setInterpreting] = useState(false);
  const [nlError, setNlError] = useState('');
  const [images, setImages] = useState([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(null);
  const [winW, setWinW] = useState(window.innerWidth);
  const [setupStatus, setSetupStatus] = useState(null); // V17: empty-library checklist

  const [bookmarks, setBookmarks] = useState([]);
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [showDuplicates, setShowDuplicates] = useState(false);
  const [duplicateScanStatus, setDuplicateScanStatus] = useState(null); // null | 'scanning' | { groups: [...] }

  // ── Find Similar mode ────────────────────────────────────────────────────
  const [similarTo, setSimilarTo] = useState(null); // {id, filename} or null
  const [similarNotice, setSimilarNotice] = useState(null); // dismissible banner text

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

  const searchRef = useRef(null);
  const autoDebounce = useRef(null);
  const autoRequestId = useRef(0);
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

  const hasFilters = chips.length > 0 || nlChips.length > 0 || noteChips.length > 0 || !!color || !!film || !!ar;

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

  // Let the slider thumb move freely; commit the value a beat after it settles.
  useEffect(() => {
    const t = setTimeout(() => { setPromApplied(prom); setExactApplied(exact); }, 220);
    return () => clearTimeout(t);
  }, [prom, exact]);

  // ── The active filter, as query params ─────────────────────────────────────
  // One place builds this. The grid, the "select all N results" button and the
  // tag-removal preview all ask the server the SAME question, and if each one
  // assembled its own params they would drift — a select-all that grabs a
  // different set of photos than the grid is showing would be worse than
  // having no select-all at all.
  const buildFilterParams = useCallback(() => {
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
  }, [chips, nlChips, noteChips, color, film, ar, promApplied, exactApplied]);

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

  // ── Load bookmarks on mount ─────────────────────────────────────────────────
  const loadBookmarks = useCallback(async () => {
    try {
      const res = await fetch('/api/bookmarks');
      setBookmarks(await res.json());
    } catch {}
  }, []);

  useEffect(() => { loadBookmarks(); }, [loadBookmarks]);

  // ── Autocomplete: fire 120ms after user stops typing ────────────────────────
  useEffect(() => {
    clearTimeout(autoDebounce.current);
    if (!searchText.trim()) {
      setAutocomplete([]);
      setShowAuto(false);
      return;
    }
    // The debounce timer alone doesn't stop an in-flight fetch for the
    // PREVIOUS keystroke from resolving after this one's — on a slow or
    // jittery connection the older, broader-prefix response (e.g. "ten")
    // can land after the newer, more specific one ("tenet") and silently
    // overwrite it with worse-ranked results. A monotonic request id lets a
    // late response recognize it's stale and drop itself instead.
    const requestId = ++autoRequestId.current;
    autoDebounce.current = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q: searchText });
        if (chips.length) params.set('chips', chips.join(','));
        const res = await fetch(`/api/autocomplete?${params}`);
        const data = await res.json();
        if (requestId !== autoRequestId.current) return; // a newer request has since superseded this one
        setAutocomplete(data);
        setShowAuto(data.length > 0);
        setHighlightedIndex(0);
      } catch {}
    }, 120);
  }, [searchText, chips]);

  // ── Close dropdowns when clicking outside ───────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (!e.target.closest('[data-search-area]')) setShowAuto(false);
      if (!e.target.closest('[data-bookmark-area]')) setShowBookmarks(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // ── Safety net: if the mouse is released outside the grid mid-drag, still end it ─
  useEffect(() => {
    if (!tagMode) return;
    const onUp = () => endDrag();
    window.addEventListener('mouseup', onUp);
    return () => window.removeEventListener('mouseup', onUp);
  }, [tagMode]);

  // ── Keyboard shortcut: 'V' toggles Select Mode ────────────────────────────────
  useEffect(() => {
    const onKeyDown = (e) => {
      // Only trigger if user isn't typing in an input
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.key === 'v' || e.key === 'V') {
        e.preventDefault();
        toggleTagMode();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [tagMode]);

  const addChip = (tag) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    if (!chips.includes(tag)) setChips(prev => [...prev, tag]);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  // Selecting a film match from the search dropdown — same 🎬 filter as
  // clicking a title/director/DP in the detail panel (onSearchFilm below).
  const selectFilm = (name) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    setFilm(name);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  // V15: selecting an aspect-ratio match ("9:16", "2.39:1") from the dropdown
  const selectAr = (label) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    setAr(label);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  // V39: selecting an on-set-notes match from the dropdown — the suggestion
  // IS the search (there's no fixed vocabulary of notes values like tags
  // have), so picking it just locks in the phrase the user already typed.
  const selectNote = (phrase) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    if (!noteChips.includes(phrase)) setNoteChips(prev => [...prev, phrase]);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  const removeChip = (tag) => setChips(prev => prev.filter(t => t !== tag));
  const removeNlChip = (phrase) => setNlChips(prev => prev.filter(n => n.phrase !== phrase));
  const removeNoteChip = (phrase) => setNoteChips(prev => prev.filter(p => p !== phrase));

  // Picking a color while in Find Similar mode exits similar mode first
  const pickColor = (hex) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    setColor(hex);
  };

  const clearAll = () => {
    setChips([]);
    setNlChips([]);
    setNoteChips([]);
    setColor(null);
    setProm(DEFAULT_PROM);
    setExact(DEFAULT_EXACT);
    setFilm(null);
    setAr(null);
    setSimilarTo(null);
    setSimilarNotice(null);
  };

  // ── NL fallback: interpret free text via Gemini ─────────────────────────────
  const interpretPhrase = async (phrase) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    setInterpreting(true);
    setShowAuto(false);
    setNlError('');
    try {
      const res = await fetch('/api/interpret', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phrase })
      });
      const data = await res.json();
      if (!res.ok) {
        setNlError(data.error || 'Could not interpret that phrase.');
      } else if (data.tags && data.tags.length) {
        setNlChips(prev =>
          prev.some(n => n.phrase === phrase) ? prev : [...prev, { phrase, tags: data.tags }]
        );
        setSearchText('');
      }
    } catch (e) {
      console.error('Interpret failed', e);
      setNlError('Could not reach the server.');
    }
    setInterpreting(false);
    searchRef.current?.focus();
  };

  const handleEnter = () => {
    const text = searchText.trim();
    if (!text) return;
    if (showAuto && autocomplete.length > 0) {
      const pick = autocomplete[highlightedIndex] || autocomplete[0];
      if (pick.type === 'film') selectFilm(pick.value);
      else if (pick.type === 'ar') selectAr(pick.value);
      else if (pick.type === 'note') selectNote(pick.value);
      else addChip(pick.value);
    } else {
      interpretPhrase(text);
    }
  };

  const handleSearchKeyDown = (e) => {
    if (e.key === 'ArrowDown') {
      if (!showAuto || !autocomplete.length) return;
      e.preventDefault();
      setHighlightedIndex(i => Math.min(i + 1, autocomplete.length - 1));
    } else if (e.key === 'ArrowUp') {
      if (!showAuto || !autocomplete.length) return;
      e.preventDefault();
      setHighlightedIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter') {
      handleEnter();
    } else if (e.key === 'Escape') {
      setShowAuto(false);
      setSearchText('');
    }
  };

  // ── Bookmarks ───────────────────────────────────────────────────────────────
  const saveBookmark = async () => {
    const name = saveName.trim();
    if (!name || !hasFilters) return;
    try {
      await fetch('/api/bookmarks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, state: { chips, nlChips, noteChips, color, film, ar, prom, exact } })
      });
      setSaveName('');
      loadBookmarks();
    } catch (e) {
      console.error('Save bookmark failed', e);
    }
  };

  const applyBookmark = (bm) => {
    setChips(bm.state.chips || []);
    setNlChips(bm.state.nlChips || []);
    setNoteChips(bm.state.noteChips || []);
    setColor(bm.state.color || null);
    setFilm(bm.state.film || null);
    setAr(bm.state.ar || null);
    // Bookmarks saved before V24 carry no knobs — they take the new defaults,
    // so they come back tighter (and cleaner) than when they were saved.
    setProm(bm.state.prom ?? DEFAULT_PROM);
    setExact(bm.state.exact ?? DEFAULT_EXACT);
    setPromApplied(bm.state.prom ?? DEFAULT_PROM);
    setExactApplied(bm.state.exact ?? DEFAULT_EXACT);
    setShowBookmarks(false);
  };

  const deleteBookmark = async (id, e) => {
    e.stopPropagation();
    try {
      await fetch(`/api/bookmarks/${id}`, { method: 'DELETE' });
      loadBookmarks();
    } catch {}
  };

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
  const startDuplicateScan = async () => {
    setDuplicateScanStatus('scanning');
    try {
      // Call the duplicate scan API in the background
      const res = await fetch('/api/duplicates/scan', { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setDuplicateScanStatus({ groups: data.groups || [] });
        // Show a toast that results are ready
        if (data.groups && data.groups.length > 0) {
          // We'll show the modal when user clicks the notification
        }
      } else {
        setDuplicateScanStatus(null);
      }
    } catch (e) {
      console.error('Duplicate scan failed', e);
      setDuplicateScanStatus(null);
    }
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
        background: '#0a0a0b',
        color: '#efeadd',
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
        position: 'relative'
      }}>

      {/* Whole-page drop target hint — admin only, appears the moment a file
          drag enters anywhere on Home, not just over the Upload button's own
          panel. Drops delegate to that same panel's upload flow. */}
      {pageDragOver && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 500,
          background: 'rgba(10,10,11,0.82)',
          border: '3px dashed #c9a253',
          margin: '10px',
          borderRadius: '16px',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: '10px', pointerEvents: 'none'
        }}>
          <div style={{ fontSize: '32px' }}>⬆</div>
          <div style={{ fontSize: '16px', fontWeight: 600, color: '#efeadd' }}>Drop to upload</div>
          <div style={{ fontSize: '12.5px', color: '#9c988d' }}>Photos go straight into your Drive folder and start tagging automatically</div>
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
          borderBottom: '1px solid rgba(255,255,255,0.065)',
          position: 'relative',
          zIndex: 40
        }}
      >
        <div style={{ display: 'flex', gap: isMobile ? '6px' : '8px', alignItems: 'center' }}>
          {/* Input */}
          <div style={{
            flex: 1,
            minWidth: 0,
            display: 'flex', alignItems: 'center', gap: '12px',
            background: '#18181b',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: '10px',
            padding: '0 14px',
            height: '46px'
          }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
              stroke="rgba(255,255,255,0.3)" strokeWidth="2">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <input
              ref={searchRef}
              value={searchText}
              onChange={e => { setSearchText(e.target.value); if (nlError) setNlError(''); }}
              onKeyDown={handleSearchKeyDown}
              onFocus={() => { if (autocomplete.length) setShowAuto(true); }}
              placeholder="Search tags — or describe a feeling and press Enter…"
              disabled={interpreting}
              style={{
                flex: 1, background: 'transparent', border: 'none', outline: 'none',
                color: '#efeadd', fontFamily: 'inherit', fontSize: '14px'
              }}
            />
            {interpreting && (
              <span style={{
                fontSize: '11px', color: '#8b7cf6',
                display: 'flex', alignItems: 'center', gap: '6px'
              }}>
                <span style={{
                  width: '10px', height: '10px',
                  border: '2px solid rgba(139,124,246,0.25)',
                  borderTopColor: '#8b7cf6',
                  borderRadius: '50%', display: 'inline-block',
                  animation: 'spin 0.7s linear infinite'
                }} />
                interpreting…
              </span>
            )}
          </div>

          {/* V18: Select Mode is for everyone — friends bulk-crop their own
              images and add to decks; the bar's tag panels stay admin-only. */}
          <button
            onClick={toggleTagMode}
            title="Select Mode — bulk-select images to crop, tag, or add to a deck (press V)"
            style={{
              height: isMobile ? '38px' : '46px', width: isMobile ? '38px' : '46px', flexShrink: 0,
              background: tagMode ? 'rgba(184,206,161,0.14)' : '#18181b',
              border: `1px solid ${tagMode ? 'rgba(184,206,161,0.6)' : 'rgba(255,255,255,0.12)'}`,
              borderRadius: '10px',
              cursor: 'pointer',
              color: tagMode ? '#b8cea1' : '#9c988d',
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
                disabled={duplicateScanStatus === 'scanning'}
                title={duplicateScanStatus === 'scanning' ? 'Scanning for duplicates...' : 'Find duplicate images (runs in background)'}
                style={{
                  height: isMobile ? '38px' : '46px', width: isMobile ? '38px' : '46px', flexShrink: 0,
                  background: duplicateScanStatus === 'scanning' ? 'rgba(217,164,65,0.14)' : '#18181b',
                  border: `1px solid ${duplicateScanStatus === 'scanning' ? 'rgba(217,164,65,0.5)' : 'rgba(255,255,255,0.12)'}`,
                  borderRadius: '10px',
                  cursor: duplicateScanStatus === 'scanning' ? 'default' : 'pointer',
                  color: duplicateScanStatus === 'scanning' ? '#d9a441' : '#9c988d',
                  fontSize: '15px',
                  opacity: duplicateScanStatus === 'scanning' ? 0.7 : 1
                }}
              >
                ⧉
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
                  background: sync.running ? 'rgba(217,164,65,0.14)' : '#18181b',
                  border: `1px solid ${sync.running ? 'rgba(217,164,65,0.5)' : 'rgba(255,255,255,0.12)'}`,
                  borderRadius: '10px',
                  cursor: sync.running ? 'default' : 'pointer',
                  color: sync.running ? '#d9a441' : '#9c988d',
                  fontSize: '15px',
                  opacity: sync.running ? 0.9 : 1,
                  transition: 'width 0.2s ease'
                }}
              >
                {sync.running ? (
                  <span style={{
                    width: '12px', height: '12px', flexShrink: 0,
                    border: '2px solid rgba(217,164,65,0.3)', borderTopColor: '#d9a441',
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

          {/* Bookmark button + dropdown */}
          <div data-bookmark-area style={{ position: 'relative', flexShrink: 0 }}>
            <button
              onClick={() => setShowBookmarks(v => !v)}
              title="Saved searches"
              style={{
                height: isMobile ? '38px' : '46px', width: isMobile ? '38px' : '46px',
                background: '#18181b',
                border: `1px solid ${showBookmarks ? 'rgba(201,162,83,0.5)' : 'rgba(255,255,255,0.12)'}`,
                borderRadius: '10px',
                cursor: 'pointer',
                color: showBookmarks ? '#dcbd76' : '#9c988d',
                fontSize: '16px'
              }}
            >
              ☆
            </button>

            {showBookmarks && (
              <div style={{
                position: 'absolute', top: '54px', right: 0,
                width: '300px',
                background: '#18181b',
                border: '1px solid rgba(255,255,255,0.12)',
                borderRadius: '10px',
                boxShadow: '0 20px 48px rgba(0,0,0,0.6)',
                zIndex: 60,
                animation: 'fapop 0.12s ease',
                overflow: 'hidden'
              }}>
                {/* Save current */}
                {hasFilters && (
                  <div style={{
                    padding: '12px 13px',
                    borderBottom: '1px solid rgba(255,255,255,0.065)',
                    display: 'flex', gap: '6px'
                  }}>
                    <input
                      value={saveName}
                      onChange={e => setSaveName(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter') saveBookmark(); }}
                      placeholder="Name this search…"
                      style={{
                        flex: 1, background: '#0a0a0b',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: '6px', padding: '7px 10px',
                        color: '#efeadd', fontSize: '12px',
                        fontFamily: 'inherit', outline: 'none'
                      }}
                    />
                    <button
                      onClick={saveBookmark}
                      style={{
                        background: 'rgba(201,162,83,0.12)',
                        border: '1px solid rgba(201,162,83,0.35)',
                        color: '#dcbd76', borderRadius: '6px',
                        padding: '0 12px', fontSize: '12px',
                        cursor: 'pointer', fontFamily: 'inherit'
                      }}
                    >
                      Save
                    </button>
                  </div>
                )}

                {/* Saved list */}
                <div style={{ maxHeight: '260px', overflowY: 'auto' }}>
                  {bookmarks.length === 0 && (
                    <div style={{ padding: '16px 13px', fontSize: '12px', color: '#65625a' }}>
                      {hasFilters
                        ? 'No saved searches yet — name this one above.'
                        : 'No saved searches yet. Add some filters, then save them here.'}
                    </div>
                  )}
                  {bookmarks.map(bm => (
                    <div
                      key={bm.id}
                      onClick={() => applyBookmark(bm)}
                      style={{
                        padding: '10px 13px',
                        cursor: 'pointer',
                        display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', gap: '8px'
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = '#222226'}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <div style={{ minWidth: 0 }}>
                        <div style={{ fontSize: '13px', color: '#efeadd' }}>{bm.name}</div>
                        <div style={{
                          fontSize: '10.5px', color: '#65625a',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                        }}>
                          {[
                            ...(bm.state.chips || []),
                            ...(bm.state.nlChips || []).map(n => `“${n.phrase}”`),
                            ...(bm.state.noteChips || []).map(p => `🔧 ${p}`),
                            ...(bm.state.film ? [`🎬 ${bm.state.film}`] : []),
                            ...(bm.state.ar ? [`▭ ${bm.state.ar}`] : []),
                            ...(bm.state.color ? [bm.state.color] : [])
                          ].join(' · ') || 'empty'}
                        </div>
                      </div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                        {bm.state.color && (
                          <span style={{
                            width: '12px', height: '12px', borderRadius: '3px',
                            background: bm.state.color,
                            border: '1px solid rgba(255,255,255,0.15)'
                          }} />
                        )}
                        <button
                          onClick={(e) => deleteBookmark(bm.id, e)}
                          style={{
                            background: 'none', border: 'none', color: '#65625a',
                            cursor: 'pointer', fontSize: '14px', padding: '2px'
                          }}
                          onMouseEnter={e => e.currentTarget.style.color = '#cf7152'}
                          onMouseLeave={e => e.currentTarget.style.color = '#65625a'}
                        >×</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {nlError && (
            <p style={{ fontSize: '12px', color: '#ffb4ab', margin: '8px 0 0' }}>
              {nlError}
            </p>
          )}
        </div>

        {/* Autocomplete dropdown */}
        {showAuto && autocomplete.length > 0 && (
          <div style={{
            position: 'absolute', top: '68px', left: '20px', right: '74px',
            background: '#18181b',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: '10px',
            boxShadow: '0 20px 48px rgba(0,0,0,0.6)',
            maxHeight: '320px', overflowY: 'auto',
            zIndex: 50,
            animation: 'fapop 0.12s ease'
          }}>
            <div style={{
              padding: '10px 13px 6px',
              fontSize: '9.5px', fontWeight: 600,
              letterSpacing: '0.12em', color: '#65625a'
            }}>
              MATCHES
            </div>
            {autocomplete.map((opt, i) => (
              <button
                key={`${opt.type}-${opt.value}`}
                onMouseDown={() => {
                  if (opt.type === 'film') selectFilm(opt.value);
                  else if (opt.type === 'ar') selectAr(opt.value);
                  else if (opt.type === 'note') selectNote(opt.value);
                  else addChip(opt.value);
                }}
                onMouseEnter={() => setHighlightedIndex(i)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center',
                  justifyContent: 'space-between', gap: '10px',
                  padding: '8px 13px',
                  background: i === highlightedIndex ? '#222226' : 'transparent',
                  border: 'none',
                  cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit'
                }}
              >
                {opt.type === 'film' ? (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ fontSize: '11px', flexShrink: 0 }}>🎬</span>
                    <span style={{ fontSize: '13.5px', color: '#8fc3d8' }}>{opt.value}</span>
                    <span style={{ fontSize: '11px', color: '#65625a' }}>
                      {FILM_FIELD_LABELS[opt.field] || opt.field}
                    </span>
                  </span>
                ) : opt.type === 'ar' ? (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ fontSize: '11px', flexShrink: 0 }}>▭</span>
                    <span style={{ fontSize: '13.5px', color: '#7dd3c8' }}>{opt.value}</span>
                    <span style={{ fontSize: '11px', color: '#65625a' }}>Aspect Ratio</span>
                  </span>
                ) : opt.type === 'note' ? (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{ fontSize: '11px', flexShrink: 0 }}>🔧</span>
                    <span style={{ fontSize: '13.5px', color: '#e0935a' }}>{opt.value}</span>
                    <span style={{ fontSize: '11px', color: '#65625a' }}>On-Set Notes</span>
                  </span>
                ) : (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <span style={{
                      width: '7px', height: '7px', borderRadius: '2px',
                      background: opt.color, flexShrink: 0
                    }} />
                    <span style={{ fontSize: '13.5px', color: '#efeadd' }}>{opt.value}</span>
                    <span style={{ fontSize: '11px', color: '#65625a' }}>{opt.catLabel}</span>
                  </span>
                )}
                <span style={{
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: '10px', color: '#65625a'
                }}>{opt.count}</span>
              </button>
            ))}
          </div>
        )}

        {/* Color swatch strip */}
        <div style={{
          display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '7px', marginTop: '12px'
        }}>
          <span style={{
            fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em',
            color: '#65625a', marginRight: '2px'
          }}>COLOR</span>
          {PRESET_SWATCHES.map(hex => (
            <button
              key={hex}
              title={hex}
              onClick={() => pickColor(color === hex ? null : hex)}
              style={{
                width: '20px', height: '20px', borderRadius: '50%',
                background: hex,
                border: color === hex
                  ? '2px solid #efeadd'
                  : '1px solid rgba(255,255,255,0.15)',
                cursor: 'pointer', padding: 0,
                transform: color === hex ? 'scale(1.15)' : 'scale(1)',
                transition: 'transform 0.12s ease'
              }}
            />
          ))}
          {/* Color wheel — custom pick, like Sidus Link */}
          <label
            title="Pick a custom color"
            style={{
              width: '20px', height: '20px', borderRadius: '50%',
              background: 'conic-gradient(red, yellow, lime, cyan, blue, magenta, red)',
              border: color && !PRESET_SWATCHES.includes(color)
                ? '2px solid #efeadd'
                : '1px solid rgba(255,255,255,0.15)',
              cursor: 'pointer', position: 'relative', overflow: 'hidden',
              transform: color && !PRESET_SWATCHES.includes(color) ? 'scale(1.15)' : 'scale(1)',
              transition: 'transform 0.12s ease'
            }}
          >
            <input
              type="color"
              value={color || '#D9A441'}
              onChange={e => pickColor(e.target.value)}
              style={{
                position: 'absolute', inset: 0, opacity: 0,
                width: '100%', height: '100%', cursor: 'pointer'
              }}
            />
          </label>
          {color && (
            <button
              onClick={() => setColor(null)}
              style={{
                background: 'none', border: 'none', color: '#65625a',
                cursor: 'pointer', fontSize: '11px', fontFamily: 'inherit',
                padding: '2px 4px'
              }}
              onMouseEnter={e => e.currentTarget.style.color = '#cf7152'}
              onMouseLeave={e => e.currentTarget.style.color = '#65625a'}
            >
              clear color
            </button>
          )}
        </div>

        {/* V24: dominance + shade-match sliders. Only meaningful with a color
            picked, so they stay out of the way until then. The live match
            count is the whole point — it turns "is 6% right?" into something
            you can see instead of guess.
            V33 renamed both: "coverage" read as a bare percentage nobody could
            picture, and "hue match" promised something hue alone cannot deliver
            (brown and orange are the same hue, so only brightness separates
            them). The words now describe what the knobs actually do. */}
        {color && (
          <div style={{
            display: 'flex', alignItems: 'center', flexWrap: 'wrap',
            gap: isMobile ? '10px' : '18px',
            marginTop: '10px', padding: '9px 12px',
            background: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.07)',
            borderRadius: '7px'
          }}>
            {[
              {
                key: 'prom',
                label: 'DOMINANCE',
                value: `${promLabel(prom)} · ${prom}%`,
                pos: promToPos(prom),
                onChange: (pos) => setProm(posToProm(pos)),
                hint: 'How much this color owns the shot — drag left for an '
                    + 'accent like a red shirt, right for a red backdrop'
              },
              {
                key: 'exact',
                label: 'SHADE MATCH',
                value: exactLabel(exact),
                pos: exact,
                onChange: (pos) => setExact(pos),
                hint: 'How close the shade has to be to the one you picked, in '
                    + 'both color and brightness — drag right to stop dark '
                    + 'brown counting as orange'
              }
            ].map(s => (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
                <span title={s.hint} style={{
                  fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em',
                  color: '#65625a', cursor: 'help', whiteSpace: 'nowrap'
                }}>{s.label}</span>
                <input
                  type="range"
                  min="0" max="100" step="1"
                  value={s.pos}
                  onChange={e => s.onChange(Number(e.target.value))}
                  aria-label={s.hint}
                  style={{ width: isMobile ? '110px' : '130px', accentColor: '#D9A441', cursor: 'pointer' }}
                />
                <span style={{
                  fontSize: '11.5px', color: '#9c988d', whiteSpace: 'nowrap',
                  minWidth: s.key === 'prom' ? '152px' : '62px'
                }}>{s.value}</span>
              </div>
            ))}

            <span style={{ fontSize: '11.5px', color: '#65625a', whiteSpace: 'nowrap' }}>
              {loading ? '…' : `${total} image${total === 1 ? '' : 's'}`}
            </span>

            {(prom !== DEFAULT_PROM || exact !== DEFAULT_EXACT) && (
              <button
                onClick={() => { setProm(DEFAULT_PROM); setExact(DEFAULT_EXACT); }}
                style={{
                  background: 'none', border: 'none', color: '#65625a',
                  cursor: 'pointer', fontSize: '11px', fontFamily: 'inherit', padding: '2px 4px'
                }}
                onMouseEnter={e => e.currentTarget.style.color = '#cf7152'}
                onMouseLeave={e => e.currentTarget.style.color = '#65625a'}
              >reset</button>
            )}
          </div>
        )}

        {/* Active chips (tags + NL phrases + notes phrases + film + aspect ratio + similar) */}
        {(chips.length > 0 || nlChips.length > 0 || noteChips.length > 0 || film || ar || similarTo) && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px', marginTop: '12px' }}>
            {/* Similar chip — from "Find Similar" in the detail panel. Soft violet, distinct from NL/film chips */}
            {similarTo && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: 'rgba(178,130,240,0.14)',
                border: '1px solid rgba(178,130,240,0.5)',
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: '#c9a8f2', fontWeight: 500
              }}>
                ≈ Similar to {similarTo.filename}
                <button
                  onClick={clearSimilar}
                  style={{
                    background: 'none', border: 'none', color: '#c9a8f2',
                    cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >×</button>
              </span>
            )}
            {/* Film chip — from clicking a title/director/DP in the detail panel */}
            {film && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: 'rgba(111,163,184,0.12)',
                border: '1px solid rgba(111,163,184,0.45)',
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: '#8fc3d8', fontWeight: 500
              }}>
                🎬 {film}
                <button
                  onClick={() => setFilm(null)}
                  style={{
                    background: 'none', border: 'none', color: '#8fc3d8',
                    cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >×</button>
              </span>
            )}
            {/* Aspect-ratio chip (V15) — from picking a format in the search dropdown */}
            {ar && (
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: 'rgba(125,211,200,0.12)',
                border: '1px solid rgba(125,211,200,0.45)',
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: '#7dd3c8', fontWeight: 500
              }}>
                ▭ {ar}
                <button
                  onClick={() => setAr(null)}
                  style={{
                    background: 'none', border: 'none', color: '#7dd3c8',
                    cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >×</button>
              </span>
            )}
            {/* Exact-tag chips. The # and the tooltip exist because these look
                almost identical to the natural-language chips below them, and
                not knowing which kind you had is what made a bulk tag edit
                come up empty — the two find completely different photos. */}
            {chips.map(chip => (
              <span key={chip} title={`Exact tag — showing only photos actually tagged “${chip}”`} style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: 'rgba(201,162,83,0.12)',
                border: '1px solid rgba(201,162,83,0.35)',
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: '#c9a253', fontWeight: 500
              }}>
                <span style={{ opacity: 0.65 }}>#</span>{chip}
                <button
                  onClick={() => removeChip(chip)}
                  style={{
                    background: 'none', border: 'none', color: '#c9a253',
                    cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >×</button>
              </span>
            ))}

            {/* NL phrase chips — styled differently (violet, italic, quoted) */}
            {nlChips.map(nl => (
              <span
                key={nl.phrase}
                title={`Describe-it search — finds photos tagged any of: ${nl.tags.join(', ')}`}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '6px',
                  background: 'rgba(139,124,246,0.12)',
                  border: '1px dashed rgba(139,124,246,0.45)',
                  borderRadius: '6px',
                  padding: '4px 8px 4px 9px',
                  fontSize: '12.5px', color: '#a99bf7',
                  fontStyle: 'italic'
                }}
              >
                “{nl.phrase}”
                <button
                  onClick={() => removeNlChip(nl.phrase)}
                  style={{
                    background: 'none', border: 'none', color: '#a99bf7',
                    cursor: 'pointer', padding: 0, fontSize: '14px',
                    lineHeight: 1, opacity: 0.6, fontStyle: 'normal'
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >×</button>
              </span>
            ))}

            {/* On-set-notes chips (V39) — amber, distinct from gold tag chips
                and violet NL chips, so it's clear at a glance the match came
                from Camera/Lens/Filter/Stop/On-Set Notes, not a tag. */}
            {noteChips.map(phrase => (
              <span
                key={phrase}
                title={`On-set notes search — finds photos whose Camera/Lens/Filter/Stop/On-Set Notes mention “${phrase}”`}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '6px',
                  background: 'rgba(224,147,90,0.14)',
                  border: '1px solid rgba(224,147,90,0.45)',
                  borderRadius: '6px',
                  padding: '4px 8px 4px 9px',
                  fontSize: '12.5px', color: '#e0935a', fontWeight: 500
                }}
              >
                🔧 {phrase}
                <button
                  onClick={() => removeNoteChip(phrase)}
                  style={{
                    background: 'none', border: 'none', color: '#e0935a',
                    cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                  }}
                  onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
                >×</button>
              </span>
            ))}

            <button
              onClick={clearAll}
              style={{
                background: 'none', border: 'none', color: '#65625a',
                cursor: 'pointer', fontSize: '12px', padding: '4px 6px',
                fontFamily: 'inherit'
              }}
              onMouseEnter={e => e.currentTarget.style.color = '#cf7152'}
              onMouseLeave={e => e.currentTarget.style.color = '#65625a'}
            >
              Clear all
            </button>
          </div>
        )}

        {/* The search bar has two modes that used to look the same: picking a
            tag from the dropdown filters by that exact tag, while typing a
            phrase and pressing Enter looks for photos that FEEL like it. The
            second one returns photos that don't carry the word you typed,
            which is bewildering if nobody tells you — so say it out loud
            whenever a describe-it search is on. */}
        {nlChips.length > 0 && (
          <div style={{
            display: 'flex', gap: '8px', marginTop: '10px',
            padding: '8px 12px',
            background: 'rgba(139,124,246,0.07)',
            border: '1px solid rgba(139,124,246,0.22)',
            borderRadius: '7px',
            fontSize: '11.5px', color: '#a99bf7', lineHeight: 1.55
          }}>
            <span style={{ flexShrink: 0 }}>ⓘ</span>
            <span>
              The dashed violet {nlChips.length === 1 ? 'chip is a' : 'chips are'} <strong>describe-it
              {nlChips.length === 1 ? ' search' : ' searches'}</strong> — {nlChips.length === 1 ? 'it looks' : 'they look'} for
              photos that feel like {nlChips.length === 1 ? 'that phrase' : 'those phrases'}, so the results may not carry that
              exact tag. To filter by a real tag instead, type it and pick it from the dropdown list —
              those chips are gold and start with a #.
            </span>
          </div>
        )}

        {/* Amber chips (V39) — say out loud what they matched, same reasoning
            as the violet describe-it note above: an on-set-notes match can
            easily be confused for a tag match otherwise. */}
        {noteChips.length > 0 && (
          <div style={{
            display: 'flex', gap: '8px', marginTop: '10px',
            padding: '8px 12px',
            background: 'rgba(224,147,90,0.07)',
            border: '1px solid rgba(224,147,90,0.22)',
            borderRadius: '7px',
            fontSize: '11.5px', color: '#e0935a', lineHeight: 1.55
          }}>
            <span style={{ flexShrink: 0 }}>ⓘ</span>
            <span>
              The amber 🔧 {noteChips.length === 1 ? 'chip matches' : 'chips match'} <strong>on-set
              notes</strong> — Camera/Rig, Lens, Lens Filter, Stop, or the On-Set Notes box on a photo's
              detail panel, not a tag.
            </span>
          </div>
        )}

        {/* Find Similar notice — e.g. image has no fingerprint yet */}
        {similarNotice && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: '10px',
            marginTop: '12px', padding: '9px 13px',
            background: 'rgba(178,130,240,0.08)',
            border: '1px solid rgba(178,130,240,0.28)',
            borderRadius: '8px',
            fontSize: '12px', color: '#c9a8f2'
          }}>
            <span style={{ flex: 1 }}>{similarNotice}</span>
            <button
              onClick={() => setSimilarNotice(null)}
              style={{
                background: 'none', border: 'none', color: '#c9a8f2',
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
        borderBottom: '1px solid rgba(255,255,255,0.065)',
        display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap', rowGap: '8px'
      }}>
        <span style={{
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '12px', color: '#9c988d'
        }}>
          <span style={{ color: '#efeadd', fontWeight: 500 }}>{total}</span> images
          {images.length > 0 && images.length < total && (
            <span style={{ color: '#65625a' }}> · {images.length} loaded</span>
          )}
          {similarTo && (
            <span style={{ color: '#65625a' }}> · showing similar matches</span>
          )}
          {!similarTo && hasFilters && (
            <span style={{ color: '#65625a' }}>
              {' '}· {chips.length + nlChips.length + noteChips.length + (color ? 1 : 0) + (film ? 1 : 0)} filter{(chips.length + nlChips.length + noteChips.length + (color ? 1 : 0) + (film ? 1 : 0)) > 1 ? 's' : ''} active
            </span>
          )}
        </span>
        {loading && (
          <span style={{
            width: '12px', height: '12px',
            border: '2px solid rgba(201,162,83,0.2)',
            borderTopColor: '#c9a253',
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
              background: 'rgba(217,164,65,0.10)',
              border: '1px solid rgba(217,164,65,0.35)',
              color: '#dcbd76', borderRadius: '7px', padding: '5px 11px',
              cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit'
            }}
          >
            Remove tag “{chip}” from all {total}…
          </button>
        ))}

        {tagRemovalMsg && (
          <span style={{ fontSize: '11.5px', color: '#b8cea1' }}>{tagRemovalMsg}</span>
        )}

        <div style={{ flex: 1 }} />

        {/* Grid density — smaller column target width = more, smaller tiles */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }} title="Grid density">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#65625a" strokeWidth="2">
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
            style={{ width: '90px', accentColor: '#c9a253' }}
          />
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#65625a" strokeWidth="2">
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
              <h2 style={{ fontSize: '20px', fontWeight: 700, color: '#efeadd', margin: '0 0 6px' }}>
                Welcome to Frame Atlas
              </h2>
              <p style={{ fontSize: '13px', color: '#9c988d', margin: 0 }}>
                Three steps and your own reference library is live:
              </p>
            </div>
            <div style={{
              width: 'min(440px, 90%)', background: '#1a1c20',
              border: '1px solid #44474f', borderRadius: '14px', padding: '10px 8px'
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
                  borderBottom: i < 2 ? '1px solid rgba(255,255,255,0.05)' : 'none'
                }}>
                  <div style={{
                    width: '26px', height: '26px', borderRadius: '50%', flexShrink: 0,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: '13px', fontWeight: 700,
                    background: step.done ? 'rgba(127,184,127,0.15)' : 'rgba(201,162,83,0.1)',
                    border: `1px solid ${step.done ? 'rgba(127,184,127,0.5)' : 'rgba(201,162,83,0.35)'}`,
                    color: step.done ? '#7fb87f' : '#c9a253'
                  }}>
                    {step.done ? '✓' : i + 1}
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: '13.5px', fontWeight: 600, color: step.done ? '#7fb87f' : '#efeadd' }}>
                      {step.label}
                      {step.optional && <span style={{ color: '#65625a', fontWeight: 400 }}> (optional)</span>}
                    </div>
                    <div style={{ fontSize: '11.5px', color: '#65625a', marginTop: '2px' }}>{step.sub}</div>
                  </div>
                  <span style={{ color: '#65625a', fontSize: '14px' }}>→</span>
                </Link>
              ))}
            </div>
            <Link to="/account" style={{
              background: '#d9a441', color: '#3d2f00', borderRadius: '8px',
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
            gap: '10px', color: '#65625a'
          }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="1.5" opacity="0.4">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <p style={{ fontSize: '14px', color: '#9c988d' }}>
              {similarTo
                ? 'No similar images found'
                : hasFilters ? 'No images match this filter' : 'No images yet — run a sync first'}
            </p>
            {similarTo ? (
              <button
                onClick={clearSimilar}
                style={{
                  fontSize: '12px', color: '#dcbd76', background: 'none',
                  border: '1px solid rgba(201,162,83,0.3)',
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
                  fontSize: '12px', color: '#dcbd76', background: 'none',
                  border: '1px solid rgba(201,162,83,0.3)',
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
                    background: '#3d3d42',
                    borderRadius: '6px',
                    overflow: 'hidden',
                    cursor: 'pointer',
                    border: isSelected ? '2px solid #b8cea1' : '1px solid rgba(255,255,255,0.04)',
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
                    background: 'linear-gradient(180deg, rgba(0,0,0,0) 40%, rgba(0,0,0,0.78) 100%)',
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
                        color: img.is_favorite ? '#dcbd76' : 'rgba(239,234,221,0.65)',
                        opacity: img.is_favorite ? 1 : (isMobile ? 0.55 : 0),
                        transition: 'opacity 120ms ease',
                        filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.7))'
                      }}
                    >★</button>
                  )}
                  {tagMode && img.is_favorite && (
                    <span style={{
                      position: 'absolute', top: '6px', right: '7px',
                      color: '#dcbd76', fontSize: '13px',
                      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.7))'
                    }}>★</span>
                  )}
                  {img.is_flagged && (
                    <span style={{
                      position: 'absolute', top: '6px', right: '7px',
                      color: '#cf7152', fontSize: '12px'
                    }}>⚑</span>
                  )}

                  {/* Similarity badge — only shown while browsing "Find Similar" results */}
                  {similarTo && typeof img.similarity === 'number' && (
                    <span style={{
                      position: 'absolute', bottom: '7px', right: '7px',
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: '9px', color: '#c9a8f2',
                      background: 'rgba(30,20,45,0.55)',
                      border: '1px solid rgba(178,130,240,0.35)',
                      padding: '2px 6px', borderRadius: '4px'
                    }}>
                      {Math.round(img.similarity * 100)}%
                    </span>
                  )}

                  {/* Tag Mode selection checkmark — top-right, offset clear of star/flag */}
                  {isSelected && (
                    <span style={{
                      position: 'absolute', top: '6px', right: '28px',
                      width: '18px', height: '18px', borderRadius: '50%',
                      background: '#b8cea1',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      boxShadow: '0 1px 3px rgba(0,0,0,0.5)'
                    }}>
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                        stroke="#243516" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
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
            background: 'rgba(184,206,161,0.14)',
            border: '1px solid #b8cea1',
            pointerEvents: 'none',
            zIndex: 500
          }} />
        )}

        {/* Infinite-scroll sentinel — when this nears the viewport, load more */}
        <div ref={sentinelRef} style={{ height: '1px' }} />

        {hasMore && (
          <div style={{
            padding: '20px', textAlign: 'center',
            fontSize: '12px', color: '#65625a',
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
          background: '#1a1c20',
          border: '1px solid #44474f',
          borderRadius: '10px',
          padding: '12px 16px',
          cursor: 'pointer',
          fontSize: '13px',
          color: '#efeadd',
          zIndex: 1000,
          maxWidth: '320px',
          boxShadow: '0 12px 32px rgba(0,0,0,0.45)'
        }}
        onClick={() => setShowDuplicates(true)}
        onMouseEnter={e => e.currentTarget.style.background = '#222226'}
        onMouseLeave={e => e.currentTarget.style.background = '#1a1c20'}
        >
          <span style={{ fontWeight: 600, display: 'block', marginBottom: '4px' }}>
            ⧉ {duplicateScanStatus.groups.length} duplicate group{duplicateScanStatus.groups.length === 1 ? '' : 's'} found
          </span>
          <span style={{ fontSize: '11px', color: '#65625a' }}>Click to review</span>
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
