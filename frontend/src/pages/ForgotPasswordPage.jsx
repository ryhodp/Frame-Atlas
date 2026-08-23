import { useState } from 'react';
import { Link } from 'react-router-dom';
import { AuthShell, FormField, inputStyle, errorStyle, submitStyle } from './LoginPage';
import { surfaceContainerLowest, outlineVariant, primary, outline } from '../theme';

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
          wordBreak: 'break-all', background: surfaceContainerLowest, border: `1px solid ${outlineVariant}`,
          borderRadius: '8px', padding: '12px', fontSize: '13px', marginBottom: '16px'
        }}>
          <a href={resetUrl} style={{ color: primary }}>{resetUrl}</a>
        </div>
        <Link to="/login" style={{ display: 'block', textAlign: 'center', fontSize: '13px', color: outline }}>
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

      <div style={{ marginTop: '20px', fontSize: '13px', color: outline, textAlign: 'center' }}>
        <Link to="/login" style={{ color: primary }}>Back to sign in</Link>
      </div>
    </AuthShell>
  );
}
