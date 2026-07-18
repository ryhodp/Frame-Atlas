import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useIsMobile } from '../hooks/useIsMobile';

// ── Confirm step — small inline modal, dark panel look (same pattern as TagModeBar) ──
function ConfirmModal({ text, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel }) {
  return (
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center'
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#2a2c31',
          border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '12px',
          padding: '18px 20px',
          width: '340px',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)',
        }}
      >
        <div style={{ fontSize: '13.5px', color: '#e2e2e6', lineHeight: 1.5, marginBottom: '16px' }}>
          {text}
        </div>
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onCancel}
            disabled={busy}
            style={{
              background: 'none', border: '1px solid rgba(255,255,255,0.12)',
              color: '#9c988d', borderRadius: '6px', padding: '7px 14px',
              cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
            }}
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={busy}
            style={{
              background: danger ? 'rgba(255,180,171,0.18)' : 'rgba(184,206,161,0.18)',
              border: `1px solid ${danger ? 'rgba(255,180,171,0.6)' : 'rgba(184,206,161,0.6)'}`,
              color: danger ? '#ffb4ab' : '#b8cea1',
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
  const isMobile = useIsMobile();

  const navigate = useNavigate();

  const loadDecks = () => {
    setLoading(true);
    fetch('/api/decks')
      .then(res => res.json())
      .then(data => setDecks(Array.isArray(data) ? data : []))
      .catch(err => console.error('Failed to load decks', err))
      .finally(() => setLoading(false));
  };

  useEffect(() => { loadDecks(); }, []);

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
    <div style={{ maxWidth: '1400px', margin: '0 auto', padding: isMobile ? '20px 16px' : '32px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '28px', flexWrap: 'wrap', gap: '16px' }}>
        <h1 style={{ fontSize: isMobile ? '24px' : '32px', lineHeight: isMobile ? '30px' : '40px', fontWeight: 700, color: '#e2e2e6', margin: 0 }}>
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
              background: '#1a1c20', color: '#e2e2e6',
              border: '1px solid #44474f',
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
              background: newDeckName.trim() ? '#d9a441' : 'rgba(217,164,65,0.2)',
              color: newDeckName.trim() ? '#3d2f00' : '#8e9099',
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
        <div style={{ color: '#8e9099', fontSize: '14px' }}>Loading decks…</div>
      ) : decks.length === 0 ? (
        <div style={{
          color: '#8e9099', fontSize: '14px',
          background: '#1a1c20', border: '1px solid #44474f',
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
  );
}

function DeckCard({ deck, onOpen, onDelete }) {
  const thumbs = deck.preview_thumbnails || [];

  return (
    <div
      onClick={onOpen}
      style={{
        background: '#1a1c20',
        border: '1px solid #44474f',
        borderRadius: '12px',
        padding: '16px',
        cursor: 'pointer',
        position: 'relative',
        transition: 'transform 150ms ease, box-shadow 150ms ease',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.transform = 'scale(1.01)';
        e.currentTarget.style.boxShadow = '0 8px 24px rgba(0,0,0,0.4)';
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
            background: 'rgba(0,0,0,0.5)', border: 'none',
            color: '#ffb4ab', borderRadius: '6px',
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
            background: 'rgba(184,206,161,0.18)',
            border: '1px solid rgba(184,206,161,0.5)',
            color: '#b8cea1', borderRadius: '6px',
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
        background: '#111317',
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
            <div key={i} style={{ width: '100%', height: '100%', background: '#2a2c31' }} />
          )
        ))}
      </div>

      <div style={{ fontSize: '16px', fontWeight: 500, color: '#e2e2e6', marginBottom: '4px' }}>
        {deck.name}
      </div>
      <div style={{ fontSize: '13px', color: '#c4c6d0' }}>
        {deck.image_count} photo{deck.image_count === 1 ? '' : 's'}
      </div>
    </div>
  );
}
