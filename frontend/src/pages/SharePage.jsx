import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';

// ── Public read-only lookbook view ────────────────────────────────────────────
// Rendered at /share/<token> with no login and no app chrome. Anyone with the
// link sees the deck name, scenes in order, frames in storyboard order, and
// notes. Thumbnails only — no edit controls, no full-res access.
export default function SharePage() {
  const { token } = useParams();
  const [deck, setDeck] = useState(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);

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

  if (loading) {
    return (
      <Centered>
        <p style={{ color: '#8e9099', fontSize: '14px' }}>Loading lookbook…</p>
      </Centered>
    );
  }

  if (error || !deck) {
    return (
      <Centered>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '20px', color: '#e2e2e6', fontWeight: 600, marginBottom: '8px' }}>
            This link isn't active
          </div>
          <p style={{ color: '#8e9099', fontSize: '14px', margin: 0 }}>
            The share link is invalid or has been revoked by the deck's owner.
          </p>
        </div>
      </Centered>
    );
  }

  const scenes = [...(deck.scenes || [])].sort((a, b) => a.sort_order - b.sort_order);
  const unsorted = deck.images.filter(img => img.scene_id === null);
  const bucketFor = (sceneId) => deck.images.filter(img => img.scene_id === sceneId);

  return (
    <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '48px 24px 80px' }}>
      {/* Deck title block */}
      <div style={{ marginBottom: '40px' }}>
        <div style={{
          fontSize: '11px', letterSpacing: '0.16em', textTransform: 'uppercase',
          color: '#d9a441', fontWeight: 600, marginBottom: '10px'
        }}>
          Frame Atlas · Shared Lookbook
        </div>
        <h1 style={{ fontSize: '36px', lineHeight: 1.15, fontWeight: 700, color: '#e2e2e6', margin: 0 }}>
          {deck.name}
        </h1>
      </div>

      {scenes.map(scene => {
        const frames = bucketFor(scene.id);
        if (frames.length === 0) return null;
        return <ShareSection key={scene.id} title={scene.name} frames={frames} />;
      })}

      {unsorted.length > 0 && (
        <ShareSection title={scenes.length > 0 ? 'More Frames' : null} frames={unsorted} />
      )}

      {deck.images.length === 0 && (
        <p style={{ color: '#8e9099', fontSize: '14px' }}>This lookbook is empty.</p>
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

function ShareSection({ title, frames }) {
  return (
    <div style={{ marginBottom: '48px' }}>
      {title && (
        <h2 style={{
          fontSize: '19px', fontWeight: 600, color: '#e2e2e6',
          margin: '0 0 16px', paddingBottom: '10px',
          borderBottom: '1px solid #2c2f35'
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
            background: '#1a1c20',
            border: '1px solid #2c2f35',
            borderRadius: '12px',
            overflow: 'hidden'
          }}>
            <div style={{ position: 'relative', background: '#111317' }}>
              {frame.thumbnail && (
                <img
                  src={frame.thumbnail}
                  alt={frame.filename || ''}
                  style={{ width: '100%', display: 'block', maxHeight: '360px', objectFit: 'contain' }}
                />
              )}
              <div style={{
                position: 'absolute', top: '8px', left: '8px',
                background: 'rgba(0,0,0,0.65)', color: '#e2e2e6',
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
                color: '#c9c5ba', borderTop: '1px solid #2c2f35',
                whiteSpace: 'pre-wrap'
              }}>
                {frame.storyboard_note}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
