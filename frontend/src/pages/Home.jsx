import { useState, useEffect, useRef, useCallback } from 'react';
import ImageDetail from '../components/ImageDetail';

export default function Home() {
  const [chips, setChips] = useState([]);
  const [searchText, setSearchText] = useState('');
  const [autocomplete, setAutocomplete] = useState([]);
  const [showAuto, setShowAuto] = useState(false);
  const [images, setImages] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selectedImageId, setSelectedImageId] = useState(null);

  const searchRef = useRef(null);
  const autoDebounce = useRef(null);

  // ── Fetch images whenever chips change ──────────────────────────────────────
  const fetchImages = useCallback(async (activeChips) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (activeChips.length) params.set('chips', activeChips.join(','));
      const res = await fetch(`/api/search?${params}`);
      const data = await res.json();
      setImages(data.images || []);
      setTotal(data.total || 0);
    } catch (e) {
      console.error('Search failed', e);
    }
    setLoading(false);
  }, []);

  useEffect(() => { fetchImages(chips); }, [chips]);

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

  // ── Close autocomplete when clicking outside ────────────────────────────────
  useEffect(() => {
    const handler = (e) => {
      if (!e.target.closest('[data-search-area]')) setShowAuto(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const addChip = (tag) => {
    if (!chips.includes(tag)) setChips(prev => [...prev, tag]);
    setSearchText('');
    setShowAuto(false);
    setAutocomplete([]);
    searchRef.current?.focus();
  };

  const removeChip = (tag) => setChips(prev => prev.filter(t => t !== tag));

  // ── Build masonry-style rows from images ────────────────────────────────────
  const buildRows = (imgs, rowH = 220, gap = 8) => {
    // Use a wide estimate — the grid will flex to fill the actual container
    const containerW = window.innerWidth - 280; // minus sidebar
    const rows = [];
    let row = [];
    let rowW = 0;

    for (const img of imgs) {
      const tileW = (img.ar_float || 1.78) * rowH;
      if (row.length > 0 && rowW + tileW + gap > containerW) {
        rows.push(row);
        row = [img];
        rowW = tileW;
      } else {
        row.push(img);
        rowW += tileW + gap;
      }
    }
    if (row.length) rows.push(row);
    return rows;
  };

  const rows = buildRows(images);

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
        {/* Input */}
        <div style={{
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
              if (e.key === 'Enter' && autocomplete.length > 0) addChip(autocomplete[0].value);
              if (e.key === 'Escape') { setShowAuto(false); setSearchText(''); }
            }}
            onFocus={() => { if (autocomplete.length) setShowAuto(true); }}
            placeholder="Search tags — type to filter…"
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              color: '#efeadd', fontFamily: 'inherit', fontSize: '14px'
            }}
          />
          <span style={{
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: '10px', color: '#65625a',
            border: '1px solid rgba(255,255,255,0.08)',
            borderRadius: '4px', padding: '2px 6px'
          }}>⌘K</span>
        </div>

        {/* Autocomplete dropdown */}
        {showAuto && autocomplete.length > 0 && (
          <div style={{
            position: 'absolute', top: '68px', left: '20px', right: '20px',
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

        {/* Active chips */}
        {chips.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px', marginTop: '12px' }}>
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
            <button
              onClick={() => setChips([])}
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
          {chips.length > 0 && (
            <span style={{ color: '#65625a' }}>
              {' '}· {chips.length} filter{chips.length > 1 ? 's' : ''} active
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
              {chips.length > 0 ? 'No images match this filter' : 'No images yet — run a sync first'}
            </p>
            {chips.length > 0 && (
              <button
                onClick={() => setChips([])}
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

        {/* Masonry rows */}
        {rows.map((row, ri) => (
          <div key={ri} style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
            {row.map(img => (
              <div
                key={img.id}
                onClick={() => setSelectedImageId(img.id)}
                style={{
                  flex: `${img.ar_float || 1.78} 1 0`,
                  position: 'relative',
                  height: '220px',
                  background: img.palette?.[0]
                    ? `linear-gradient(135deg, ${img.palette[0]}, ${img.palette[1] || img.palette[0]})`
                    : '#141318',
                  borderRadius: '6px',
                  overflow: 'hidden',
                  cursor: 'pointer',
                  border: '1px solid rgba(255,255,255,0.04)',
                  transition: 'transform 0.15s ease'
                }}
                onMouseEnter={e => e.currentTarget.style.transform = 'scale(1.01)'}
                onMouseLeave={e => e.currentTarget.style.transform = 'scale(1)'}
              >
                {/* Thumbnail image */}
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

                {/* Aspect ratio label */}
                <span style={{
                  position: 'absolute', top: '7px', left: '7px',
                  fontFamily: "'JetBrains Mono', monospace",
                  fontSize: '8px', color: 'rgba(239,234,221,0.5)',
                  background: 'rgba(0,0,0,0.4)',
                  padding: '2px 5px', borderRadius: '3px'
                }}>
                  {img.aspect_ratio}
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
              </div>
            ))}
          </div>
        ))}

        <div style={{ height: '30px' }} />
      </div>

      {/* Detail panel */}
      {selectedImageId && (
        <ImageDetail
          imageId={selectedImageId}
          onClose={() => setSelectedImageId(null)}
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
