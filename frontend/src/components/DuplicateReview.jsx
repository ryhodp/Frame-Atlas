import { useEffect, useState } from 'react';

export default function DuplicateReview({ onClose, onImageDeleted }) {
  const [groups, setGroups] = useState(null);   // null = scanning
  const [error, setError] = useState(null);
  const [confirmId, setConfirmId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);

  useEffect(() => {
    // Scan backfills fingerprints for any new images, then returns the groups
    fetch('/api/duplicates/scan', { method: 'POST' })
      .then(res => res.json())
      .then(data => setGroups(data.groups || []))
      .catch(() => setError('Scan failed — check your connection and try again.'));
  }, []);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const deleteImage = async (id) => {
    setDeletingId(id);
    setError(null);
    try {
      const res = await fetch(`/api/images/${id}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Delete failed');
        setDeletingId(null);
        setConfirmId(null);
        return;
      }
      // Drop the image from its group; groups with one image left disappear
      setGroups(prev => prev
        .map(g => ({ ...g, images: g.images.filter(img => img.id !== id) }))
        .filter(g => g.images.length > 1)
      );
      onImageDeleted?.(id);
    } catch {
      setError('Delete failed — check your connection and try again.');
    }
    setDeletingId(null);
    setConfirmId(null);
  };

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
          zIndex: 999, animation: 'fadeIn 0.2s ease'
        }}
      />
      <div style={{
        position: 'fixed', top: '5vh', left: '50%', transform: 'translateX(-50%)',
        width: 'min(860px, 92vw)', maxHeight: '90vh',
        background: '#0a0a0b',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: '14px',
        zIndex: 1000,
        display: 'flex', flexDirection: 'column',
        color: '#efeadd',
        fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
        boxShadow: '0 30px 80px rgba(0,0,0,0.7)',
        overflow: 'hidden'
      }}>
        {/* Header */}
        <div style={{
          padding: '16px 22px',
          borderBottom: '1px solid rgba(255,255,255,0.065)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center'
        }}>
          <div>
            <div style={{ fontSize: '15px', fontWeight: 600 }}>Duplicate Review</div>
            <div style={{ fontSize: '11px', color: '#65625a', marginTop: '2px' }}>
              {groups === null
                ? 'Scanning library for matching images…'
                : groups.length === 0
                  ? 'No duplicates found'
                  : `${groups.length} group${groups.length > 1 ? 's' : ''} of matching images — deleting moves the file to _Removed in Drive (recoverable)`}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: '#65625a',
              cursor: 'pointer', fontSize: '20px', lineHeight: 1
            }}
          >×</button>
        </div>

        {error && (
          <div style={{
            padding: '10px 22px', fontSize: '11.5px', color: '#cf7152',
            background: 'rgba(207,113,82,0.06)',
            borderBottom: '1px solid rgba(207,113,82,0.25)'
          }}>
            {error}
          </div>
        )}

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 22px' }}>
          {groups === null && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '10px',
              padding: '30px 0', justifyContent: 'center', color: '#9c988d', fontSize: '13px'
            }}>
              <span style={{
                width: '14px', height: '14px',
                border: '2px solid rgba(201,162,83,0.2)',
                borderTopColor: '#c9a253',
                borderRadius: '50%', display: 'inline-block',
                animation: 'spin 0.7s linear infinite'
              }} />
              Scanning…
            </div>
          )}

          {groups !== null && groups.length === 0 && (
            <div style={{
              padding: '40px 0', textAlign: 'center',
              color: '#9c988d', fontSize: '13.5px'
            }}>
              ✓ Your library is clean — no exact or near-duplicate images found.
            </div>
          )}

          {groups !== null && groups.map((group, gi) => (
            <div key={gi} style={{
              marginBottom: '20px',
              padding: '14px',
              background: 'rgba(255,255,255,0.02)',
              border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: '10px'
            }}>
              <div style={{
                fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em',
                color: group.kind === 'exact' ? '#cf7152' : '#dcbd76',
                marginBottom: '10px'
              }}>
                {group.kind === 'exact'
                  ? 'EXACT DUPLICATES — identical files'
                  : 'NEAR DUPLICATES — visually matching'}
              </div>
              <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
                {group.images.map(img => (
                  <div key={img.id} style={{ width: '180px' }}>
                    <div style={{
                      width: '100%', height: '130px',
                      background: '#141318', borderRadius: '7px',
                      overflow: 'hidden', marginBottom: '6px',
                      border: '1px solid rgba(255,255,255,0.06)'
                    }}>
                      <img
                        src={img.thumbnail}
                        alt={img.filename}
                        style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                      />
                    </div>
                    <div style={{
                      fontSize: '10.5px', color: '#9c988d',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                    }} title={img.filename}>
                      {img.filename}
                    </div>
                    <div style={{ fontSize: '9.5px', color: '#65625a', marginBottom: '6px' }}>
                      added {String(img.date_added || '').slice(0, 10)}
                    </div>
                    {confirmId === img.id ? (
                      <div style={{ display: 'flex', gap: '5px' }}>
                        <button
                          onClick={() => deleteImage(img.id)}
                          disabled={deletingId === img.id}
                          style={{
                            flex: 1, background: 'rgba(207,113,82,0.85)',
                            border: '1px solid rgba(207,113,82,1)',
                            color: '#efeadd', borderRadius: '5px',
                            padding: '5px 0', fontSize: '10.5px',
                            cursor: 'pointer', fontFamily: 'inherit',
                            opacity: deletingId === img.id ? 0.6 : 1
                          }}
                        >
                          {deletingId === img.id ? 'Deleting…' : 'Yes, delete'}
                        </button>
                        <button
                          onClick={() => setConfirmId(null)}
                          style={{
                            background: 'none',
                            border: '1px solid rgba(255,255,255,0.15)',
                            color: '#9c988d', borderRadius: '5px',
                            padding: '5px 8px', fontSize: '10.5px',
                            cursor: 'pointer', fontFamily: 'inherit'
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmId(img.id)}
                        style={{
                          width: '100%', background: 'none',
                          border: '1px solid rgba(207,113,82,0.3)',
                          color: '#cf7152', borderRadius: '5px',
                          padding: '5px 0', fontSize: '10.5px',
                          cursor: 'pointer', fontFamily: 'inherit'
                        }}
                      >
                        Delete this one
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <style>{`
        @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </>
  );
}
