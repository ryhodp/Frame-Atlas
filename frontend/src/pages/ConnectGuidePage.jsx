import { Link } from 'react-router-dom';

// Placeholder image slots — drop real screenshots into
// frontend/public/guide-images/ with these exact filenames and they'll
// appear here automatically. Until then, each step shows a plain description
// instead so the guide is never broken.
const STEPS = [
  {
    n: 1,
    title: 'Click "Connect Google Drive"',
    body: "On the Account page, click the gold \"Connect Google Drive\" button. Your browser will jump to a Google sign-in page — this is normal, you're leaving Frame Atlas for a second to prove to Google it's really you.",
    image: '/guide-images/step1-connect-button.png'
  },
  {
    n: 2,
    title: 'Sign in with your Google account',
    body: 'Pick the Google account that owns the Drive folder full of your reference images (the same account you\'d use to open drive.google.com). If you\'re already signed in to Google in this browser, it may skip straight to the next step.',
    image: '/guide-images/step2-google-signin.png'
  },
  {
    n: 3,
    title: 'Approve the permission screen',
    body: 'Google will show a screen asking if it\'s okay for Frame Atlas to see your Drive files. Click "Continue" or "Allow." This only lets Frame Atlas read the one folder you choose in the next step — it can\'t see your email, your other files, or anything else in your Google account.',
    image: '/guide-images/step3-permission-screen.png'
  },
  {
    n: 4,
    title: 'You\'re back in Frame Atlas — now pick a folder',
    body: 'You\'ll land back on the Account page with a green "Google account connected" checkmark. Click "Choose folder" and pick the Drive folder with your inspiration images in it.',
    image: '/guide-images/step4-back-in-app.png'
  }
];

export default function ConnectGuidePage() {
  return (
    <div style={{ maxWidth: '640px', margin: '0 auto', padding: '40px 24px', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <Link to="/account" style={{ fontSize: '12.5px', color: '#c9a253', textDecoration: 'none' }}>
        ← Back to Account
      </Link>

      <h1 style={{ fontSize: '26px', fontWeight: 700, margin: '14px 0 6px' }}>
        Connecting Google Drive
      </h1>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 28px', lineHeight: 1.6 }}>
        A step-by-step walkthrough for linking your own Google Drive folder to Frame Atlas.
        This is a one-time setup — once it's connected, syncing new photos later is a single click.
      </p>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {STEPS.map(step => (
          <div key={step.n} style={{
            background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px'
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px' }}>
              <div style={{
                width: '24px', height: '24px', borderRadius: '50%', background: 'rgba(201,162,83,0.15)',
                border: '1px solid rgba(201,162,83,0.4)', color: '#c9a253', fontSize: '12px', fontWeight: 700,
                display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0
              }}>
                {step.n}
              </div>
              <div style={{ fontSize: '15px', fontWeight: 600 }}>{step.title}</div>
            </div>
            <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 12px', lineHeight: 1.6 }}>
              {step.body}
            </p>
            {/* Real screenshot slot — falls back to nothing (not a broken
                image icon) until a file actually exists at this path. */}
            <img
              src={step.image}
              alt=""
              style={{ width: '100%', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.08)', display: 'block' }}
              onError={e => { e.currentTarget.style.display = 'none'; }}
            />
          </div>
        ))}
      </div>

      <div style={{
        marginTop: '20px', padding: '14px 16px', background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)', borderRadius: '10px'
      }}>
        <p style={{ fontSize: '12.5px', color: '#9c988d', margin: 0, lineHeight: 1.6 }}>
          Still stuck? Message Ryan directly — screenshots of whatever screen you're on make it a lot
          faster to figure out what's going wrong.
        </p>
      </div>
    </div>
  );
}
