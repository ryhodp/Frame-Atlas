// SearchBox — the search bar's typing box, its describe-it error line and its
// suggestions dropdown (Day 45 / V93).
//
// Three separate exports, not one component, on purpose: in the page these
// three sit in three different places (the error line ends the top row,
// AFTER the bookmarks button; the dropdown sits below the whole row), and
// keeping the page's structure identical was the rule for this move. Home
// places each one exactly where its JSX used to be.
//
// All state and behaviour come from useSearch() — passed in whole as `search`.
import { accentBlueLight, accentOrange, accentTeal, accentViolet, black, error, onSurfaceFaint, onSurfaceWarm, surfaceContainerDark, surfaceContainerHover, white, withAlpha } from '../theme';

const FILM_FIELD_LABELS = { title: 'Title', director: 'Director', dp: 'DP', painter: 'Painter', photographer: 'Photographer' };

// The typing box + the "interpreting…" spinner shown while Gemini reads a phrase.
export function SearchInput({ search }) {
  const { searchText, setSearchText, autocomplete, setShowAuto, interpreting, nlError, setNlError, searchRef, handleSearchKeyDown } = search;
  return (
    <>
      {/* Input */}
      <div style={{
        flex: 1,
        minWidth: 0,
        display: 'flex', alignItems: 'center', gap: '12px',
        background: surfaceContainerDark,
        border: `1px solid ${withAlpha(white,0.12)}`,
        borderRadius: '10px',
        padding: '0 14px',
        height: '46px'
      }}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
          stroke={withAlpha(white,0.3)} strokeWidth="2">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <input
          ref={searchRef}
          value={searchText}
          onChange={e => { setSearchText(e.target.value); if (nlError) setNlError(''); }}
          onKeyDown={handleSearchKeyDown}
          onFocus={() => { if (autocomplete.length) setShowAuto(true); }}
          placeholder="Search tags — or describe a feeling and press Enter…"
          disabled={interpreting}
          style={{
            flex: 1, background: 'transparent', border: 'none', outline: 'none',
            color: onSurfaceWarm, fontFamily: 'inherit', fontSize: '14px'
          }}
        />
        {interpreting && (
          <span style={{
            fontSize: '11px', color: accentViolet,
            display: 'flex', alignItems: 'center', gap: '6px'
          }}>
            <span style={{
              width: '10px', height: '10px',
              border: `2px solid ${withAlpha(accentViolet,0.25)}`,
              borderTopColor: accentViolet,
              borderRadius: '50%', display: 'inline-block',
              animation: 'spin 0.7s linear infinite'
            }} />
            interpreting…
          </span>
        )}
      </div>
    </>
  );
}

// The describe-it error line (e.g. no Gemini key saved).
export function SearchError({ search }) {
  const { nlError } = search;
  return (
    <>
      {nlError && (
        <p style={{ fontSize: '12px', color: error, margin: '8px 0 0' }}>
          {nlError}
        </p>
      )}
    </>
  );
}

// The MATCHES dropdown: tags, films, aspect ratios, on-set notes.
export function AutocompleteDropdown({ search }) {
  const { autocomplete, showAuto, highlightedIndex, setHighlightedIndex, addChip, selectFilm, selectAr, selectNote } = search;
  return (
    <>
      {/* Autocomplete dropdown */}
      {showAuto && autocomplete.length > 0 && (
        <div style={{
          position: 'absolute', top: '68px', left: '20px', right: '74px',
          background: surfaceContainerDark,
          border: `1px solid ${withAlpha(white,0.12)}`,
          borderRadius: '10px',
          boxShadow: `0 20px 48px ${withAlpha(black,0.6)}`,
          maxHeight: '320px', overflowY: 'auto',
          zIndex: 50,
          animation: 'fapop 0.12s ease'
        }}>
          <div style={{
            padding: '10px 13px 6px',
            fontSize: '9.5px', fontWeight: 600,
            letterSpacing: '0.12em', color: onSurfaceFaint
          }}>
            MATCHES
          </div>
          {autocomplete.map((opt, i) => (
            <button
              key={`${opt.type}-${opt.value}`}
              onMouseDown={() => {
                if (opt.type === 'film') selectFilm(opt.value);
                else if (opt.type === 'ar') selectAr(opt.value);
                else if (opt.type === 'note') selectNote(opt.value);
                else addChip(opt.value);
              }}
              onMouseEnter={() => setHighlightedIndex(i)}
              style={{
                width: '100%', display: 'flex', alignItems: 'center',
                justifyContent: 'space-between', gap: '10px',
                padding: '8px 13px',
                background: i === highlightedIndex ? surfaceContainerHover : 'transparent',
                border: 'none',
                cursor: 'pointer', textAlign: 'left', fontFamily: 'inherit'
              }}
            >
              {opt.type === 'film' ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '11px', flexShrink: 0 }}>🎬</span>
                  <span style={{ fontSize: '13.5px', color: accentBlueLight }}>{opt.value}</span>
                  <span style={{ fontSize: '11px', color: onSurfaceFaint }}>
                    {FILM_FIELD_LABELS[opt.field] || opt.field}
                  </span>
                </span>
              ) : opt.type === 'ar' ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '11px', flexShrink: 0 }}>▭</span>
                  <span style={{ fontSize: '13.5px', color: accentTeal }}>{opt.value}</span>
                  <span style={{ fontSize: '11px', color: onSurfaceFaint }}>Aspect Ratio</span>
                </span>
              ) : opt.type === 'note' ? (
                <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '11px', flexShrink: 0 }}>🔧</span>
                  <span style={{ fontSize: '13.5px', color: accentOrange }}>{opt.value}</span>
                  <span style={{ fontSize: '11px', color: onSurfaceFaint }}>On-Set Notes</span>
                </span>
              ) : (
                <span style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{
                    width: '7px', height: '7px', borderRadius: '2px',
                    background: opt.color, flexShrink: 0
                  }} />
                  <span style={{ fontSize: '13.5px', color: onSurfaceWarm }}>{opt.value}</span>
                  <span style={{ fontSize: '11px', color: onSurfaceFaint }}>{opt.catLabel}</span>
                </span>
              )}
              <span style={{
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: '10px', color: onSurfaceFaint
              }}>{opt.count}</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}
