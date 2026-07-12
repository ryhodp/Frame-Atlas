import { useState } from 'react';
import { useAuth } from '../AuthContext';
import { AuthShell, FormField, inputStyle, errorStyle, submitStyle } from './LoginPage';

export default function SetupPage({ onDone }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { refresh } = useAuth();

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Setup failed');
        setBusy(false);
        return;
      }
      await refresh();
      onDone?.();
    } catch {
      setError('Could not reach the server — check your connection.');
      setBusy(false);
    }
  };

  return (
    <AuthShell title="Welcome to Frame Atlas" subtitle="This is a one-time step — set your admin login">
      <form onSubmit={submit}>
        <FormField label="Email">
          <input
            autoFocus
            type="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
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
          disabled={busy || !email || password.length < 8}
          style={submitStyle(busy || !email || password.length < 8)}
        >
          {busy ? 'Setting up…' : 'Create admin account'}
        </button>
      </form>

      <div style={{ marginTop: '18px', fontSize: '11.5px', color: '#65625a', textAlign: 'center' }}>
        Your existing library, decks, and favorites will be attached to this account.
        This screen only ever works once.
      </div>
    </AuthShell>
  );
}
