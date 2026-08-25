import { useEffect, useRef, useState } from 'react';
import ImageDetail from '../components/ImageDetail';
import SelectModeHeader from '../components/SelectModeHeader';
import TagModeBar from '../components/TagModeBar';
import CropModal from '../components/CropModal';
import { rangeIdsBetween } from '../selectionRange';
import { useIsMobile } from '../hooks/useIsMobile';
import { PAGE_BG, accentBlueMuted, black, onSurfaceFaint, onSurfaceMuted, onSurfaceWarm, onTertiary, surfaceContainerWarmDark, tertiary, warning, white, withAlpha } from '../theme';

// Two personalities in one page. view="favorites" shows starred images
// (click the star to unstar); view="recent" shows images added within a
// slider-adjustable window. (A third, view="flagged", was removed in V55 —
// see the session log if this file's history is ever needed.)
const VIEW_CONFIG = {
  favorites: {
    title: 'Favorites',
    subtitle: 'Every image you’ve starred. Click the star to unstar.',
    icon: '★',
    accent: warning,
    emptyText: 'No favorites yet — open any image and hit ☆ Favorite.',
  },
  recent: {
    title: 'Recently Added',
    subtitle: 'Images added to your library within the window below.',
    icon: null,
    accent: accentBlueMuted,
    emptyText: 'Nothing added in this window — try dragging the slider further back.',
  },
};

const DEFAULT_RECENT_DAYS = 7;

