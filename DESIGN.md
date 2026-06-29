# DESIGN.md — Frame Atlas
*Visual design system. Reference this before writing any UI code.*

---

## Design Philosophy

Frame Atlas is a **premium visual reference tool for cinematographers**. The aesthetic is:

> "Cinematic, quiet, and image-first."

Inspired by Arc Browser, Obsidian, and Apple Photos. Every UI decision should make the images the hero — chrome (buttons, labels, borders) should recede. The interface should feel like a high-end pro app, not a generic web dashboard.

---

## Color System

These are the exact color values. Use them by name, not by hex, wherever possible.

### Surfaces (backgrounds and panels)
| Name | Hex | Use it for |
|---|---|---|
| `surface` | `#111317` | Default page background |
| `surface-dim` | `#111317` | Same as surface — dimmed contexts |
| `surface-bright` | `#37393e` | Highlighted surface areas |
| `surface-container-lowest` | `#0c0e12` | Deepest background (behind everything) |
| `surface-container-low` | `#1a1c20` | Cards, panels resting on surface |
| `surface-container-high` | `#2a2c31` | Elevated panels, dropdowns |
| `surface-container-highest` | `#37393e` | Topmost floating elements |

### Text
| Name | Hex | Use it for |
|---|---|---|
| `on-surface` | `#e2e2e6` | Primary body text |
| `on-surface-variant` | `#c4c6d0` | Secondary/supporting text, labels |

### Borders
| Name | Hex | Use it for |
|---|---|---|
| `outline` | `#8e9099` | Visible borders (input fields, dividers) |
| `outline-variant` | `#44474f` | Subtle borders (panel edges, separators) |

### Primary — Warm Gold (the accent color)
| Name | Hex | Use it for |
|---|---|---|
| `primary` | `#d9a441` | Buttons, active states, highlights, selected chips |
| `on-primary` | `#3d2f00` | Text/icons sitting ON a gold background |
| `primary-container` | `#594400` | Gold tinted backgrounds (hover states, badges) |
| `on-primary-container` | `#ffdf9d` | Text sitting inside a gold container |

### Secondary — Warm Taupe
| Name | Hex | Use it for |
|---|---|---|
| `secondary` | `#d1c5b4` | Secondary actions, passive chips |
| `on-secondary` | `#362f24` | Text on secondary backgrounds |
| `secondary-container` | `#4d4639` | Secondary tinted containers |
| `on-secondary-container` | `#eee1cf` | Text in secondary containers |

### Tertiary — Muted Sage
| Name | Hex | Use it for |
|---|---|---|
| `tertiary` | `#b8cea1` | Occasional accent, success states |
| `on-tertiary` | `#243516` | Text on tertiary backgrounds |
| `tertiary-container` | `#3a4c2b` | Tertiary tinted containers |
| `on-tertiary-container` | `#d4eabb` | Text in tertiary containers |

### Error
| Name | Hex | Use it for |
|---|---|---|
| `error` | `#ffb4ab` | Error text, destructive action indicators |
| `on-error` | `#690005` | Text on error backgrounds |
| `error-container` | `#93000a` | Error background fill |
| `on-error-container` | `#ffdad6` | Text inside error containers |

---

## Typography

**Font family:** Manrope (import from Google Fonts)

```css
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700&display=swap');
```

| Style name | Size | Line height | Weight | Use it for |
|---|---|---|---|---|
| `headline-lg` | 32px | 40px | 700 | Page titles |
| `headline-md` | 28px | 36px | 600 | Section headers |
| `title-lg` | 22px | 28px | 600 | Panel titles, modal headers |
| `title-md` | 16px | 24px | 500 | Card titles, sidebar labels |
| `body-lg` | 16px | 24px | 400 | Primary body text |
| `body-md` | 14px | 20px | 400 | Secondary body, metadata |
| `label-lg` | 14px | 20px | 500 | Button labels, tag chips |
| `label-md` | 12px | 16px | 500 | Captions, small labels |

---

## Spacing

| Token | Value | Use it for |
|---|---|---|
| `gutter-grid` | 24px | Gap between image grid columns |
| `panel-padding` | 16px | Internal padding inside panels and cards |
| `item-spacing` | 12px | Space between items in a list or stack |

