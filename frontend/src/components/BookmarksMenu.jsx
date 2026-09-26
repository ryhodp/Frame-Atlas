// BookmarksMenu — the ☆ saved-searches button and its menu (Day 45 / V93).
// Save the current filters under a name, re-apply one, or delete one. The
// saving/restoring itself is useSearch() + searchParams.js.
import { black, danger, onSurfaceFaint, onSurfaceMuted, onSurfaceWarm, primaryDim, surfaceContainerDark, surfaceContainerHover, surfaceContainerLowest, warning, white, withAlpha } from '../theme';

export default function BookmarksMenu({ search, isMobile }) {
  const { hasFilters, bookmarks, showBookmarks, setShowBookmarks, saveName, setSaveName, saveBookmark, applyBookmark, deleteBookmark } = search;
  return (
    <>
      {/* Bookmark button + dropdown */}
      <div data-bookmark-area style={{ position: 'relative', flexShrink: 0 }}>
        <button
          onClick={() => setShowBookmarks(v => !v)}
          title="Saved searches"
          style={{
            height: isMobile ? '38px' : '46px', width: isMobile ? '38px' : '46px',
            background: surfaceContainerDark,
            border: `1px solid ${showBookmarks ? withAlpha(primaryDim,0.5) : withAlpha(white,0.12)}`,
            borderRadius: '10px',
            cursor: 'pointer',
            color: showBookmarks ? warning : onSurfaceMuted,
            fontSize: '16px'
          }}
        >
          ☆
        </button>

        {showBookmarks && (
          <div style={{
            position: 'absolute', top: '54px', right: 0,
            width: '300px',
            background: surfaceContainerDark,
            border: `1px solid ${withAlpha(white,0.12)}`,
            borderRadius: '10px',
            boxShadow: `0 20px 48px ${withAlpha(black,0.6)}`,
            zIndex: 60,
            animation: 'fapop 0.12s ease',
            overflow: 'hidden'
          }}>
            {/* Save current */}
            {hasFilters && (
              <div style={{
                padding: '12px 13px',
                borderBottom: `1px solid ${withAlpha(white,0.065)}`,
                display: 'flex', gap: '6px'
              }}>
                <input
                  value={saveName}
                  onChange={e => setSaveName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveBookmark(); }}
                  placeholder="Name this search…"
                  style={{
                    flex: 1, background: surfaceContainerLowest,
                    border: `1px solid ${withAlpha(white,0.1)}`,
                    borderRadius: '6px', padding: '7px 10px',
                    color: onSurfaceWarm, fontSize: '12px',
                    fontFamily: 'inherit', outline: 'none'
                  }}
                />
                <button
                  onClick={saveBookmark}
                  style={{
                    background: withAlpha(primaryDim,0.12),
                    border: `1px solid ${withAlpha(primaryDim,0.35)}`,
                    color: warning, borderRadius: '6px',
                    padding: '0 12px', fontSize: '12px',
                    cursor: 'pointer', fontFamily: 'inherit'
                  }}
                >
                  Save
                </button>
              </div>
            )}

            {/* Saved list */}
            <div style={{ maxHeight: '260px', overflowY: 'auto' }}>
              {bookmarks.length === 0 && (
                <div style={{ padding: '16px 13px', fontSize: '12px', color: onSurfaceFaint }}>
                  {hasFilters
                    ? 'No saved searches yet — name this one above.'
                    : 'No saved searches yet. Add some filters, then save them here.'}
                </div>
              )}
              {bookmarks.map(bm => (
                <div
                  key={bm.id}
                  onClick={() => applyBookmark(bm)}
                  style={{
                    padding: '10px 13px',
                    cursor: 'pointer',
                    display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', gap: '8px'
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = surfaceContainerHover}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: '13px', color: onSurfaceWarm }}>{bm.name}</div>
                    <div style={{
                      fontSize: '10.5px', color: onSurfaceFaint,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
                    }}>
                      {[
                        ...(bm.state.chips || []),
                        ...(bm.state.nlChips || []).map(n => `“${n.phrase}”`),
                        ...(bm.state.noteChips || []).map(p => `🔧 ${p}`),
                        ...(bm.state.film ? [`🎬 ${bm.state.film}`] : []),
                        ...(bm.state.ar ? [`▭ ${bm.state.ar}`] : []),
                        ...(bm.state.color ? [bm.state.color] : [])
                      ].join(' · ') || 'empty'}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                    {bm.state.color && (
                      <span style={{
                        width: '12px', height: '12px', borderRadius: '3px',
                        background: bm.state.color,
                        border: `1px solid ${withAlpha(white,0.15)}`
                      }} />
                    )}
                    <button
                      onClick={(e) => deleteBookmark(bm.id, e)}
                      style={{
                        background: 'none', border: 'none', color: onSurfaceFaint,
                        cursor: 'pointer', fontSize: '14px', padding: '2px'
                      }}
                      onMouseEnter={e => e.currentTarget.style.color = danger}
                      onMouseLeave={e => e.currentTarget.style.color = onSurfaceFaint}
                    >×</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
