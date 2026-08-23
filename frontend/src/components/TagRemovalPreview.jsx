import { useEffect, useState } from 'react';
import { error as errorColor, onPrimary, onSurfaceMuted, onSurfaceVariant, onSurfaceWarm, outline, outlineVariant, primary, surfaceContainerHigh, surfaceContainerLow, surfaceContainerMuted } from '../theme';

// How many ids we hand the server in one request. Matches SQL_PARAM_CHUNK in
// backend/app.py — big id lists get sliced up there too, and batching here as
// well means one failed batch reports itself instead of taking the rest down
// with it (the same skip-and-continue rule bulk delete follows).
const REMOVE_BATCH = 400;

/**
 * V32 — "remove this tag from every photo in the current search."
 *
 * Ryan's explicit choice was to SEE the affected photos before anything is
 * removed: not a bare are-you-sure box, not an undo window. So this loads a
 * preview from /api/tags/removal-preview (read-only — it writes nothing) and
 * only calls the existing /api/tags/bulk-remove once he confirms from here.
 *
 * Deliberately styled gold/amber, never the red used by Delete. Removing a
 * tag and deleting a photo must not look like the same button — one is a
 * label coming off, the other moves the actual picture to Drive's _Removed.
 */
export default function TagRemovalPreview({ value, filterParams, onClose, onRemoved }) {
  const [loading, setLoading] = useState(true);
  const [groups, setGroups] = useState([]);
  const [chosenCats, setChosenCats] = useState(new Set());
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    const params = new URLSearchParams(filterParams);
    params.set('value', value);
    fetch(`/api/tags/removal-preview?${params}`)
      .then(res => res.json())
      .then(data => {
        if (!live) return;
        const gs = data.groups || [];
        setGroups(gs);
        // Everything starts ticked — the usual case is one category and one
        // obviously-wrong tag. Multiple categories is the case worth pausing
        // on, and that's exactly when the checkboxes become visible.
        setChosenCats(new Set(gs.map(g => g.category)));
        setLoading(false);
      })
      .catch(() => {
        if (!live) return;
        setError("Couldn't load the preview — check your connection and try again.");
        setLoading(false);
      });
    return () => { live = false; };
  }, [value, filterParams]);

  const chosen = groups.filter(g => chosenCats.has(g.category));
  const totalChosen = chosen.reduce((n, g) => n + g.count, 0);

  const toggleCat = (cat) => {
    setChosenCats(prev => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
      return next;
    });
  };

  const runRemoval = async () => {
    if (!totalChosen || busy) return;
    setBusy(true);
    setError('');
    let removed = 0;
    let failedBatches = 0;
    for (const group of chosen) {
      for (let i = 0; i < group.image_ids.length; i += REMOVE_BATCH) {
        const batch = group.image_ids.slice(i, i + REMOVE_BATCH);
        try {
          const res = await fetch('/api/tags/bulk-remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ image_ids: batch, category: group.category, value })
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.error || 'failed');
          removed += data.removed || 0;
        } catch (e) {
          // Skip and continue: one bad batch shouldn't hold the rest hostage.
          console.error('Tag removal batch failed', e);
          failedBatches += 1;
        }
      }
    }
    setBusy(false);
    onRemoved?.(removed, failedBatches);
  };

  return (
    <div
      onClick={() => !busy && onClose()}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.62)',
        zIndex: 1200, display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '20px'
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: surfaceContainerLow,
          border: `1px solid ${outlineVariant}`,
          borderRadius: '14px',
          width: 'min(920px, 100%)',
          maxHeight: '86vh',
          display: 'flex', flexDirection: 'column',
          boxShadow: '0 24px 60px rgba(0,0,0,0.65)',
          animation: 'fapop 0.12s ease'
        }}
      >
        {/* Header */}
        <div style={{ padding: '18px 22px 14px', borderBottom: `1px solid ${surfaceContainerHigh}` }}>
          <div style={{ fontSize: '16px', fontWeight: 700, color: onSurfaceWarm }}>
            Remove the tag “{value}”
          </div>
          <div style={{ fontSize: '12.5px', color: onSurfaceMuted, marginTop: '6px', lineHeight: 1.55 }}>
            Below are the photos that would lose this tag. Only the tag comes off —
            every photo stays in your library exactly where it is.
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 22px' }}>
          {loading && (
            <div style={{ fontSize: '12.5px', color: outline, padding: '30px 0', textAlign: 'center' }}>
              Finding the photos…
            </div>
          )}

          {!loading && error && (
            <div style={{ fontSize: '12.5px', color: errorColor, padding: '20px 0' }}>{error}</div>
          )}

          {!loading && !error && groups.length === 0 && (
            <div style={{ fontSize: '12.5px', color: onSurfaceMuted, padding: '20px 0', lineHeight: 1.6 }}>
              None of the photos in this search actually carry a tag called “{value}”.
              <br />
              That usually means the search was a describe-it search rather than a tag
              search — it found photos that <em>feel</em> like “{value}” without any of
              them being tagged that word.
            </div>
          )}

          {!loading && !error && groups.map(group => {
            const picked = chosenCats.has(group.category);
            const hidden = group.count - group.samples.length;
            return (
              <div key={group.category} style={{ marginBottom: '22px' }}>
                <label style={{
                  display: 'flex', alignItems: 'center', gap: '10px',
                  marginBottom: '10px', cursor: groups.length > 1 ? 'pointer' : 'default'
                }}>
                  {/* Only offer the choice when there IS one. "car (Location)"
                      and "car (Objects)" are two different true facts about a
                      photo, so a multi-category tag must not be wiped by one
                      undifferentiated click. */}
                  {groups.length > 1 && (
                    <input
                      type="checkbox"
                      checked={picked}
                      onChange={() => toggleCat(group.category)}
                      style={{ accentColor: primary, width: '15px', height: '15px', cursor: 'pointer' }}
                    />
                  )}
                  <span style={{
                    width: '8px', height: '8px', borderRadius: '2px',
                    background: group.color, flexShrink: 0
                  }} />
                  <span style={{ fontSize: '13.5px', color: onSurfaceWarm, fontWeight: 600 }}>
                    {group.catLabel}
                  </span>
                  <span style={{ fontSize: '12px', color: onSurfaceMuted }}>
                    {group.count} photo{group.count === 1 ? '' : 's'} would lose “{value}”
                  </span>
                </label>

                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))',
                  gap: '6px',
                  opacity: picked ? 1 : 0.32,
                  transition: 'opacity 0.12s ease'
                }}>
                  {group.samples.map(img => (
                    <div
                      key={img.id}
                      title={img.filename}
                      style={{
                        position: 'relative', width: '100%', aspectRatio: '1',
                        borderRadius: '5px', overflow: 'hidden', background: surfaceContainerMuted,
                        border: '1px solid rgba(255,255,255,0.06)'
                      }}
                    >
                      <img
                        src={img.thumbnail}
                        alt={img.filename}
                        loading="lazy"
                        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover' }}
                      />
                    </div>
                  ))}
                </div>

                {hidden > 0 && (
                  <div style={{ fontSize: '11.5px', color: outline, marginTop: '8px' }}>
                    Showing the first {group.samples.length}. Another {hidden} photo
                    {hidden === 1 ? '' : 's'} not pictured {hidden === 1 ? 'is' : 'are'} also
                    included — all {group.count} lose the tag.
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        <div style={{
          padding: '14px 22px', borderTop: `1px solid ${surfaceContainerHigh}`,
          display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap'
        }}>
          <span style={{ fontSize: '12px', color: onSurfaceMuted, flex: 1, minWidth: '180px' }}>
            {totalChosen > 0
              ? `${totalChosen} photo${totalChosen === 1 ? '' : 's'} will lose this tag. No photos are deleted.`
              : 'Nothing selected to remove.'}
          </span>
          <button
            onClick={() => !busy && onClose()}
            style={{
              background: 'none', border: `1px solid ${outlineVariant}`,
              color: onSurfaceVariant, borderRadius: '8px', padding: '8px 16px',
              cursor: 'pointer', fontSize: '12.5px', fontFamily: 'inherit'
            }}
          >
            Cancel
          </button>
          <button
            onClick={runRemoval}
            disabled={!totalChosen || busy}
            style={{
              background: totalChosen && !busy ? primary : 'rgba(217,164,65,0.22)',
              color: totalChosen && !busy ? onPrimary : outline,
              border: 'none', borderRadius: '8px', padding: '8px 18px',
              fontSize: '12.5px', fontWeight: 600,
              cursor: totalChosen && !busy ? 'pointer' : 'default', fontFamily: 'inherit'
            }}
          >
            {busy ? 'Removing…' : `Remove tag from ${totalChosen} photo${totalChosen === 1 ? '' : 's'}`}
          </button>
        </div>
      </div>
    </div>
  );
}
