import { useCallback, useEffect, useRef, useState } from 'react';
import { addImagesToDeck } from '../deckAdd';

// ── AddPhotosModal — Frame Atlas V46 ─────────────────────────────────────────
// Pick photos out of your library and drop them into a deck, without leaving
// the deck page.
//
// This exists because the only way to fill a deck used to be Home → Select Mode
// → the "ADD TO DECK" panel in the bottom bar. That panel works fine, but Select
// Mode reads as a bulk-EDITING tool, so nobody looking at an empty lookbook ever
// found it — the empty deck even said "Add some photos to this lookbook first"
// and then offered no way to do it.
//
// No new backend. POST /api/decks/<id>/images already existed, already skips
// photos the deck holds, and already reports {added, already_in_deck,
// invalid_ids} — this reads all three rather than assuming success, which is
// what lets the result line be honest about "already in this deck".
//
// The search box filters by TAG, using the same /api/autocomplete vocabulary and
// the same `chips` param as Home's gold chips — deliberately not Home's whole
// filter stack (colour, ratio, film, notes). Rebuilding that here would mean a
// second hand-copied copy of buildFilterParams, which is exactly the drift
// CLAUDE.md warns about for build_search_filters. One search box, one meaning.

const PAGE_SIZE = 60;

export default function AddPhotosModal({ deckId, deckName, existingImageIds, onClose, onAdded }) {
  const [chips, setChips] = useState([]);          // exact tag values, AND'd — same as Home
  const [text, setText] = useState('');
  const [auto, setAuto] = useState([]);
  const [showAuto, setShowAuto] = useState(false);

  const [images, setImages] = useState([]);
  const [total, setTotal] = useState(0);
  // /api/search pages are ZERO-indexed — the offset is `page * per`. Starting at
  // 1 silently skips the first 60 photos in your library, which looks like a
  // sorting quirk rather than a bug.
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const [selected, setSelected] = useState(() => new Set());
  const [adding, setAdding] = useState(false);
  const [result, setResult] = useState('');

  // The deck's current contents, so a photo already in it renders as "In deck"
  // instead of quietly adding nothing. Held in a ref-like Set built once per
  // render from the prop — the deck page owns this list, not us.
  const inDeck = existingImageIds instanceof Set
    ? existingImageIds
    : new Set(existingImageIds || []);

  // Every fetch is stamped, and a response whose stamp isn't the newest is
  // dropped. Typing "car" fires three overlapping searches and the slowest one
  // must not be allowed to land last and overwrite the right answer.
  const reqId = useRef(0);

  const buildParams = useCallback((pageNum) => {
    const params = new URLSearchParams();
    if (chips.length) params.set('chips', chips.join(','));
    params.set('page', String(pageNum));
    params.set('per', String(PAGE_SIZE));
    return params;
  }, [chips]);

  const load = useCallback(async (pageNum, append) => {
    const mine = ++reqId.current;
    setLoading(true);
    setLoadError('');
    try {
      const res = await fetch(`/api/search?${buildParams(pageNum).toString()}`);
      if (!res.ok) throw new Error(`Search failed (HTTP ${res.status}).`);
      const data = await res.json();
      if (mine !== reqId.current) return; // a newer search already answered
      const batch = Array.isArray(data.images) ? data.images : [];
      setImages(prev => (append ? [...prev, ...batch] : batch));
      setTotal(typeof data.total === 'number' ? data.total : batch.length);
      setHasMore(!!data.has_more);
      setPage(pageNum);
    } catch (e) {
      if (mine !== reqId.current) return;
      setLoadError(e.message || 'Could not load your photos.');
      if (!append) setImages([]);
    } finally {
      if (mine === reqId.current) setLoading(false);
    }
  }, [buildParams]);

  useEffect(() => { load(0, false); }, [load]);

  // Tag autocomplete, same endpoint and same debounce feel as Home's search bar.
  useEffect(() => {
    const q = text.trim();
    if (!q) { setAuto([]); setShowAuto(false); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const res = await fetch(`/api/autocomplete?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (cancelled) return;
        // Tags only. Film/aspect-ratio/note suggestions would need filter params
        // this picker deliberately doesn't carry, and offering a suggestion that
        // then does nothing is worse than not offering it.
        setAuto((Array.isArray(data) ? data : []).filter(s => s.type === 'tag').slice(0, 8));
        setShowAuto(true);
      } catch { /* autocomplete is a convenience; the grid still works */ }
    }, 180);
    return () => { cancelled = true; clearTimeout(t); };
  }, [text]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const addChip = (value) => {
    setChips(prev => (prev.includes(value) ? prev : [...prev, value]));
    setText('');
    setAuto([]);
    setShowAuto(false);
  };
  const removeChip = (value) => setChips(prev => prev.filter(c => c !== value));

  const toggle = (id) => {
    if (inDeck.has(id)) return;
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectAllLoaded = () => {
    setSelected(prev => {
      const next = new Set(prev);
      images.forEach(img => { if (!inDeck.has(img.id)) next.add(img.id); });
      return next;
    });
  };

  const submit = async () => {
    const ids = Array.from(selected);
    if (!ids.length || adding) return;
    setAdding(true);
    setResult('');
    try {
      const data = await addImagesToDeck(deckId, ids);
      onAdded?.(data);
      onClose();
    } catch (e) {
      // Stay open on failure — closing would drop a selection the user may have
      // spent real time building, with nothing to show for it.
      setResult(e.message || 'Could not add those photos.');
      setAdding(false);
    }
  };

  const selectableLoaded = images.filter(img => !inDeck.has(img.id)).length;
  const count = selected.size;

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.72)',
        zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#1b1d21', border: '1px solid #33353b', borderRadius: '14px',
          width: 'min(1080px, 100%)', height: 'min(760px, 100%)',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0,0,0,0.6)',
        }}
      >
        {/* Header */}
        <div style={{
          padding: '18px 22px 14px', borderBottom: '1px solid #2a2c31',
          display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap',
        }}>
          <div style={{ flex: 1, minWidth: '200px' }}>
            <div style={{ fontSize: '16px', fontWeight: 700, color: '#e2e2e6' }}>
              Add photos to “{deckName}”
            </div>
            <div style={{ fontSize: '12px', color: '#8e9099', marginTop: '3px' }}>
              {total.toLocaleString()} photo{total === 1 ? '' : 's'} to choose from
              {chips.length > 0 && ' matching your search'}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: '1px solid #44474f', color: '#e2e2e6',
              borderRadius: '8px', padding: '7px 14px', cursor: 'pointer',
              fontSize: '13px', fontFamily: 'inherit',
            }}
          >
            Cancel
          </button>
        </div>

        {/* Search */}
        <div style={{ padding: '12px 22px', borderBottom: '1px solid #2a2c31', position: 'relative' }}>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            {chips.map(chip => (
              <span
                key={chip}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '7px',
                  background: 'rgba(217,164,65,0.14)', border: '1px solid rgba(217,164,65,0.5)',
                  color: '#d9a441', borderRadius: '6px', padding: '4px 9px', fontSize: '12px',
                }}
              >
                {chip}
                <button
                  onClick={() => removeChip(chip)}
                  aria-label={`Remove ${chip} from this search`}
                  style={{
                    background: 'none', border: 'none', color: '#d9a441',
                    cursor: 'pointer', fontSize: '13px', lineHeight: 1, padding: 0,
                    fontFamily: 'inherit',
                  }}
                >
                  ×
                </button>
              </span>
            ))}
            <input
              value={text}
              onChange={e => setText(e.target.value)}
              onFocus={() => { if (auto.length) setShowAuto(true); }}
              onKeyDown={e => {
                if (e.key === 'Enter' && auto.length) { e.preventDefault(); addChip(auto[0].value); }
                if (e.key === 'Backspace' && !text && chips.length) removeChip(chips[chips.length - 1]);
              }}
              placeholder={chips.length ? 'Narrow it down further…' : 'Search your library by tag…'}
              style={{
                flex: 1, minWidth: '180px', background: '#111317', color: '#e2e2e6',
                border: '1px solid #44474f', borderRadius: '7px', padding: '8px 11px',
                fontSize: '13px', fontFamily: 'inherit', outline: 'none',
              }}
            />
          </div>

          {showAuto && auto.length > 0 && (
            <div style={{
              position: 'absolute', top: 'calc(100% - 4px)', left: '22px', right: '22px',
              background: '#2a2c31', border: '1px solid #44474f', borderRadius: '10px',
              boxShadow: '0 8px 32px rgba(0,0,0,0.5)', zIndex: 20,
              maxHeight: '240px', overflowY: 'auto',
            }}>
              {auto.map(s => (
                <button
                  key={`${s.category}:${s.value}`}
                  onClick={() => addChip(s.value)}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center',
                    justifyContent: 'space-between', gap: '10px', padding: '8px 12px',
                    background: 'transparent', border: 'none', cursor: 'pointer',
                    textAlign: 'left', fontFamily: 'inherit',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = '#37393e'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <span style={{ fontSize: '13px', color: '#e2e2e6' }}>{s.value}</span>
                  <span style={{ fontSize: '10.5px', color: s.color || '#8e9099' }}>
                    {s.catLabel} · {s.count}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Grid */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 22px' }}>
          {loadError && (
            <div style={{
              background: 'rgba(207,113,82,0.08)', border: '1px solid rgba(207,113,82,0.3)',
              color: '#cf7152', borderRadius: '8px', padding: '10px 12px',
              fontSize: '12.5px', marginBottom: '14px',
            }}>
              {loadError}
            </div>
          )}

          {!loading && images.length === 0 && !loadError && (
            <div style={{
              textAlign: 'center', color: '#8e9099', fontSize: '13px', padding: '48px 12px',
            }}>
              {chips.length
                ? 'No photos match that search. Try removing a tag.'
                : 'No photos in your library yet.'}
            </div>
          )}

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(128px, 1fr))',
            gap: '10px',
          }}>
            {images.map(img => {
              const already = inDeck.has(img.id);
              const isSel = selected.has(img.id);
              return (
                <button
                  key={img.id}
                  onClick={() => toggle(img.id)}
                  disabled={already}
                  title={already ? 'Already in this deck' : img.filename}
                  style={{
                    position: 'relative', padding: 0, border: `2px solid ${
                      already ? '#33353b' : isSel ? '#d9a441' : 'transparent'
                    }`,
                    borderRadius: '8px', overflow: 'hidden', background: '#111317',
                    cursor: already ? 'default' : 'pointer', aspectRatio: '1 / 1',
                    opacity: already ? 0.4 : 1,
                  }}
                >
                  {img.thumbnail && (
                    <img
                      src={img.thumbnail}
                      alt=""
                      loading="lazy"
                      style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                    />
                  )}
                  {already && (
                    <span style={{
                      position: 'absolute', bottom: '4px', left: '4px', right: '4px',
                      background: 'rgba(0,0,0,0.72)', color: '#b8cea1',
                      fontSize: '10px', borderRadius: '4px', padding: '2px 5px',
                    }}>
                      In deck
                    </span>
                  )}
                  {isSel && !already && (
                    <span style={{
                      position: 'absolute', top: '5px', right: '5px',
                      background: '#d9a441', color: '#3d2f00',
                      width: '20px', height: '20px', borderRadius: '50%',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: '12px', fontWeight: 700,
                    }}>
                      ✓
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {images.length > 0 && hasMore && (
            <div style={{ textAlign: 'center', marginTop: '18px' }}>
              <button
                onClick={() => load(page + 1, true)}
                disabled={loading}
                style={{
                  background: 'none', border: '1px solid #44474f', color: '#e2e2e6',
                  borderRadius: '8px', padding: '9px 20px', cursor: loading ? 'default' : 'pointer',
                  fontSize: '13px', fontFamily: 'inherit',
                }}
              >
                {loading ? 'Loading…' : `Load more (${images.length} of ${total})`}
              </button>
            </div>
          )}

          {loading && images.length === 0 && (
            <div style={{ textAlign: 'center', color: '#8e9099', fontSize: '13px', padding: '48px 12px' }}>
              Loading your library…
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '14px 22px', borderTop: '1px solid #2a2c31',
          display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap',
        }}>
          <div style={{ flex: 1, minWidth: '180px', fontSize: '12.5px', color: result ? '#cf7152' : '#8e9099' }}>
            {result || (count > 0
              ? `${count} photo${count === 1 ? '' : 's'} selected`
              : 'Click photos to choose them')}
          </div>
          {selectableLoaded > 0 && (
            <button
              onClick={selectAllLoaded}
              style={{
                background: 'none', border: '1px solid #44474f', color: '#e2e2e6',
                borderRadius: '8px', padding: '8px 14px', cursor: 'pointer',
                fontSize: '12.5px', fontFamily: 'inherit', whiteSpace: 'nowrap',
              }}
            >
              Select all {selectableLoaded} shown
            </button>
          )}
          <button
            onClick={submit}
            disabled={count === 0 || adding}
            style={{
              background: count > 0 && !adding ? '#d9a441' : 'rgba(217,164,65,0.2)',
              color: count > 0 && !adding ? '#3d2f00' : '#8e9099',
              border: 'none', borderRadius: '8px', padding: '10px 22px',
              fontSize: '13px', fontWeight: 700, fontFamily: 'inherit',
              cursor: count > 0 && !adding ? 'pointer' : 'default', whiteSpace: 'nowrap',
            }}
          >
            {adding ? 'Adding…' : `Add ${count || ''} to deck`.replace('  ', ' ')}
          </button>
        </div>
      </div>
    </div>
  );
}
