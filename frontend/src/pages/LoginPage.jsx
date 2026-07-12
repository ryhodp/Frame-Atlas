import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext';

export default function LoginPage() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { refresh } = useAuth();
  const navigate = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Login failed');
        setBusy(false);
        return;
      }
      await refresh();
      navigate('/');
    } catch {
      setError('Could not reach the server — check your connection.');
      setBusy(false);
    }
  };

  return (
    <AuthShell title="Frame Atlas" subtitle="Sign in to your library">
      <form onSubmit={submit}>
        <FormField label="Username">
          <input
            autoFocus
            value={username}
            onChange={e => setUsername(e.target.value)}
            style={inputStyle}
          />
        </FormField>
        <FormField label="Password">
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            style={inputStyle}
          />
        </FormField>

        {error && <div style={errorStyle}>{error}</div>}

        <button type="submit" disabled={busy || !username || !password} style={submitStyle(busy || !username || !password)}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>

      <div style={{ marginTop: '20px', fontSize: '13px', color: '#8e9099', textAlign: 'center' }}>
        Have an invite code? <Link to="/register" style={{ color: '#d9a441' }}>Create an account</Link>
      </div>
    </AuthShell>
  );
}

export function AuthShell({ title, subtitle, children }) {
  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#0a0a0b', padding: '24px'
    }}>
      <div style={{
        width: 'min(380px, 100%)',
        background: '#1a1c20',
        border: '1px solid #44474f',
        borderRadius: '14px',
        padding: '32px 28px',
      }}>
        <div style={{ textAlign: 'center', marginBottom: '28px' }}>
          <div style={{ fontSize: '22px', fontWeight: 600, color: '#e2e2e6', marginBottom: '6px' }}>{title}</div>
          <div style={{ fontSize: '13px', color: '#8e9099' }}>{subtitle}</div>
        </div>
        {children}
      </div>
    </div>
  );
}

export function FormField({ label, children }) {
  return (
    <div style={{ marginBottom: '16px' }}>
      <label style={{ display: 'block', fontSize: '12px', color: '#9c988d', marginBottom: '6px' }}>
        {label}
      </label>
      {children}
    </div>
  );
}

export const inputStyle = {
  width: '100%',
  background: '#0a0a0b',
  color: '#e2e2e6',
  border: '1px solid #44474f',
  borderRadius: '8px',
  padding: '10px 12px',
  fontSize: '14px',
  fontFamily: 'inherit',
  outline: 'none',
  boxSizing: 'border-box',
};

export const errorStyle = {
  background: 'rgba(255,180,171,0.1)',
  border: '1px solid rgba(255,180,171,0.35)',
  color: '#ffb4ab',
  borderRadius: '8px',
  padding: '10px 12px',
  fontSize: '12.5px',
  marginBottom: '16px',
};

export function submitStyle(disabled) {
  return {
    width: '100%',
    background: disabled ? 'rgba(217,164,65,0.2)' : '#d9a441',
    color: disabled ? '#8e9099' : '#3d2f00',
    border: 'none',
    borderRadius: '8px',
    padding: '11px',
    fontSize: '14px',
    fontWeight: 600,
    cursor: disabled ? 'default' : 'pointer',
    fontFamily: 'inherit',
  };
}
