import { useEffect, useState } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';

const CAT_LABELS = {
  'mood': 'Mood', 'lighting_quality': 'Lighting',
  'lighting_color_temperature': 'Color Temp', 'color_palette': 'Palette',
  'shot_type': 'Shot', 'framing_composition': 'Framing',
  'location_type': 'Location', 'time_of_day_weather': 'Time / Weather',
  'source_type': 'Source', 'subject_count': 'Subjects',
  'subject_camera_relationship': 'Camera Rel.', 'genre_aesthetic': 'Genre',
  'era_decade': 'Era', 'camera_format': 'Format',
  'performance_emotion': 'Emotion',
  'my_work': 'My Work',
};

// my_work leads: it's rare (only Ryan's own projects carry it), so when
// present it's the most important thing on the card.
const CAT_ORDER = [
  'my_work',
  'mood', 'lighting_quality', 'lighting_color_temperature', 'color_palette',
  'shot_type', 'framing_composition', 'location_type', 'time_of_day_weather',
  'source_type', 'subject_count', 'subject_camera_relationship', 'performance_emotion',
  'genre_aesthetic', 'era_decade', 'camera_format'
];

export default function ImageDetail({ image, onClose, onUpdated, onDeleted, onSearchFilm, onFindSimilar }) {
  const isMobile = useIsMobile();
  const [fullImage, setFullImage] = useState(null);
  const [fullError, setFullError] = useState(false);

  const [tags, setTags] = useState(image?.tags || []);
  const [isFavorite, setIsFavorite] = useState(!!image?.is_favorite);
  const [isFlagged, setIsFlagged] = useState(!!image?.is_flagged);

  const [editingTags, setEditingTags] = useState(false);
  const [newTagCat, setNewTagCat] = useState(''); // blank = misc, matches the backend default
  const [newTagValue, setNewTagValue] = useState('');

  const [film, setFilm] = useState(image?.filmography || null);
  const [editingFilm, setEditingFilm] = useState(false);
  const [filmDraft, setFilmDraft] = useState({ title: '', director: '', dp: '', year: '' });

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
    setFilm(image.filmography || null);
    setEditingFilm(false);
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

  const startEditFilm = () => {
    setFilmDraft({
      title: film?.title || '', director: film?.director || '',
      dp: film?.dp || '', year: film?.year || ''
    });
    setEditingFilm(true);
  };

  const saveFilm = async (draft) => {
    try {
      const res = await fetch(`/api/images/${image.id}/filmography`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft)
      });
      const data = await res.json();
      if (data.success) {
        setFilm(data.filmography);
        onUpdated?.(image.id, { filmography: data.filmography });
        setEditingFilm(false);
      }
    } catch {}
  };

  const clearFilm = () => saveFilm({ title: '', director: '', dp: '', year: '' });

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

      {/* Side panel — full-width on mobile since there's no room for a 360px+
          fixed pane beside the grid */}
      <div
        style={{
          position: 'fixed', right: 0, top: 0, bottom: 0,
          width: isMobile ? '100vw' : 'clamp(360px, 45%, 600px)',
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
            {/* Filmography title card — film info Gemini recognized, editable */}
            {(film || editingFilm) && (
              <div style={{
                marginBottom: '20px', padding: '14px 16px',
                background: 'rgba(201,162,83,0.05)',
                border: '1px solid rgba(201,162,83,0.18)',
                borderRadius: '10px'
              }}>
                {!editingFilm ? (
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: '10px' }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {film.title && (
                        <div style={{ marginBottom: '4px' }}>
                          <button
                            onClick={() => onSearchFilm?.(film.title)}
                            title={`Search all frames from “${film.title}”`}
                            style={{
                              background: 'none', border: 'none', padding: 0,
                              color: '#efeadd', fontSize: '16px', fontWeight: 700,
                              letterSpacing: '0.02em', cursor: 'pointer',
                              fontFamily: 'inherit', textAlign: 'left'
                            }}
                            onMouseEnter={e => e.currentTarget.style.color = '#dcbd76'}
                            onMouseLeave={e => e.currentTarget.style.color = '#efeadd'}
                          >
                            {film.title}
                          </button>
                          {film.year && (
                            <span style={{ fontSize: '13px', color: '#9c988d', marginLeft: '7px' }}>
                              ({film.year})
                            </span>
                          )}
                        </div>
                      )}
                      <div style={{ fontSize: '12px', color: '#9c988d', display: 'flex', flexWrap: 'wrap', gap: '4px 14px' }}>
                        {film.director && (
                          <span>
                            dir.{' '}
                            <button
                              onClick={() => onSearchFilm?.(film.director)}
                              title={`Search all frames directed by ${film.director}`}
                              style={{
                                background: 'none', border: 'none', padding: 0,
                                color: '#dcbd76', fontSize: '12px', cursor: 'pointer',
                                fontFamily: 'inherit', textDecoration: 'underline',
                                textDecorationColor: 'rgba(201,162,83,0.35)', textUnderlineOffset: '2px'
                              }}
                            >
                              {film.director}
                            </button>
                          </span>
                        )}
                        {film.dp && (
                          <span>
                            DP{' '}
                            <button
                              onClick={() => onSearchFilm?.(film.dp)}
                              title={`Search all frames shot by ${film.dp}`}
                              style={{
                                background: 'none', border: 'none', padding: 0,
                                color: '#dcbd76', fontSize: '12px', cursor: 'pointer',
                                fontFamily: 'inherit', textDecoration: 'underline',
                                textDecorationColor: 'rgba(201,162,83,0.35)', textUnderlineOffset: '2px'
                              }}
                            >
                              {film.dp}
                            </button>
                          </span>
                        )}
                      </div>
                    </div>
                    <button
                      onClick={startEditFilm}
                      title="Edit film info (AI guesses can be wrong)"
                      style={{
                        background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                        color: '#9c988d', borderRadius: '5px', padding: '3px 9px',
                        cursor: 'pointer', fontSize: '10.5px', fontFamily: 'inherit', flexShrink: 0
                      }}
                    >
                      Edit
                    </button>
                  </div>
                ) : (
                  <div>
                    <div style={{
                      display: 'grid', gridTemplateColumns: '1fr 76px',
                      gap: '6px', marginBottom: '6px'
                    }}>
                      <input
                        value={filmDraft.title}
                        onChange={e => setFilmDraft(d => ({ ...d, title: e.target.value }))}
                        placeholder="Film title"
                        style={{
                          background: '#18181b', color: '#efeadd',
                          border: '1px solid rgba(255,255,255,0.12)', borderRadius: '6px',
                          padding: '7px 10px', fontSize: '12px', fontFamily: 'inherit', outline: 'none'
                        }}
                      />
                      <input
                        value={filmDraft.year}
                        onChange={e => setFilmDraft(d => ({ ...d, year: e.target.value }))}
                        placeholder="Year"
                        style={{
                          background: '#18181b', color: '#efeadd',
                          border: '1px solid rgba(255,255,255,0.12)', borderRadius: '6px',
                          padding: '7px 10px', fontSize: '12px', fontFamily: 'inherit', outline: 'none'
                        }}
                      />
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '10px' }}>
                      <input
                        value={filmDraft.director}
                        onChange={e => setFilmDraft(d => ({ ...d, director: e.target.value }))}
                        placeholder="Director"
                        style={{
                          background: '#18181b', color: '#efeadd',
                          border: '1px solid rgba(255,255,255,0.12)', borderRadius: '6px',
                          padding: '7px 10px', fontSize: '12px', fontFamily: 'inherit', outline: 'none'
                        }}
                      />
                      <input
                        value={filmDraft.dp}
                        onChange={e => setFilmDraft(d => ({ ...d, dp: e.target.value }))}
                        placeholder="Cinematographer (DP)"
                        style={{
                          background: '#18181b', color: '#efeadd',
                          border: '1px solid rgba(255,255,255,0.12)', borderRadius: '6px',
                          padding: '7px 10px', fontSize: '12px', fontFamily: 'inherit', outline: 'none'
                        }}
                      />
                    </div>
                    <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                      <button
                        onClick={() => saveFilm(filmDraft)}
                        style={{
                          background: 'rgba(201,162,83,0.12)',
                          border: '1px solid rgba(201,162,83,0.35)',
                          color: '#dcbd76', borderRadius: '6px', padding: '5px 13px',
                          fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit'
                        }}
                      >
                        Save
                      </button>
                      <button
                        onClick={() => setEditingFilm(false)}
                        style={{
                          background: 'none', border: '1px solid rgba(255,255,255,0.12)',
                          color: '#9c988d', borderRadius: '6px', padding: '5px 13px',
                          fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit'
                        }}
                      >
                        Cancel
                      </button>
                      <div style={{ flex: 1 }} />
                      {film && (
                        <button
                          onClick={clearFilm}
                          title="Remove film info entirely (wrong guess)"
                          style={{
                            background: 'none', border: '1px solid rgba(207,113,82,0.3)',
                            color: '#cf7152', borderRadius: '6px', padding: '5px 13px',
                            fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit'
                          }}
                        >
                          Not a film / wrong
                        </button>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Add film info manually when Gemini didn't recognize one */}
            {!film && !editingFilm && (
              <button
                onClick={startEditFilm}
                style={{
                  background: 'none', border: 'none', padding: 0,
                  color: '#65625a', fontSize: '11px', cursor: 'pointer',
                  fontFamily: 'inherit', marginBottom: '16px', display: 'block'
                }}
                onMouseEnter={e => e.currentTarget.style.color = '#dcbd76'}
                onMouseLeave={e => e.currentTarget.style.color = '#65625a'}
              >
                + Add film info
              </button>
            )}

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
                  <option value="">— optional —</option>
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
            {/* CAT_ORDER first, then any category not in that fixed list (misc,
                and defensively anything else) tacked on at the end so nothing
                typed without a category silently disappears from view. */}
            {[...CAT_ORDER, ...Object.keys(categories).filter(c => !CAT_ORDER.includes(c))].map(cat => {
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
          display: 'flex', gap: '8px', alignItems: 'center',
          flexWrap: isMobile ? 'wrap' : 'nowrap',
          rowGap: '8px'
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
          {onFindSimilar && (
            <button
              onClick={() => onFindSimilar(image)}
              title="Find visually similar images"
              style={footBtn('#a99bf7')}
            >
              ≈ Find Similar
            </button>
          )}

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
