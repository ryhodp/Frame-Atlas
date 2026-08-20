import { useEffect, useRef, useState } from 'react';
import { useIsMobile } from '../hooks/useIsMobile';
import CompositionOverlay, { OVERLAY_MODES, OVERLAY_LABELS, OVERLAY_ROTATABLE } from './CompositionOverlay';
import { fetchDecks, addImagesToDeck, createDeckWithImages, describeAddResult } from '../deckAdd';
import { PAGE_BG } from '../theme';

const CAT_LABELS = {
  'mood': 'Mood', 'lighting_quality': 'Lighting',
  'lighting_color_temperature': 'Color Temp', 'color_palette': 'Palette',
  'shot_type': 'Shot', 'framing_composition': 'Framing',
  'location_type': 'Location', 'time_of_day_weather': 'Time / Weather',
  'source_type': 'Source', 'subject_count': 'Subjects',
  'subject_camera_relationship': 'Camera Rel.', 'genre_aesthetic': 'Genre',
  'era_decade': 'Era', 'camera_format': 'Format',
  'performance_emotion': 'Emotion',
  'subjects': 'Objects',
  'my_work': 'My Work',
};

// my_work leads: it's rare (only Ryan's own projects carry it), so when
// present it's the most important thing on the card.
const CAT_ORDER = [
  'my_work',
  'mood', 'lighting_quality', 'lighting_color_temperature', 'color_palette',
  'shot_type', 'framing_composition', 'location_type', 'time_of_day_weather',
  'source_type', 'subject_count', 'subject_camera_relationship', 'performance_emotion',
  'genre_aesthetic', 'era_decade', 'camera_format', 'subjects'
];

