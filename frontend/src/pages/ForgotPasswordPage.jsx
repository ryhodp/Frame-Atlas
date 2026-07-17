import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AuthShell, FormField, inputStyle, errorStyle, submitStyle } from './LoginPage';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [resetUrl, setResetUrl] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || 'Could not find that account');
        setBusy(false);
        return;
      }
      setResetUrl(window.location.origin + data.reset_path);
    } catch {
      setError('Could not reach the server — check your connection.');
    }
    setBusy(false);
  };

  if (resetUrl) {
    return (
      <AuthShell title="Reset link ready" subtitle="This link works once and expires in 1 hour">
        <div style={{
          wordBreak: 'break-all', background: '#0a0a0b', border: '1px solid #44474f',
          borderRadius: '8px', padding: '12px', fontSize: '13px', marginBottom: '16px'
        }}>
          <a href={resetUrl} style={{ color: '#d9a441' }}>{resetUrl}</a>
        </div>
        <Link to="/login" style={{ display: 'block', textAlign: 'center', fontSize: '13px', color: '#8e9099' }}>
          Back to sign in
        </Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Forgot password" subtitle="Enter the email on your account">
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

        {error && <div style={errorStyle}>{error}</div>}

        <button type="submit" disabled={busy || !email} style={submitStyle(busy || !email)}>
          {busy ? 'Looking up account…' : 'Get reset link'}
        </button>
      </form>

      <div style={{ marginTop: '20px', fontSize: '13px', color: '#8e9099', textAlign: 'center' }}>
        <Link to="/login" style={{ color: '#d9a441' }}>Back to sign in</Link>
      </div>
    </AuthShell>
  );
}
