import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import StoryboardView from '../components/StoryboardView';
import { useOfflineCache } from '../hooks/useOfflineCache';

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

  const [storyboard, setStoryboard] = useState(null); // { sceneId, title } or null
  const [shareOpen, setShareOpen] = useState(false);
  const [membersOpen, setMembersOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);

  // Offline caching
  const cache = useOfflineCache();
  const [remoteUpdates, setRemoteUpdates] = useState(false); // "New changes" banner
  const [isCached, setIsCached] = useState(false); // Is this deck in the offline cache?

  const loadDeck = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/decks/${id}`);
      const data = await res.json();
      setDeck(data);
      setDeckNameDraft(data.name || '');

      // Offline caching: cache the deck and check for remote updates
      if (cache.ready) {
        cache.cacheDeck(data);
        const cached = await cache.getCachedDeck(parseInt(id));
        if (cached) {
          setIsCached(true);
          // Check if remote has newer updates than what we cached
          if (cache.hasRemoteUpdates(cached, data.updated_at)) {
            setRemoteUpdates(true);
          }
        }
      }
    } catch (err) {
      console.error('Failed to load deck', err);
    } finally {
      setLoading(false);
    }
  }, [id, cache]);

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

      {/* "New changes" banner for offline cache */}
      {remoteUpdates && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '12px',
          background: 'rgba(217,164,65,0.12)', border: '1px solid rgba(217,164,65,0.35)',
          borderRadius: '8px', padding: '12px 14px', marginBottom: '16px'
        }}>
          <span style={{ fontSize: '12px', color: '#dcbd76', flex: 1 }}>
            ✓ This deck has been updated — refresh to see the latest changes
          </span>
          <button
            onClick={() => { setRemoteUpdates(false); loadDeck(); }}
            style={{
              background: '#d9a441', color: '#3d2f00', border: 'none',
              borderRadius: '6px', padding: '6px 12px', fontSize: '12px',
              fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit', flexShrink: 0
            }}
          >
            Refresh
          </button>
        </div>
      )}

      {/* Deck name — click to rename — plus Share/Members/Activity */}
      <div style={{ marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
        {deck.is_owner && renamingDeck ? (
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
            onClick={deck.is_owner ? () => { setDeckNameDraft(deck.name); setRenamingDeck(true); } : undefined}
            title={deck.is_owner ? 'Click to rename' : undefined}
            style={{
              fontSize: '32px', lineHeight: '40px', fontWeight: 700,
              color: '#e2e2e6', margin: 0, cursor: deck.is_owner ? 'pointer' : 'default',
              display: 'inline-block'
            }}
          >
            {deck.name}
          </h1>
        )}

        {deck.is_owner ? (
          <>
            <button
              onClick={() => setShareOpen(true)}
              style={{
                background: deck.share_token ? 'rgba(217,164,65,0.14)' : 'none',
                border: `1px solid ${deck.share_token ? 'rgba(217,164,65,0.55)' : '#44474f'}`,
                color: deck.share_token ? '#d9a441' : '#e2e2e6',
                borderRadius: '8px', padding: '8px 16px',
                cursor: 'pointer', fontSize: '13px', fontFamily: 'inherit',
                whiteSpace: 'nowrap'
              }}
              title={deck.share_token ? 'Public share link is active' : 'Create a read-only public share link'}
            >
              {deck.share_token ? '🔗 Shared' : 'Share'}
            </button>
            <button
              onClick={() => setMembersOpen(true)}
              style={{
                background: 'none', border: '1px solid #44474f',
                color: '#e2e2e6', borderRadius: '8px', padding: '8px 16px',
                cursor: 'pointer', fontSize: '13px', fontFamily: 'inherit',
                whiteSpace: 'nowrap'
              }}
            >
              👥 Crew
            </button>
          </>
        ) : (
          <div style={{
            background: 'rgba(184,206,161,0.14)', border: '1px solid rgba(184,206,161,0.4)',
            color: '#b8cea1', borderRadius: '8px', padding: '7px 14px',
            fontSize: '12.5px', whiteSpace: 'nowrap'
          }}>
            👁 Shared by {deck.owner_name} · view only
          </div>
        )}

        <button
          onClick={() => setActivityOpen(true)}
          style={{
            background: 'none', border: '1px solid #44474f',
            color: '#8e9099', borderRadius: '8px', padding: '8px 16px',
            cursor: 'pointer', fontSize: '13px', fontFamily: 'inherit',
            whiteSpace: 'nowrap'
          }}
        >
          🕘 Activity
        </button>
      </div>

      {!deck.is_owner && (
        <div style={{ fontSize: '12.5px', color: '#8e9099', marginBottom: '18px' }}>
          You can look through this lookbook, but only {deck.owner_name} can edit it.
        </div>
      )}

      {/* + New Scene */}
      {deck.is_owner && (
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
      )}

      {/* Unsorted — always shown, always a valid drop target */}
      <SceneSection
        sceneKey="unsorted"
        title="Unsorted"
        images={unsorted}
        collapsedState={collapsed}
        toggleCollapsed={toggleCollapsed}
        isDragOver={dragOverKey === 'unsorted'}
        onDragEnter={deck.is_owner ? () => setDragOverKey('unsorted') : undefined}
        onDragLeave={deck.is_owner ? () => setDragOverKey(prev => (prev === 'unsorted' ? null : prev)) : undefined}
        onDrop={deck.is_owner ? e => { setDragOverKey(null); handleDrop(null, e); } : undefined}
        onRemoveImage={deck.is_owner ? removeDeckImage : undefined}
        onStoryboard={deck.is_owner ? () => setStoryboard({ sceneId: null, title: 'Unsorted' }) : undefined}
        canEdit={deck.is_owner}
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
            onDragEnter={deck.is_owner ? () => setDragOverKey(key) : undefined}
            onDragLeave={deck.is_owner ? () => setDragOverKey(prev => (prev === key ? null : prev)) : undefined}
            onDrop={deck.is_owner ? e => { setDragOverKey(null); handleDrop(scene.id, e); } : undefined}
            onRemoveImage={deck.is_owner ? removeDeckImage : undefined}
            onStoryboard={deck.is_owner ? () => setStoryboard({ sceneId: scene.id, title: scene.name }) : undefined}
            canEdit={deck.is_owner}
            editable={deck.is_owner}
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

      {storyboard && (
        <StoryboardView
          deckId={Number(id)}
          sceneId={storyboard.sceneId}
          title={storyboard.title}
          images={bucketFor(storyboard.sceneId)}
          onClose={(changed) => { setStoryboard(null); if (changed) loadDeck(); }}
        />
      )}

      {shareOpen && (
        <ShareModal
          deckId={Number(id)}
          shareToken={deck.share_token}
          onTokenChange={(token) => setDeck(prev => ({ ...prev, share_token: token }))}
          onClose={() => setShareOpen(false)}
        />
      )}

      {membersOpen && (
        <MembersModal
          deckId={Number(id)}
          onClose={() => setMembersOpen(false)}
        />
      )}

      {activityOpen && (
        <ActivityPanel
          deckId={Number(id)}
          onClose={() => setActivityOpen(false)}
        />
      )}
    </div>
  );
}

// ── Crew (members) modal — invite by email, invite link, current member list ──
function MembersModal({ deckId, onClose }) {
  const [members, setMembers] = useState(null);
  const [inviteToken, setInviteToken] = useState(null);
  const [email, setEmail] = useState('');
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState('');
  const [inviteSuccess, setInviteSuccess] = useState('');
  const [linkWorking, setLinkWorking] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmRevokeLink, setConfirmRevokeLink] = useState(false);
  const [removeTarget, setRemoveTarget] = useState(null); // member object or null
  const [busy, setBusy] = useState(false);

  const loadMembers = () => {
    fetch(`/api/decks/${deckId}/members`)
      .then(res => res.json())
      .then(data => setMembers(Array.isArray(data) ? data : []))
      .catch(err => console.error('Failed to load members', err));
  };

  useEffect(() => { loadMembers(); }, [deckId]);

  const sendInvite = async () => {
    const value = email.trim();
    if (!value || inviting) return;
    setInviting(true);
    setInviteError('');
    setInviteSuccess('');
    try {
      const res = await fetch(`/api/decks/${deckId}/invite`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: value })
      });
      const data = await res.json();
      if (!res.ok) {
        setInviteError(data.message || data.error || 'Could not invite that email.');
      } else {
        setInviteSuccess(`${data.name} can now view this deck.`);
        setEmail('');
        loadMembers();
      }
    } catch (e) {
      console.error('Invite failed', e);
      setInviteError('Something went wrong — try again.');
    }
    setInviting(false);
  };

  const createLink = async () => {
    setLinkWorking(true);
    try {
      const res = await fetch(`/api/decks/${deckId}/invite-link`, { method: 'POST' });
      const data = await res.json();
      if (data.invite_token) setInviteToken(data.invite_token);
    } catch (e) {
      console.error('Create invite link failed', e);
    }
    setLinkWorking(false);
  };

  const revokeLink = async () => {
    setLinkWorking(true);
    try {
      await fetch(`/api/decks/${deckId}/invite-link`, { method: 'DELETE' });
      setInviteToken(null);
      setConfirmRevokeLink(false);
    } catch (e) {
      console.error('Revoke invite link failed', e);
    }
    setLinkWorking(false);
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/invite/${inviteToken}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard API unavailable — the visible URL text is the fallback.
    }
  };

  const confirmRemove = async () => {
    if (!removeTarget) return;
    setBusy(true);
    try {
      await fetch(`/api/decks/${deckId}/members/${removeTarget.user_id}`, { method: 'DELETE' });
      loadMembers();
    } catch (e) {
      console.error('Remove member failed', e);
    }
    setBusy(false);
    setRemoveTarget(null);
  };

  return (
    <div
      onClick={onClose}
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
          padding: '20px 22px',
          width: '460px', maxWidth: 'calc(100vw - 48px)',
          maxHeight: 'calc(100vh - 80px)', overflowY: 'auto',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)'
        }}
      >
        <div style={{ fontSize: '15px', fontWeight: 600, color: '#e2e2e6', marginBottom: '6px' }}>
          Crew on this deck
        </div>
        <div style={{ fontSize: '12.5px', color: '#9c988d', lineHeight: 1.5, marginBottom: '16px' }}>
          Crew members can view scenes, frame order, and notes — signed in, view-only.
          They can't edit anything.
        </div>

        {/* Invite by email */}
        <div style={{ display: 'flex', gap: '8px', marginBottom: '6px' }}>
          <input
            value={email}
            onChange={e => { setEmail(e.target.value); setInviteError(''); setInviteSuccess(''); }}
            onKeyDown={e => { if (e.key === 'Enter') sendInvite(); }}
            placeholder="friend@email.com"
            style={{
              flex: 1, background: '#111317', color: '#e2e2e6',
              border: '1px solid #44474f', borderRadius: '8px',
              padding: '9px 10px', fontSize: '13px', fontFamily: 'inherit',
              outline: 'none'
            }}
          />
          <button
            onClick={sendInvite}
            disabled={!email.trim() || inviting}
            style={{
              background: email.trim() ? '#d9a441' : 'rgba(217,164,65,0.2)',
              color: email.trim() ? '#3d2f00' : '#8e9099',
              border: 'none', borderRadius: '8px',
              padding: '9px 16px', fontSize: '13px', fontWeight: 600,
              cursor: email.trim() ? 'pointer' : 'default',
              fontFamily: 'inherit', whiteSpace: 'nowrap'
            }}
          >
            {inviting ? 'Inviting…' : 'Invite'}
          </button>
        </div>
        {inviteError && (
          <div style={{ fontSize: '12px', color: '#ffb4ab', marginBottom: '10px' }}>{inviteError}</div>
        )}
        {inviteSuccess && (
          <div style={{ fontSize: '12px', color: '#b8cea1', marginBottom: '10px' }}>{inviteSuccess}</div>
        )}

        <div style={{ fontSize: '12px', color: '#8e9099', margin: '14px 0 8px' }}>
          Or share an invite link — they'll join automatically once signed in:
        </div>

        {!inviteToken ? (
          <button
            onClick={createLink}
            disabled={linkWorking}
            style={{
              background: 'none', border: '1px solid #44474f',
              color: '#e2e2e6', borderRadius: '8px', padding: '8px 14px',
              fontSize: '12.5px', cursor: 'pointer', fontFamily: 'inherit',
              opacity: linkWorking ? 0.6 : 1
            }}
          >
            {linkWorking ? 'Creating…' : 'Get Invite Link'}
          </button>
        ) : (
          <>
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px' }}>
              <input
                readOnly
                value={`${window.location.origin}/invite/${inviteToken}`}
                onFocus={e => e.target.select()}
                style={{
                  flex: 1, background: '#111317', color: '#e2e2e6',
                  border: '1px solid #44474f', borderRadius: '8px',
                  padding: '9px 10px', fontSize: '11.5px', fontFamily: 'inherit',
                  outline: 'none'
                }}
              />
              <button
                onClick={copyLink}
                style={{
                  background: copied ? 'rgba(184,206,161,0.18)' : '#d9a441',
                  color: copied ? '#b8cea1' : '#3d2f00',
                  border: copied ? '1px solid rgba(184,206,161,0.6)' : 'none',
                  borderRadius: '8px', padding: '9px 14px',
                  fontSize: '12.5px', fontWeight: 600, cursor: 'pointer',
                  fontFamily: 'inherit', whiteSpace: 'nowrap'
                }}
              >
                {copied ? 'Copied ✓' : 'Copy'}
              </button>
            </div>
            {confirmRevokeLink ? (
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '12px', color: '#ffb4ab' }}>Link stops working for everyone.</span>
                <div style={{ flex: 1 }} />
                <button
                  onClick={() => setConfirmRevokeLink(false)}
                  disabled={linkWorking}
                  style={{
                    background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                    color: '#9c988d', borderRadius: '6px', padding: '6px 12px',
                    cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit'
                  }}
                >
                  Keep
                </button>
                <button
                  onClick={revokeLink}
                  disabled={linkWorking}
                  style={{
                    background: 'rgba(255,180,171,0.18)', border: '1px solid rgba(255,180,171,0.6)',
                    color: '#ffb4ab', borderRadius: '6px', padding: '6px 12px',
                    cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit'
                  }}
                >
                  {linkWorking ? 'Revoking…' : 'Revoke'}
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmRevokeLink(true)}
                style={{
                  background: 'none', border: '1px solid rgba(255,180,171,0.35)',
                  color: '#ffb4ab', borderRadius: '6px', padding: '5px 10px',
                  cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit'
                }}
              >
                Revoke Link…
              </button>
            )}
          </>
        )}

        <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', margin: '18px 0 12px' }} />

        <div style={{ fontSize: '12px', color: '#8e9099', marginBottom: '10px' }}>
          {members === null ? 'Loading…' : members.length === 0 ? 'No crew yet.' : `${members.length} crew member${members.length === 1 ? '' : 's'}`}
        </div>

        {members && members.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {members.map(m => (
              <div
                key={m.user_id}
                style={{
                  display: 'flex', alignItems: 'center', gap: '10px',
                  background: '#1a1c20', border: '1px solid #35373d',
                  borderRadius: '8px', padding: '8px 10px'
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '13px', color: '#e2e2e6' }}>{m.name}</div>
                  {m.email && <div style={{ fontSize: '11px', color: '#8e9099' }}>{m.email}</div>}
                </div>
                <button
                  onClick={() => setRemoveTarget(m)}
                  style={{
                    background: 'none', border: '1px solid rgba(255,180,171,0.35)',
                    color: '#ffb4ab', borderRadius: '6px', padding: '5px 10px',
                    cursor: 'pointer', fontSize: '11px', fontFamily: 'inherit',
                    whiteSpace: 'nowrap'
                  }}
                >
                  Remove
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {removeTarget && (
        <ConfirmModal
          text={<>Remove <strong>{removeTarget.name}</strong> from this deck? They'll lose access immediately.</>}
          confirmLabel="Remove"
          danger
          busy={busy}
          onConfirm={confirmRemove}
          onCancel={() => !busy && setRemoveTarget(null)}
        />
      )}
    </div>
  );
}

// ── Activity feed — recent changes, visible to owner + crew ─────────────────
const ACTIVITY_TEXT = {
  invited: (a) => `${a.actor} invited ${a.detail}`,
  joined: (a) => `${a.actor} joined via invite link`,
  renamed: (a) => `${a.actor} renamed the deck to "${a.detail}"`,
  added_scene: (a) => `${a.actor} created scene "${a.detail}"`,
  renamed_scene: (a) => `${a.actor} renamed a scene to "${a.detail}"`,
  deleted_scene: (a) => `${a.actor} deleted scene "${a.detail}"`,
  added_photos: (a) => `${a.actor} added ${a.detail}`,
  moved_photo: (a) => `${a.actor} moved a photo to ${a.detail}`,
  copied_photo: (a) => `${a.actor} copied a photo to ${a.detail}`,
  removed_photo: (a) => `${a.actor} removed a photo`,
  edited_note: (a) => `${a.actor} edited a note`,
};

function ActivityPanel({ deckId, onClose }) {
  const [activity, setActivity] = useState(null);

  useEffect(() => {
    fetch(`/api/decks/${deckId}/activity`)
      .then(res => res.json())
      .then(data => setActivity(Array.isArray(data) ? data : []))
      .catch(err => console.error('Failed to load activity', err));
  }, [deckId]);

  return (
    <div
      onClick={onClose}
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
          padding: '20px 22px',
          width: '420px', maxWidth: 'calc(100vw - 48px)',
          maxHeight: 'calc(100vh - 80px)', overflowY: 'auto',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)'
        }}
      >
        <div style={{ fontSize: '15px', fontWeight: 600, color: '#e2e2e6', marginBottom: '14px' }}>
          Recent activity
        </div>

        {activity === null ? (
          <div style={{ fontSize: '12.5px', color: '#8e9099' }}>Loading…</div>
        ) : activity.length === 0 ? (
          <div style={{ fontSize: '12.5px', color: '#8e9099' }}>Nothing yet.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {activity.map((a, i) => (
              <div key={i} style={{ fontSize: '12.5px', color: '#c4c6d0', lineHeight: 1.5 }}>
                <div>{(ACTIVITY_TEXT[a.action] || (() => `${a.actor} did something`))(a)}</div>
                <div style={{ fontSize: '10.5px', color: '#6b6d75' }}>{a.created_at}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Share link modal — create, copy, revoke ──────────────────────────────────
function ShareModal({ deckId, shareToken, onTokenChange, onClose }) {
  const [working, setWorking] = useState(false);
  const [copied, setCopied] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);

  const shareUrl = shareToken ? `${window.location.origin}/share/${shareToken}` : null;

  const createLink = async () => {
    setWorking(true);
    try {
      const res = await fetch(`/api/decks/${deckId}/share`, { method: 'POST' });
      const data = await res.json();
      if (data.share_token) onTokenChange(data.share_token);
    } catch (e) {
      console.error('Create share link failed', e);
    }
    setWorking(false);
  };

  const revokeLink = async () => {
    setWorking(true);
    try {
      await fetch(`/api/decks/${deckId}/share`, { method: 'DELETE' });
      onTokenChange(null);
      setConfirmRevoke(false);
    } catch (e) {
      console.error('Revoke share link failed', e);
    }
    setWorking(false);
  };

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard API can be unavailable — select-and-copy fallback is the
      // visible URL text itself, so just skip the confirmation flash.
    }
  };

  return (
    <div
      onClick={onClose}
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
          padding: '20px 22px',
          width: '440px', maxWidth: 'calc(100vw - 48px)',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)'
        }}
      >
        <div style={{ fontSize: '15px', fontWeight: 600, color: '#e2e2e6', marginBottom: '6px' }}>
          Share this deck
        </div>
        <div style={{ fontSize: '12.5px', color: '#9c988d', lineHeight: 1.5, marginBottom: '16px' }}>
          Anyone with the link can view this lookbook — scenes, frame order, and notes —
          without signing in. They can't edit anything or download originals.
        </div>

        {!shareToken ? (
          <button
            onClick={createLink}
            disabled={working}
            style={{
              background: '#d9a441', color: '#3d2f00', border: 'none',
              borderRadius: '8px', padding: '10px 18px',
              fontSize: '13.5px', fontWeight: 600, cursor: 'pointer',
              fontFamily: 'inherit', opacity: working ? 0.6 : 1
            }}
          >
            {working ? 'Creating…' : 'Create Share Link'}
          </button>
        ) : (
          <>
            <div style={{
              display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '14px'
            }}>
              <input
                readOnly
                value={shareUrl}
                onFocus={e => e.target.select()}
                style={{
                  flex: 1, background: '#111317', color: '#e2e2e6',
                  border: '1px solid #44474f', borderRadius: '8px',
                  padding: '9px 10px', fontSize: '12px', fontFamily: 'inherit',
                  outline: 'none'
                }}
              />
              <button
                onClick={copyLink}
                style={{
                  background: copied ? 'rgba(184,206,161,0.18)' : '#d9a441',
                  color: copied ? '#b8cea1' : '#3d2f00',
                  border: copied ? '1px solid rgba(184,206,161,0.6)' : 'none',
                  borderRadius: '8px', padding: '9px 14px',
                  fontSize: '12.5px', fontWeight: 600, cursor: 'pointer',
                  fontFamily: 'inherit', whiteSpace: 'nowrap'
                }}
              >
                {copied ? 'Copied ✓' : 'Copy'}
              </button>
            </div>

            {confirmRevoke ? (
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '12.5px', color: '#ffb4ab' }}>
                  The link will stop working for everyone.
                </span>
                <div style={{ flex: 1 }} />
                <button
                  onClick={() => setConfirmRevoke(false)}
                  disabled={working}
                  style={{
                    background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                    color: '#9c988d', borderRadius: '6px', padding: '6px 12px',
                    cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
                  }}
                >
                  Keep
                </button>
                <button
                  onClick={revokeLink}
                  disabled={working}
                  style={{
                    background: 'rgba(255,180,171,0.18)',
                    border: '1px solid rgba(255,180,171,0.6)',
                    color: '#ffb4ab', borderRadius: '6px', padding: '6px 12px',
                    cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
                  }}
                >
                  {working ? 'Revoking…' : 'Revoke Link'}
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmRevoke(true)}
                style={{
                  background: 'none', border: '1px solid rgba(255,180,171,0.35)',
                  color: '#ffb4ab', borderRadius: '6px', padding: '6px 12px',
                  cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
                }}
              >
                Revoke Link…
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function SceneSection({
  sceneKey, title, images, collapsedState, toggleCollapsed,
  isDragOver, onDragEnter, onDragLeave, onDrop,
  onRemoveImage, onStoryboard, editable, onRename, onDelete, canEdit
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

        {canEdit && images.length > 0 && (
          <button
            onClick={onStoryboard}
            title="Open storyboard — sequence frames and add notes"
            style={{
              background: 'rgba(217,164,65,0.12)',
              border: '1px solid rgba(217,164,65,0.45)',
              color: '#d9a441', borderRadius: '6px', padding: '5px 10px',
              cursor: 'pointer', fontSize: '11.5px', fontFamily: 'inherit',
              marginRight: editable ? '8px' : 0
            }}
          >
            ⊞ Storyboard
          </button>
        )}

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
            {canEdit ? 'Drag photos here' : 'No photos here yet'}
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
            gap: '8px'
          }}>
            {images.map(img => (
              <DeckTile
                key={img.deck_image_id}
                img={img}
                canEdit={canEdit}
                onRemove={canEdit ? () => onRemoveImage(img.deck_image_id) : undefined}
              />
            ))}
          </div>
        )
      )}
    </div>
  );
}

function DeckTile({ img, onRemove, canEdit }) {
  return (
    <div
      draggable={!!canEdit}
      onDragStart={canEdit ? e => e.dataTransfer.setData('text/plain', String(img.deck_image_id)) : undefined}
      style={{
        position: 'relative',
        aspectRatio: '1',
        borderRadius: '8px',
        overflow: 'hidden',
        background: '#111317',
        cursor: canEdit ? 'grab' : 'default'
      }}
    >
      {img.thumbnail && (
        <img
          src={img.thumbnail}
          alt={img.filename || ''}
          style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
        />
      )}
      {canEdit && (
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
      )}
    </div>
  );
}
