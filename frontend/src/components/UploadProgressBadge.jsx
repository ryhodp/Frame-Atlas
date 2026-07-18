import { useEffect, useState, useRef } from 'react';

export default function UploadProgressBadge() {
  const [progress, setProgress] = useState(null); // {status, message, pct, done, total, running}
  const [dismissed, setDismissed] = useState(false);
  const eventSourceRef = useRef(null);

  useEffect(() => {
    // Don't set up SSE if dismissed
    if (dismissed) return;

    const es = new EventSource('/api/tag-progress/stream');
    eventSourceRef.current = es;

    es.addEventListener('message', (event) => {
      try {
        const data = JSON.parse(event.data);
        setProgress(data);
        // If tagging just finished, auto-undismiss for a fresh upload
        if (data.status === 'complete' || data.status === 'idle') {
          setDismissed(false);
        }
      } catch (e) {
        console.error('SSE parse error:', e);
      }
    });

    es.addEventListener('error', () => {
      es.close();
    });

    return () => {
      es.close();
      eventSourceRef.current = null;
    };
  }, [dismissed]);

  // Only show the badge if there's active work (running=true or has a message)
  const isActive = progress?.running || (progress?.status && progress.status !== 'idle');

  if (!isActive || dismissed) return null;

  let displayText = '';
  let displayIcon = '';
  let displayPercent = null;

  if (progress.status === 'tagging' || (progress.running && progress.status !== 'syncing')) {
    displayIcon = '⚡';
    displayText = 'Tagging images';
    displayPercent = progress.pct;
  } else if (progress.status === 'syncing') {
    displayIcon = '🔄';
    displayText = 'Syncing from Drive';
    displayPercent = progress.pct;
  } else if (progress.message?.includes('Connecting') || progress.message?.includes('Scanning')) {
    displayIcon = '🔄';
    displayText = 'Syncing from Drive';
    displayPercent = 0;
  } else if (progress.running) {
    displayIcon = '⚙';
    displayText = progress.message || 'Working…';
    displayPercent = progress.pct;
  }

  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: '8px',
      background: 'rgba(201,162,83,0.12)',
      border: '1px solid rgba(201,162,83,0.35)',
      borderRadius: '8px', padding: '8px 12px',
      fontSize: '12px', color: '#dcbd76',
      fontFamily: "'Hanken Grotesk', system-ui, sans-serif"
    }}>
      <span style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: '16px', height: '16px', fontSize: '13px'
      }}>
        {displayIcon === '⚡' ? (
          <span style={{
            width: '12px', height: '12px',
            border: '2px solid rgba(201,162,83,0.25)', borderTopColor: '#c9a253',
            borderRadius: '50%', display: 'inline-block',
            animation: 'spin 0.7s linear infinite'
          }} />
        ) : displayIcon === '🔄' ? (
          <span style={{
            display: 'inline-block',
            animation: 'spin 1.2s linear infinite',
            transformOrigin: 'center'
          }}>🔄</span>
        ) : (
          '⚙'
        )}
      </span>

      <span>{displayText}</span>

      {displayPercent !== null && displayPercent > 0 && (
        <span style={{ fontSize: '11px', color: '#c9a253' }}>
          {displayPercent}%
        </span>
      )}

      <button
        onClick={() => setDismissed(true)}
        title="Dismiss (work will continue in background)"
        style={{
          background: 'none', border: 'none', color: '#dcbd76',
          cursor: 'pointer', padding: '2px 4px', lineHeight: 1,
          opacity: 0.6, fontSize: '13px'
        }}
        onMouseEnter={e => e.currentTarget.style.opacity = '1'}
        onMouseLeave={e => e.currentTarget.style.opacity = '0.6'}
      >
        ×
      </button>
    </div>
  );
}
