/**
 * V41 — the running order for fullscreen presentation mode.
 *
 * Pulled out of PresentationMode.jsx as a plain function so it can be tested
 * directly (see scripts/test_presentation_order.mjs), the same reasoning as
 * selectionRange.js: CLAUDE.md's verification notes are explicit that browser
 * automation can't drive this kind of interaction reliably, so the logic has
 * to be reachable from code. It is also the one part of the feature that can
 * be silently WRONG rather than visibly broken — a mis-ordered pitch still
 * looks fine on screen, it just isn't the deck Ryan built.
 *
 * Three rules, all deliberate:
 *
 * 1. Scene order comes from `sort_order` (V38's drag-reorder writes it), and
 *    photo order INSIDE a scene comes from the order `images` already arrives
 *    in — the server sorts that by storyboard_order with unordered rows last.
 *    Filtering preserves it, so this function never re-sorts photos itself.
 * 2. Title cards ONLY when there are 2+ non-empty sections. A one-scene deck
 *    opens straight on its first frame rather than making you click past a
 *    card announcing the only thing in the deck.
 * 3. An empty scene emits NOTHING — no stranded title card with no photos
 *    behind it. Same rule as the PDF exporter (V40).
 *
 * Unsorted photos always come last, as their own section, matching how the
 * deck page and the PDF both treat them.
 *
 * @param {Array<{id:number, name:string, sort_order:number}>} scenes
 * @param {Array<{scene_id:number|null, storyboard_note?:string}>} images
 *        the deck's flat image list, already in storyboard order
 * @returns {Array<Object>} slides, each either
 *          {type:'title', label, count} or
 *          {type:'photo', img, label, number}  — `number` is the photo's
 *          position among photos only, so the on-screen counter doesn't count
 *          title cards as frames.
 */
export function buildSlides(scenes, images) {
  const ordered = [...(scenes || [])].sort((a, b) => a.sort_order - b.sort_order);
  const photos = images || [];
  const sections = [];

  for (const scene of ordered) {
    const inScene = photos.filter(img => img.scene_id === scene.id);
    // Rule 3: a scene with no photos contributes nothing at all.
    if (inScene.length) {
      sections.push({ label: scene.name || 'Untitled scene', photos: inScene });
    }
  }

  const unsorted = photos.filter(img => img.scene_id === null || img.scene_id === undefined);
  if (unsorted.length) sections.push({ label: 'Unsorted', photos: unsorted });

  // Rule 2.
  const useTitleCards = sections.length >= 2;

  const slides = [];
  let number = 0;
  for (const section of sections) {
    if (useTitleCards) {
      slides.push({ type: 'title', label: section.label, count: section.photos.length });
    }
    for (const img of section.photos) {
      number += 1;
      slides.push({ type: 'photo', img, label: section.label, number });
    }
  }
  return slides;
}
