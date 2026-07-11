import { useEffect, useState } from 'react';

// ── Section wrapper — dark panel with an uppercase label ─────────────────────
function Panel({ label, children, style }) {
  return (
    <div style={{
      background: '#18181b',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '12px',
      padding: '18px 20px',
      ...style
    }}>
      <div style={{
        fontSize: '10px', fontWeight: 600, letterSpacing: '0.14em',
        color: '#65625a', marginBottom: '14px'
      }}>
        {label}
      </div>
      {children}
    </div>
  );
}

// ── Headline stat card ───────────────────────────────────────────────────────
function StatCard({ label, value, accent }) {
  return (
    <div style={{
      flex: 1, minWidth: '130px',
      background: '#18181b',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '12px',
      padding: '16px 18px'
    }}>
      <div style={{
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: '26px', fontWeight: 500,
        color: accent || '#efeadd', lineHeight: 1.1
      }}>
        {value}
      </div>
      <div style={{
        fontSize: '11px', color: '#9c988d', marginTop: '6px',
        letterSpacing: '0.04em'
      }}>
        {label}
      </div>
    </div>
  );
}

// ── Horizontal bar list — one category's tag counts ──────────────────────────
function BarList({ items, color, maxBars = 10 }) {
  const shown = items.slice(0, maxBars);
  const max = Math.max(...shown.map(i => i.count), 1);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
      {shown.map(item => (
        <div key={item.value} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <span style={{
            width: '110px', flexShrink: 0, textAlign: 'right',
            fontSize: '11.5px', color: '#c8c3b8',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'
          }}>
            {item.value}
          </span>
          <div style={{ flex: 1, height: '14px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{
              width: `${(item.count / max) * 100}%`,
              minWidth: '2px', height: '100%',
              background: `linear-gradient(90deg, ${color}55, ${color}cc)`,
              borderRadius: '3px'
            }} />
            <span style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '10px', color: '#9c988d', flexShrink: 0
            }}>
              {item.count}
            </span>
          </div>
        </div>
      ))}
      {shown.length === 0 && (
        <div style={{ fontSize: '12px', color: '#65625a' }}>No tags in this category yet.</div>
      )}
    </div>
  );
}

