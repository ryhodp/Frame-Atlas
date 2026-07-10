import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';

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

export default function DeckDetail() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [deck, setDeck] = useState(null);
  const [loading, setLoading] = useState(true);

  const [renamingDeck, setRenamingDeck] = useState(false);
  const [deckNameDraft, setDeckNameDraft] = useState('');

  const [newSceneName, setNewSceneName] = useState('');
  const [creatingScene, setCreatingScene] = useState(false);

  const [collapsed, setCollapsed] = useState({}); // sceneKey -> bool
  const [dragOverKey, setDragOverKey] = useState(null); // sceneKey currently being dragged over

  const [sceneToDelete, setSceneToDelete] = useState(null); // scene object or null
  const [busy, setBusy] = useState(false);

  const loadDeck = useCallback(() => {
    setLoading(true);
    fetch(`/api/decks/${id}`)
      .then(res => res.json())
      .then(data => {
        setDeck(data);
        setDeckNameDraft(data.name || '');
      })
      .catch(err => console.error('Failed to load deck', err))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => { loadDeck(); }, [loadDeck]);

  if (loading && !deck) {
    return (
      <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '32px 24px', color: '#8e9099', fontSize: '14px' }}>
        Loading deck…
      </div>
    );
  }

  if (!deck) {
    return (
      <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '32px 24px', color: '#8e9099', fontSize: '14px' }}>
        Deck not found.
      </div>
    );
  }

  // ── Group flat images list into Unsorted + per-scene buckets ────────────────
  const unsorted = deck.images.filter(img => img.scene_id === null);
  const scenes = [...(deck.scenes || [])].sort((a, b) => a.sort_order - b.sort_order);
  const bucketFor = (sceneId) => deck.images.filter(img => img.scene_id === sceneId);

  // ── Deck rename ───────────────────────────────────────────────────────────
  const saveDeckName = async () => {
    const name = deckNameDraft.trim();
    setRenamingDeck(false);
    if (!name || name === deck.name) {
      setDeckNameDraft(deck.name);
      return;
    }
    setDeck(prev => ({ ...prev, name }));
    try {
      await fetch(`/api/decks/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
    } catch (e) {
      console.error('Rename deck failed', e);
    }
  };

  // ── New scene ─────────────────────────────────────────────────────────────
  const createScene = async () => {
    const name = newSceneName.trim();
    if (!name || creatingScene) return;
    setCreatingScene(true);
    try {
      await fetch('/api/scenes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deck_id: Number(id), name })
      });
      setNewSceneName('');
      loadDeck();
    } catch (e) {
      console.error('Create scene failed', e);
    }
    setCreatingScene(false);
  };

  // ── Scene rename ──────────────────────────────────────────────────────────
  const renameScene = async (sceneId, name) => {
    try {
      await fetch(`/api/scenes/${sceneId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      loadDeck();
    } catch (e) {
      console.error('Rename scene failed', e);
    }
  };

  // ── Scene delete ──────────────────────────────────────────────────────────
  const confirmDeleteScene = async () => {
    if (!sceneToDelete) return;
    setBusy(true);
    try {
      await fetch(`/api/scenes/${sceneToDelete.id}`, { method: 'DELETE' });
      loadDeck();
    } catch (e) {
      console.error('Delete scene failed', e);
    }
    setBusy(false);
    setSceneToDelete(null);
  };

  // ── Remove one photo from one scene/Unsorted ────────────────────────────────
  const removeDeckImage = async (deckImageId) => {
    // Optimistic local splice
    setDeck(prev => ({ ...prev, images: prev.images.filter(img => img.deck_image_id !== deckImageId) }));
    try {
      await fetch(`/api/deck-images/${deckImageId}`, { method: 'DELETE' });
    } catch (e) {
      console.error('Remove photo failed', e);
      loadDeck();
    }
  };

  // ── Move / copy on drop ──────────────────────────────────────────────────────
  // Move-vs-copy is decided server-side. We just call the endpoint and then
  // always refetch the full deck — simplest way to reflect either outcome
  // (an in-place scene_id change for "moved", or a brand new row for "copied").
  const handleDrop = async (targetSceneId, e) => {
    e.preventDefault();
    const deckImageId = e.dataTransfer.getData('text/plain');
    if (!deckImageId) return;
    try {
      await fetch(`/api/deck-images/${deckImageId}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_scene_id: targetSceneId })
      });
    } catch (e2) {
      console.error('Move/copy photo failed', e2);
    }
    loadDeck();
  };

  const toggleCollapsed = (key) => {
    setCollapsed(prev => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '32px 24px' }}>
      <button
        onClick={() => navigate('/decks')}
        style={{
          background: 'none', border: 'none', color: '#8e9099',
          cursor: 'pointer', fontSize: '13px', fontFamily: 'inherit',
          padding: 0, marginBottom: '16px'
        }}
      >
        ← All Decks
      </button>

      {/* Deck name — click to rename */}
      <div style={{ marginBottom: '24px' }}>
        {renamingDeck ? (
          <input
            autoFocus
            value={deckNameDraft}
            onChange={e => setDeckNameDraft(e.target.value)}
            onBlur={saveDeckName}
            onKeyDown={e => { if (e.key === 'Enter') e.target.blur(); }}
            style={{
              fontSize: '32px', lineHeight: '40px', fontWeight: 700,
              color: '#e2e2e6', background: '#1a1c20',
              border: '1px solid #d9a441', borderRadius: '8px',
              padding: '4px 10px', fontFamily: 'inherit',
              outline: 'none', width: '100%', maxWidth: '600px'
            }}
          />
        ) : (
          <h1
            onClick={() => { setDeckNameDraft(deck.name); setRenamingDeck(true); }}
            title="Click to rename"
            style={{
              fontSize: '32px', lineHeight: '40px', fontWeight: 700,
              color: '#e2e2e6', margin: 0, cursor: 'pointer',
              display: 'inline-block'
            }}
          >
            {deck.name}
          </h1>
        )}
      </div>

      {/* + New Scene */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '28px' }}>
        <input
          value={newSceneName}
          onChange={e => setNewSceneName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') createScene(); }}
          placeholder="New scene name…"
          style={{
            background: '#1a1c20', color: '#e2e2e6',
            border: '1px solid #44474f',
            borderRadius: '8px', padding: '9px 12px',
            fontSize: '14px', fontFamily: 'inherit', outline: 'none',
            width: '220px'
          }}
        />
        <button
          onClick={createScene}
          disabled={!newSceneName.trim() || creatingScene}
          style={{
            background: newSceneName.trim() ? '#d9a441' : 'rgba(217,164,65,0.2)',
            color: newSceneName.trim() ? '#3d2f00' : '#8e9099',
            border: 'none', borderRadius: '8px',
            padding: '9px 16px', fontSize: '14px', fontWeight: 500,
            cursor: newSceneName.trim() ? 'pointer' : 'default',
            fontFamily: 'inherit', whiteSpace: 'nowrap'
          }}
        >
          {creatingScene ? 'Creating…' : '+ New Scene'}
        </button>
      </div>

      {/* Unsorted — always shown, always a valid drop target */}
      <SceneSection
        sceneKey="unsorted"
        title="Unsorted"
        images={unsorted}
        collapsedState={collapsed}
        toggleCollapsed={toggleCollapsed}
        isDragOver={dragOverKey === 'unsorted'}
        onDragEnter={() => setDragOverKey('unsorted')}
        onDragLeave={() => setDragOverKey(prev => (prev === 'unsorted' ? null : prev))}
        onDrop={e => { setDragOverKey(null); handleDrop(null, e); }}
        onRemoveImage={removeDeckImage}
      />

      {/* One section per scene, in sort_order */}
      {scenes.map(scene => {
        const key = `scene-${scene.id}`;
        return (
          <SceneSection
            key={scene.id}
            sceneKey={key}
            title={scene.name}
            images={bucketFor(scene.id)}
            collapsedState={collapsed}
            toggleCollapsed={toggleCollapsed}
            isDragOver={dragOverKey === key}
            onDragEnter={() => setDragOverKey(key)}
            onDragLeave={() => setDragOverKey(prev => (prev === key ? null : prev))}
            onDrop={e => { setDragOverKey(null); handleDrop(scene.id, e); }}
            onRemoveImage={removeDeckImage}
            editable
            onRename={(name) => renameScene(scene.id, name)}
            onDelete={() => setSceneToDelete(scene)}
          />
        );
      })}

      {sceneToDelete && (
        <ConfirmModal
          text={<>Delete scene "<strong>{sceneToDelete.name}</strong>"? Its {bucketFor(sceneToDelete.id).length} photo{bucketFor(sceneToDelete.id).length === 1 ? '' : 's'} will be removed from this deck.</>}
          confirmLabel="Delete Scene"
          danger
          busy={busy}
          onConfirm={confirmDeleteScene}
          onCancel={() => !busy && setSceneToDelete(null)}
        />
      )}
    </div>
  );
}

function SceneSection({
  sceneKey, title, images, collapsedState, toggleCollapsed,
  isDragOver, onDragEnter, onDragLeave, onDrop,
  onRemoveImage, editable, onRename, onDelete
}) {
  const isCollapsed = !!collapsedState[sceneKey];
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(title);

  const saveRename = () => {
    setRenaming(false);
    const name = draft.trim();
    if (!name || name === title) {
      setDraft(title);
      return;
    }
    onRename?.(name);
  };

  return (
    <div
      onDragOver={e => e.preventDefault()}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      style={{
        marginBottom: '24px',
        borderRadius: '12px',
        border: `1px solid ${isDragOver ? '#d9a441' : '#44474f'}`,
        background: isDragOver ? 'rgba(217,164,65,0.08)' : '#1a1c20',
        padding: '14px 16px',
        transition: 'background 150ms ease, border-color 150ms ease'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: isCollapsed ? 0 : '14px' }}>
        <button
          onClick={() => toggleCollapsed(sceneKey)}
          style={{
            background: 'none', border: 'none', color: '#8e9099',
            cursor: 'pointer', fontSize: '13px', padding: '2px 4px',
            transform: isCollapsed ? 'rotate(-90deg)' : 'none',
            transition: 'transform 150ms ease'
          }}
          title={isCollapsed ? 'Expand' : 'Collapse'}
        >
          ▾
        </button>

        {renaming ? (
          <input
            autoFocus
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onBlur={saveRename}
            onKeyDown={e => { if (e.key === 'Enter') e.target.blur(); }}
            style={{
              fontSize: '16px', fontWeight: 500, color: '#e2e2e6',
              background: '#111317', border: '1px solid #d9a441',
              borderRadius: '6px', padding: '3px 8px', fontFamily: 'inherit',
              outline: 'none'
            }}
          />
        ) : (
          <span
            onClick={editable ? () => { setDraft(title); setRenaming(true); } : undefined}
            style={{
              fontSize: '16px', fontWeight: 500, color: '#e2e2e6',
              cursor: editable ? 'pointer' : 'default'
            }}
            title={editable ? 'Click to rename' : undefined}
          >
            {title}
          </span>
        )}

        <span style={{ fontSize: '12px', color: '#8e9099' }}>
          {images.length} photo{images.length === 1 ? '' : 's'}
        </span>

        <div style={{ flex: 1 }} />

        {editable && (
          <button
            onClick={onDelete}
            title="Delete scene"
            style={{
              background: 'none', border: '1px solid rgba(255,180,171,0.35)',
              color: '#ffb4ab', borderRadius: '6px', padding: '5px 10px',
              cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit'
            }}
          >
            Delete Scene
          </button>
        )}
      </div>

      {!isCollapsed && (
        images.length === 0 ? (
          <div style={{
            fontSize: '12.5px', color: '#8e9099',
            padding: '14px', textAlign: 'center',
            border: '1px dashed #44474f', borderRadius: '8px'
          }}>
            Drag photos here
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
            gap: '8px'
          }}>
            {images.map(img => (
              <DeckTile key={img.deck_image_id} img={img} onRemove={() => onRemoveImage(img.deck_image_id)} />
            ))}
          </div>
        )
      )}
    </div>
  );
}

function DeckTile({ img, onRemove }) {
  return (
    <div
      draggable
      onDragStart={e => e.dataTransfer.setData('text/plain', String(img.deck_image_id))}
      style={{
        position: 'relative',
        aspectRatio: '1',
        borderRadius: '8px',
        overflow: 'hidden',
        background: '#111317',
        cursor: 'grab'
      }}
    >
      {img.thumbnail && (
        <img
          src={img.thumbnail}
          alt={img.filename || ''}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      )}
      <button
        onClick={() => onRemove()}
        title="Remove from this section"
        style={{
          position: 'absolute', top: '4px', right: '4px',
          background: 'rgba(0,0,0,0.55)', border: 'none',
          color: '#ffb4ab', borderRadius: '5px',
          width: '20px', height: '20px', lineHeight: 1,
          cursor: 'pointer', fontSize: '13px'
        }}
      >×</button>
    </div>
  );
}
