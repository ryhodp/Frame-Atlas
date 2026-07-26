const DEFAULT_BASE_URL = 'https://frame-atlas-production.up.railway.app';

const urlInput = document.getElementById('url');
const status = document.getElementById('status');

const setStatus = (text, isError) => {
  status.textContent = text;
  status.className = isError ? 'err' : '';
};

chrome.storage.sync.get('baseUrl').then(({ baseUrl }) => {
  urlInput.value = baseUrl || DEFAULT_BASE_URL;
});

document.getElementById('save').addEventListener('click', async () => {
  const raw = urlInput.value.trim().replace(/\/+$/, '');
  if (!raw) {
    await chrome.storage.sync.set({ baseUrl: DEFAULT_BASE_URL });
    urlInput.value = DEFAULT_BASE_URL;
    setStatus('Reset to the default address.');
    return;
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    setStatus('That doesn\'t look like a web address.', true);
    return;
  }
  // Cookies for a Frame Atlas session only exist on http/https origins, and
  // anything else would fail confusingly at clip time instead of here.
  if (!/^https?:$/.test(parsed.protocol)) {
    setStatus('Address must start with http:// or https://', true);
    return;
  }

  await chrome.storage.sync.set({ baseUrl: raw });
  setStatus('Saved.');
});
