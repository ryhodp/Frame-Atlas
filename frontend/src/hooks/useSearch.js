import { useState, useEffect, useRef, useCallback } from 'react';
import {
  DEFAULT_PROM, DEFAULT_EXACT,
  hasActiveFilters, filterParams, bookmarkState, filtersFromBookmark,
} from '../searchParams';

// useSearch — every piece of Home's search state and behaviour (Day 44 / V92).
//
// Filters (tag chips, describe-it chips, on-set-notes chips, colour + its two
// sliders, film, aspect ratio), the search box with its autocomplete dropdown
// and the Gemini describe-it fallback, and saved bookmarks. Moved out of
// Home.jsx unchanged — Home destructures every name this returns, so its JSX
// reads exactly as before.
//
// Search does NOT own "Find Similar" mode, but most search actions leave it.
// Home passes two callbacks for that, and the difference between them is
// real, not cosmetic:
//   onBeforeFilter() — called by every action that starts a new filter
//                      (add a chip, pick a colour, film, ratio, note, or a
//                      describe-it search). Home only exits Similar mode if
//                      it's ACTIVE, so a stale "not fingerprinted yet" notice
//                      survives adding a tag, exactly as before.
//   onClearAll()     — called by "Clear all", which always wipes Similar
//                      mode and its notice.
//
// The pure parts (the server query, bookmark save/restore) live in
// searchParams.js with their own test.
export function useSearch({ onBeforeFilter, onClearAll }) {
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

  const [bookmarks, setBookmarks] = useState([]);
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [saveName, setSaveName] = useState('');

  const searchRef = useRef(null);
  const autoDebounce = useRef(null);
  const autoRequestId = useRef(0);

  const hasFilters = hasActiveFilters({ chips, nlChips, noteChips, color, film, ar });

  // Let the slider thumb move freely; commit the value a beat after it settles.
  useEffect(() => {
    const t = setTimeout(() => { setPromApplied(prom); setExactApplied(exact); }, 220);
    return () => clearTimeout(t);
  }, [prom, exact]);

  // The active filter, as query params — see filterParams() in searchParams.js
  // for why there is exactly one of these.
  const buildFilterParams = useCallback(
    () => filterParams({ chips, nlChips, noteChips, color, promApplied, exactApplied, film, ar }),
    [chips, nlChips, noteChips, color, film, ar, promApplied, exactApplied]
  );

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

  const addChip = (tag) => {
    onBeforeFilter();
    if (!chips.includes(tag)) setChips(prev => [...prev, tag]);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  // Selecting a film match from the search dropdown — same 🎬 filter as
  // clicking a title/director/DP in the detail panel (onSearchFilm in Home).
  const selectFilm = (name) => {
    onBeforeFilter();
    setFilm(name);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  // V15: selecting an aspect-ratio match ("9:16", "2.39:1") from the dropdown
  const selectAr = (label) => {
    onBeforeFilter();
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
    onBeforeFilter();
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
    onBeforeFilter();
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
    onClearAll();
  };

  // ── NL fallback: interpret free text via Gemini ─────────────────────────────
  const interpretPhrase = async (phrase) => {
    onBeforeFilter();
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
        body: JSON.stringify({ name, state: bookmarkState({ chips, nlChips, noteChips, color, film, ar, prom, exact }) })
      });
      setSaveName('');
      loadBookmarks();
    } catch (e) {
      console.error('Save bookmark failed', e);
    }
  };

  const applyBookmark = (bm) => {
    const f = filtersFromBookmark(bm.state);
    setChips(f.chips);
    setNlChips(f.nlChips);
    setNoteChips(f.noteChips);
    setColor(f.color);
    setFilm(f.film);
    setAr(f.ar);
    // Applied values are set straight away too (not left to the slider
    // debounce), so the restored search runs once with the restored knobs.
    setProm(f.prom);
    setExact(f.exact);
    setPromApplied(f.prom);
    setExactApplied(f.exact);
    setShowBookmarks(false);
  };

  const deleteBookmark = async (id, e) => {
    e.stopPropagation();
    try {
      await fetch(`/api/bookmarks/${id}`, { method: 'DELETE' });
      loadBookmarks();
    } catch {}
  };

  return {
    // filter state
    chips, setChips, nlChips, setNlChips, noteChips, setNoteChips,
    color, setColor, prom, setProm, exact, setExact,
    promApplied, setPromApplied, exactApplied, setExactApplied,
    film, setFilm, ar, setAr, hasFilters, buildFilterParams,
    // search box + autocomplete + describe-it
    searchText, setSearchText, autocomplete, setAutocomplete,
    showAuto, setShowAuto, highlightedIndex, setHighlightedIndex,
    interpreting, nlError, setNlError, searchRef,
    addChip, selectFilm, selectAr, selectNote,
    removeChip, removeNlChip, removeNoteChip, pickColor, clearAll,
    interpretPhrase, handleEnter, handleSearchKeyDown,
    // bookmarks
    bookmarks, showBookmarks, setShowBookmarks, saveName, setSaveName,
    loadBookmarks, saveBookmark, applyBookmark, deleteBookmark,
  };
}
