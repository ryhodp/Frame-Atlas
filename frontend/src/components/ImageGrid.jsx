import { useState, useEffect, useRef, useCallback } from 'react';
import { accentSimilar, accentVioletLighter, black, onSurfaceFaint, onSurfaceWarm, onTertiary, overlayViolet, surfaceContainerMuted, tertiary, warning, white, withAlpha } from '../theme';

// ImageGrid — Home's photo grid (Day 47 / V95).
//
// The masonry layout (how many columns fit, then each photo dropped into the
// shortest column, full aspect ratio, never cropped), every tile (thumbnail,
// quick-favourite star, Find Similar % badge, Select Mode checkmark), the
// box-drag rectangle overlay, infinite scroll (the V80 halfway prefetch AND
// the bottom-of-page sentinel safety net), and V14 view-logging (a tile
// counts as seen once half of it is on screen; the batch is sent when the
// page is hidden or left, never mid-scroll, so the shuffle can't shift under
// an open page).
//
// Moved out of Home.jsx verbatim. Props keep the ORIGINAL names the code
// used (setSelectedImage, toggleFavorite, fetchPage, fetchingRef, pageRef,
// tileRefs, …) so it reads exactly as before:
//   - fetching the next page stays Home's job (it's tied to search and Find
//     Similar); the grid only decides WHEN, via fetchPage / fetchingRef /
//     pageRef / hasMore
//   - Select Mode's state and mouse handlers come from useSelection() via Home
//   - colWidth (the density slider, which lives in Home's result-count bar)
//     and drawerOffset (also Home's main-column margin) are passed down
//   - this component is always rendered inside Home, so the view-log flush on
//     unmount still fires exactly when leaving Home, as before
export default function ImageGrid({
  images, similarTo, colWidth, drawerOffset, isMobile, perPage,
  hasMore, fetchPage, fetchingRef, pageRef,
  tagMode, selectedIds, tileRefs, justDraggedRef, toggleTileSelection, dragRect,
  onGridMouseDown, onGridMouseMove, onGridMouseUp,
  setSelectedImage, toggleFavorite,
}) {
  const [winW, setWinW] = useState(window.innerWidth);
  const sentinelRef = useRef(null);
  const viewObserverRef = useRef(null);   // watches tiles entering the viewport
  const seenIdsRef = useRef(new Set());   // every id already queued this visit
  const pendingViewsRef = useRef(new Set()); // queued but not yet sent to the server

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
    const midIndex = Math.max(0, images.length - Math.floor(perPage / 2));
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

  // Sidebar only reserves real width on tablet/desktop — on mobile it's an
  // overlay drawer, so the grid gets the full window width to itself.
  // Add the drawer width (280px) when it's open.
  const sidebarOffset = isMobile ? 24 : 280;
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
    <>
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
    </>
  );
}