export default function ImageDetail({ image, onClose, onUpdated, onDeleted, onSearchFilm, onFindSimilar, onCrop }) {
  const isMobile = useIsMobile();
  const [fullImage, setFullImage] = useState(null);
  const [fullError, setFullError] = useState(false);

  const [tags, setTags] = useState(image?.tags || []);
  const [isFavorite, setIsFavorite] = useState(!!image?.is_favorite);

  const [editingTags, setEditingTags] = useState(false);
  const [newTagCat, setNewTagCat] = useState(''); // blank = misc, matches the backend default
  const [newTagValue, setNewTagValue] = useState('');

  const [film, setFilm] = useState(image?.filmography || null);
  const [editingFilm, setEditingFilm] = useState(false);
  const [filmDraft, setFilmDraft] = useState({ title: '', director: '', dp: '', year: '' });

  // V39: DP technical notes — camera/rig, lens, lens filter, stop, freeform
  // on-set notes. Collapsed by default (Ryan's call — most photos won't have
  // this filled in, and the panel is already dense), directly editable when
  // expanded rather than a separate read/edit toggle like filmography above:
  // these are short structured fields with no clickable-search behavior to
  // justify a distinct read view.
  const [notes, setNotes] = useState(image?.notes || null);
  const [notesExpanded, setNotesExpanded] = useState(false);
  const [notesDraft, setNotesDraft] = useState({ camera_rig: '', lens: '', lens_filter: '', stop: '', onset_notes: '' });

  // V46: add this one photo to a deck without going back to Home and hunting
  // for Select Mode. Deck list is fetched lazily, the first time the popover
  // opens — most visits to this panel never touch it.
  const [deckOpen, setDeckOpen] = useState(false);
  const [decks, setDecks] = useState(null);   // null = not fetched yet
  const [deckBusy, setDeckBusy] = useState(false);
  const [deckMsg, setDeckMsg] = useState(null); // {tone, message}
  const [newDeckName, setNewDeckName] = useState('');

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  // Composition-guide overlay (thirds / golden ratio / spiral / diagonal / cross)
  const [overlayMode, setOverlayMode] = useState('off');
  const [overlayOrientation, setOverlayOrientation] = useState(0);
  const [overlayMenuOpen, setOverlayMenuOpen] = useState(false);
  const imgWrapRef = useRef(null);
  const [imgBoxSize, setImgBoxSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    if (!image) return;
    let objectUrl = null;
    setFullImage(null);
    setFullError(false);
    setTags(image.tags || []);
    setIsFavorite(!!image.is_favorite);
    setEditingTags(false);
    setFilm(image.filmography || null);
    setEditingFilm(false);
    setNotes(image.notes || null);
    setNotesExpanded(false);
    setNotesDraft({
      camera_rig: image.notes?.camera_rig || '', lens: image.notes?.lens || '',
      lens_filter: image.notes?.lens_filter || '', stop: image.notes?.stop || '',
      onset_notes: image.notes?.onset_notes || ''
    });
    setConfirmDelete(false);
    setDeleteError(null);
    setOverlayMode('off');
    setOverlayMenuOpen(false);

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
    // Re-fetch on aspect_ratio change too, not just id: a crop (V18) replaces
    // the Drive file in place, same image id, so id alone won't tell this
    // effect the full-res bytes it already fetched are now stale.
  }, [image?.id, image?.aspect_ratio]);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  // Tracks the img's own rendered box (not the letterboxed container) so the
  // overlay lines up with the actual picture at any zoom/resize.
  useEffect(() => {
    const el = imgWrapRef.current;
    if (!el) return;
    const update = () => setImgBoxSize({ width: el.clientWidth, height: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fullImage, image?.id]);

  if (!image) return null;

  const categories = {};
  tags.forEach(tag => {
    if (!categories[tag.category]) categories[tag.category] = [];
    categories[tag.category].push(tag.value);
  });

  const hasTags = tags.length > 0;

  // ── Add to deck ─────────────────────────────────────────────────────────────
  const toggleDeckPopover = async () => {
    const opening = !deckOpen;
    setDeckOpen(opening);
    setDeckMsg(null);
    if (opening && decks === null) setDecks(await fetchDecks());
  };

  const addToDeck = async (deck) => {
    if (deckBusy) return;
    setDeckBusy(true);
    try {
      const result = await addImagesToDeck(deck.id, [image.id]);
      setDeckMsg(describeAddResult(result, deck.name));
    } catch (e) {
      setDeckMsg({ tone: 'error', message: e.message || 'Could not add to that deck.' });
    }
    setDeckBusy(false);
  };

  const addToNewDeck = async () => {
    const name = newDeckName.trim();
    if (!name || deckBusy) return;
    setDeckBusy(true);
    try {
      const { deck, result } = await createDeckWithImages(name, [image.id]);
      setDeckMsg(describeAddResult(result, deck.name));
      setNewDeckName('');
      // The new deck has to appear in the list, otherwise adding a second photo
      // to it means creating a duplicate deck of the same name.
      setDecks(await fetchDecks());
    } catch (e) {
      setDeckMsg({ tone: 'error', message: e.message || 'Could not create that deck.' });
    }
    setDeckBusy(false);
  };

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

  const saveNotes = async (draft) => {
    try {
      const res = await fetch(`/api/images/${image.id}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(draft)
      });
      const data = await res.json();
      if (data.success) {
        setNotes(data.notes);
        onUpdated?.(image.id, { notes: data.notes });
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
    color, borderRadius: '6px',
    padding: isMobile ? '11px 14px' : '7px 14px',
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
          background: PAGE_BG,
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
              cursor: 'pointer', fontSize: '20px', lineHeight: 1, flexShrink: 0,
              padding: isMobile ? '11px' : '0', margin: isMobile ? '-11px' : 0
            }}
          >×</button>
        </div>

        {/* Action toolbar — above the photo, so it's not competing with tags
            for attention down at the bottom of a long scroll */}
        <div style={{
          padding: '10px 20px',
          borderBottom: '1px solid rgba(255,255,255,0.065)',
          display: 'flex', gap: '8px', alignItems: 'center',
          flexWrap: 'wrap', rowGap: '8px'
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

          {/* Composition-guide overlay: icon button opens a popover of modes;
              a rotate control appears only for the two directional guides */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setOverlayMenuOpen(v => !v)}
              title="Show composition guides over the photo"
              style={{
                ...footBtn('#7fb3d9'),
                background: overlayMode !== 'off' ? 'rgba(127,179,217,0.18)' : 'none',
                borderColor: overlayMode !== 'off' ? 'rgba(127,179,217,0.6)' : 'rgba(127,179,217,0.3)'
              }}
            >
              ▦ {overlayMode === 'off' ? 'Overlay' : OVERLAY_LABELS[overlayMode]}
            </button>
            {overlayMenuOpen && (
              <>
                {/* click-outside catcher */}
                <div onClick={() => setOverlayMenuOpen(false)}
                  style={{ position: 'fixed', inset: 0, zIndex: 1001 }} />
                <div style={{
                  position: 'absolute', top: 'calc(100% + 6px)', left: 0,
                  background: '#18181b', border: '1px solid rgba(255,255,255,0.12)',
                  borderRadius: '8px', padding: '4px', zIndex: 1002,
                  minWidth: '160px', boxShadow: '0 8px 24px rgba(0,0,0,0.4)'
                }}>
                  {OVERLAY_MODES.map(m => (
                    <button
                      key={m}
                      onClick={() => { setOverlayMode(m); setOverlayMenuOpen(false); }}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left',
                        background: overlayMode === m ? 'rgba(127,179,217,0.15)' : 'none',
                        border: 'none', color: overlayMode === m ? '#7fb3d9' : '#efeadd',
                        borderRadius: '5px', padding: '7px 10px',
                        cursor: 'pointer', fontSize: '12px', fontFamily: 'inherit'
                      }}
                    >
                      {OVERLAY_LABELS[m]}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          {OVERLAY_ROTATABLE[overlayMode] && (
            <button
              onClick={() => setOverlayOrientation(o => (o + 1) % 4)}
              title="Rotate the guide to a different corner"
              style={footBtn('#7fb3d9')}
            >
              ⟳
            </button>
          )}

          {/* V46: put this frame straight into a lookbook. Before this, the only
              route was Home → Select Mode → the bottom bar, so looking at one
              great frame and wanting it in a deck meant closing this panel. */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={toggleDeckPopover}
              title="Add this photo to a lookbook"
              style={footBtn('#b8cea1')}
              aria-expanded={deckOpen}
            >
              ⧉ Add to Deck
            </button>

            {deckOpen && (
              <div style={{
                // Opens DOWNWARD, unlike the visually identical picker in
                // TagModeBar. That one lives in a bar pinned to the bottom of
                // the screen so it has to open up; this button sits in a row at
                // the TOP of the detail panel, and opening upward put the deck
                // list off-screen with only the "new deck" field reachable.
                position: 'absolute', top: 'calc(100% + 8px)', left: 0,
                width: '250px', background: '#2a2c31', border: '1px solid #44474f',
                borderRadius: '10px', boxShadow: '0 8px 32px rgba(0,0,0,0.55)',
                zIndex: 40, maxHeight: '300px', overflowY: 'auto',
              }}>
                {decks === null && (
                  <div style={{ padding: '10px 12px', fontSize: '11.5px', color: '#8e9099' }}>
                    Loading your decks…
                  </div>
                )}
                {decks !== null && decks.length === 0 && (
                  <div style={{ padding: '10px 12px', fontSize: '11.5px', color: '#8e9099' }}>
                    No decks yet — name one below.
                  </div>
                )}
                {(decks || []).map(deck => (
                  <button
                    key={deck.id}
                    onClick={() => addToDeck(deck)}
                    disabled={deckBusy}
                    style={{
                      width: '100%', display: 'flex', alignItems: 'center',
                      justifyContent: 'space-between', gap: '10px', padding: '8px 12px',
                      background: 'transparent', border: 'none',
                      cursor: deckBusy ? 'default' : 'pointer',
                      textAlign: 'left', fontFamily: 'inherit',
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = '#37393e'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <span style={{ fontSize: '13px', color: '#e2e2e6' }}>{deck.name}</span>
                    <span style={{ fontSize: '10px', color: '#8e9099' }}>{deck.image_count}</span>
                  </button>
                ))}

                <div style={{
                  display: 'flex', gap: '6px', padding: '8px 10px',
                  borderTop: (decks || []).length > 0 ? '1px solid #44474f' : 'none',
                }}>
                  <input
                    value={newDeckName}
                    onChange={e => setNewDeckName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') addToNewDeck(); }}
                    placeholder="+ New deck…"
                    style={{
                      flex: 1, minWidth: 0, background: '#111317', color: '#e2e2e6',
                      border: '1px solid #44474f', borderRadius: '6px',
                      padding: '6px 8px', fontSize: '12px',
                      fontFamily: 'inherit', outline: 'none',
                    }}
                  />
                  <button
                    onClick={addToNewDeck}
                    disabled={!newDeckName.trim() || deckBusy}
                    style={{
                      background: newDeckName.trim() ? '#d9a441' : 'rgba(217,164,65,0.2)',
                      color: newDeckName.trim() ? '#3d2f00' : '#8e9099',
                      border: 'none', borderRadius: '6px', padding: '0 10px',
                      fontSize: '12px', fontWeight: 500, fontFamily: 'inherit',
                      cursor: newDeckName.trim() && !deckBusy ? 'pointer' : 'default',
                    }}
                  >
                    Add
                  </button>
                </div>

                {deckMsg && (
                  <div style={{
                    padding: '8px 12px', fontSize: '11.5px',
                    borderTop: '1px solid #44474f',
                    color: deckMsg.tone === 'error' ? '#cf7152'
                      : deckMsg.tone === 'info' ? '#d9a441' : '#b8cea1',
                  }}>
                    {deckMsg.message}
                  </div>
                )}
              </div>
            )}
          </div>

          {onFindSimilar && (
            <button
              onClick={() => onFindSimilar(image)}
              title="Find visually similar images"
              style={footBtn('#a99bf7')}
            >
              ≈ Find Similar
            </button>
          )}
          {onCrop && (
            <button
              onClick={() => onCrop(image)}
              title="Auto-detect and remove letterbox bars / screenshot chrome"
              style={footBtn('#d9a441')}
            >
              ✂ Crop
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

        {/* Delete error */}
        {deleteError && (
          <div style={{
            padding: '10px 20px', fontSize: '11.5px', color: '#cf7152',
            borderBottom: '1px solid rgba(207,113,82,0.25)',
            background: 'rgba(207,113,82,0.06)'
          }}>
            {deleteError}
          </div>
        )}

        {/* Scrollable content */}
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {/* Full-res image (falls back to thumbnail while loading) */}
          <div style={{
            background: '#141318',
            minHeight: '200px', maxHeight: '420px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            overflow: 'hidden'
          }}>
            <div ref={imgWrapRef} style={{ position: 'relative', display: 'inline-block', maxWidth: '100%', maxHeight: '420px' }}>
              <img
                src={fullImage || image.thumbnail}
                alt={image.filename}
                style={{
                  display: 'block',
                  maxWidth: '100%', maxHeight: '420px',
                  objectFit: 'contain',
                  filter: fullImage ? 'none' : 'blur(0.5px)',
                  transition: 'filter 0.3s ease'
                }}
                onLoad={() => imgWrapRef.current && setImgBoxSize({
                  width: imgWrapRef.current.clientWidth, height: imgWrapRef.current.clientHeight
                })}
              />
              <CompositionOverlay
                mode={overlayMode}
                orientation={overlayOrientation}
                width={imgBoxSize.width}
                height={imgBoxSize.height}
              />
            </div>
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

            {/* On-Set Notes — DP technical fields (V39): camera/rig, lens,
                lens filter, stop, freeform notes. Collapsed by default. */}
            {(() => {
              const hasNotesContent = !!(notes && (
                notes.camera_rig || notes.lens || notes.lens_filter || notes.stop || notes.onset_notes
              ));
              const noteFieldStyle = {
                background: '#18181b', color: '#efeadd',
                border: '1px solid rgba(255,255,255,0.12)', borderRadius: '6px',
                padding: '7px 10px', fontSize: '12px', fontFamily: 'inherit', outline: 'none'
              };
              return (
                <div style={{ marginBottom: '20px' }}>
                  <button
                    onClick={() => setNotesExpanded(v => !v)}
                    style={{
                      background: 'none', border: 'none', padding: 0,
                      display: 'flex', alignItems: 'center', gap: '6px',
                      color: hasNotesContent ? '#dcbd76' : '#65625a',
                      fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em',
                      cursor: 'pointer', fontFamily: 'inherit'
                    }}
                  >
                    <span style={{
                      display: 'inline-block', fontSize: '10px',
                      transform: notesExpanded ? 'none' : 'rotate(-90deg)',
                      transition: 'transform 150ms ease'
                    }}>▾</span>
                    ON-SET NOTES
                    {hasNotesContent && !notesExpanded && (
                      <span style={{ fontSize: '10px', color: '#65625a', fontWeight: 400 }}>· filled in</span>
                    )}
                  </button>

                  {notesExpanded && (
                    <div style={{
                      marginTop: '10px', padding: '14px 16px',
                      background: 'rgba(255,255,255,0.02)',
                      border: '1px solid rgba(255,255,255,0.08)',
                      borderRadius: '10px'
                    }}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '6px' }}>
                        <input
                          value={notesDraft.camera_rig}
                          onChange={e => setNotesDraft(d => ({ ...d, camera_rig: e.target.value }))}
                          placeholder="Camera / Rig"
                          style={noteFieldStyle}
                        />
                        <input
                          value={notesDraft.lens}
                          onChange={e => setNotesDraft(d => ({ ...d, lens: e.target.value }))}
                          placeholder="Lens"
                          style={noteFieldStyle}
                        />
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px', marginBottom: '6px' }}>
                        <input
                          value={notesDraft.lens_filter}
                          onChange={e => setNotesDraft(d => ({ ...d, lens_filter: e.target.value }))}
                          placeholder="Lens Filter"
                          style={noteFieldStyle}
                        />
                        <input
                          value={notesDraft.stop}
                          onChange={e => setNotesDraft(d => ({ ...d, stop: e.target.value }))}
                          placeholder="Stop (e.g. T2.8)"
                          style={noteFieldStyle}
                        />
                      </div>
                      <textarea
                        value={notesDraft.onset_notes}
                        onChange={e => setNotesDraft(d => ({ ...d, onset_notes: e.target.value }))}
                        placeholder="On-set notes — rain machine, colored chase, technique…"
                        rows={3}
                        style={{ ...noteFieldStyle, width: '100%', resize: 'vertical', marginBottom: '10px', boxSizing: 'border-box' }}
                      />
                      <button
                        onClick={() => saveNotes(notesDraft)}
                        style={{
                          background: 'rgba(201,162,83,0.12)',
                          border: '1px solid rgba(201,162,83,0.35)',
                          color: '#dcbd76', borderRadius: '6px', padding: '5px 13px',
                          fontSize: '11px', cursor: 'pointer', fontFamily: 'inherit'
                        }}
                      >
                        Save
                      </button>
                    </div>
                  )}
                </div>
              );
            })()}

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
