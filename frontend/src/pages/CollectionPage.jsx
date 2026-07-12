import { useEffect, useRef, useState } from 'react';
import ImageDetail from '../components/ImageDetail';

// Three personalities in one page. view="favorites" shows starred images
// (click the star to unstar); view="flagged" shows the flag queue with clear
// buttons (clearing only removes the marker — nothing here ever deletes an
// image); view="recent" shows images added within a slider-adjustable window.
const VIEW_CONFIG = {
  favorites: {
    title: 'Favorites',
    subtitle: 'Every image you’ve starred. Click the star to unstar.',
    icon: '★',
    accent: '#dcbd76',
    emptyText: 'No favorites yet — open any image and hit ☆ Favorite.',
  },
  flagged: {
    title: 'Flagged',
    subtitle: 'Images you’ve flagged for review. Clearing removes only the flag — the image stays in your library.',
    icon: '⚑',
    accent: '#cf7152',
    emptyText: 'Nothing flagged — the queue is clear.',
  },
  recent: {
    title: 'Recently Added',
    subtitle: 'Images added to your library within the window below.',
    icon: null,
    accent: '#7fa9d9',
    emptyText: 'Nothing added in this window — try dragging the slider further back.',
  },
};

const DEFAULT_RECENT_DAYS = 7;

