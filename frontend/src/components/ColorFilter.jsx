// ColorFilter — the colour swatch strip, the custom colour wheel, and the
// DOMINANCE / SHADE MATCH sliders that appear once a colour is picked
// (Day 45 / V93). The live photo count beside the sliders comes from the grid
// (`total`, `loading`), passed in by Home.
import { SWATCH_COLORS as PRESET_SWATCHES, danger, onSurfaceFaint, onSurfaceMuted, onSurfaceWarm, primary, white, withAlpha } from '../theme';
import { DEFAULT_PROM, DEFAULT_EXACT } from '../searchParams';

// V33: the slider runs 0.5%–95%, not 0.5%–40%. The old 40% ceiling was
// arbitrary — a real photo's biggest single color reaches 96% of the frame,
// and 16 of 19 of Ryan's reference shots have a color over 40%. Capping at 40
// meant "orange is the whole shot" was a question you literally could not ask.
// Log scale because most of the useful range sits low: a linear slider would
// bunch every meaningful setting into its first inch.
const PROM_MIN = 0.5, PROM_SPAN = 190;   // 0.5 * 190 = 95% at the top
const posToProm = (pos) => +(PROM_MIN * Math.pow(PROM_SPAN, pos / 100)).toFixed(1);
const promToPos = (p) => Math.round(100 * Math.log(p / PROM_MIN) / Math.log(PROM_SPAN));

// The slider asks "how much does this color OWN the frame", so it's labelled
// the way Ryan described it rather than as a bare percentage: low means a red
// shirt or red lipstick, high means a red backdrop. Above 50% the color is
// automatically the largest thing in frame — nothing else has room to beat it
// — which is why no separate "dominant color" toggle was needed.
const promLabel = (p) =>
  p < 3  ? 'a small accent' :
  p < 10 ? 'a noticeable part' :
  p < 25 ? 'a major element' :
  p < 50 ? 'most of the frame' :
           'fills the frame';

// V33: exactness now controls hue AND brightness together, because brown is
// not its own hue — brown IS dark orange. See EXACTNESS_TIGHT_VAL in app.py.
const exactLabel = (e) => (e < 25 ? 'very loose' : e < 50 ? 'loose' : e < 75 ? 'close' : 'exact');

