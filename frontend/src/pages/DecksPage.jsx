import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useIsMobile } from '../hooks/useIsMobile';
import { useOfflineCache } from '../hooks/useOfflineCache';
import { PAGE_BG, black, error, offlineAccent, onPrimary, onSurface, onSurfaceCool, onSurfaceMuted, onSurfaceVariant, outline, outlineVariant, primary, surfaceContainerHigh, surfaceContainerLow, surfaceContainerLowestAlt, tertiary, white, withAlpha } from '../theme';

// ── Confirm step — small inline modal, dark panel look (same pattern as TagModeBar) ──
function ConfirmModal({ text, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel }) {
  return (
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0, background: withAlpha(black,0.5),
        zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center'
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: surfaceContainerHigh,
          border: `1px solid ${withAlpha(white,0.12)}`,
          borderRadius: '12px',
          padding: '18px 20px',
          width: '340px',
          boxShadow: `0 20px 48px ${withAlpha(black,0.6)}`,
        }}
      >
        <div style={{ fontSize: '13.5px', color: onSurface, lineHeight: 1.5, marginBottom: '16px' }}>
          {text}
        </div>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={busy}
            style={{
              background: 'none', border: `1px solid ${withAlpha(white,0.12)}`,
              color: onSurfaceMuted, borderRadius: '6px', padding: '7px 14px',
              cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            style={{
              background: danger ? withAlpha(error,0.18) : withAlpha(tertiary,0.18),
              border: `1px solid ${danger ? withAlpha(error,0.6) : withAlpha(tertiary,0.6)}`,
              color: danger ? error : tertiary,
              borderRadius: '6px', padding: '7px 14px',
              cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit',
              opacity: busy ? 0.6 : 1
            }}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function DecksPage() {
  const [decks, setDecks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [newDeckName, setNewDeckName] = useState('');
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null); // deck object or null
  const [busy, setBusy] = useState(false);
  const [showingCached, setShowingCached] = useState(false);
  const isMobile = useIsMobile();

  const cache = useOfflineCache();
  const { ready: cacheReady, error: cacheError, getCachedDecks } = cache;

  const navigate = useNavigate();

  const loadDecks = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch('/api/decks');
      if (!res.ok) throw new Error(`decks fetch failed: ${res.status}`);
      const data = await res.json();
      setDecks(Array.isArray(data) ? data : []);
      setShowingCached(false);
    } catch (err) {
      // Offline: show whatever decks this device has saved, so the list is
      // still a way in to the cached deck pages rather than a dead end.
      const cached = await getCachedDecks();
      if (cached.length) {
        setDecks(cached.map(entry => entry.data).filter(Boolean));
        setShowingCached(true);
      } else {
        console.error('Failed to load decks', err);
      }
    } finally {
      setLoading(false);
    }
  }, [getCachedDecks]);

  useEffect(() => {
    if (cacheReady || cacheError) loadDecks();
  }, [loadDecks, cacheReady, cacheError]);

  const createDeck = async () => {
    const name = newDeckName.trim();
    if (!name || creating) return;
    setCreating(true);
    try {
      const res = await fetch('/api/decks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      const deck = await res.json();
      setNewDeckName('');
      setDecks(prev => [deck, ...prev]);
      // Nice UX — you just made it, go organize it
      navigate(`/decks/${deck.id}`);
    } catch (e) {
      console.error('Create deck failed', e);
    }
    setCreating(false);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await fetch(`/api/decks/${deleteTarget.id}`, { method: 'DELETE' });
      setDecks(prev => prev.filter(d => d.id !== deleteTarget.id));
    } catch (e) {
      console.error('Delete deck failed', e);
    }
    setBusy(false);
    setDeleteTarget(null);
  };

  return (
    // Full-bleed color on an outer wrapper, THEN the centered content column —
    // background on the maxWidth div alone would leave the old near-black
    // showing on both sides on anything wider than 1400px.
    <div style={{ background: PAGE_BG, minHeight: '100%' }}>
      <div style={{ maxWidth: '1400px', margin: '0 auto', padding: isMobile ? '20px 16px' : '32px 24px' }}>
        {showingCached && (
          <div style={{
            background: withAlpha(offlineAccent,0.12), border: `1px solid ${withAlpha(offlineAccent,0.35)}`,
            borderRadius: '8px', padding: '12px 14px', marginBottom: '16px',
            fontSize: '12px', color: onSurfaceCool
          }}>
            ⚡ Offline — showing {decks.length} deck{decks.length === 1 ? '' : 's'} saved to this device.
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '28px', flexWrap: 'wrap', gap: '16px' }}>
          <h1 style={{ fontSize: isMobile ? '24px' : '32px', lineHeight: isMobile ? '30px' : '40px', fontWeight: 700, color: onSurface, margin: 0 }}>
            Decks
          </h1>

          {/* + New Deck — inline name input + create button */}
          <div style={{ display: 'flex', gap: '6px', width: isMobile ? '100%' : 'auto' }}>
            <input
              value={newDeckName}
              onChange={e => setNewDeckName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') createDeck(); }}
              placeholder="New deck name…"
              style={{
                background: surfaceContainerLow, color: onSurface,
                border: `1px solid ${outlineVariant}`,
                borderRadius: '8px', padding: '9px 12px',
                fontSize: '14px', fontFamily: 'inherit', outline: 'none',
                width: isMobile ? undefined : '220px',
                flex: isMobile ? 1 : 'none',
                minWidth: 0
              }}
            />
            <button
              onClick={createDeck}
              disabled={!newDeckName.trim() || creating}
              style={{
                background: newDeckName.trim() ? primary : withAlpha(primary,0.2),
                color: newDeckName.trim() ? onPrimary : outline,
                border: 'none', borderRadius: '8px',
                padding: '9px 16px', fontSize: '14px', fontWeight: 500,
                cursor: newDeckName.trim() ? 'pointer' : 'default',
                fontFamily: 'inherit', whiteSpace: 'nowrap', flexShrink: 0
              }}
            >
              {creating ? 'Creating…' : '+ New Deck'}
            </button>
          </div>
        </div>

        {loading ? (
          <div style={{ color: outline, fontSize: '14px' }}>Loading decks…</div>
        ) : decks.length === 0 ? (
          <div style={{
            color: outline, fontSize: '14px',
            background: surfaceContainerLow, border: `1px solid ${outlineVariant}`,
            borderRadius: '12px', padding: '32px', textAlign: 'center'
          }}>
            No decks yet. Create one above to start building a lookbook.
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: isMobile ? 'repeat(2, 1fr)' : 'repeat(auto-fill, minmax(260px, 1fr))',
            gap: isMobile ? '12px' : '20px'
          }}>
            {decks.map(deck => (
              <DeckCard
                key={deck.id}
                deck={deck}
                onOpen={() => navigate(`/decks/${deck.id}`)}
                onDelete={() => setDeleteTarget(deck)}
              />
            ))}
          </div>
        )}

        {deleteTarget && (
          <ConfirmModal
            text={<>Delete "<strong>{deleteTarget.name}</strong>" and everything in it? This removes all its scenes and photo groupings — the photos themselves stay in your library.</>}
            confirmLabel="Delete"
            danger
            busy={busy}
            onConfirm={confirmDelete}
            onCancel={() => !busy && setDeleteTarget(null)}
          />
        )}
      </div>
    </div>
  );
}

