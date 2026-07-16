import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

// V17: the guide now walks through the "share your folder with the robot
// email" flow (how personal libraries actually connect). The old Google
// sign-in walkthrough survives at the bottom — that flow still exists, but
// only for the optional Upload button.
//
// Screenshot slots: drop real images into frontend/public/guide-images/
// with these exact filenames and they'll appear automatically. Until then,
// each step shows its text description only — never a broken-image icon.
const SHARE_STEPS = [
  {
    n: 1,
    title: 'Find your images folder in Google Drive',
    body: 'Go to drive.google.com and find the folder that holds your reference images. Everything inside it (including subfolders) will come into Frame Atlas, so make sure it only holds images you want in your library.',
    image: '/guide-images/share-step1-find-folder.png'
  },
  {
    n: 2,
    title: 'Share the folder with the Frame Atlas robot email',
    body: 'Right-click the folder → Share → Share. In the "Add people" box, paste the robot email (it\'s shown on your Account page with a Copy button — it looks like a long address ending in .iam.gserviceaccount.com). Set it to "Viewer" and click Send. Viewer means Frame Atlas can only ever look at the folder — it can\'t change or delete anything.',
    image: '/guide-images/share-step2-share-dialog.png'
  },
  {
    n: 3,
    title: 'Copy the folder\'s link',
    body: 'Open the folder (double-click it) and copy the web address from your browser\'s address bar — it looks like drive.google.com/drive/folders/ followed by a long code. Right-click → Share → Copy link works too.',
    image: '/guide-images/share-step3-copy-link.png'
  },
  {
    n: 4,
    title: 'Paste the link into Frame Atlas',
    body: 'Back on your Account page, paste the link into Step 2 and click Connect. Frame Atlas checks it can see the folder and tells you how many images it found. Then hit Sync Now — that\'s it.',
    image: '/guide-images/share-step4-paste-link.png'
  }
];

const UPLOAD_STEPS = [
  {
    n: 1,
    title: 'Click "Connect Google Drive" in the Uploads section',
    body: 'On the Account page, scroll to the "Optional · Uploads" card and click "Connect Google Drive". Your browser will jump to a Google sign-in page.',
    image: '/guide-images/step1-connect-button.png'
  },
  {
    n: 2,
    title: 'Sign in with your Google account',
    body: 'Pick the Google account that owns your Drive folder (the same account you\'d use to open drive.google.com).',
    image: '/guide-images/step2-google-signin.png'
  },
  {
    n: 3,
    title: 'Approve the permission screen',
    body: 'Google asks if it\'s okay for Frame Atlas to manage files it creates. Click "Continue" or "Allow." This permission only covers files Frame Atlas itself uploads — it cannot see your existing files, email, or anything else.',
    image: '/guide-images/step3-permission-screen.png'
  },
  {
    n: 4,
    title: 'You\'re done — the Upload button now works',
    body: 'You\'ll land back on the Account page with a green "Google account connected" checkmark. The ⬆ Upload button on the home page can now add images from this device straight into your Drive folder and library.',
    image: '/guide-images/step4-back-in-app.png'
  }
];

function StepCard({ step }) {
  return (
    <div style={{ background: '#1a1c20', border: '1px solid #44474f', borderRadius: '12px', padding: '20px' }}>
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
      <img
        src={step.image}
        alt=""
        style={{ width: '100%', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.08)', display: 'block' }}
        onError={e => { e.currentTarget.style.display = 'none'; }}
      />
    </div>
  );
}

export default function ConnectGuidePage() {
  const [robotEmail, setRobotEmail] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    fetch('/api/account/setup-status')
      .then(r => r.json())
      .then(d => setRobotEmail(d.service_account_email))
      .catch(() => {});
  }, []);

  const copyEmail = async () => {
    if (!robotEmail) return;
    try {
      await navigator.clipboard.writeText(robotEmail);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  };

  return (
    <div style={{ maxWidth: '640px', margin: '0 auto', padding: '40px 24px', fontFamily: "'Hanken Grotesk', system-ui, sans-serif", color: '#efeadd' }}>
      <Link to="/account" style={{ fontSize: '12.5px', color: '#c9a253', textDecoration: 'none' }}>
        ← Back to Account
      </Link>

      <h1 style={{ fontSize: '26px', fontWeight: 700, margin: '14px 0 6px' }}>
        Connecting your Drive folder
      </h1>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 20px', lineHeight: 1.6 }}>
        A one-time, two-minute setup: you share your folder with Frame Atlas's robot
        account (like sharing with a friend, except the friend is an app), then paste
        the folder's link. After that, syncing new photos is a single click.
      </p>

      {robotEmail && (
        <div style={{
          display: 'flex', gap: '8px', alignItems: 'stretch', marginBottom: '24px',
          padding: '14px 16px', background: 'rgba(201,162,83,0.06)',
          border: '1px solid rgba(201,162,83,0.25)', borderRadius: '10px'
        }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em', color: '#65625a', marginBottom: '6px' }}>
              THE ROBOT EMAIL (FOR STEP 2)
            </div>
            <code style={{ fontSize: '12px', color: '#dcbd76', fontFamily: "'JetBrains Mono', monospace", overflowWrap: 'anywhere' }}>
              {robotEmail}
            </code>
          </div>
          <button onClick={copyEmail} style={{
            background: 'none', border: '1px solid rgba(217,164,65,0.4)', color: '#d9a441',
            borderRadius: '8px', padding: '9px 16px', fontSize: '13px', cursor: 'pointer',
            fontFamily: 'inherit', alignSelf: 'center'
          }}>
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {SHARE_STEPS.map(step => <StepCard key={step.n} step={step} />)}
      </div>

      <h2 style={{ fontSize: '19px', fontWeight: 700, margin: '36px 0 6px' }}>
        Optional: connect Google for uploads
      </h2>
      <p style={{ fontSize: '13px', color: '#9c988d', margin: '0 0 20px', lineHeight: 1.6 }}>
        Totally separate from syncing — only needed if you want the ⬆ Upload button,
        which adds single images from your phone or computer straight into your folder.
      </p>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
        {UPLOAD_STEPS.map(step => <StepCard key={step.n} step={step} />)}
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