export default function ColorFilter({ search, isMobile, total, loading }) {
  const { color, setColor, prom, setProm, exact, setExact, pickColor } = search;
  return (
    <>
      {/* Color swatch strip */}
      <div style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '7px', marginTop: '12px'
      }}>
        <span style={{
          fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em',
          color: onSurfaceFaint, marginRight: '2px'
        }}>COLOR</span>
        {PRESET_SWATCHES.map(hex => (
          <button
            key={hex}
            title={hex}
            onClick={() => pickColor(color === hex ? null : hex)}
            style={{
              width: '20px', height: '20px', borderRadius: '50%',
              background: hex,
              border: color === hex
                ? `2px solid ${onSurfaceWarm}`
                : `1px solid ${withAlpha(white,0.15)}`,
              cursor: 'pointer', padding: 0,
              transform: color === hex ? 'scale(1.15)' : 'scale(1)',
              transition: 'transform 0.12s ease'
            }}
          />
        ))}
        {/* Color wheel — custom pick, like Sidus Link */}
        <label
          title="Pick a custom color"
          style={{
            width: '20px', height: '20px', borderRadius: '50%',
            background: 'conic-gradient(red, yellow, lime, cyan, blue, magenta, red)',
            border: color && !PRESET_SWATCHES.includes(color)
              ? `2px solid ${onSurfaceWarm}`
              : `1px solid ${withAlpha(white,0.15)}`,
            cursor: 'pointer', position: 'relative', overflow: 'hidden',
            transform: color && !PRESET_SWATCHES.includes(color) ? 'scale(1.15)' : 'scale(1)',
            transition: 'transform 0.12s ease'
          }}
        >
          <input
            type="color"
            value={color || primary}
            onChange={e => pickColor(e.target.value)}
            style={{
              position: 'absolute', inset: 0, opacity: 0,
              width: '100%', height: '100%', cursor: 'pointer'
            }}
          />
        </label>
        {color && (
          <button
            onClick={() => setColor(null)}
            style={{
              background: 'none', border: 'none', color: onSurfaceFaint,
              cursor: 'pointer', fontSize: '11px', fontFamily: 'inherit',
              padding: '2px 4px'
            }}
            onMouseEnter={e => e.currentTarget.style.color = danger}
            onMouseLeave={e => e.currentTarget.style.color = onSurfaceFaint}
          >
            clear color
          </button>
        )}
      </div>

      {/* V24: dominance + shade-match sliders. Only meaningful with a color
          picked, so they stay out of the way until then. The live match
          count is the whole point — it turns "is 6% right?" into something
          you can see instead of guess.
          V33 renamed both: "coverage" read as a bare percentage nobody could
          picture, and "hue match" promised something hue alone cannot deliver
          (brown and orange are the same hue, so only brightness separates
          them). The words now describe what the knobs actually do. */}
      {color && (
        <div style={{
          display: 'flex', alignItems: 'center', flexWrap: 'wrap',
          gap: isMobile ? '10px' : '18px',
          marginTop: '10px', padding: '9px 12px',
          background: withAlpha(white,0.03),
          border: `1px solid ${withAlpha(white,0.07)}`,
          borderRadius: '7px'
        }}>
          {[
            {
              key: 'prom',
              label: 'DOMINANCE',
              value: `${promLabel(prom)} · ${prom}%`,
              pos: promToPos(prom),
              onChange: (pos) => setProm(posToProm(pos)),
              hint: 'How much this color owns the shot — drag left for an '
                  + 'accent like a red shirt, right for a red backdrop'
            },
            {
              key: 'exact',
              label: 'SHADE MATCH',
              value: exactLabel(exact),
              pos: exact,
              onChange: (pos) => setExact(pos),
              hint: 'How close the shade has to be to the one you picked, in '
                  + 'both color and brightness — drag right to stop dark '
                  + 'brown counting as orange'
            }
          ].map(s => (
            <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
              <span title={s.hint} style={{
                fontSize: '9.5px', fontWeight: 600, letterSpacing: '0.1em',
                color: onSurfaceFaint, cursor: 'help', whiteSpace: 'nowrap'
              }}>{s.label}</span>
              <input
                type="range"
                min="0" max="100" step="1"
                value={s.pos}
                onChange={e => s.onChange(Number(e.target.value))}
                aria-label={s.hint}
                style={{ width: isMobile ? '110px' : '130px', accentColor: primary, cursor: 'pointer' }}
              />
              <span style={{
                fontSize: '11.5px', color: onSurfaceMuted, whiteSpace: 'nowrap',
                minWidth: s.key === 'prom' ? '152px' : '62px'
              }}>{s.value}</span>
            </div>
          ))}

          <span style={{ fontSize: '11.5px', color: onSurfaceFaint, whiteSpace: 'nowrap' }}>
            {loading ? '…' : `${total} image${total === 1 ? '' : 's'}`}
          </span>

          {(prom !== DEFAULT_PROM || exact !== DEFAULT_EXACT) && (
            <button
              onClick={() => { setProm(DEFAULT_PROM); setExact(DEFAULT_EXACT); }}
              style={{
                background: 'none', border: 'none', color: onSurfaceFaint,
                cursor: 'pointer', fontSize: '11px', fontFamily: 'inherit', padding: '2px 4px'
              }}
              onMouseEnter={e => e.currentTarget.style.color = danger}
              onMouseLeave={e => e.currentTarget.style.color = onSurfaceFaint}
            >reset</button>
          )}
        </div>
      )}
    </>
  );
}
