import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext';
import { AuthShell, FormField, inputStyle, errorStyle, submitStyle } from './LoginPage';

export default function RegisterPage() {
  const [searchParams] = useSearchParams();
  const [inviteCode, setInviteCode] = useState(searchParams.get('code') || '');
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
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invite_code: inviteCode.trim(), username, password })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Could not create account');
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
    <AuthShell title="Create your account" subtitle="You'll need an invite code from the library owner">
      <form onSubmit={submit}>
        <FormField label="Invite code">
          <input
            autoFocus
            value={inviteCode}
            onChange={e => setInviteCode(e.target.value)}
            style={inputStyle}
          />
        </FormField>
        <FormField label="Username">
          <input
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
          <div style={{ fontSize: '11px', color: '#65625a', marginTop: '5px' }}>At least 8 characters</div>
        </FormField>

        {error && <div style={errorStyle}>{error}</div>}

        <button
          type="submit"
          disabled={busy || !inviteCode.trim() || !username || password.length < 8}
          style={submitStyle(busy || !inviteCode.trim() || !username || password.length < 8)}
        >
          {busy ? 'Creating account…' : 'Create account'}
        </button>
      </form>

      <div style={{ marginTop: '20px', fontSize: '13px', color: '#8e9099', textAlign: 'center' }}>
        Already have an account? <Link to="/login" style={{ color: '#d9a441' }}>Sign in</Link>
      </div>
    </AuthShell>
  );
}