export default function CollectionPage({ view }) {
  const cfg = VIEW_CONFIG[view];
  const [images, setImages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedImage, setSelectedImage] = useState(null);
  const [winW, setWinW] = useState(window.innerWidth);
  const [confirmClearAll, setConfirmClearAll] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [days, setDays] = useState(DEFAULT_RECENT_DAYS);
  const daysDebounce = useRef(null);

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
    setConfirmClearAll(false);
    setDays(DEFAULT_RECENT_DAYS);
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

  // Detail-panel edits: patch the tile; if the image no longer belongs in
  // this view (unstarred on Favorites, unflagged on Flagged), drop it.
  // Recent isn't affected by favorite/flag edits, so nothing gets dropped there.
  const handleImageUpdated = (id, patch) => {
    const stillBelongs = (img) => {
      if (view === 'favorites') return !!img.is_favorite;
      if (view === 'flagged') return !!img.is_flagged;
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

  // Flagged-only: clear one flag straight from the tile
  const clearFlag = async (img, e) => {
    e.stopPropagation();
    try {
      const res = await fetch(`/api/images/${img.id}/flag`, { method: 'POST' });
      const data = await res.json();
      if (!data.is_flagged) {
        setImages(prev => prev.filter(i => i.id !== img.id));
        setSelectedImage(prev => (prev && prev.id === img.id) ? null : prev);
      }
    } catch (err) {
      console.error('Clear flag failed', err);
    }
  };

  // Favorites-only: unstar one straight from the tile, same instant pattern as clearFlag
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

  // Flagged-only: clear the whole queue in one call
  const clearAllFlags = async () => {
    setClearing(true);
    try {
      const res = await fetch('/api/flags/clear-all', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        setImages([]);
        setSelectedImage(null);
      }
    } catch (err) {
      console.error('Clear all flags failed', err);
    }
    setClearing(false);
    setConfirmClearAll(false);
  };

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
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
      color: '#efeadd'
    }}>
      {/* Page header */}
      <div style={{
        padding: '24px 24px 16px',
        borderBottom: '1px solid rgba(255,255,255,0.065)',
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
              fontSize: '13px', fontWeight: 400, color: '#9c988d'
            }}>
              {images.length}
            </span>
          </h2>
          <p style={{ fontSize: '12.5px', color: '#9c988d', margin: 0, maxWidth: '560px' }}>
            {cfg.subtitle}
          </p>
        </div>

        {/* Day-range slider — recent view only */}
        {view === 'recent' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0, width: '260px' }}>
            <span style={{ fontSize: '12px', color: '#9c988d', whiteSpace: 'nowrap' }}>
              Last <strong style={{ color: '#efeadd' }}>{days}</strong> day{days === 1 ? '' : 's'}
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

        {/* Clear-all — flagged view only, with an inline confirm step */}
        {view === 'flagged' && images.length > 0 && (
          !confirmClearAll ? (
            <button
              onClick={() => setConfirmClearAll(true)}
              style={{
                background: 'none', border: '1px solid rgba(207,113,82,0.35)',
                color: '#cf7152', borderRadius: '7px', padding: '8px 14px',
                cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit', flexShrink: 0
              }}
            >
              Clear all flags
            </button>
          ) : (
            <span style={{ display: 'inline-flex', gap: '6px', alignItems: 'center', flexShrink: 0 }}>
              <span style={{ fontSize: '11.5px', color: '#cf7152' }}>
                Unflag all {images.length}? Images stay in the library.
              </span>
              <button
                onClick={clearAllFlags}
                disabled={clearing}
                style={{
                  background: 'rgba(207,113,82,0.18)', border: '1px solid rgba(207,113,82,0.6)',
                  color: '#cf7152', borderRadius: '6px', padding: '7px 12px',
                  cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
                  opacity: clearing ? 0.6 : 1
                }}
              >
                {clearing ? 'Clearing…' : 'Yes, clear all'}
              </button>
              <button
                onClick={() => setConfirmClearAll(false)}
                disabled={clearing}
                style={{
                  background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#9c988d', borderRadius: '6px', padding: '7px 12px',
                  cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
                }}
              >
                Cancel
              </button>
            </span>
          )
        )}
      </div>

      {/* Grid */}
      <div style={{ padding: '16px' }}>
        {loading && (
          <div style={{ padding: '40px', textAlign: 'center', fontSize: '13px', color: '#65625a' }}>
            Loading…
          </div>
        )}

        {!loading && images.length === 0 && (
          <div style={{
            padding: '80px 20px', display: 'flex', flexDirection: 'column',
            alignItems: 'center', gap: '10px', color: '#65625a'
          }}>
            <span style={{ fontSize: '32px', opacity: 0.4, color: cfg.accent }}>{cfg.icon}</span>
            <p style={{ fontSize: '14px', color: '#9c988d', margin: 0 }}>{cfg.emptyText}</p>
          </div>
        )}

        <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
          {columns.map((col, ci) => (
            <div key={ci} style={{
              flex: 1, minWidth: 0,
              display: 'flex', flexDirection: 'column', gap: '10px'
            }}>
              {col.map(img => (
                <div
                  key={img.id}
                  onClick={() => setSelectedImage(img)}
                  style={{
                    position: 'relative',
                    width: '100%',
                    aspectRatio: `${img.ar_float || 1.78}`,
                    background: '#141318',
                    borderRadius: '6px',
                    overflow: 'hidden',
                    cursor: 'pointer',
                    border: '1px solid rgba(255,255,255,0.04)',
                    transition: 'transform 0.15s ease'
                  }}
                  onMouseEnter={e => e.currentTarget.style.transform = 'scale(1.01)'}
                  onMouseLeave={e => e.currentTarget.style.transform = 'scale(1)'}
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
                    background: 'linear-gradient(180deg, rgba(0,0,0,0) 55%, rgba(0,0,0,0.7) 100%)',
                    pointerEvents: 'none'
                  }} />

                  {/* View marker — on Favorites, the star itself unfavorites on click */}
                  {cfg.icon && (view === 'favorites' ? (
                    <button
                      onClick={(e) => unfavorite(img, e)}
                      title="Unfavorite"
                      style={{
                        position: 'absolute', top: '4px', right: '5px',
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: cfg.accent, fontSize: '15px', padding: '4px',
                        filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.7))', lineHeight: 1
                      }}
                    >
                      {cfg.icon}
                    </button>
                  ) : (
                    <span style={{
                      position: 'absolute', top: '6px', right: '7px',
                      color: cfg.accent, fontSize: '13px',
                      filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.7))'
                    }}>
                      {cfg.icon}
                    </span>
                  ))}

                  {/* Flagged view: one-click clear on the tile */}
                  {view === 'flagged' && (
                    <button
                      onClick={(e) => clearFlag(img, e)}
                      title="Clear this flag (keeps the image)"
                      style={{
                        position: 'absolute', bottom: '8px', right: '8px',
                        background: 'rgba(10,10,11,0.75)',
                        border: '1px solid rgba(207,113,82,0.5)',
                        color: '#cf7152', borderRadius: '6px',
                        padding: '4px 10px', fontSize: '11px',
                        cursor: 'pointer', fontFamily: 'inherit'
                      }}
                    >
                      Clear flag
                    </button>
                  )}

                  {img.caption && (
                    <div style={{
                      position: 'absolute', left: '9px', bottom: '9px',
                      right: view === 'flagged' ? '92px' : '9px',
                      fontSize: '10.5px', lineHeight: '1.35',
                      color: 'rgba(239,234,221,0.9)',
                      overflow: 'hidden',
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      textShadow: '0 1px 3px rgba(0,0,0,0.6)',
                      pointerEvents: 'none'
                    }}>
                      {img.caption}
                    </div>
                  )}
                </div>
              ))}
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
        />
      )}
    </div>
  );
}
