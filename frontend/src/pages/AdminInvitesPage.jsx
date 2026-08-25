import { useEffect, useState } from 'react';
import { error as errorColor, onPrimary, onSurface, onSurfaceFaint, onSurfaceMuted, outline, outlineVariant, primary, surfaceContainerLow, white, withAlpha } from '../theme';

export default function AdminInvitesPage() {
  const [codes, setCodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [error, setError] = useState('');

  const load = () => {
    setLoading(true);
    fetch('/api/admin/invite-codes')
      .then(res => res.json())
      .then(data => setCodes(Array.isArray(data) ? data : []))
      .catch(() => setError('Could not load invite codes'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const createCode = async () => {
    setCreating(true);
    setError('');
    try {
      const res = await fetch('/api/admin/invite-codes', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Could not create invite code');
      } else {
        setCodes(prev => [{ id: data.id, code: data.code, created_at: new Date().toISOString(), used_at: null, used_by_username: null }, ...prev]);
      }
    } catch {
      setError('Could not reach the server');
    }
    setCreating(false);
  };

  const revoke = async (id) => {
    try {
      await fetch(`/api/admin/invite-codes/${id}`, { method: 'DELETE' });
      setCodes(prev => prev.filter(c => c.id !== id));
    } catch {
      setError('Could not revoke that code');
    }
  };

  const copy = (code, id) => {
    const url = `${window.location.origin}/register?code=${encodeURIComponent(code)}`;
    navigator.clipboard?.writeText(url);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  return (
    <div style={{ maxWidth: '900px', margin: '0 auto', padding: '32px 24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
        <h1 style={{ fontSize: '28px', fontWeight: 700, color: onSurface, margin: 0 }}>Invite Friends</h1>
        <button
          onClick={createCode}
          disabled={creating}
          style={{
            background: primary, color: onPrimary, border: 'none', borderRadius: '8px',
            padding: '10px 18px', fontSize: '14px', fontWeight: 600,
            cursor: creating ? 'default' : 'pointer', fontFamily: 'inherit',
            opacity: creating ? 0.6 : 1,
          }}
        >
          {creating ? 'Generating…' : '+ New invite code'}
        </button>
      </div>
      <div style={{ fontSize: '13px', color: outline, marginBottom: '24px' }}>
        Each code works once. Send the link to one friend — once they create an account with it, the code is used up.
      </div>

      {error && (
        <div style={{
          background: withAlpha(errorColor,0.1), border: `1px solid ${withAlpha(errorColor,0.35)}`,
          color: errorColor, borderRadius: '8px', padding: '10px 12px', fontSize: '12.5px', marginBottom: '16px'
        }}>{error}</div>
      )}

      {loading ? (
        <div style={{ color: outline, fontSize: '14px' }}>Loading…</div>
      ) : codes.length === 0 ? (
        <div style={{
          color: outline, fontSize: '14px', background: surfaceContainerLow, border: `1px solid ${outlineVariant}`,
          borderRadius: '12px', padding: '32px', textAlign: 'center'
        }}>
          No invite codes yet. Generate one above to invite a friend.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {codes.map(c => (
            <div key={c.id} style={{
              background: surfaceContainerLow, border: `1px solid ${outlineVariant}`, borderRadius: '10px',
              padding: '14px 16px', display: 'flex', alignItems: 'center', gap: '14px'
            }}>
              <code style={{
                fontSize: '13px', color: c.used_at ? onSurfaceFaint : onSurface,
                textDecoration: c.used_at ? 'line-through' : 'none', flex: 1,
                fontFamily: 'monospace',
              }}>
                {c.code}
              </code>

              {c.used_at ? (
                <span style={{ fontSize: '12px', color: outline }}>
                  used by {c.used_by_username || 'someone'}
                </span>
              ) : (
                <>
                  <button
                    onClick={() => copy(c.code, c.id)}
                    style={{
                      background: 'none', border: `1px solid ${withAlpha(primary,0.4)}`, color: primary,
                      borderRadius: '6px', padding: '6px 12px', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit'
                    }}
                  >
                    {copiedId === c.id ? 'Copied ✓' : 'Copy link'}
                  </button>
                  <button
                    onClick={() => revoke(c.id)}
                    title="Revoke this code"
                    style={{
                      background: 'none', border: `1px solid ${withAlpha(white,0.12)}`, color: onSurfaceMuted,
                      borderRadius: '6px', padding: '6px 10px', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit'
                    }}
                  >
                    Revoke
                  </button>
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
