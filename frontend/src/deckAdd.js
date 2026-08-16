// ── deckAdd — the one place "put these photos in that deck" is expressed ──────
// Frame Atlas V46.
//
// Three different screens now add photos to a deck: the Select Mode bottom bar
// (TagModeBar), the picker on the deck page (AddPhotosModal), and a single
// photo's detail panel (ImageDetail). Their LAYOUTS are genuinely different and
// should stay that way — a full-width panel, a modal footer, and a small
// popover want different markup. What must not fork is the behaviour: which
// endpoint, and what the response actually means.
//
// That second part is why this file exists at all. POST /api/decks/<id>/images
// returns {added, already_in_deck, invalid_ids} — it silently skips photos the
// deck already holds. TagModeBar ignored the response entirely and reported
// "Added 12 photos" whether it added 12, 1, or none, which is a lie the user has
// no way to check. describeAddResult() is the single honest reading of it.

/** Every deck the signed-in user can add to. Returns [] rather than throwing —
 *  a picker with no decks is a valid state (it offers to create one). */
export async function fetchDecks() {
  try {
    const res = await fetch('/api/decks');
    const data = await res.json();
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

/** Add image ids to an existing deck. Throws on failure so callers can keep the
 *  user's selection rather than silently pretending it worked. */
export async function addImagesToDeck(deckId, imageIds) {
  const res = await fetch(`/api/decks/${deckId}/images`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_ids: imageIds }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Could not add photos (HTTP ${res.status}).`);
  return data;
}

/** Create a deck and fill it in one go. Returns {deck, result}. */
export async function createDeckWithImages(name, imageIds) {
  const res = await fetch('/api/decks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  const deck = await res.json().catch(() => ({}));
  if (!res.ok || !deck?.id) throw new Error(deck?.error || 'Could not create that deck.');
  const result = await addImagesToDeck(deck.id, imageIds);
  return { deck, result };
}

/**
 * Plain-language reading of what the server actually did.
 *
 * `deckName` is optional — the deck page already says which deck you're on, so
 * naming it again there would just be noise.
 */
export function describeAddResult(result, deckName) {
  const added = result?.added || 0;
  const skipped = result?.already_in_deck || 0;
  const invalid = (result?.invalid_ids || []).length;
  const where = deckName ? ` to “${deckName}”` : '';

  if (added > 0) {
    let msg = `Added ${added} photo${added === 1 ? '' : 's'}${where}`;
    const notes = [];
    if (skipped) notes.push(`${skipped} already there`);
    if (invalid) notes.push(`${invalid} skipped`);
    if (notes.length) msg += ` · ${notes.join(', ')}`;
    return { tone: 'success', message: msg };
  }

  if (skipped > 0) {
    return {
      tone: 'info',
      message: `${skipped} photo${skipped === 1 ? ' was' : 's were'} already in this deck — nothing to add`,
    };
  }

  return { tone: 'error', message: 'Nothing was added.' };
}