---

## Shape

- **Border radius:** 12px on all cards, panels, buttons, chips, and modals
- Inputs: 8px radius is acceptable for smaller elements
- Images in the grid: 8px radius

---

## Layout Architecture

Three-column layout on desktop:

```
┌─────────────┬──────────────────────────┬─────────────┐
│             │                          │             │
│  Left       │   Center Content         │  Right      │
│  Sidebar    │   (Masonry Image Grid)   │  Inspector  │
│  Nav        │                          │  Panel      │
│             │                          │  (slides in)│
│  ~220px     │   Fills remaining space  │  ~320px     │
│             │                          │             │
└─────────────┴──────────────────────────┴─────────────┘
```

- **Left sidebar:** `surface-container-low` background (`#1a1c20`), `outline-variant` right border
- **Center:** `surface` background (`#111317`), masonry grid with `gutter-grid` (24px) gaps
- **Right inspector:** Slides in from right on image click, `surface-container-low` background, does not push content (overlaps)

---

## Component Patterns

### Image Cards (grid thumbnails)
- No border by default
- 8px border radius
- On hover: subtle scale up (`transform: scale(1.02)`), transition 150ms ease
- On hover: very subtle shadow (`box-shadow: 0 8px 24px rgba(0,0,0,0.4)`)
- Selected state: `primary` (`#d9a441`) border, 2px

### Tag Chips
- Background: `surface-container-high` (`#2a2c31`)
- Text: `on-surface-variant` (`#c4c6d0`)
- Border: `outline-variant` (`#44474f`)
- Border radius: 99px (fully rounded, pill shape)
- Padding: 6px 12px
- Font: `label-lg` (14px / 500)
- **Active/selected chip:** background `primary-container` (`#594400`), text `on-primary-container` (`#ffdf9d`), border `primary` (`#d9a441`)
- Remove (×) button inside chip: appears on hover

### Search Bar
- Background: `surface-container-low` (`#1a1c20`)
- Border: `outline-variant` (`#44474f`), 1px
- Border radius: 12px
- On focus: border changes to `primary` (`#d9a441`)
- Font: `body-lg` (16px / 400)
- Placeholder text color: `outline` (`#8e9099`)

### Buttons — Primary
- Background: `primary` (`#d9a441`)
- Text: `on-primary` (`#3d2f00`)
- Border radius: 12px
- Font: `label-lg` (14px / 500)
- Hover: slightly lighter gold, subtle shadow

### Buttons — Secondary / Ghost
- Background: transparent
- Border: `outline-variant` (`#44474f`), 1px
- Text: `on-surface` (`#e2e2e6`)
- Hover: background `surface-container-high` (`#2a2c31`)

### Panels and Cards
- Background: `surface-container-low` (`#1a1c20`)
- Border: `outline-variant` (`#44474f`), 1px
- Border radius: 12px
- Padding: `panel-padding` (16px)

### Dropdowns / Autocomplete
- Background: `surface-container-high` (`#2a2c31`)
- Border: `outline-variant` (`#44474f`), 1px
- Border radius: 12px
- Item hover: background `surface-bright` (`#37393e`)
- Shadow: `0 8px 32px rgba(0,0,0,0.5)`

---

## Interaction Principles

1. **Hover states are always subtle** — scale, shadow, or background lightening. Never jarring.
2. **Transitions:** 150ms ease for most interactions. 250ms for panels sliding in/out.
3. **Glassmorphism** for tooltips and floating overlays: `backdrop-filter: blur(12px)`, semi-transparent background.
4. **Focus states:** Gold outline (`primary`) on all interactive elements for accessibility.
5. **Loading states:** Skeleton screens using `surface-container-high` with a shimmer animation — never spinners on image content.

---

## What to Avoid

- Bright white backgrounds — always use the dark surface tokens
- Harsh borders — prefer `outline-variant` over `outline` wherever possible
- Rounded corners less than 8px or more than 16px
- Multiple competing accent colors — gold is the one accent, use it sparingly
- Dense text — Frame Atlas is image-first; UI chrome should be minimal
- Generic "web app" feel — every component should feel considered and intentional

---

## Reference File

`/docs/Frame_Atlas.html` — open this in a browser to see the full visual reference for the intended look and feel.