// ── Library growth — hand-built SVG area chart of cumulative image count ─────
function GrowthChart({ growth }) {
  if (!growth || growth.length === 0) {
    return <div style={{ fontSize: '12px', color: '#65625a' }}>No images yet — growth appears after your first sync.</div>;
  }

  const W = 800, H = 240;
  const PAD_L = 46, PAD_R = 16, PAD_T = 14, PAD_B = 30;
  const plotW = W - PAD_L - PAD_R;
  const plotH = H - PAD_T - PAD_B;
  const maxTotal = Math.max(...growth.map(g => g.total), 1);

  // Single month → center one dot instead of drawing a zero-width line
  const xFor = (i) => growth.length === 1
    ? PAD_L + plotW / 2
    : PAD_L + (i / (growth.length - 1)) * plotW;
  const yFor = (total) => PAD_T + plotH - (total / maxTotal) * plotH;

  const points = growth.map((g, i) => ({ x: xFor(i), y: yFor(g.total), ...g }));
  const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
  const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(1)} ${PAD_T + plotH} L ${points[0].x.toFixed(1)} ${PAD_T + plotH} Z`;

  // Round y-axis gridlines: 4 steps up to a rounded ceiling of maxTotal
  const yTicks = [0.25, 0.5, 0.75, 1].map(f => Math.round(maxTotal * f));

  // Month labels: show at most ~8 so they never collide
  const labelEvery = Math.max(1, Math.ceil(growth.length / 8));
  const monthLabel = (m) => {
    const [y, mo] = m.split('-');
    const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${names[parseInt(mo, 10) - 1]} ’${y.slice(2)}`;
  };

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
      <defs>
        <linearGradient id="growthFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#c9a253" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#c9a253" stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* Gridlines + y labels */}
      {yTicks.map(t => (
        <g key={t}>
          <line
            x1={PAD_L} x2={W - PAD_R} y1={yFor(t)} y2={yFor(t)}
            stroke="rgba(255,255,255,0.06)" strokeWidth="1"
          />
          <text
            x={PAD_L - 8} y={yFor(t) + 3} textAnchor="end"
            fontSize="10" fill="#65625a" fontFamily="'JetBrains Mono', monospace"
          >
            {t}
          </text>
        </g>
      ))}
      <line
        x1={PAD_L} x2={W - PAD_R} y1={PAD_T + plotH} y2={PAD_T + plotH}
        stroke="rgba(255,255,255,0.12)" strokeWidth="1"
      />

      {/* Area + line */}
      {growth.length > 1 && <path d={areaPath} fill="url(#growthFill)" />}
      {growth.length > 1 && (
        <path d={linePath} fill="none" stroke="#d9a441" strokeWidth="2" strokeLinejoin="round" />
      )}

      {/* Dots + month labels */}
      {points.map((p, i) => (
        <g key={p.month}>
          <circle cx={p.x} cy={p.y} r={growth.length === 1 ? 5 : 3} fill="#d9a441" />
          {(i % labelEvery === 0 || i === points.length - 1) && (
            <text
              x={p.x} y={PAD_T + plotH + 18} textAnchor="middle"
              fontSize="10" fill="#9c988d" fontFamily="'JetBrains Mono', monospace"
            >
              {monthLabel(p.month)}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}

// ── Tag frequency heatmap — chip intensity scales with how often you use it ──
function TagHeatmap({ categories, categoryLabels, maxChips = 40 }) {
  const all = [];
  Object.entries(categories || {}).forEach(([cat, items]) => {
    items.forEach(i => all.push({ ...i, category: cat }));
  });
  all.sort((a, b) => b.count - a.count);
  const shown = all.slice(0, maxChips);
  if (shown.length === 0) {
    return <div style={{ fontSize: '12px', color: '#65625a' }}>No tags yet — run auto-tagging first.</div>;
  }
  const max = shown[0].count;

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '7px' }}>
      {shown.map(t => {
        const heat = t.count / max; // 0..1
        const alpha = 0.07 + heat * 0.45;
        return (
          <span
            key={`${t.category}:${t.value}`}
            title={`${categoryLabels?.[t.category] || t.category} — used ${t.count}×`}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '7px',
              background: `rgba(201,162,83,${alpha.toFixed(3)})`,
              border: '1px solid rgba(201,162,83,0.25)',
              borderRadius: '6px',
              padding: '5px 9px',
              fontSize: `${11 + heat * 3}px`,
              color: heat > 0.55 ? '#f4e8cd' : '#d6c9a8'
            }}
          >
            {t.value}
            <span style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: '9px', color: 'rgba(239,234,221,0.45)'
            }}>
              {t.count}
            </span>
          </span>
        );
      })}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function AnalyticsPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch('/api/analytics')
      .then(res => res.json())
      .then(setData)
      .catch(err => { console.error('Analytics load failed', err); setError(true); });
  }, []);

  if (error) {
    return (
      <div style={{ padding: '60px 24px', textAlign: 'center', color: '#9c988d', fontSize: '14px' }}>
        Couldn’t load analytics — check your connection and refresh.
      </div>
    );
  }

  if (!data) {
    return (
      <div style={{ padding: '60px 24px', textAlign: 'center', color: '#65625a', fontSize: '13px' }}>
        Crunching the numbers…
      </div>
    );
  }

  const { totals, categories, category_labels: labels, category_colors: colors, growth } = data;

  // The four distributions Day 13 calls out by name
  const DIST_PANELS = [
    { cat: 'mood', label: 'MOOD DISTRIBUTION' },
    { cat: 'location_type', label: 'LOCATION SPREAD' },
    { cat: 'time_of_day_weather', label: 'TIME OF DAY & WEATHER' },
    { cat: 'source_type', label: 'SOURCE TYPE BREAKDOWN' },
  ];

  return (
    <div style={{
      maxWidth: '1200px', margin: '0 auto', padding: '28px 24px 60px',
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif"
    }}>
      <h2 style={{ fontSize: '24px', fontWeight: 600, color: '#efeadd', margin: '0 0 4px' }}>
        Analytics
      </h2>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 24px' }}>
        What’s in your library — and what your eye gravitates toward.
      </p>

      {/* Headline stats */}
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '16px' }}>
        <StatCard label="Images" value={totals.images} />
        <StatCard label="Favorites" value={totals.favorites} accent="#dcbd76" />
        <StatCard label="Flagged" value={totals.flagged} accent={totals.flagged > 0 ? '#cf7152' : undefined} />
        <StatCard label="Added this week" value={totals.added_last_7_days} />
        <StatCard label="Tags applied" value={totals.tags} />
        <StatCard label="Decks" value={totals.decks} />
      </div>

      {/* Growth */}
      <Panel label="LIBRARY GROWTH — TOTAL IMAGES OVER TIME" style={{ marginBottom: '16px' }}>
        <GrowthChart growth={growth} />
      </Panel>

      {/* Heatmap */}
      <Panel label="TAG FREQUENCY HEATMAP — BIGGER & BRIGHTER = USED MORE" style={{ marginBottom: '16px' }}>
        <TagHeatmap categories={categories} categoryLabels={labels} />
      </Panel>

      {/* The four named distributions */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))',
        gap: '16px'
      }}>
        {DIST_PANELS.map(p => (
          <Panel key={p.cat} label={p.label}>
            <BarList
              items={categories?.[p.cat] || []}
              color={colors?.[p.cat] || '#c9a253'}
            />
          </Panel>
        ))}
      </div>
    </div>
  );
}
