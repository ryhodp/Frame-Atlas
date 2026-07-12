import { useState, useEffect, useRef, useCallback } from 'react';
import ImageDetail from '../components/ImageDetail';
import DuplicateReview from '../components/DuplicateReview';
import UploadButton from '../components/UploadButton';
import TagModeBar from '../components/TagModeBar';
import { useAuth } from '../AuthContext';

const PRESET_SWATCHES = [
  '#D9A441', '#E08840', '#B33A3A', '#C75B8B',
  '#7B5BC7', '#3A5BB3', '#2E8B8B', '#6FA3B8',
  '#4E7A3A', '#8A7A3A', '#E8DFC8', '#1A1A1E'
];

const PER_PAGE = 60;

export default function Home() {
  const { isAdmin } = useAuth();
  const [chips, setChips] = useState([]);
  const [nlChips, setNlChips] = useState([]);        // [{phrase, tags[]}]
  const [color, setColor] = useState(null);           // active hex or null
  const [film, setFilm] = useState(null);             // film/director/DP text filter
  const [searchText, setSearchText] = useState('');
  const [autocomplete, setAutocomplete] = useState([]);
  const [showAuto, setShowAuto] = useState(false);
  const [interpreting, setInterpreting] = useState(false);
  const [images, setImages] = useState([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(null);
  const [winW, setWinW] = useState(window.innerWidth);
  const [recent, setRecent] = useState([]);       // Recently Added strip (last 7 days)

  const [bookmarks, setBookmarks] = useState([]);
  const [showBookmarks, setShowBookmarks] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [showDuplicates, setShowDuplicates] = useState(false);

  // ── Find Similar mode ────────────────────────────────────────────────────
  const [similarTo, setSimilarTo] = useState(null); // {id, filename} or null
  const [similarNotice, setSimilarNotice] = useState(null); // dismissible banner text

  // ── Tag Mode: bulk-select images and bulk-edit their tags ───────────────────
  const [tagMode, setTagMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [dragRect, setDragRect] = useState(null); // {left, top, width, height} in viewport coords, or null
  const tileRefs = useRef(new Map()); // image id -> tile DOM node
  const dragStateRef = useRef(null); // { startX, startY, dragging, baseSelected }
  const justDraggedRef = useRef(false); // true for the brief window between mouseup-after-drag and the resulting click

  const searchRef = useRef(null);
  const autoDebounce = useRef(null);
  const pageRef = useRef(0);
  const fetchingRef = useRef(false);
  const sentinelRef = useRef(null);

  const hasFilters = chips.length > 0 || nlChips.length > 0 || !!color || !!film;

  // ── Fetch one page of results; append=true keeps existing images ───────────
  const fetchPage = useCallback(async (pageNum, append) => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (chips.length) params.set('chips', chips.join(','));
      if (nlChips.length) params.set('nl', JSON.stringify(nlChips.map(n => n.tags)));
      if (color) params.set('color', color);
      if (film) params.set('film', film);
      params.set('page', pageNum);
      params.set('per', PER_PAGE);
      const res = await fetch(`/api/search?${params}`);
      const data = await res.json();
      setImages(prev => append ? [...prev, ...(data.images || [])] : (data.images || []));
      setTotal(data.total || 0);
      setHasMore(!!data.has_more);
      pageRef.current = pageNum;
    } catch (e) {
      console.error('Search failed', e);
    }
    setLoading(false);
    fetchingRef.current = false;
  }, [chips, nlChips, color, film]);

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

  // ── Track window width for responsive column count ─────────────────────────
  useEffect(() => {
    const onResize = () => setWinW(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // ── Recently Added strip: anything from the last 7 days, capped at 30 ──────
  // The strip hides itself when nothing is recent (or while filtering).
  const loadRecent = useCallback(async () => {
    try {
      const res = await fetch('/api/views/recent?days=7&limit=30');
      const data = await res.json();
      setRecent(data.images || []);
    } catch {}
  }, []);

  useEffect(() => { loadRecent(); }, [loadRecent]);

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
    autoDebounce.current = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q: searchText });
        if (chips.length) params.set('chips', chips.join(','));
        const res = await fetch(`/api/autocomplete?${params}`);
        const data = await res.json();
        setAutocomplete(data);
        setShowAuto(data.length > 0);
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

  const addChip = (tag) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    if (!chips.includes(tag)) setChips(prev => [...prev, tag]);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  const removeChip = (tag) => setChips(prev => prev.filter(t => t !== tag));
  const removeNlChip = (phrase) => setNlChips(prev => prev.filter(n => n.phrase !== phrase));

  // Picking a color while in Find Similar mode exits similar mode first
  const pickColor = (hex) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    setColor(hex);
  };

  const clearAll = () => {
    setChips([]);
    setNlChips([]);
    setColor(null);
    setFilm(null);
    setSimilarTo(null);
    setSimilarNotice(null);
  };

  // ── NL fallback: interpret free text via Gemini ─────────────────────────────
  const interpretPhrase = async (phrase) => {
    if (similarTo) { setSimilarTo(null); setSimilarNotice(null); }
    setInterpreting(true);
    setShowAuto(false);
    try {
      const res = await fetch('/api/interpret', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phrase })
      });
      const data = await res.json();
      if (data.tags && data.tags.length) {
        setNlChips(prev =>
          prev.some(n => n.phrase === phrase) ? prev : [...prev, { phrase, tags: data.tags }]
        );
        setSearchText('');
      }
    } catch (e) {
      console.error('Interpret failed', e);
    }
    setInterpreting(false);
    searchRef.current?.focus();
  };

  const handleEnter = () => {
    const text = searchText.trim();
    if (!text) return;
    if (autocomplete.length > 0) {
      addChip(autocomplete[0].value);
    } else {
      interpretPhrase(text);
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
        body: JSON.stringify({ name, state: { chips, nlChips, color, film } })
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
    setColor(bm.state.color || null);
    setFilm(bm.state.film || null);
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
    setRecent(prev => prev.map(img => img.id === id ? { ...img, ...patch } : img));
    setSelectedImage(prev => (prev && prev.id === id) ? { ...prev, ...patch } : prev);
  };

  const handleImageDeleted = (id) => {
    setImages(prev => prev.filter(img => img.id !== id));
    setRecent(prev => prev.filter(img => img.id !== id));
    setTotal(t => Math.max(0, t - 1));
    setSelectedImage(prev => (prev && prev.id === id) ? null : prev);
  };

  // ── Tag Mode: toggling in/out, tile clicks, box-select drag ─────────────────
  const toggleTagMode = () => {
    setTagMode(v => {
      const next = !v;
      if (!next) setSelectedIds(new Set()); // turning OFF clears selection
      return next;
    });
  };

  const toggleTileSelection = (id) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  // Apply a bulk patch to any currently-loaded images that were part of the bulk op
  const handleBulkTagsChanged = (ids, patchFn) => {
    const idSet = new Set(ids);
    setImages(prev => prev.map(img => idSet.has(img.id) ? patchFn(img) : img));
    setSelectedImage(prev => (prev && idSet.has(prev.id)) ? patchFn(prev) : prev);
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
  // images that are already on screen.
  const colCount = Math.max(2, Math.min(5, Math.floor((winW - 280) / 320)));
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
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: '#0a0a0b',
      color: '#efeadd',
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif"
    }}>

      {/* ── Search bar ─────────────────────────────────────────────────────── */}
      <div
        data-search-area
        style={{
          padding: '16px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.065)',
          position: 'relative',
          zIndex: 40
        }}
      >
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {/* Input */}
          <div style={{
            flex: 1,
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
              onChange={e => setSearchText(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') handleEnter();
                if (e.key === 'Escape') { setShowAuto(false); setSearchText(''); }
              }}
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

          {/* Upload, Tag Mode, and Duplicate review all edit the shared library
              directly — admin-only until per-user libraries exist (Day 17). */}
          {isAdmin && (
            <>
              <UploadButton onUploaded={() => { fetchPage(0, false); loadRecent(); }} />

              <button
                onClick={toggleTagMode}
                title="Tag Mode — bulk-select images and bulk-edit their tags"
                style={{
                  height: '46px', width: '46px',
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

              <button
                onClick={() => setShowDuplicates(true)}
                title="Find duplicate images"
                style={{
                  height: '46px', width: '46px',
                  background: '#18181b',
                  border: '1px solid rgba(255,255,255,0.12)',
                  borderRadius: '10px',
                  cursor: 'pointer',
                  color: '#9c988d',
                  fontSize: '15px'
                }}
              >
                ⧉
              </button>
            </>
          )}

          {/* Bookmark button + dropdown */}
          <div data-bookmark-area style={{ position: 'relative' }}>
            <button
              onClick={() => setShowBookmarks(v => !v)}
              title="Saved searches"
              style={{
                height: '46px', width: '46px',
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
                            ...(bm.state.film ? [`🎬 ${bm.state.film}`] : []),
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
              MATCHING TAGS
            </div>
            {autocomplete.map(opt => (
              <button
                key={opt.value}
                onMouseDown={() => addChip(opt.value)}
                style={{
                  width: '100%', display: 'flex', alignItems: 'center',
                  justifyContent: 'space-between', gap: '10px',
                  padding: '8px 13px',
                  background: 'transparent', border: 'none',
                  cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit'
                }}
                onMouseEnter={e => e.currentTarget.style.background = '#222226'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              >
                <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{
                    width: '7px', height: '7px', borderRadius: '2px',
                    background: opt.color, flexShrink: 0
                  }} />
                  <span style={{ fontSize: '13.5px', color: '#efeadd' }}>{opt.value}</span>
                  <span style={{ fontSize: '11px', color: '#65625a' }}>{opt.catLabel}</span>
                </span>
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
          display: 'flex', alignItems: 'center', gap: '7px', marginTop: '12px'
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

        {/* Active chips (tags + NL phrases + film + similar) */}
        {(chips.length > 0 || nlChips.length > 0 || film || similarTo) && (
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
            {chips.map(chip => (
              <span key={chip} style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: 'rgba(201,162,83,0.12)',
                border: '1px solid rgba(201,162,83,0.35)',
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: '#c9a253', fontWeight: 500
              }}>
                {chip}
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
                title={`Interpreted as: ${nl.tags.join(', ')}`}
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
        padding: '10px 20px',
        borderBottom: '1px solid rgba(255,255,255,0.065)',
        display: 'flex', alignItems: 'center', gap: '12px'
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
              {' '}· {chips.length + nlChips.length + (color ? 1 : 0) + (film ? 1 : 0)} filter{(chips.length + nlChips.length + (color ? 1 : 0) + (film ? 1 : 0)) > 1 ? 's' : ''} active
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
      </div>

      {/* ── Image grid ──────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px' }}>

        {/* Recently Added strip — last 7 days, hides while filtering/selecting */}
        {recent.length > 0 && !hasFilters && !similarTo && !tagMode && (
          <div style={{ marginBottom: '18px' }}>
            <div style={{
              fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.12em',
              color: '#65625a', marginBottom: '8px'
            }}>
              RECENTLY ADDED · LAST 7 DAYS
              <span style={{
                fontFamily: "'JetBrains Mono', monospace",
                marginLeft: '8px', color: '#9c988d', letterSpacing: 0
              }}>
                {recent.length}
              </span>
            </div>
            <div style={{
              display: 'flex', gap: '8px', overflowX: 'auto',
              paddingBottom: '6px'
            }}>
              {recent.map(img => (
                <div
                  key={`recent-${img.id}`}
                  onClick={() => setSelectedImage(img)}
                  title={img.filename}
                  style={{
                    height: '92px',
                    width: `${Math.round(92 * (img.ar_float || 1.78))}px`,
                    flexShrink: 0,
                    borderRadius: '6px',
                    overflow: 'hidden',
                    cursor: 'pointer',
                    position: 'relative',
                    border: '1px solid rgba(255,255,255,0.06)',
                    transition: 'transform 0.15s ease'
                  }}
                  onMouseEnter={e => e.currentTarget.style.transform = 'scale(1.03)'}
                  onMouseLeave={e => e.currentTarget.style.transform = 'scale(1)'}
                >
                  <img
                    src={img.thumbnail}
                    alt={img.filename}
                    style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                    loading="lazy"
                  />
                  {img.is_favorite && (
                    <span style={{
                      position: 'absolute', top: '4px', right: '5px',
                      color: '#dcbd76', fontSize: '10px',
                      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.7))'
                    }}>★</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {!loading && images.length === 0 && (
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
                  ref={node => {
                    if (node) tileRefs.current.set(img.id, node);
                    else tileRefs.current.delete(img.id);
                  }}
                  onClick={() => {
                    if (tagMode) {
                      // Don't toggle if this click was the tail end of a drag
                      if (justDraggedRef.current) return;
                      toggleTileSelection(img.id);
                    } else {
                      setSelectedImage(img);
                    }
                  }}
                  style={{
                    position: 'relative',
                    width: '100%',
                    aspectRatio: `${img.ar_float || 1.78}`,
                    background: img.palette?.[0]
                      ? `linear-gradient(135deg, ${img.palette[0]}, ${img.palette[1] || img.palette[0]})`
                      : '#141318',
                    borderRadius: '6px',
                    overflow: 'hidden',
                    cursor: 'pointer',
                    border: isSelected ? '2px solid #b8cea1' : '1px solid rgba(255,255,255,0.04)',
                    transition: 'transform 0.15s ease'
                  }}
                  onMouseEnter={e => e.currentTarget.style.transform = 'scale(1.01)'}
                  onMouseLeave={e => e.currentTarget.style.transform = 'scale(1)'}
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

                  {/* Aspect ratio label — normalized to standard formats */}
                  <span style={{
                    position: 'absolute', top: '7px', left: '7px',
                    fontFamily: "'JetBrains Mono', monospace",
                    fontSize: '8px', color: 'rgba(239,234,221,0.5)',
                    background: 'rgba(0,0,0,0.4)',
                    padding: '2px 5px', borderRadius: '3px'
                  }}>
                    {img.ar_label || img.aspect_ratio}
                  </span>

                  {/* Star / flag */}
                  {img.is_favorite && (
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

                  {/* Caption + color palette */}
                  <div style={{
                    position: 'absolute', left: '9px', right: '9px', bottom: '9px',
                    pointerEvents: 'none'
                  }}>
                    {img.caption && (
                      <div style={{
                        fontSize: '10.5px', lineHeight: '1.35',
                        color: 'rgba(239,234,221,0.9)',
                        overflow: 'hidden',
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        textShadow: '0 1px 3px rgba(0,0,0,0.6)',
                        marginBottom: '5px'
                      }}>
                        {img.caption}
                      </div>
                    )}
                    <div style={{ display: 'flex', gap: '3px' }}>
                      {(img.palette || []).slice(0, 5).map((hex, i) => (
                        <span key={i} style={{
                          width: '14px', height: '4px',
                          borderRadius: '1px', background: hex
                        }} />
                      ))}
                    </div>
                  </div>

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
        />
      )}

      {/* Duplicate review modal */}
      {showDuplicates && (
        <DuplicateReview
          onClose={() => setShowDuplicates(false)}
          onImageDeleted={handleImageDeleted}
        />
      )}

      {/* Tag Mode bulk-selection toolbar */}
      {tagMode && (
        <TagModeBar
          images={images}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onExit={toggleTagMode}
          onBulkChanged={handleBulkTagsChanged}
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
