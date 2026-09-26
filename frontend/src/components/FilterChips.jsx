// FilterChips — the row of active-filter chips (Find Similar, film, aspect
// ratio, exact tags, describe-it phrases, on-set notes) with "Clear all", plus
// the two plain-language notes explaining what violet and amber chips match
// (Day 45 / V93). Find Similar isn't search state, so its chip's data and its
// × come in from Home as `similarTo` / `onClearSimilar`.
import { accentBlueLight, accentFilm, accentOrange, accentSimilar, accentTeal, accentViolet, accentVioletLight, accentVioletLighter, danger, onSurfaceFaint, primaryDim, withAlpha } from '../theme';

export default function FilterChips({ search, similarTo, onClearSimilar }) {
  const { chips, nlChips, noteChips, film, setFilm, ar, setAr, removeChip, removeNlChip, removeNoteChip, clearAll } = search;
  return (
    <>
      {/* Active chips (tags + NL phrases + notes phrases + film + aspect ratio + similar) */}
      {(chips.length > 0 || nlChips.length > 0 || noteChips.length > 0 || film || ar || similarTo) && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px', marginTop: '12px' }}>
          {/* Similar chip — from "Find Similar" in the detail panel. Soft violet, distinct from NL/film chips */}
          {similarTo && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '6px',
              background: withAlpha(accentSimilar,0.14),
              border: `1px solid ${withAlpha(accentSimilar,0.5)}`,
              borderRadius: '6px',
              padding: '4px 8px 4px 9px',
              fontSize: '12.5px', color: accentVioletLighter, fontWeight: 500
            }}>
              ≈ Similar to {similarTo.filename}
              <button
                onClick={onClearSimilar}
                style={{
                  background: 'none', border: 'none', color: accentVioletLighter,
                  cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
              >×</button>
            </span>
          )}
          {/* Film chip — from clicking a title/director/DP in the detail panel */}
          {film && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '6px',
              background: withAlpha(accentFilm,0.12),
              border: `1px solid ${withAlpha(accentFilm,0.45)}`,
              borderRadius: '6px',
              padding: '4px 8px 4px 9px',
              fontSize: '12.5px', color: accentBlueLight, fontWeight: 500
            }}>
              🎬 {film}
              <button
                onClick={() => setFilm(null)}
                style={{
                  background: 'none', border: 'none', color: accentBlueLight,
                  cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
              >×</button>
            </span>
          )}
          {/* Aspect-ratio chip (V15) — from picking a format in the search dropdown */}
          {ar && (
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: '6px',
              background: withAlpha(accentTeal,0.12),
              border: `1px solid ${withAlpha(accentTeal,0.45)}`,
              borderRadius: '6px',
              padding: '4px 8px 4px 9px',
              fontSize: '12.5px', color: accentTeal, fontWeight: 500
            }}>
              ▭ {ar}
              <button
                onClick={() => setAr(null)}
                style={{
                  background: 'none', border: 'none', color: accentTeal,
                  cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
              >×</button>
            </span>
          )}
          {/* Exact-tag chips. The # and the tooltip exist because these look
              almost identical to the natural-language chips below them, and
              not knowing which kind you had is what made a bulk tag edit
              come up empty — the two find completely different photos. */}
          {chips.map(chip => (
            <span key={chip} title={`Exact tag — showing only photos actually tagged “${chip}”`} style={{
              display: 'inline-flex', alignItems: 'center', gap: '6px',
              background: withAlpha(primaryDim,0.12),
              border: `1px solid ${withAlpha(primaryDim,0.35)}`,
              borderRadius: '6px',
              padding: '4px 8px 4px 9px',
              fontSize: '12.5px', color: primaryDim, fontWeight: 500
            }}>
              <span style={{ opacity: 0.65 }}>#</span>{chip}
              <button
                onClick={() => removeChip(chip)}
                style={{
                  background: 'none', border: 'none', color: primaryDim,
                  cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
              >×</button>
            </span>
          ))}

          {/* NL phrase chips — styled differently (violet, italic, quoted) */}
          {nlChips.map(nl => (
            <span
              key={nl.phrase}
              title={`Describe-it search — finds photos tagged any of: ${nl.tags.join(', ')}`}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: withAlpha(accentViolet,0.12),
                border: `1px dashed ${withAlpha(accentViolet,0.45)}`,
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: accentVioletLight,
                fontStyle: 'italic'
              }}
            >
              “{nl.phrase}”
              <button
                onClick={() => removeNlChip(nl.phrase)}
                style={{
                  background: 'none', border: 'none', color: accentVioletLight,
                  cursor: 'pointer', padding: 0, fontSize: '14px',
                  lineHeight: 1, opacity: 0.6, fontStyle: 'normal'
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
              >×</button>
            </span>
          ))}

          {/* On-set-notes chips (V39) — amber, distinct from gold tag chips
              and violet NL chips, so it's clear at a glance the match came
              from Camera/Lens/Filter/Stop/On-Set Notes, not a tag. */}
          {noteChips.map(phrase => (
            <span
              key={phrase}
              title={`On-set notes search — finds photos whose Camera/Lens/Filter/Stop/On-Set Notes mention “${phrase}”`}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: '6px',
                background: withAlpha(accentOrange,0.14),
                border: `1px solid ${withAlpha(accentOrange,0.45)}`,
                borderRadius: '6px',
                padding: '4px 8px 4px 9px',
                fontSize: '12.5px', color: accentOrange, fontWeight: 500
              }}
            >
              🔧 {phrase}
              <button
                onClick={() => removeNoteChip(phrase)}
                style={{
                  background: 'none', border: 'none', color: accentOrange,
                  cursor: 'pointer', padding: 0, fontSize: '14px', lineHeight: 1, opacity: 0.6
                }}
                onMouseEnter={e => e.currentTarget.style.opacity = '1'}
                onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
              >×</button>
            </span>
          ))}

          <button
            onClick={clearAll}
            style={{
              background: 'none', border: 'none', color: onSurfaceFaint,
              cursor: 'pointer', fontSize: '12px', padding: '4px 6px',
              fontFamily: 'inherit'
            }}
            onMouseEnter={e => e.currentTarget.style.color = danger}
            onMouseLeave={e => e.currentTarget.style.color = onSurfaceFaint}
          >
            Clear all
          </button>
        </div>
      )}

      {/* The search bar has two modes that used to look the same: picking a
          tag from the dropdown filters by that exact tag, while typing a
          phrase and pressing Enter looks for photos that FEEL like it. The
          second one returns photos that don't carry the word you typed,
          which is bewildering if nobody tells you — so say it out loud
          whenever a describe-it search is on. */}
      {nlChips.length > 0 && (
        <div style={{
          display: 'flex', gap: '8px', marginTop: '10px',
          padding: '8px 12px',
          background: withAlpha(accentViolet,0.07),
          border: `1px solid ${withAlpha(accentViolet,0.22)}`,
          borderRadius: '7px',
          fontSize: '11.5px', color: accentVioletLight, lineHeight: 1.55
        }}>
          <span style={{ flexShrink: 0 }}>ⓘ</span>
          <span>
            The dashed violet {nlChips.length === 1 ? 'chip is a' : 'chips are'} <strong>describe-it
            {nlChips.length === 1 ? ' search' : ' searches'}</strong> — {nlChips.length === 1 ? 'it looks' : 'they look'} for
            photos that feel like {nlChips.length === 1 ? 'that phrase' : 'those phrases'}, so the results may not carry that
            exact tag. To filter by a real tag instead, type it and pick it from the dropdown list —
            those chips are gold and start with a #.
          </span>
        </div>
      )}

      {/* Amber chips (V39) — say out loud what they matched, same reasoning
          as the violet describe-it note above: an on-set-notes match can
          easily be confused for a tag match otherwise. */}
      {noteChips.length > 0 && (
        <div style={{
          display: 'flex', gap: '8px', marginTop: '10px',
          padding: '8px 12px',
          background: withAlpha(accentOrange,0.07),
          border: `1px solid ${withAlpha(accentOrange,0.22)}`,
          borderRadius: '7px',
          fontSize: '11.5px', color: accentOrange, lineHeight: 1.55
        }}>
          <span style={{ flexShrink: 0 }}>ⓘ</span>
          <span>
            The amber 🔧 {noteChips.length === 1 ? 'chip matches' : 'chips match'} <strong>on-set
            notes</strong> — Camera/Rig, Lens, Lens Filter, Stop, or the On-Set Notes box on a photo's
            detail panel, not a tag.
          </span>
        </div>
      )}
    </>
  );
}
