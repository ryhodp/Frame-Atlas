import { useRef, useState } from 'react';

const AUTOCOMPLETE_FIELDS = ['title', 'director', 'dp', 'painter', 'photographer'];
const EMPTY_FILM_DRAFT = { title: '', director: '', dp: '', year: '', painter: '', photographer: '' };

function emptyPerField(value) {
  return Object.fromEntries(AUTOCOMPLETE_FIELDS.map(f => [f, value]));
}

// Shared filmography-editing state + Obsidian-style autocomplete (type,
// arrow keys, Enter) for the 5 fields that support it (title/director/dp/
// painter/photographer — Year is plain text everywhere and never
// autocompletes). Used by BOTH the single-photo editor (ImageDetail.jsx)
// and the bulk Select Mode panel (TagModeBar.jsx) so the two literally
// cannot drift apart — before this hook existed, TagModeBar's boxes had no
// autocomplete at all while ImageDetail's did, which is exactly the
// inconsistency this was built to close.
export function useFilmAutocomplete(initialDraft = EMPTY_FILM_DRAFT) {
  const [draft, setDraft] = useState(initialDraft);
  const [suggestions, setSuggestions] = useState(() => emptyPerField([]));
  const [focused, setFocused] = useState(null); // 'title' | 'director' | 'dp' | 'painter' | 'photographer' | null
  const [highlight, setHighlight] = useState(-1); // index within the focused field's suggestion list
  // "User just picked a suggestion, don't reopen the dropdown until they
  // type again" — separate from `suggestions` so a picked value with 0
  // fresh matches doesn't fall through to a confusing "No X yet" placeholder
  // right after a successful pick.
  const [dismissed, setDismissed] = useState(() => emptyPerField(false));
  // Per-field request counter — typing fast fires one fetch per keystroke,
  // and network timing can land an older response (e.g. for "R") after a
  // newer one ("Ry"), silently blanking out correct suggestions with stale
  // ones. Only the response matching the latest-fired request applies.
  const requestIdRef = useRef(emptyPerField(0));
  // Set the moment the user types or picks anything, cleared only by
  // resetDraft(). A ref, not state — a caller that wants to autofill this
  // draft from elsewhere (TagModeBar's bulk-selection consensus) needs to
  // check the CURRENT value at the moment its own async response lands,
  // which can be well after the render that kicked off that fetch; a
  // useState flag read through that stale closure would still report the
  // old value. Exists so a background refresh can never silently overwrite
  // something the user already typed or picked.
  const touchedRef = useRef(false);
  // Which title string most recently triggered the exact-match autofill
  // below — guards against re-clobbering a Director/DP/Year the user
  // deliberately edited AFTER the autofill fired. Without this, retyping
  // (or even just blurring/refocusing) a title that still resolves to the
  // same exact match would re-fire the fill and overwrite a correction the
  // user just made on purpose (e.g. "this print's DP was actually
  // different"). Only a genuinely NEW exact match re-triggers it.
  const lastAutoFilledTitleRef = useRef(null);

  const setField = (field, value) => setDraft(prev => ({ ...prev, [field]: value }));

  // Shared by pickSuggestion (explicit click/Enter) and the exact-match
  // autofill below (typed the full correct title, no dropdown interaction
  // needed) — only overwrites a sibling the record actually has a value
  // for, so a title with no recorded DP never blanks out one already
  // typed in by hand.
  const applyTitleSiblings = (prevDraft, source) => {
    const next = { ...prevDraft };
    for (const sibling of ['director', 'dp', 'year', 'painter', 'photographer']) {
      if (source[sibling]) next[sibling] = source[sibling];
    }
    return next;
  };

  const fetchSuggestions = async (field, value) => {
    setDismissed(prev => ({ ...prev, [field]: false })); // typing reopens the dropdown
    const requestId = ++requestIdRef.current[field];
    if (!value || value.length < 1) {
      setSuggestions(prev => ({ ...prev, [field]: [] }));
      setHighlight(-1);
      return;
    }
    try {
      const res = await fetch(`/api/filmography/autocomplete?field=${field}&q=${encodeURIComponent(value)}`);
      const data = await res.json();
      if (requestIdRef.current[field] !== requestId) return; // a newer request superseded this one
      setSuggestions(prev => ({ ...prev, [field]: data }));
      setHighlight(-1);

      // Title autofill without ever touching the dropdown: correcting a
      // Gemini mislabel by typing the real title out in full (rather than
      // picking it from the list) should still pull in that title's
      // already-recorded Director/DP/Year — "I change the title, I also
      // want it to change the director, DP and year... to correct
      // Gemini's work." Fires once per newly-typed exact match, not on
      // every keystroke of an already-matched title.
      if (field === 'title') {
        const typed = value.trim().toLowerCase();
        const exact = data.find(s => s.value.toLowerCase() === typed);
        if (exact && lastAutoFilledTitleRef.current !== typed) {
          lastAutoFilledTitleRef.current = typed;
          setDraft(prev => applyTitleSiblings(prev, exact));
        }
      }
    } catch (err) {
      console.error('Filmography autocomplete failed:', err);
    }
  };

  // The one function every input's onChange calls: updates the draft AND
  // kicks off the autocomplete fetch for that field, in one place.
  const onFieldChange = (field, value) => {
    touchedRef.current = true;
    setField(field, value);
    fetchSuggestions(field, value);
  };

  // Picking a Title suggestion also fills in whatever Director/DP/Year/
  // Painter/Photographer that title already had recorded elsewhere — "if
  // the data for that movie already exists... those fields would
  // autopopulate, and then I can change them if it's different." Only
  // overwrites a sibling field the suggestion actually has a value for, so
  // a title with no recorded DP (say) never blanks out a DP already typed
  // in by hand.
  const pickSuggestion = (field, suggestion) => {
    touchedRef.current = true;
    if (field === 'title') lastAutoFilledTitleRef.current = suggestion.value.trim().toLowerCase();
    setDraft(prev => {
      const withValue = { ...prev, [field]: suggestion.value };
      return field === 'title' ? applyTitleSiblings(withValue, suggestion) : withValue;
    });
    setDismissed(prev => ({ ...prev, [field]: true }));
    setHighlight(-1);
  };

  // Arrow keys navigate the focused field's dropdown; Enter picks the
  // highlighted suggestion and finishes the field (blurs it) rather than
  // leaving the cursor sitting there looking unfinished.
  const handleKeyDown = (e, field) => {
    const current = suggestions[field] || [];
    if (!current.length || dismissed[field]) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlight(prev => (prev + 1) % current.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight(prev => (prev - 1 + current.length) % current.length);
    } else if (e.key === 'Enter' && highlight >= 0 && highlight < current.length) {
      e.preventDefault();
      pickSuggestion(field, current[highlight]);
      e.target.blur();
    }
  };

  // Swap in a fresh draft (a different photo opened, or a new selection in
  // Select Mode) — resets dropdown state so nothing from the previous
  // context lingers.
  const resetDraft = (newDraft = EMPTY_FILM_DRAFT) => {
    setDraft(newDraft);
    setDismissed(emptyPerField(false));
    setSuggestions(emptyPerField([]));
    setHighlight(-1);
    setFocused(null);
    touchedRef.current = false;
    lastAutoFilledTitleRef.current = null;
  };

  return {
    draft, setDraft, setField, resetDraft,
    suggestions, focused, setFocused, highlight, dismissed,
    onFieldChange, pickSuggestion, handleKeyDown, touchedRef,
  };
}