export default function CollectionPage({ view }) {
  const cfg = VIEW_CONFIG[view];
  const isMobile = useIsMobile();
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(null);
  const [winW, setWinW] = useState(window.innerWidth);
  const [days, setDays] = useState(DEFAULT_RECENT_DAYS);
  const daysDebounce = useRef(null);

  // ── Select Mode: bulk-select images here to tag, crop, or delete — same
  //    pattern as Home.jsx's grid, minus the drag-rectangle select (these
  //    views are small, unpaginated lists, so click + shift-click covers it) ──
  const [tagMode, setTagMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [tagDrawerOpen, setTagDrawerOpen] = useState(false);
  const [cropImages, setCropImages] = useState(null);
  const rangeAnchorRef = useRef(null); // last tile clicked — the far end of a shift-click range

  const load = (daysArg) => {
    setLoading(true);
    const url = view === 'recent' ? `/api/views/recent?days=${daysArg ?? days}` : `/api/views/${view}`;
    fetch(url)
      .then(res => res.json())
      .then(data => setImages(data.images || []))
      .catch(err => console.error(`Failed to load ${view}`, err))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setSelectedImage(null);
    setDays(DEFAULT_RECENT_DAYS);
    // Switching between Favorites/Recent swaps out the whole image list from
    // under any in-progress selection — drop it rather than carry stale ids
    // into a different view.
    setTagMode(false);
    setSelectedIds(new Set());
    setTagDrawerOpen(false);
    setCropImages(null);
    load(DEFAULT_RECENT_DAYS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // Slider drags fire fast — debounce the refetch so it doesn't hammer the API.
  const onDaysChange = (next) => {
    setDays(next);
    clearTimeout(daysDebounce.current);
    daysDebounce.current = setTimeout(() => load(next), 200);
  };

  useEffect(() => {
    const onResize = () => setWinW(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // ── Keyboard shortcuts: 'V' toggles Select Mode; with photos selected,
  //    'T' opens the tag drawer, 'C' crops, Delete/Backspace deletes ─────────
  useEffect(() => {
    const onKeyDown = (e) => {
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

  // Detail-panel edits: patch the tile; if the image no longer belongs in
  // this view (unstarred on Favorites), drop it. Recent isn't affected by
  // favorite edits, so nothing gets dropped there.
  const handleImageUpdated = (id, patch) => {
    const stillBelongs = (img) => {
      if (view === 'favorites') return !!img.is_favorite;
      return true;
    };
    setImages(prev => prev
      .map(img => img.id === id ? { ...img, ...patch } : img)
      .filter(img => img.id !== id || stillBelongs(img))
    );
    setSelectedImage(prev => (prev && prev.id === id) ? { ...prev, ...patch } : prev);
  };

  const handleImageDeleted = (id) => {
    setImages(prev => prev.filter(img => img.id !== id));
    setSelectedImage(prev => (prev && prev.id === id) ? null : prev);
  };

  // Favorites-only: unstar one straight from the tile, same instant pattern
  const unfavorite = async (img, e) => {
    e.stopPropagation();
    try {
      const res = await fetch(`/api/images/${img.id}/favorite`, { method: 'POST' });
      const data = await res.json();
      if (!data.is_favorite) {
        setImages(prev => prev.filter(i => i.id !== img.id));
        setSelectedImage(prev => (prev && prev.id === img.id) ? null : prev);
      }
    } catch (err) {
      console.error('Unfavorite failed', err);
    }
  };

  // Recent: quick-favorite straight from the tile, same star Home.jsx's grid
  // has. Favorites view doesn't need this — its own star already toggles
  // (and removes the image on unstar) via `unfavorite` above.
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

  // ── Select Mode: toggling in/out, tile clicks ───────────────────────────────
  const toggleTagMode = () => {
    setTagMode(v => {
      const next = !v;
      if (!next) {
        setSelectedIds(new Set()); // turning OFF clears selection
        setTagDrawerOpen(false);
      }
      return next;
    });
  };

  // Shift-click adds a whole run of photos at once — see selectionRange.js for
  // why the run follows list order, not screen position. Shift only ever
  // ADDS; it never unselects.
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

  // These views never paginate — `load()` always fetches the whole list in
  // one shot — so "select all" never needs a server round trip the way
  // Home.jsx's does.
  const handleSelectAllResults = () => {
    setSelectedIds(new Set(images.map(i => i.id)));
  };

  const openTagDrawer = () => setTagDrawerOpen(true);
  const closeTagDrawer = () => setTagDrawerOpen(false);

  // Apply a bulk tag/filmography patch to any currently-loaded images
  const handleBulkTagsChanged = (ids, patchFn) => {
    const idSet = new Set(ids);
    setImages(prev => prev.map(img => idSet.has(img.id) ? patchFn(img) : img));
    setSelectedImage(prev => (prev && idSet.has(prev.id)) ? patchFn(prev) : prev);
  };

  // Unlike Home.jsx's search results, tags/filmography never change whether a
  // photo belongs on Favorites/Recent, so there's nothing to re-sync.
  const handleBulkMutated = () => {};

  // A bulk delete already tells us exactly which ids are gone.
  const handleBulkDeleted = (ids) => {
    const idSet = new Set(ids);
    setImages(prev => prev.filter(img => !idSet.has(img.id)));
    setSelectedImage(prev => (prev && idSet.has(prev.id)) ? null : prev);
  };

  const handleBulkDeleteClick = () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
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
        if (!res.ok) load(); // Re-sync on error
      } catch (e) {
        console.error('Bulk delete failed', e);
        load(); // Re-sync on error
      }
    })();
  };

  const everythingLoaded = true; // these views are never paginated
  const allLoadedAndSelected = selectedIds.size > 0 && selectedIds.size >= images.length;

  // Same masonry layout as Home: shortest column first, no cropping
  const colCount = Math.max(2, Math.min(5, Math.floor((winW - 280) / 320)));
  const columns = (() => {
    const cols = Array.from({ length: colCount }, () => ({ items: [], h: 0 }));
    for (const img of images) {
      const shortest = cols.reduce((a, b) => (a.h <= b.h ? a : b));
      shortest.items.push(img);
      shortest.h += 1 / (img.ar_float || 1.78);
    }
    return cols.map(c => c.items);
  })();

  return (
    <div style={{
      background: PAGE_BG,
      minHeight: '100%',
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      color: onSurfaceWarm
    }}>
      {/* ── Select Mode header (only when tagMode is on) ──────────────────── */}
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
          selectingAll={false}
          selectMsg=""
          totalResults={images.length}
          images={images}
          everythingLoaded={everythingLoaded}
          allLoadedAndSelected={allLoadedAndSelected}
        />
      )}

      {/* Page header */}
      <div style={{
        padding: '24px 24px 16px',
        borderBottom: `1px solid ${withAlpha(white,0.065)}`,
        display: 'flex', alignItems: 'flex-start', gap: '16px'
      }}>
        <div style={{ flex: 1 }}>
          <h2 style={{
            fontSize: '22px', fontWeight: 600, margin: '0 0 4px',
            display: 'flex', alignItems: 'center', gap: '10px'
          }}>
            <span style={{ color: cfg.accent }}>{cfg.icon}</span>
            {cfg.title}
            <span style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '13px', fontWeight: 400, color: onSurfaceMuted
            }}>
              {images.length}
            </span>
          </h2>
          <p style={{ fontSize: '12.5px', color: onSurfaceMuted, margin: 0, maxWidth: '560px' }}>
            {cfg.subtitle}
          </p>
        </div>

        {/* Day-range slider — recent view only */}
        {view === 'recent' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, width: '260px' }}>
            <span style={{ fontSize: '12px', color: onSurfaceMuted, whiteSpace: 'nowrap' }}>
              Last <strong style={{ color: onSurfaceWarm }}>{days}</strong> day{days === 1 ? '' : 's'}
            </span>
            <input
              type="range"
              min={1}
              max={60}
              value={days}
              onChange={e => onDaysChange(Number(e.target.value))}
              style={{ flex: 1, accentColor: cfg.accent }}
            />
          </div>
        )}

      </div>

      {/* Grid */}
      <div style={{ padding: '16px' }}>
        {loading && (
          <div style={{ padding: '40px', textAlign: 'center', fontSize: '13px', color: onSurfaceFaint }}>
            Loading…
          </div>
        )}

        {!loading && images.length === 0 && (
          <div style={{
            padding: '80px 20px', display: 'flex', flexDirection: 'column',
            alignItems: 'center', gap: '10px', color: onSurfaceFaint
          }}>
            <span style={{ fontSize: '32px', opacity: 0.4, color: cfg.accent }}>{cfg.icon}</span>
            <p style={{ fontSize: '14px', color: onSurfaceMuted, margin: 0 }}>{cfg.emptyText}</p>
          </div>
        )}

        <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', userSelect: tagMode ? 'none' : 'auto' }}>
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
                  onClick={(e) => {
                    if (tagMode) {
                      toggleTileSelection(img.id, e.shiftKey);
                    } else {
                      setSelectedImage(img);
                    }
                  }}
                  style={{
                    position: 'relative',
                    width: '100%',
                    aspectRatio: `${img.ar_float || 1.78}`,
                    background: surfaceContainerWarmDark,
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

                  <div style={{
                    position: 'absolute', inset: 0,
                    background: `linear-gradient(180deg, ${withAlpha(black,0)} 55%, ${withAlpha(black,0.7)} 100%)`,
                    pointerEvents: 'none'
                  }} />

                  {/* Quick-favorite star — Recent only (Favorites view's own star
                      below already does this, plus unstars on click). Hidden
                      entirely in Select Mode so it doesn't fight tile-selection
                      clicks — same rule Home.jsx's grid follows. */}
                  {view !== 'favorites' && !tagMode && (
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
                  {view !== 'favorites' && tagMode && img.is_favorite && (
                    <span style={{
                      position: 'absolute', top: '6px', right: '7px',
                      color: warning, fontSize: '13px',
                      filter: `drop-shadow(0 1px 2px ${withAlpha(black,0.7)})`
                    }}>★</span>
                  )}

                  {/* View marker — on Favorites, the star itself unfavorites on click
                      (only outside Select Mode, so it doesn't fight tile-selection clicks) */}
                  {cfg.icon && (view === 'favorites' && !tagMode ? (
                    <button
                      onClick={(e) => unfavorite(img, e)}
                      title="Unfavorite"
                      style={{
                        position: 'absolute', top: '4px', right: '5px',
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: cfg.accent, fontSize: '15px', padding: '4px',
                        filter: `drop-shadow(0 1px 2px ${withAlpha(black,0.7)})`, lineHeight: 1
                      }}
                    >
                      {cfg.icon}
                    </button>
                  ) : (
                    <span style={{
                      position: 'absolute', top: '6px', right: '7px',
                      color: cfg.accent, fontSize: '13px',
                      filter: `drop-shadow(0 1px 2px ${withAlpha(black,0.7)})`
                    }}>
                      {cfg.icon}
                    </span>
                  ))}

                  {/* Select Mode selection checkmark — top-left, clear of the
                      star marker which lives top-right */}
                  {isSelected && (
                    <span style={{
                      position: 'absolute', top: '6px', left: '7px',
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
      </div>

      {selectedImage && (
        <ImageDetail
          image={selectedImage}
          onClose={() => setSelectedImage(null)}
          onUpdated={handleImageUpdated}
          onDeleted={handleImageDeleted}
          onCrop={(img) => setCropImages([img])}
        />
      )}

      {/* Crop review modal — auto-detects letterbox/chrome, applies on approve */}
      {cropImages && (
        <CropModal
          images={cropImages}
          onClose={(started) => {
            setCropImages(null);
            if (started && tagMode) toggleTagMode();
          }}
          onImageCropped={(id, patch) => handleImageUpdated(id, patch)}
        />
      )}

      {/* Select Mode drawer — right sidebar when tagMode is on and drawer is open */}
      {tagMode && (
        <TagModeBar
          images={images}
          totalResults={images.length}
          selectedIds={selectedIds}
          setSelectedIds={setSelectedIds}
          onSelectAllResults={handleSelectAllResults}
          onExit={toggleTagMode}
          onBulkChanged={handleBulkTagsChanged}
          onBulkMutated={handleBulkMutated}
          onBulkDeleted={handleBulkDeleted}
          onResync={load}
          onCrop={() => {
            const sel = images.filter(i => selectedIds.has(i.id));
            if (sel.length) setCropImages(sel);
          }}
          isOpen={tagDrawerOpen}
          onClose={closeTagDrawer}
        />
      )}
    </div>
  );
}
