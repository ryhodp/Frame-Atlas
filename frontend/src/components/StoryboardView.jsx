import { useEffect, useRef, useState } from 'react';

// ── Full-screen storyboard for one scene (or Unsorted) ────────────────────────
// Frames appear in sequence order with a number badge and an always-visible
// note field underneath. Drag a card onto another card to reorder — the drop
// inserts the dragged frame at that position. Order persists via
// POST /api/decks/<id>/reorder, notes via POST /api/deck-images/<id>/note.
export default function StoryboardView({ deckId, sceneId, title, images, onClose, onChanged }) {
  // Local copy of the sequence — reordering happens here instantly, then the
  // server is told. Parent refetches the deck when the storyboard closes.
  const [frames, setFrames] = useState(images);
  const [dragIndex, setDragIndex] = useState(null);   // index of card being dragged
  const [overIndex, setOverIndex] = useState(null);   // index currently hovered as drop target
  const [saveState, setSaveState] = useState('');     // '', 'saving', 'saved', 'error'
  const changedRef = useRef(false);

  // ESC closes
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(changedRef.current); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // Lock page scroll behind the overlay while open
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, []);

  const flashSave = (state) => {
    setSaveState(state);
    if (state === 'saved') setTimeout(() => setSaveState(s => (s === 'saved' ? '' : s)), 1500);
  };

  const persistOrder = async (ordered) => {
    flashSave('saving');
    try {
      const res = await fetch(`/api/decks/${deckId}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          scene_id: sceneId,
          deck_image_ids: ordered.map(f => f.deck_image_id)
        })
      });
      if (!res.ok) throw new Error('reorder failed');
      changedRef.current = true;
      flashSave('saved');
    } catch (e) {
      console.error('Save order failed', e);
      flashSave('error');
    }
  };

  const handleDrop = (targetIndex) => {
    setOverIndex(null);
    if (dragIndex === null || dragIndex === targetIndex) { setDragIndex(null); return; }
    const next = [...frames];
    const [moved] = next.splice(dragIndex, 1);
    next.splice(targetIndex, 0, moved);
    setFrames(next);
    setDragIndex(null);
    persistOrder(next);
  };

  const saveNote = async (deckImageId, note) => {
    flashSave('saving');
    try {
      const res = await fetch(`/api/deck-images/${deckImageId}/note`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note })
      });
      if (!res.ok) throw new Error('note save failed');
      changedRef.current = true;
      flashSave('saved');
    } catch (e) {
      console.error('Save note failed', e);
      flashSave('error');
    }
  };

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      background: '#0a0a0b',
      display: 'flex', flexDirection: 'column'
    }}>
      {/* Header bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: '14px',
        padding: '14px 24px',
        borderBottom: '1px solid #44474f',
        background: '#1a1c20',
        flexShrink: 0
      }}>
        <span style={{ fontSize: '12px', letterSpacing: '0.12em', color: '#d9a441', textTransform: 'uppercase', fontWeight: 600 }}>
          Storyboard
        </span>
        <span style={{ fontSize: '17px', fontWeight: 600, color: '#e2e2e6' }}>{title}</span>
        <span style={{ fontSize: '12.5px', color: '#8e9099' }}>
          {frames.length} frame{frames.length === 1 ? '' : 's'} · drag to reorder
        </span>

        <div style={{ flex: 1 }} />

        <span style={{
          fontSize: '12px', minWidth: '70px', textAlign: 'right',
          color: saveState === 'error' ? '#ffb4ab' : saveState === 'saved' ? '#b8cea1' : '#8e9099'
        }}>
          {saveState === 'saving' ? 'Saving…' : saveState === 'saved' ? 'Saved ✓' : saveState === 'error' ? 'Save failed' : ''}
        </span>

        <button
          onClick={() => onClose(changedRef.current)}
          style={{
            background: 'none', border: '1px solid #44474f',
            color: '#e2e2e6', borderRadius: '8px', padding: '7px 16px',
            cursor: 'pointer', fontSize: '13px', fontFamily: 'inherit'
          }}
        >
          Done
        </button>
      </div>

      {/* Frame sequence */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '28px 24px' }}>
        {frames.length === 0 ? (
          <div style={{ color: '#8e9099', fontSize: '14px', textAlign: 'center', marginTop: '80px' }}>
            No frames in this section yet — add photos from the deck view first.
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
            gap: '20px',
            maxWidth: '1400px',
            margin: '0 auto'
          }}>
            {frames.map((frame, i) => (
              <StoryboardCard
                key={frame.deck_image_id}
                frame={frame}
                index={i}
                isDragging={dragIndex === i}
                isDropTarget={overIndex === i && dragIndex !== null && dragIndex !== i}
                onDragStart={() => setDragIndex(i)}
                onDragEnd={() => { setDragIndex(null); setOverIndex(null); }}
                onDragEnter={() => setOverIndex(i)}
                onDrop={() => handleDrop(i)}
                onSaveNote={(note) => saveNote(frame.deck_image_id, note)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function StoryboardCard({
  frame, index, isDragging, isDropTarget,
  onDragStart, onDragEnd, onDragEnter, onDrop, onSaveNote
}) {
  const [note, setNote] = useState(frame.storyboard_note || '');
  const savedNoteRef = useRef(frame.storyboard_note || '');

  const handleBlur = () => {
    const trimmed = note.trim();
    if (trimmed === savedNoteRef.current) return;
    savedNoteRef.current = trimmed;
    onSaveNote(trimmed);
  };

  return (
    <div
      draggable
      onDragStart={e => { e.dataTransfer.setData('text/plain', String(frame.deck_image_id)); onDragStart(); }}
      onDragEnd={onDragEnd}
      onDragOver={e => e.preventDefault()}
      onDragEnter={onDragEnter}
      onDrop={e => { e.preventDefault(); onDrop(); }}
      style={{
        background: '#1a1c20',
        border: `1px solid ${isDropTarget ? '#d9a441' : '#44474f'}`,
        borderRadius: '12px',
        overflow: 'hidden',
        opacity: isDragging ? 0.35 : 1,
        boxShadow: isDropTarget ? '0 0 0 2px rgba(217,164,65,0.35)' : 'none',
        transition: 'border-color 120ms ease, box-shadow 120ms ease, opacity 120ms ease',
        cursor: 'grab'
      }}
    >
      <div style={{ position: 'relative', background: '#111317' }}>
        {frame.thumbnail && (
          <img
            src={frame.thumbnail}
            alt={frame.filename || ''}
            draggable={false}
            style={{ width: '100%', display: 'block', maxHeight: '340px', objectFit: 'contain' }}
          />
        )}
        {/* Sequence number badge */}
        <div style={{
          position: 'absolute', top: '8px', left: '8px',
          background: '#d9a441', color: '#3d2f00',
          borderRadius: '6px', minWidth: '24px', height: '24px',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '13px', fontWeight: 700, padding: '0 6px'
        }}>
          {index + 1}
        </div>
      </div>

      <textarea
        value={note}
        onChange={e => setNote(e.target.value)}
        onBlur={handleBlur}
        // Stop drag from hijacking text selection inside the note field
        draggable
        onDragStart={e => { e.preventDefault(); e.stopPropagation(); }}
        onMouseDown={e => e.stopPropagation()}
        placeholder="Add a note — camera move, lighting cue, why this frame…"
        rows={2}
        style={{
          width: '100%', boxSizing: 'border-box', resize: 'vertical',
          background: '#111317', color: '#e2e2e6',
          border: 'none', borderTop: '1px solid #2c2f35',
          padding: '10px 12px', fontSize: '13px', lineHeight: 1.5,
          fontFamily: 'inherit', outline: 'none', display: 'block'
        }}
      />
    </div>
  );
}
