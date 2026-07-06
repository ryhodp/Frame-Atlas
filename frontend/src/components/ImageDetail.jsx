import { useEffect, useState } from 'react';

const CAT_LABELS = {
  'mood': 'Mood', 'lighting_quality': 'Lighting',
  'lighting_color_temperature': 'Color Temp', 'color_palette': 'Palette',
  'shot_type': 'Shot', 'framing_composition': 'Framing',
  'location_type': 'Location', 'time_of_day_weather': 'Time / Weather',
  'source_type': 'Source', 'subject_count': 'Subjects',
  'subject_camera_relationship': 'Camera Rel.', 'genre_aesthetic': 'Genre',
  'era_decade': 'Era', 'camera_format': 'Format',
  'performance_emotion': 'Emotion',
};

const CAT_ORDER = [
  'mood', 'lighting_quality', 'lighting_color_temperature', 'color_palette',
  'shot_type', 'framing_composition', 'location_type', 'time_of_day_weather',
  'source_type', 'subject_count', 'subject_camera_relationship', 'performance_emotion',
  'genre_aesthetic', 'era_decade', 'camera_format'
];

export default function ImageDetail({ image, onClose, onUpdated, onDeleted }) {
  const [fullImage, setFullImage] = useState(null);
  const [fullError, setFullError] = useState(false);

  const [tags, setTags] = useState(image?.tags || []);
  const [isFavorite, setIsFavorite] = useState(!!image?.is_favorite);
  const [isFlagged, setIsFlagged] = useState(!!image?.is_flagged);

  const [editingTags, setEditingTags] = useState(false);
  const [newTagCat, setNewTagCat] = useState('mood');
  const [newTagValue, setNewTagValue] = useState('');

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  useEffect(() => {
    if (!image) return;
    let objectUrl = null;
    setFullImage(null);
    setFullError(false);
    setTags(image.tags || []);
    setIsFavorite(!!image.is_favorite);
    setIsFlagged(!!image.is_flagged);
    setEditingTags(false);
    setConfirmDelete(false);
    setDeleteError(null);

    fetch(`/api/images/${image.id}/full`)
      .then(res => {
        if (!res.ok) throw new Error('full-res failed');
        return res.blob();
      })
      .then(blob => {
        objectUrl = URL.createObjectURL(blob);
        setFullImage(objectUrl);
      })
      .catch(() => setFullError(true));

    return () => { if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [image?.id]);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  if (!image) return null;

  const categories = {};
  tags.forEach(tag => {
    if (!categories[tag.category]) categories[tag.category] = [];
    categories[tag.category].push(tag.value);
  });

  const hasTags = tags.length > 0;

  // ── Actions ─────────────────────────────────────────────────────────────────
  const toggleFavorite = async () => {
    const next = !isFavorite;
    setIsFavorite(next); // optimistic — flip back on failure
    try {
      const res = await fetch(`/api/images/${image.id}/favorite`, { method: 'POST' });
      const data = await res.json();
      setIsFavorite(!!data.is_favorite);
      onUpdated?.(image.id, { is_favorite: data.is_favorite });
    } catch {
      setIsFavorite(!next);
    }
  };

  const toggleFlag = async () => {
    const next = !isFlagged;
    setIsFlagged(next);
    try {
      const res = await fetch(`/api/images/${image.id}/flag`, { method: 'POST' });
      const data = await res.json();
      setIsFlagged(!!data.is_flagged);
      onUpdated?.(image.id, { is_flagged: data.is_flagged });
    } catch {
      setIsFlagged(!next);
    }
  };

  const removeTag = async (category, value) => {
    setTags(prev => prev.filter(t => !(t.category === category && t.value === value)));
    try {
      const res = await fetch(`/api/images/${image.id}/tags`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category, value })
      });
      const data = await res.json();
      if (data.tags) {
        setTags(data.tags);
        onUpdated?.(image.id, { tags: data.tags });
      }
    } catch {}
  };

  const addTag = async () => {
    const value = newTagValue.trim().toLowerCase();
    if (!value) return;
    setNewTagValue('');
    try {
      const res = await fetch(`/api/images/${image.id}/tags`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: newTagCat, value })
      });
      const data = await res.json();
      if (data.tags) {
        setTags(data.tags);
        onUpdated?.(image.id, { tags: data.tags });
      }
    } catch {}
  };

  const doDelete = async () => {
    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await fetch(`/api/images/${image.id}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) {
        setDeleteError(data.error || 'Delete failed');
        setDeleting(false);
        return;
      }
      onDeleted?.(image.id);
      onClose();
    } catch (e) {
      setDeleteError('Delete failed — check your connection and try again.');
      setDeleting(false);
    }
  };

  const footBtn = (color) => ({
    background: 'none',
    border: `1px solid ${color}4d`,
    color, borderRadius: '6px', padding: '7px 14px',
    cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
  });

  return (
    <>
      {/* Overlay backdrop */}
      <div
        onClick={onClose}
        style={{
          position: 'fixed', inset: 0,
          background: 'rgba(0,0,0,0.5)',
          zIndex: 999,
          animation: 'fadeIn 0.2s ease'
        }}
      />

      {/* Side panel */}
      <div
        style={{
          position: 'fixed', right: 0, top: 0, bottom: 0,
          width: 'clamp(360px, 45%, 600px)',
          background: '#0a0a0b',
          borderLeft: '1px solid rgba(255,255,255,0.065)',
          zIndex: 1000,
          display: 'flex', flexDirection: 'column',
          color: '#efeadd',
          fontFamily: "'Hanken Grotesk', system-ui, sans-serif",
          animation: 'slideInRight 0.3s cubic-bezier(0.16, 1, 0.3, 1)'
        }}
      >
        {/* Header — filename lives in the metadata card below */}
        <div style={{
          padding: '12px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.065)',
          display: 'flex', justifyContent: 'flex-end', alignItems: 'center'
        }}>
          <button
            onClick={onClose}
            style={{
              background: 'none', border: 'none', color: '#65625a',
              cursor: 'pointer', fontSize: '20px', lineHeight: 1, flexShrink: 0
            }}
          >×</button>
        </div>

        {/* Scrollable content */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {/* Full-res image (falls back to thumbnail while loading) */}
          <div style={{
            background: '#141318',
            minHeight: '200px', maxHeight: '420px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            overflow: 'hidden'
          }}>
            <img
              src={fullImage || image.thumbnail}
              alt={image.filename}
              style={{
                maxWidth: '100%', maxHeight: '420px',
                objectFit: 'contain',
                filter: fullImage ? 'none' : 'blur(0.5px)',
                transition: 'filter 0.3s ease'
              }}
            />
          </div>
          {!fullImage && !fullError && (
            <div style={{
              padding: '6px 20px', fontSize: '10px', color: '#65625a',
              fontFamily: "'JetBrains Mono', monospace"
            }}>
              loading full resolution…
            </div>
          )}
          {fullError && (
            <div style={{ padding: '6px 20px', fontSize: '10px', color: '#cf7152' }}>
              Couldn't load full-res — showing thumbnail
            </div>
          )}

          {/* Metadata */}
          <div style={{ padding: '20px' }}>
            {/* Caption */}
            {image.caption && (
              <p style={{
                fontSize: '13px', lineHeight: '1.5',
                color: '#dcbd76', margin: '0 0 20px'
              }}>
                {image.caption}
              </p>
            )}

            {/* Aspect Ratio & Date */}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr',
              gap: '12px', marginBottom: '20px',
              padding: '12px', background: 'rgba(255,255,255,0.02)',
              borderRadius: '8px'
            }}>
              <div>
                <div style={{ fontSize: '9px', fontWeight: 600, color: '#65625a', letterSpacing: '0.08em' }}>ASPECT RATIO</div>
                <div style={{ fontSize: '13px', color: '#efeadd', marginTop: '4px' }}>
                  {image.ar_label || image.aspect_ratio}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '9px', fontWeight: 600, color: '#65625a', letterSpacing: '0.08em' }}>FILENAME</div>
                <div style={{
                  fontSize: '13px', color: '#efeadd', marginTop: '4px',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                }}>{image.filename}</div>
              </div>
            </div>

            {/* Color palette — above tags */}
            {image.palette && image.palette.length > 0 && (
              <div style={{ marginBottom: '20px' }}>
                <div style={{
                  fontSize: '9px', fontWeight: 600, color: '#65625a',
                  letterSpacing: '0.08em', marginBottom: '7px'
                }}>
                  COLOR PALETTE
                </div>
                <div style={{ display: 'flex', gap: '4px' }}>
                  {image.palette.map((hex, i) => (
                    <div key={i} title={hex} style={{
                      flex: 1, height: '32px',
                      background: hex, borderRadius: '6px',
                      border: '1px solid rgba(255,255,255,0.08)'
                    }} />
                  ))}
                </div>
              </div>
            )}

            {/* Tags header + edit toggle */}
            <div style={{
              display: 'flex', justifyContent: 'space-between',
              alignItems: 'center', marginBottom: '10px'
            }}>
              <div style={{
                fontSize: '9px', fontWeight: 600, color: '#65625a', letterSpacing: '0.08em'
              }}>
                TAGS
              </div>
              <button
                onClick={() => setEditingTags(v => !v)}
                style={{
                  background: 'none',
                  border: `1px solid ${editingTags ? 'rgba(201,162,83,0.5)' : 'rgba(255,255,255,0.12)'}`,
                  color: editingTags ? '#dcbd76' : '#9c988d',
                  borderRadius: '5px', padding: '3px 9px',
                  cursor: 'pointer', fontSize: '10.5px', fontFamily: 'inherit'
                }}
              >
                {editingTags ? 'Done' : 'Edit tags'}
              </button>
            </div>

            {/* Add-tag row (edit mode) */}
            {editingTags && (
              <div style={{
                display: 'flex', gap: '6px', marginBottom: '14px',
                padding: '10px', background: 'rgba(255,255,255,0.02)',
                borderRadius: '8px'
              }}>
                <select
                  value={newTagCat}
                  onChange={e => setNewTagCat(e.target.value)}
                  style={{
                    background: '#18181b', color: '#efeadd',
                    border: '1px solid rgba(255,255,255,0.12)',
                    borderRadius: '6px', padding: '6px 8px',
                    fontSize: '11.5px', fontFamily: 'inherit', outline: 'none'
                  }}
                >
                  {CAT_ORDER.map(cat => (
                    <option key={cat} value={cat}>{CAT_LABELS[cat] || cat}</option>
                  ))}
                </select>
                <input
                  value={newTagValue}
                  onChange={e => setNewTagValue(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') addTag(); }}
                  placeholder="new tag…"
                  style={{
                    flex: 1, background: '#18181b', color: '#efeadd',
                    border: '1px solid rgba(255,255,255,0.12)',
                    borderRadius: '6px', padding: '6px 10px',
                    fontSize: '11.5px', fontFamily: 'inherit', outline: 'none'
                  }}
                />
                <button
                  onClick={addTag}
                  style={{
                    background: 'rgba(201,162,83,0.12)',
                    border: '1px solid rgba(201,162,83,0.35)',
                    color: '#dcbd76', borderRadius: '6px',
                    padding: '0 12px', fontSize: '11.5px',
                    cursor: 'pointer', fontFamily: 'inherit'
                  }}
                >
                  Add
                </button>
              </div>
            )}

            {/* Tags by category */}
            {!hasTags && (
              <p style={{ fontSize: '12px', color: '#65625a' }}>
                No tags yet — this image hasn't been through the AI tagging pass.
              </p>
            )}
            {CAT_ORDER.map(cat => {
              if (!categories[cat] || categories[cat].length === 0) return null;
              return (
                <div key={cat} style={{ marginBottom: '16px' }}>
                  <div style={{
                    fontSize: '9px', fontWeight: 600, color: '#65625a',
                    letterSpacing: '0.08em', marginBottom: '7px'
                  }}>
                    {(CAT_LABELS[cat] || cat).toUpperCase()}
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
                    {categories[cat].map(val => (
                      <span key={val} style={{
                        display: 'inline-flex', alignItems: 'center', gap: '5px',
                        background: 'rgba(201,162,83,0.12)',
                        border: '1px solid rgba(201,162,83,0.25)',
                        borderRadius: '5px',
                        padding: '4px 9px',
                        fontSize: '11.5px', color: '#dcbd76'
                      }}>
                        {val}
                        {editingTags && (
                          <button
                            onClick={() => removeTag(cat, val)}
                            title="Remove tag"
                            style={{
                              background: 'none', border: 'none', color: '#cf7152',
                              cursor: 'pointer', padding: 0, fontSize: '13px', lineHeight: 1
                            }}
                          >×</button>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}

          </div>
        </div>

        {/* Delete error */}
        {deleteError && (
          <div style={{
            padding: '10px 20px', fontSize: '11.5px', color: '#cf7152',
            borderTop: '1px solid rgba(207,113,82,0.25)',
            background: 'rgba(207,113,82,0.06)'
          }}>
            {deleteError}
          </div>
        )}

        {/* Footer actions */}
        <div style={{
          padding: '12px 20px',
          borderTop: '1px solid rgba(255,255,255,0.065)',
          display: 'flex', gap: '8px', alignItems: 'center'
        }}>
          <button
            onClick={toggleFavorite}
            style={{
              ...footBtn('#dcbd76'),
              background: isFavorite ? 'rgba(201,162,83,0.18)' : 'none',
              borderColor: isFavorite ? 'rgba(201,162,83,0.6)' : 'rgba(201,162,83,0.3)'
            }}
          >
            {isFavorite ? '★ Favorited' : '☆ Favorite'}
          </button>
          <button
            onClick={toggleFlag}
            style={{
              ...footBtn('#cf7152'),
              background: isFlagged ? 'rgba(207,113,82,0.18)' : 'none',
              borderColor: isFlagged ? 'rgba(207,113,82,0.6)' : 'rgba(207,113,82,0.3)'
            }}
          >
            ⚑ {isFlagged ? 'Flagged' : 'Flag'}
          </button>

          <div style={{ flex: 1 }} />

          <a
            href={`/api/images/${image.id}/download`}
            download={image.filename}
            style={{ ...footBtn('#9c988d'), textDecoration: 'none', display: 'inline-block' }}
            title="Download full-resolution original"
          >
            ↓ Download
          </a>

          {!confirmDelete ? (
            <button
              onClick={() => setConfirmDelete(true)}
              style={footBtn('#cf7152')}
            >
              Delete
            </button>
          ) : (
            <span style={{ display: 'inline-flex', gap: '6px', alignItems: 'center' }}>
              <span style={{ fontSize: '11px', color: '#cf7152' }}>Sure?</span>
              <button
                onClick={doDelete}
                disabled={deleting}
                style={{
                  ...footBtn('#efeadd'),
                  background: 'rgba(207,113,82,0.85)',
                  border: '1px solid rgba(207,113,82,1)',
                  opacity: deleting ? 0.6 : 1
                }}
              >
                {deleting ? 'Deleting…' : 'Yes, delete'}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                disabled={deleting}
                style={footBtn('#9c988d')}
              >
                Cancel
              </button>
            </span>
          )}
        </div>
      </div>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to   { transform: translateX(0); }
        }
      `}</style>
    </>
  );
}
