import { useEffect, useState } from 'react';

// ── Owner-side Feedback panel (Day 24 / V42) ──────────────────────────────────
// Reads GET /api/decks/<id>/feedback — the SAME shape and the SAME function
// (_deck_feedback_payload on the backend) the public share page's own
// feedback view uses, so this can never show Ryan a different picture than
// what viewers themselves see.
//
// Frames are pre-ranked most-picked-first by the server; this component just
// renders that order and cross-references each deck_image_id against the
// `images` array the deck page already has in memory for the thumbnail and
// filename — the feedback endpoint itself stays small by not duplicating that.
export default function FeedbackPanel({ deckId, images, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch(`/api/decks/${deckId}/feedback`)
      .then(res => {
        if (!res.ok) throw new Error('failed');
        return res.json();
      })
      .then(setData)
      .catch(() => setError(true));
  }, [deckId]);

  const imageByDeckImageId = {};
  for (const img of images || []) imageByDeckImageId[img.deck_image_id] = img;

  const deleteComment = async (commentId, deckImageId) => {
    // Optimistic — this is a one-way "remove it" action with no undo, so
    // there's nothing meaningful to roll back to on failure beyond re-adding
    // it, which would be a stranger surprise than just re-fetching.
    setData(prev => {
      if (!prev) return prev;
      const key = String(deckImageId);
      const frame = prev.frames[key];
      if (!frame) return prev;
      return {
        ...prev,
        total_comments: prev.total_comments - 1,
        frames: { ...prev.frames, [key]: { ...frame, comments: frame.comments.filter(c => c.id !== commentId) } }
      };
    });
    try {
      await fetch(`/api/decks/${deckId}/comments/${commentId}`, { method: 'DELETE' });
    } catch (e) {
      console.error('Delete comment failed', e);
    }
  };

  // Deleting the only comment on a frame with no picks can leave it with
  // nothing to show — filtered out here rather than left as a stranded
  // empty row, same no-stranded-header rule the PDF exporter (V40) follows.
  const visibleIds = data
    ? data.ranked_deck_image_ids.filter(id => {
        const f = data.frames[String(id)];
        return f && (f.pick_count > 0 || f.comments.length > 0);
      })
    : [];

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px'
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: '#2a2c31',
          border: '1px solid rgba(255,255,255,0.12)',
          borderRadius: '12px',
          padding: '20px 22px',
          width: '560px', maxWidth: 'calc(100vw - 48px)',
          maxHeight: 'calc(100vh - 80px)', display: 'flex', flexDirection: 'column',
          boxShadow: '0 20px 48px rgba(0,0,0,0.6)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '4px' }}>
          <div style={{ fontSize: '15px', fontWeight: 600, color: '#e2e2e6' }}>Feedback</div>
          {data && (
            <div style={{ fontSize: '12px', color: '#8e9099' }}>
              {data.total_picks} pick{data.total_picks === 1 ? '' : 's'} · {data.total_comments} comment{data.total_comments === 1 ? '' : 's'}
            </div>
          )}
        </div>
        <div style={{ fontSize: '12.5px', color: '#9c988d', lineHeight: 1.5, marginBottom: '16px' }}>
          Most-picked frame first. Everyone with the share link sees the same picks and comments.
        </div>

        <div style={{ overflowY: 'auto', flex: 1, marginRight: '-8px', paddingRight: '8px' }}>
          {error && (
            <div style={{ fontSize: '13px', color: '#ffb4ab' }}>Couldn't load feedback — try again.</div>
          )}
          {!error && !data && (
            <div style={{ fontSize: '13px', color: '#8e9099' }}>Loading…</div>
          )}
          {data && visibleIds.length === 0 && (
            <div style={{ fontSize: '13px', color: '#8e9099', lineHeight: 1.6 }}>
              No picks or comments yet. Once someone opens the share link and leaves feedback,
              it'll show up here.
            </div>
          )}
          {data && visibleIds.map(deckImageId => (
            <FrameFeedback
              key={deckImageId}
              image={imageByDeckImageId[deckImageId]}
              data={data.frames[String(deckImageId)]}
              onDeleteComment={(commentId) => deleteComment(commentId, deckImageId)}
            />
          ))}
        </div>

        <div style={{ paddingTop: '14px', display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: '1px solid #44474f', color: '#9c988d',
              borderRadius: '8px', padding: '9px 18px', fontSize: '13px',
              cursor: 'pointer', fontFamily: 'inherit'
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function FrameFeedback({ image, data, onDeleteComment }) {
  const { pick_count, pickers, comments } = data;
  return (
    <div style={{
      display: 'flex', gap: '12px', padding: '12px 0',
      borderBottom: '1px solid #33353b'
    }}>
      <div style={{
        width: '76px', height: '76px', flexShrink: 0, borderRadius: '8px',
        overflow: 'hidden', background: '#111317', border: '1px solid #33353b'
      }}>
        {image?.thumbnail && (
          <img src={image.thumbnail} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        )}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        {pick_count > 0 && (
          <div style={{ fontSize: '12.5px', color: '#d9a441', fontWeight: 600, marginBottom: '4px' }}>
            ★ {pick_count} pick{pick_count === 1 ? '' : 's'}
            <span style={{ color: '#9c988d', fontWeight: 400 }}> — {pickers.join(', ')}</span>
          </div>
        )}
        {comments.map(c => (
          <div key={c.id} style={{ display: 'flex', gap: '6px', marginBottom: '5px', alignItems: 'flex-start' }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <span style={{ fontSize: '12px', fontWeight: 600, color: '#e2e2e6' }}>{c.viewer_name}</span>
              <span style={{ fontSize: '13px', color: '#c9c5ba', marginLeft: '6px' }}>{c.body}</span>
            </div>
            <button
              onClick={() => onDeleteComment(c.id)}
              title="Delete this comment"
              style={{
                flexShrink: 0, background: 'none', border: 'none', color: '#6b6d75',
                cursor: 'pointer', fontSize: '13px', padding: '0 2px', lineHeight: 1
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
