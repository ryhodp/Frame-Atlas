import { useEffect, useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';

// Landing page for a "join this deck as a viewer" link (/invite/:token).
// Only reachable while logged in — App.jsx's Shell already requires a
// session before any route under here renders.
export default function InviteAcceptPage() {
  const { token } = useParams();
  const navigate = useNavigate();
  const [status, setStatus] = useState('working'); // working | error
  const [error, setError] = useState('');
  const [deckName, setDeckName] = useState('');

  useEffect(() => {
    fetch(`/api/decks/invite/${token}/accept`, { method: 'POST' })
      .then(async res => {
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Invite link not found or revoked.');
        setDeckName(data.name);
        setTimeout(() => navigate(`/decks/${data.deck_id}`, { replace: true }), 900);
      })
      .catch(err => {
        setStatus('error');
        setError(err.message);
      });
  }, [token, navigate]);

  return (
    <div style={{
      maxWidth: '440px', margin: '0 auto', padding: '80px 24px',
      textAlign: 'center', color: '#e2e2e6'
    }}>
      {status === 'working' ? (
        <>
          <div style={{ fontSize: '15px', marginBottom: '8px' }}>Joining {deckName || 'the lookbook'}…</div>
          <div style={{ fontSize: '12.5px', color: '#8e9099' }}>Taking you there now.</div>
        </>
      ) : (
        <>
          <div style={{ fontSize: '15px', color: '#ffb4ab', marginBottom: '8px' }}>
            Couldn't join that deck
          </div>
          <div style={{ fontSize: '12.5px', color: '#8e9099', marginBottom: '20px' }}>{error}</div>
          <Link to="/decks" style={{ color: '#d9a441', fontSize: '13px' }}>← Back to your decks</Link>
        </>
      )}
    </div>
  );
}
