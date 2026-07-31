import { useEffect, useState } from 'react';

export default function DuplicateReview({ onClose, onImageDeleted }) {
  const [groups, setGroups] = useState(null);   // null = scanning
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(new Set());
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [resultMsg, setResultMsg] = useState(null);

  useEffect(() => {
    // Scan backfills fingerprints for any new images, then returns the groups
    fetch('/api/duplicates/scan', { method: 'POST' })
      .then(res => res.json())
      .then(data => {
        const found = data.groups || [];
        setGroups(found);
        // Pre-check every image except the first (oldest) in each group, so a
        // library with 100+ duplicates arrives ready to clear in one click
        // instead of a hundred. Ryan can still uncheck/check anything by hand.
        const preChecked = new Set();
        found.forEach(g => {
          g.images.slice(1).forEach(img => preChecked.add(img.id));
        });
        setSelected(preChecked);
      })
      .catch(() => setError('Scan failed — check your connection and try again.'));
  }, []);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const toggleSelected = (id) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const deleteSelected = async () => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBulkBusy(true);
    setError(null);
    setResultMsg(null);
    try {
      const res = await fetch('/api/images/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_ids: ids })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Delete failed');
        setBulkBusy(false);
        setConfirmBulk(false);
        return;
      }
      const deletedIds = new Set(data.deleted || []);
      const failed = data.errors || [];
      // Drop deleted images from their groups; groups with one image left disappear
      setGroups(prev => prev
        .map(g => ({ ...g, images: g.images.filter(img => !deletedIds.has(img.id)) }))
        .filter(g => g.images.length > 1)
      );
      setSelected(prev => {
        const next = new Set(prev);
        deletedIds.forEach(id => next.delete(id));
        return next;
      });
      deletedIds.forEach(id => onImageDeleted?.(id));
      setResultMsg(
        failed.length > 0
          ? `Deleted ${deletedIds.size}, ${failed.length} failed — ${failed[0].error}`
          : `Deleted ${deletedIds.size} photo${deletedIds.size === 1 ? '' : 's'}`
      );
    } catch {
      setError('Delete failed — check your connection and try again.');
    }
    setBulkBusy(false);
    setConfirmBulk(false);
  };

  const selectedCount = selected.size;

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
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '14px'
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
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
            {groups !== null && groups.length > 0 && !confirmBulk && (
              <button
                onClick={() => setConfirmBulk(true)}
                disabled={selectedCount === 0 || bulkBusy}
                style={{
                  background: selectedCount === 0 ? 'rgba(207,113,82,0.15)' : 'rgba(207,113,82,0.85)',
                  border: '1px solid rgba(207,113,82,1)',
                  color: '#efeadd', borderRadius: '6px',
                  padding: '7px 14px', fontSize: '11.5px', fontWeight: 600,
                  cursor: selectedCount === 0 ? 'not-allowed' : 'pointer',
                  fontFamily: 'inherit',
                  opacity: selectedCount === 0 ? 0.5 : 1
                }}
              >
                Delete Selected ({selectedCount})
              </button>
            )}
            {confirmBulk && (
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '11.5px', color: '#cf7152' }}>
                  Delete {selectedCount} photo{selectedCount === 1 ? '' : 's'}?
                </span>
                <button
                  onClick={deleteSelected}
                  disabled={bulkBusy}
                  style={{
                    background: 'rgba(207,113,82,0.85)',
                    border: '1px solid rgba(207,113,82,1)',
                    color: '#efeadd', borderRadius: '6px',
                    padding: '7px 14px', fontSize: '11.5px', fontWeight: 600,
                    cursor: 'pointer', fontFamily: 'inherit',
                    opacity: bulkBusy ? 0.6 : 1
                  }}
                >
                  {bulkBusy ? 'Deleting…' : 'Yes, delete'}
                </button>
                <button
                  onClick={() => setConfirmBulk(false)}
                  disabled={bulkBusy}
                  style={{
                    background: 'none',
                    border: '1px solid rgba(255,255,255,0.15)',
                    color: '#9c988d', borderRadius: '6px',
                    padding: '7px 10px', fontSize: '11.5px',
                    cursor: 'pointer', fontFamily: 'inherit'
                  }}
                >
                  Cancel
                </button>
              </div>
            )}
            <button
              onClick={onClose}
              style={{
                background: 'none', border: 'none', color: '#65625a',
                cursor: 'pointer', fontSize: '20px', lineHeight: 1
              }}
            >×</button>
          </div>
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

        {resultMsg && (
          <div style={{
            padding: '10px 22px', fontSize: '11.5px', color: '#9c988d',
            background: 'rgba(217,164,65,0.06)',
            borderBottom: '1px solid rgba(217,164,65,0.2)'
          }}>
            {resultMsg}
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
                      position: 'relative',
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
                      <label
                        onClick={(e) => e.stopPropagation()}
                        style={{
                          position: 'absolute', top: '6px', left: '6px',
                          display: 'flex', alignItems: 'center',
                          background: 'rgba(0,0,0,0.55)', borderRadius: '5px',
                          padding: '4px 5px', cursor: bulkBusy ? 'default' : 'pointer'
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(img.id)}
                          onChange={() => toggleSelected(img.id)}
                          disabled={bulkBusy}
                          style={{ accentColor: '#d9a441', width: '16px', height: '16px', cursor: bulkBusy ? 'default' : 'pointer' }}
                        />
                      </label>
                    </div>
                    <div style={{
                      fontSize: '10.5px', color: '#9c988d',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                    }} title={img.filename}>
                      {img.filename}
                    </div>
                    <div style={{ fontSize: '9.5px', color: '#65625a' }}>
                      added {String(img.date_added || '').slice(0, 10)}
                    </div>
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
