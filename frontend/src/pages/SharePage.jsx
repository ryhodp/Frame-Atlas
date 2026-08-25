import { useEffect, useState, useRef, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { getViewerToken, getViewerName, setViewerName as saveViewerName } from '../viewerIdentity';
import { dangerWarm, onPrimary, onSurface, onSurfaceMuted, onSurfaceWarmDim, outline, outlineDim, outlineMuted, outlineVariant, primary, surfaceContainerHigh, surfaceContainerLow, surfaceContainerLowestAlt } from '../theme';

// ── Public read-only lookbook view ────────────────────────────────────────────
// Rendered at /share/<token> with no login and no app chrome. Anyone with the
// link sees the deck name, scenes in order, frames in storyboard order, and
// notes. Thumbnails only — no edit controls, no full-res access.
//
// V42 (Day 24): when the deck owner has feedback turned on, viewers can also
// pick a frame ("this one") and leave a comment on it, with no login — they
// type a display name once (kept in this browser's localStorage) and every
// pick/comment they leave is attributed to it. Everyone holding the link sees
// the same picks and comments (Ryan's call: collaborative, one conversation
// for the whole agency side).
export default function SharePage() {
  const { token } = useParams();
  const [deck, setDeck] = useState(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState(null);   // null until fetched (or feedback is off)
  const [viewerName, setViewerNameState] = useState(getViewerName());
  const [namePromptOpen, setNamePromptOpen] = useState(false);
  const pendingActionRef = useRef(null);
  const viewerToken = useRef(getViewerToken()).current;

  useEffect(() => {
    fetch(`/api/share/${token}`)
      .then(res => {
        if (!res.ok) throw new Error('not found');
        return res.json();
      })
      .then(setDeck)
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    if (!deck || !deck.feedback_enabled) return;
    fetch(`/api/share/${token}/feedback`, { headers: { 'X-FA-Viewer': viewerToken } })
      .then(res => res.json())
      .then(setFeedback)
      .catch(() => {});
  }, [deck, token, viewerToken]);

  // Runs `action(name)` immediately if a name is already known; otherwise
  // opens the one-time name prompt and holds the action until it's answered.
  // This is the ONLY place a name gets asked for — the first pick or the
  // first comment send, whichever a viewer does first.
  const ensureName = useCallback((action) => {
    if (viewerName) {
      action(viewerName);
      return;
    }
    pendingActionRef.current = action;
    setNamePromptOpen(true);
  }, [viewerName]);

  const handleNameSubmit = (name) => {
    saveViewerName(name);
    setViewerNameState(name);
    setNamePromptOpen(false);
    const action = pendingActionRef.current;
    pendingActionRef.current = null;
    if (action) action(name);
  };

  const patchFrame = (deckImageId, updater) => {
    setFeedback(prev => {
      if (!prev) return prev;
      const key = String(deckImageId);
      const current = prev.frames[key] || { pick_count: 0, pickers: [], picked_by_me: false, comments: [] };
      return { ...prev, frames: { ...prev.frames, [key]: updater(current) } };
    });
  };

  const doPick = async (deckImageId, name) => {
    // Optimistic — most picks succeed, and waiting for the round trip on a
    // one-tap "this one" gesture would make the signal feel laggy.
    patchFrame(deckImageId, f => ({
      ...f, pick_count: f.pick_count + 1, picked_by_me: true, pickers: [...f.pickers, name]
    }));
    try {
      const res = await fetch(`/api/share/${token}/picks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deck_image_id: deckImageId, viewer_token: viewerToken, viewer_name: name })
      });
      if (!res.ok) throw new Error('pick failed');
      const data = await res.json();
      patchFrame(deckImageId, f => ({ ...f, pick_count: data.pick_count, picked_by_me: true }));
    } catch {
      patchFrame(deckImageId, f => ({
        ...f, pick_count: Math.max(0, f.pick_count - 1), picked_by_me: false,
        pickers: f.pickers.filter((_, i) => i !== f.pickers.length - 1)
      }));
    }
  };

  const doUnpick = async (deckImageId) => {
    patchFrame(deckImageId, f => ({ ...f, pick_count: Math.max(0, f.pick_count - 1), picked_by_me: false }));
    try {
      const res = await fetch(`/api/share/${token}/picks`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deck_image_id: deckImageId, viewer_token: viewerToken })
      });
      if (!res.ok) throw new Error('unpick failed');
      const data = await res.json();
      patchFrame(deckImageId, f => ({ ...f, pick_count: data.pick_count, picked_by_me: false }));
    } catch {
      patchFrame(deckImageId, f => ({ ...f, pick_count: f.pick_count + 1, picked_by_me: true }));
    }
  };

  const doComment = async (deckImageId, body, name) => {
    try {
      const res = await fetch(`/api/share/${token}/comments`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deck_image_id: deckImageId, viewer_token: viewerToken, viewer_name: name, body })
      });
      if (!res.ok) return false;
      const comment = await res.json();
      patchFrame(deckImageId, f => ({ ...f, comments: [...f.comments, comment] }));
      return true;
    } catch {
      return false;
    }
  };

  if (loading) {
    return (
      <Centered>
        <p style={{ color: outline, fontSize: '14px' }}>Loading lookbook…</p>
      </Centered>
    );
  }

  if (error || !deck) {
    return (
      <Centered>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '20px', color: onSurface, fontWeight: 600, marginBottom: '8px' }}>
            This link isn't active
          </div>
          <p style={{ color: outline, fontSize: '14px', margin: 0 }}>
            The share link is invalid or has been revoked by the deck's owner.
          </p>
        </div>
      </Centered>
    );
  }

  const scenes = [...(deck.scenes || [])].sort((a, b) => a.sort_order - b.sort_order);
  const unsorted = deck.images.filter(img => img.scene_id === null);
  const bucketFor = (sceneId) => deck.images.filter(img => img.scene_id === sceneId);

  const feedbackProps = deck.feedback_enabled ? { feedback, ensureName, doPick, doUnpick, doComment } : null;

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '48px 24px 80px' }}>
      {/* Deck title block */}
      <div style={{ marginBottom: '40px' }}>
        <div style={{
          fontSize: '11px', letterSpacing: '0.16em', textTransform: 'uppercase',
          color: primary, fontWeight: 600, marginBottom: '10px'
        }}>
          Frame Atlas · Shared Lookbook
        </div>
        <h1 style={{ fontSize: '36px', lineHeight: 1.15, fontWeight: 700, color: onSurface, margin: 0 }}>
          {deck.name}
        </h1>
        {deck.feedback_enabled && (
          <div style={{ fontSize: '12.5px', color: outline, marginTop: '12px' }}>
            Pick your favorite frames and leave notes — {viewerName
              ? <>you're leaving feedback as <strong style={{ color: onSurfaceWarmDim }}>{viewerName}</strong></>
              : "everyone with this link can see what's picked and said"}.
          </div>
        )}
      </div>

      {scenes.map(scene => {
        const frames = bucketFor(scene.id);
        if (frames.length === 0) return null;
        return <ShareSection key={scene.id} title={scene.name} frames={frames} feedbackProps={feedbackProps} />;
      })}

      {unsorted.length > 0 && (
        <ShareSection title={scenes.length > 0 ? 'More Frames' : null} frames={unsorted} feedbackProps={feedbackProps} />
      )}

      {deck.images.length === 0 && (
        <p style={{ color: outline, fontSize: '14px' }}>This lookbook is empty.</p>
      )}

      {namePromptOpen && (
        <NamePromptModal onSubmit={handleNameSubmit} onClose={() => { pendingActionRef.current = null; setNamePromptOpen(false); }} />
      )}
    </div>
  );
}

function Centered({ children }) {
  return (
    <div style={{
      minHeight: '60vh', display: 'flex',
      alignItems: 'center', justifyContent: 'center', padding: '24px'
    }}>
      {children}
    </div>
  );
}

function ShareSection({ title, frames, feedbackProps }) {
  return (
    <div style={{ marginBottom: '48px' }}>
      {title && (
        <h2 style={{
          fontSize: '19px', fontWeight: 600, color: onSurface,
          margin: '0 0 16px', paddingBottom: '10px',
          borderBottom: `1px solid ${outlineDim}`
        }}>
          {title}
        </h2>
      )}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
        gap: '20px'
      }}>
        {frames.map((frame, i) => (
          <div key={frame.deck_image_id} style={{
            background: surfaceContainerLow,
            border: `1px solid ${outlineDim}`,
            borderRadius: '12px',
            overflow: 'hidden'
          }}>
            <div style={{ position: 'relative', background: surfaceContainerLowestAlt }}>
              {frame.thumbnail && (
                <img
                  src={frame.thumbnail}
                  alt={frame.filename || ''}
                  style={{ width: '100%', display: 'block', maxHeight: '360px', objectFit: 'contain' }}
                />
              )}
              <div style={{
                position: 'absolute', top: '8px', left: '8px',
                background: 'rgba(0,0,0,0.65)', color: onSurface,
                borderRadius: '6px', minWidth: '22px', height: '22px',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '12px', fontWeight: 600, padding: '0 6px'
              }}>
                {i + 1}
              </div>
            </div>
            {frame.storyboard_note && (
              <div style={{
                padding: '10px 12px', fontSize: '13px', lineHeight: 1.5,
                color: onSurfaceWarmDim, borderTop: `1px solid ${outlineDim}`,
                whiteSpace: 'pre-wrap'
              }}>
                {frame.storyboard_note}
              </div>
            )}
            {feedbackProps && (
              <FeedbackStrip deckImageId={frame.deck_image_id} {...feedbackProps} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

const EMPTY_FRAME_FEEDBACK = { pick_count: 0, pickers: [], picked_by_me: false, comments: [] };

function FeedbackStrip({ deckImageId, feedback, ensureName, doPick, doUnpick, doComment }) {
  const [expanded, setExpanded] = useState(false);
  const [text, setText] = useState('');
  const [posting, setPosting] = useState(false);
  const [commentError, setCommentError] = useState(false);

  const data = feedback?.frames?.[String(deckImageId)] || EMPTY_FRAME_FEEDBACK;
  const { pick_count, picked_by_me, comments } = data;

  const handlePickClick = () => {
    if (picked_by_me) { doUnpick(deckImageId); return; }
    ensureName((name) => doPick(deckImageId, name));
  };

  const handleSend = () => {
    const body = text.trim();
    if (!body || posting) return;
    ensureName(async (name) => {
      setPosting(true);
      setCommentError(false);
      const ok = await doComment(deckImageId, body, name);
      setPosting(false);
      if (ok) { setText(''); setExpanded(true); }
      else setCommentError(true);
    });
  };

  return (
    <div style={{ padding: '10px 12px', borderTop: `1px solid ${outlineDim}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <button
          onClick={handlePickClick}
          style={{
            display: 'flex', alignItems: 'center', gap: '5px',
            background: picked_by_me ? 'rgba(217,164,65,0.16)' : 'none',
            border: `1px solid ${picked_by_me ? 'rgba(217,164,65,0.6)' : outlineMuted}`,
            color: picked_by_me ? primary : onSurfaceWarmDim,
            borderRadius: '999px', padding: '5px 12px',
            fontSize: '12.5px', fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit'
          }}
        >
          {picked_by_me ? '★ Picked' : '☆ Pick this one'}
        </button>
        {pick_count > 0 && (
          <span style={{ fontSize: '12px', color: outline }}>
            {pick_count} pick{pick_count === 1 ? '' : 's'}
          </span>
        )}
        <button
          onClick={() => setExpanded(e => !e)}
          style={{
            marginLeft: 'auto', background: 'none', border: 'none',
            color: outline, fontSize: '12.5px', cursor: 'pointer', fontFamily: 'inherit',
            padding: '5px 4px'
          }}
        >
          💬 {comments.length > 0 ? `${comments.length} comment${comments.length === 1 ? '' : 's'}` : 'Comment'}
        </button>
      </div>

      {expanded && (
        <div style={{ marginTop: '10px' }}>
          {comments.map(c => (
            <div key={c.id} style={{ marginBottom: '8px' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: primary, marginBottom: '1px' }}>
                {c.viewer_name}
              </div>
              <div style={{ fontSize: '13px', color: onSurfaceWarmDim, lineHeight: 1.45, whiteSpace: 'pre-wrap' }}>
                {c.body}
              </div>
            </div>
          ))}
          <div style={{ display: 'flex', gap: '8px', marginTop: comments.length ? '10px' : 0 }}>
            <textarea
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSend(); }}
              placeholder="Leave a note on this frame…"
              rows={2}
              style={{
                flex: 1, resize: 'none', background: surfaceContainerLowestAlt, color: onSurface,
                border: `1px solid ${outlineMuted}`, borderRadius: '8px', padding: '8px 10px',
                fontSize: '13px', fontFamily: 'inherit', lineHeight: 1.4
              }}
            />
            <button
              onClick={handleSend}
              disabled={posting || !text.trim()}
              style={{
                background: primary, color: onPrimary, border: 'none',
                borderRadius: '8px', padding: '0 16px',
                fontSize: '12.5px', fontWeight: 600, fontFamily: 'inherit',
                cursor: posting || !text.trim() ? 'default' : 'pointer',
                opacity: posting || !text.trim() ? 0.5 : 1
              }}
            >
              Send
            </button>
          </div>
          {commentError && (
            <div style={{ fontSize: '11.5px', color: dangerWarm, marginTop: '6px' }}>
              That didn't go through — try again.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NamePromptModal({ onSubmit, onClose }) {
  const [name, setName] = useState('');
  const inputRef = useRef(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const submit = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    onSubmit(trimmed.slice(0, 60));
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
        zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px'
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: surfaceContainerHigh, border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '12px', padding: '22px', width: '380px', maxWidth: '100%',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)'
        }}
      >
        <div style={{ fontSize: '15px', fontWeight: 600, color: onSurface, marginBottom: '6px' }}>
          What's your name?
        </div>
        <div style={{ fontSize: '12.5px', color: onSurfaceMuted, lineHeight: 1.5, marginBottom: '14px' }}>
          So the deck's owner (and everyone else with this link) knows who left it. You'll only
          be asked once on this device.
        </div>
        <input
          ref={inputRef}
          value={name}
          onChange={e => setName(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') submit(); }}
          maxLength={60}
          placeholder="Your name"
          style={{
            width: '100%', boxSizing: 'border-box', background: surfaceContainerLowestAlt, color: onSurface,
            border: `1px solid ${outlineMuted}`, borderRadius: '8px', padding: '10px 12px',
            fontSize: '14px', fontFamily: 'inherit', marginBottom: '16px'
          }}
        />
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: `1px solid ${outlineVariant}`, color: onSurfaceMuted,
              borderRadius: '8px', padding: '9px 16px', fontSize: '13px',
              cursor: 'pointer', fontFamily: 'inherit'
            }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!name.trim()}
            style={{
              background: primary, color: onPrimary, border: 'none',
              borderRadius: '8px', padding: '9px 18px', fontSize: '13px', fontWeight: 600,
              cursor: name.trim() ? 'pointer' : 'default', opacity: name.trim() ? 1 : 0.5,
              fontFamily: 'inherit'
            }}
          >
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
