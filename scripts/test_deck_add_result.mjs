/**
 * Frame Atlas — local test for V46's describeAddResult().
 *
 * POST /api/decks/<id>/images silently skips photos the deck already holds and
 * reports {added, already_in_deck, invalid_ids}. Before V46 the Select Mode
 * panel ignored that response and always announced "Added N photos", where N
 * was what it ASKED for — so adding 12 photos that were already in the deck
 * said "Added 12". That is the kind of wrong that looks completely fine on
 * screen, which is why the message-building lives in a plain function and gets
 * tested here rather than by driving a page (same reasoning as
 * test_selection_range.mjs and test_presentation_order.mjs).
 *
 * The endpoint's own contract is pinned separately, in
 * scripts/test_decks_locally.py (checks 5 and 6).
 *
 * Usage (from the frame-atlas folder):
 *     node scripts/test_deck_add_result.mjs
 */

import { describeAddResult } from '../frontend/src/deckAdd.js';

let failures = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${label} — ${ok ? 'OK' : `FAIL\n      got  ${JSON.stringify(got)}\n      want ${JSON.stringify(want)}`}`);
  if (!ok) failures += 1;
}

console.log('Adding photos to a deck — what the user is told:');

check('a clean add names the deck',
  describeAddResult({ added: 3, already_in_deck: 0, invalid_ids: [] }, 'Wolves'),
  { tone: 'success', message: 'Added 3 photos to “Wolves”' });

check('one photo is singular',
  describeAddResult({ added: 1, already_in_deck: 0, invalid_ids: [] }, 'Wolves'),
  { tone: 'success', message: 'Added 1 photo to “Wolves”' });

check('no deck name given — the page already says which deck',
  describeAddResult({ added: 2, already_in_deck: 0, invalid_ids: [] }),
  { tone: 'success', message: 'Added 2 photos' });

// The bug this function exists for.
check('a partial add does NOT claim the whole selection',
  describeAddResult({ added: 4, already_in_deck: 8, invalid_ids: [] }, 'Wolves'),
  { tone: 'success', message: 'Added 4 photos to “Wolves” · 8 already there' });

check('nothing added because it was all already there',
  describeAddResult({ added: 0, already_in_deck: 12, invalid_ids: [] }, 'Wolves'),
  { tone: 'info', message: '12 photos were already in this deck — nothing to add' });

check('a single already-present photo reads as singular',
  describeAddResult({ added: 0, already_in_deck: 1, invalid_ids: [] }, 'Wolves'),
  { tone: 'info', message: '1 photo was already in this deck — nothing to add' });

check('ids the server rejected are surfaced, not hidden',
  describeAddResult({ added: 2, already_in_deck: 0, invalid_ids: [999, 1000] }, 'Wolves'),
  { tone: 'success', message: 'Added 2 photos to “Wolves” · 2 skipped' });

check('both kinds of shortfall at once',
  describeAddResult({ added: 1, already_in_deck: 3, invalid_ids: [999] }, 'Wolves'),
  { tone: 'success', message: 'Added 1 photo to “Wolves” · 3 already there, 1 skipped' });

// Degenerate shapes must never render "Added undefined photos".
check('an empty response is an error, not a silent success',
  describeAddResult({}, 'Wolves'),
  { tone: 'error', message: 'Nothing was added.' });

check('a missing response is an error too',
  describeAddResult(undefined),
  { tone: 'error', message: 'Nothing was added.' });

console.log();
if (failures) {
  console.log(`${failures} CHECK(S) FAILED`);
  process.exit(1);
}
console.log('ALL DECK-ADD RESULT TESTS PASSED');
