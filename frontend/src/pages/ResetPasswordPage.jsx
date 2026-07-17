import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { AuthShell, FormField, inputStyle, errorStyle, submitStyle } from './LoginPage';

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get('token') || '';
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const navigate = useNavigate();

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (password !== confirm) {
      setError('Passwords don\'t match');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Could not reset password');
        setBusy(false);
        return;
      }
      setDone(true);
      setTimeout(() => navigate('/login'), 2000);
    } catch {
      setError('Could not reach the server — check your connection.');
      setBusy(false);
    }
  };

  if (!token) {
    return (
      <AuthShell title="Reset password" subtitle="This link is missing its token">
        <div style={errorStyle}>This reset link looks broken. Request a new one.</div>
        <Link to="/forgot-password" style={{ color: '#d9a441', fontSize: '13px' }}>Request a new link</Link>
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell title="Password updated" subtitle="Taking you to sign in…">
        <Link to="/login" style={{ color: '#d9a441', fontSize: '13px' }}>Sign in now</Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Choose a new password" subtitle="At least 8 characters">
      <form onSubmit={submit}>
        <FormField label="New password">
          <input
            autoFocus
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            style={inputStyle}
          />
        </FormField>
        <FormField label="Confirm password">
          <input
            type="password"
            value={confirm}
            onChange={e => setConfirm(e.target.value)}
            style={inputStyle}
          />
        </FormField>

        {error && <div style={errorStyle}>{error}</div>}

        <button
          type="submit"
          disabled={busy || password.length < 8 || !confirm}
          style={submitStyle(busy || password.length < 8 || !confirm)}
        >
          {busy ? 'Saving…' : 'Save new password'}
        </button>
      </form>
    </AuthShell>
  );
}
