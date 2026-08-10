/**
 * Frame Atlas — local test for V41 presentation-mode running order.
 *
 * CLAUDE.md's verification notes say browser automation can't reliably drive
 * this kind of interaction, so the order-building lives in a plain function
 * and gets tested here instead of by driving a page. This is also the part of
 * presentation mode that can be silently WRONG rather than visibly broken: a
 * mis-ordered pitch still looks fine on screen, it just isn't the deck Ryan
 * built.
 *
 * Usage (from the frame-atlas folder):
 *     node scripts/test_presentation_order.mjs
 */

import { buildSlides } from '../frontend/src/presentationOrder.js';

let failures = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}`);
  if (!ok) {
    console.log(`        got  ${JSON.stringify(got)}`);
    console.log(`        want ${JSON.stringify(want)}`);
    failures += 1;
  }
}

// Compact readable shape for a slide list.
const shape = (slides) => slides.map(s =>
  s.type === 'title' ? `[${s.label} x${s.count}]` : `${s.img.id}`
);
const labels = (slides) => slides.filter(s => s.type === 'photo').map(s => s.label);
const numbers = (slides) => slides.filter(s => s.type === 'photo').map(s => s.number);

const img = (id, sceneId, note) => ({ id, scene_id: sceneId, storyboard_note: note || null });

console.log('\n--- scene order follows sort_order, not scene id ---');
{
  // Scene 9 was dragged ABOVE scene 3 (V38 reordering), so it presents first.
  const scenes = [
    { id: 3, name: 'Interiors', sort_order: 1 },
    { id: 9, name: 'Exteriors', sort_order: 0 }
  ];
  const images = [img(101, 3), img(102, 9), img(103, 3), img(104, 9)];
  const slides = buildSlides(scenes, images);
  check('dragged-up scene presents first',
    shape(slides),
    ['[Exteriors x2]', '102', '104', '[Interiors x2]', '101', '103']);
}

console.log('\n--- photo order inside a scene is the order the server sent ---');
{
  // The server already sorted by storyboard_order (unordered last). The array
  // order is deliberately NOT id order — this is the whole point.
  const scenes = [{ id: 1, name: 'Night', sort_order: 0 }];
  const images = [img(77, 1), img(12, 1), img(50, 1)];
  const slides = buildSlides(scenes, images);
  check('storyboard order is preserved, never re-sorted by id',
    shape(slides), ['77', '12', '50']);
}

console.log('\n--- title cards only when there are 2+ sections ---');
{
  const oneScene = buildSlides(
    [{ id: 1, name: 'Only Scene', sort_order: 0 }],
    [img(1, 1), img(2, 1)]
  );
  check('a single-scene deck opens straight on its first frame',
    shape(oneScene), ['1', '2']);

  const twoScenes = buildSlides(
    [{ id: 1, name: 'A', sort_order: 0 }, { id: 2, name: 'B', sort_order: 1 }],
    [img(1, 1), img(2, 2)]
  );
  check('two scenes get their title cards',
    shape(twoScenes), ['[A x1]', '1', '[B x1]', '2']);
}

console.log('\n--- an empty scene emits nothing (no stranded title card) ---');
{
  const scenes = [
    { id: 1, name: 'Has photos', sort_order: 0 },
    { id: 2, name: 'Empty', sort_order: 1 },
    { id: 3, name: 'Also has photos', sort_order: 2 }
  ];
  const slides = buildSlides(scenes, [img(1, 1), img(2, 3)]);
  check('the empty scene contributes no card and no frames',
    shape(slides), ['[Has photos x1]', '1', '[Also has photos x1]', '2']);

  // One real scene + one empty one is ONE section, so no cards at all.
  const collapsed = buildSlides(
    [{ id: 1, name: 'Real', sort_order: 0 }, { id: 2, name: 'Empty', sort_order: 1 }],
    [img(1, 1)]
  );
  check('an empty scene does not count toward the 2-section title-card rule',
    shape(collapsed), ['1']);
}

console.log('\n--- unsorted photos come last, as their own section ---');
{
  const scenes = [{ id: 1, name: 'Scene One', sort_order: 0 }];
  const images = [img(1, null), img(2, 1), img(3, null)];
  const slides = buildSlides(scenes, images);
  check('unsorted is a trailing section, in server order',
    shape(slides), ['[Scene One x1]', '2', '[Unsorted x2]', '1', '3']);

  const onlyUnsorted = buildSlides([], [img(1, null), img(2, null)]);
  check('a deck with only unsorted photos needs no title card',
    shape(onlyUnsorted), ['1', '2']);
}

console.log('\n--- the frame counter counts photos, never title cards ---');
{
  const scenes = [
    { id: 1, name: 'A', sort_order: 0 },
    { id: 2, name: 'B', sort_order: 1 }
  ];
  const slides = buildSlides(scenes, [img(1, 1), img(2, 1), img(3, 2)]);
  check('numbering runs 1..N across the whole deck',
    numbers(slides), [1, 2, 3]);
  check('each frame carries its own scene label for the on-screen readout',
    labels(slides), ['A', 'A', 'B']);
}

console.log('\n--- degenerate decks do not throw ---');
{
  check('no scenes and no images yields no slides', shape(buildSlides([], [])), []);
  check('null arguments yield no slides', shape(buildSlides(null, null)), []);
  check('scenes but no photos yields no slides',
    shape(buildSlides([{ id: 1, name: 'A', sort_order: 0 }], [])), []);
  check('a scene with no name still presents',
    shape(buildSlides(
      [{ id: 1, name: '', sort_order: 0 }, { id: 2, name: 'B', sort_order: 1 }],
      [img(1, 1), img(2, 2)]
    )),
    ['[Untitled scene x1]', '1', '[B x1]', '2']);
}

console.log('\n--- a photo whose scene was deleted is not silently dropped ---');
{
  // scene_id pointing at a scene that no longer exists would vanish from every
  // section loop; the deck page treats those as unsorted, and so must this.
  const slides = buildSlides([{ id: 1, name: 'A', sort_order: 0 }], [img(1, 1), img(2, undefined)]);
  check('an undefined scene_id lands in Unsorted rather than disappearing',
    shape(slides), ['[A x1]', '1', '[Unsorted x1]', '2']);
}

console.log();
if (failures) {
  console.log(`${failures} CHECK(S) FAILED`);
  process.exit(1);
}
console.log('ALL PRESENTATION ORDER TESTS PASSED');