function DeckCard({ deck, onOpen, onDelete }) {
  const thumbs = deck.preview_thumbnails || [];

  return (
    <div
      onClick={onOpen}
      style={{
        background: surfaceContainerLow,
        border: `1px solid ${outlineVariant}`,
        borderRadius: '12px',
        padding: '16px',
        cursor: 'pointer',
        position: 'relative',
        transition: 'transform 150ms ease, box-shadow 150ms ease',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.transform = 'scale(1.01)';
        e.currentTarget.style.boxShadow = `0 8px 24px ${withAlpha(black,0.4)}`;
      }}
      onMouseLeave={e => {
        e.currentTarget.style.transform = 'none';
        e.currentTarget.style.boxShadow = 'none';
      }}
    >
      {deck.is_owner ? (
        <button
          onClick={e => { e.stopPropagation(); onDelete(); }}
          title="Delete deck"
          style={{
            position: 'absolute', top: '10px', right: '10px',
            background: withAlpha(black,0.5), border: 'none',
            color: error, borderRadius: '6px',
            width: '24px', height: '24px',
            cursor: 'pointer', fontSize: '15px', lineHeight: 1,
            zIndex: 2
          }}
        >×</button>
      ) : (
        <div
          title={`Shared by ${deck.owner_name}`}
          style={{
            position: 'absolute', top: '10px', right: '10px',
            background: withAlpha(tertiary,0.18),
            border: `1px solid ${withAlpha(tertiary,0.5)}`,
            color: tertiary, borderRadius: '6px',
            padding: '3px 8px',
            fontSize: '10.5px', lineHeight: 1.4,
            zIndex: 2, whiteSpace: 'nowrap'
          }}
        >
          👁 {deck.owner_name}
        </div>
      )}

      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gridTemplateRows: '1fr 1fr',
        gap: '3px',
        width: '100%',
        aspectRatio: '1',
        borderRadius: '8px',
        overflow: 'hidden',
        background: surfaceContainerLowestAlt,
        marginBottom: '12px'
      }}>
        {Array.from({ length: 4 }).map((_, i) => (
          thumbs[i] ? (
            <img
              key={i}
              src={thumbs[i]}
              alt=""
              style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            />
          ) : (
            <div key={i} style={{ width: '100%', height: '100%', background: surfaceContainerHigh }} />
          )
        ))}
      </div>

      <div style={{ fontSize: '16px', fontWeight: 500, color: onSurface, marginBottom: '4px' }}>
        {deck.name}
      </div>
      <div style={{ fontSize: '13px', color: onSurfaceVariant }}>
        {deck.image_count} photo{deck.image_count === 1 ? '' : 's'}
      </div>
    </div>
  );
}
