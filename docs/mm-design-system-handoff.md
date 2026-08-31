# Musical Mycology design system: handoff summary

A self-contained brief for a designer or design tool prototyping the
Terrarium Console overhaul. Everything here is distilled from the canonical
system; nothing is invented.

**Canonical source:** `~/projects/mm-documents/Musical Mycology Design System/`
(README.md, SKILL.md, `colors_and_type.css`, `fonts/`, `assets/`,
`preview/`, `ui_kits/website/`, `slides/`). It ships as an Agent Skill named
`musical-mycology-design`. Import `colors_and_type.css` first in any new
HTML; every token below is a `--mm-*` custom property in that file.

---

## 0. Read this first: the tension to resolve

The MM design system was built for a **marketing website and a pitch deck**.
Its whole premise is warm cream paper, generous air, and slow motion. The
Terrarium Console is an **operator panel used in a dark room, at a glance,
while a show is running**. Those two things want opposite palettes.

**(recommended) Build a dark-surface variant of the existing system rather
than a new system or a straight light-mode port.** The brand already
sanctions dark surfaces (`--mm-mushroom` #5c3d2e for dark cards,
`--mm-dark-brown` #3a2318 for the deepest surface) and already specifies
what accents do there (`--mm-gold` #d4a843 is *only* used on dark). So a
dark console is an existing, documented branch of the system, not a
departure from it: cream becomes the ink, mushroom-brown becomes the
surface, gold becomes the accent.

Supporting points:
- The console's own CSS currently pins `background-color: #fff` with a
  comment saying a real dark theme is a separate design decision. That
  decision is exactly what this overhaul is for.
- A light cream panel at 100 % brightness next to a 6 m LED array is a
  glare source that wrecks the operator's dark adaptation and washes out
  the room.
- What would change the call: if the console ends up mostly used on a
  tablet in daylight during setup rather than at the back of a dark room
  during a show, the light cream system is the better fit as-is.

---

## 1. Brand at a glance

| | |
|---|---|
| **Tagline** | "Finding magic in mushrooms." |
| **BHAG** | "To remove the barriers people put up between themselves and playing music with others." |
| **Voice** | Earnest, plain, gently whimsical. Never slick. Allowed to be excited, allowed to admit doubt, always showing the work. |
| **Whimsy lives in** | Nouns (Tune Shrooms, Terrarium, Bits, Cart, Concertina), never adjectives. |
| **Never** | Emoji. Pure black. Cool greys. Neon. Bluish-purple gradients. Photorealism. |
| **Ornament** | One mark only: ✦ (U+2726, four-pointed sparkle). `*` for footnotes. |

### Copy rules that matter for a UI

- **You and we, never "users."** People, players, participants, the audience.
- **Title Case for headings**, sentence case for body.
- Headings tend to be short verb-led phrases: "Open the Doors," "Remove
  Judgment," "Gently Lead."
- No exclamation points except as genuine enthusiasm, sparingly.
- **Capitalize the brand nouns**: Cart, Tune Shrooms, Terrarium, Bits.
  Note this repo already capitalizes Room, Bit and Terrarium in code and
  docs, which is consistent.

---

## 2. Color tokens

### Surfaces (paper creams)
| Token | Hex | Use |
|---|---|---|
| `--mm-cream` | `#fdf3e4` | Primary surface, default body |
| `--mm-cream-pale` | `#fdebd5` | Hero surface, slightly warmer |
| `--mm-warm-tan` | `#f5dfc0` | Secondary surface, sub-section |
| `--mm-tan-edge` | `#f0c9a0` | Hairline borders and dividers |

### Browns (ink and dark surfaces)
| Token | Hex | Use |
|---|---|---|
| `--mm-mushroom` | `#5c3d2e` | Default text, dark surface |
| `--mm-mushroom-2` | `#4a3020` | Heavier text on cream |
| `--mm-dark-brown` | `#3a2318` | Footer, deepest surface |
| `--mm-stem` | `#b89070` | Hairline brown lines |

### Accents (cap colors, used sparingly)
| Token | Hex | Use |
|---|---|---|
| `--mm-terracotta` | `#c07850` | **Primary accent.** Eyebrows, dots, spore particles |
| `--mm-terracotta-2` | `#a06040` | Terracotta, darker |
| `--mm-rose` | `#d96680` | Amanita pink-cap. **Very rare. A punctuation mark, not a recurring color.** |
| `--mm-sage` | `#7a9e6e` | Forest sage. Rare. |
| `--mm-gold` | `#d4a843` | Spore gold. **On dark surfaces only.** |
| `--mm-gold-pale` | `#e8c090` | Warm sand spore |

### Semantic
```
--mm-bg           = cream          --mm-fg              = mushroom
--mm-bg-alt       = warm-tan       --mm-fg-strong       = mushroom-2
--mm-bg-hero      = cream-pale     --mm-fg-muted        = #8a6a55
--mm-bg-dark      = mushroom       --mm-fg-on-dark      = cream
--mm-bg-deepest   = dark-brown     --mm-fg-on-dark-muted= rgba(253,243,228,0.75)
--mm-accent       = terracotta     --mm-accent-on-dark  = gold
--mm-border       = tan-edge       --mm-border-soft     = rgba(184,144,112,0.5)
                                   --mm-border-dark     = rgba(255,255,255,0.15)
```

**Rule:** "muted" is achieved by lowering the opacity of brown over cream,
never by reaching for grey. All neutrals are warm.

**Gap to flag for the console:** the system has **no status palette**. There
is no defined success / warning / error / running colour. The console needs
one (state badges, error rows, the `ADMIN MANUAL` tag, connection status).
Sage reads naturally as go/ok and rose as fail, but both are marked "rare"
in the brand doc, so promoting them to a recurring status role is a real
extension and should be an explicit decision rather than a quiet one.

---

## 3. Typography

**Two families, clear roles. Never mix them up.**

| Role | Family | Where |
|---|---|---|
| **Display** | **Londrina Solid** (local TTF, weights 100 / 300 / 400 / 900) | Titles, eyebrows, the subtitle bar, button labels, page numbers |
| **Body** | **Atkinson Hyperlegible** (Google Fonts) | Paragraph copy, lead, captions, quotes, and any UI label longer than ~3 words |
| Mono | `ui-monospace, 'SF Mono', Menlo, monospace` | Not a brand face; the console uses it heavily for cue scripts and the log |

**Never use Londrina for paragraphs.** Fallback stack for both is
`system-ui, sans-serif`.

### Scale
```
--mm-fs-eyebrow  0.78rem      --mm-lh-tight  1.05
--mm-fs-caption  0.72rem      --mm-lh-snug   1.2
--mm-fs-small    0.82rem      --mm-lh-body   1.6
--mm-fs-body     1rem
--mm-fs-lead     1.15rem      --mm-tracking-eyebrow 0.2em
--mm-fs-h3       1.25rem      --mm-tracking-button  0.04em
--mm-fs-h2       clamp(1.8rem, 4vw, 2.8rem)
--mm-fs-h1       clamp(3rem, 8vw, 6rem)
--mm-fs-display  clamp(3rem, 8vw, 7rem)
```

Headings are **tight and large**; body is **generous and modest**. That
contrast is part of the voice.

**Eyebrows**: uppercase, terracotta (gold on dark), 0.78rem, letter-spacing
0.2em. Used to label sections. The console's existing `.kind` badges
(`LIGHT`, `AUDIO`, `TOOL`, `R_GAME`, `DEVICE`, `ROOM`) are already
eyebrow-shaped and are the natural place to land this.

**The signature "subtitle bar"**: a mushroom-brown pill-rectangle at
`--mm-radius-xs` (4px, almost square) with cream text inside, sitting under
a hero title. The near-square radius contrasts deliberately with the round
cards above and below it.

---

## 4. Spacing, radii, borders, shadows

```
Spacing   4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 80 / 128 px
Radii     xs 4px (subtitle bar) · sm 10px (badges) · md 16px (hero card)
          lg 18px (inner cards) · xl 20px (section boxes) · pill 9999px (buttons, chips)
Borders   1.5px default · 2.5px strong (outline buttons) · warm-tan, never grey
```

**Shadows are warm and brown-tinted, never neutral grey. No inner shadows,
no gradient borders, no neumorphism.**
```
--mm-shadow-card    0 3px 16px  rgba(92,61,46,0.07)   barely there
--mm-shadow-card-2  0 4px 20px  rgba(92,61,46,0.09)
--mm-shadow-lift    0 14px 40px rgba(92,61,46,0.16)   hovered
--mm-shadow-slide   0 12px 48px rgba(58,35,24,0.13)   confident lift
--mm-shadow-button  0 10px 28px rgba(0,0,0,0.14)
--mm-shadow-shroom  drop-shadow(0 8px 20px rgba(58,35,24,0.13))
```

### Card anatomy
Background white on tan sections, warm-tan on cream sections,
mushroom-brown for feature/dark cards. Radius 18px, padding 1.75 to 2rem,
1.5px warm-tan border, soft warm shadow. **Cards on dark sections drop the
border, keep the radius, and use gold for accent text.**

### Layout
- Sections ~5rem (80px) vertical padding.
- Inner content max-width ~1060px, centered.
- Grid gaps 1.25 to 1.5rem.
- Sticky top nav, 64px, `rgba(253,235,213,0.96)` + `backdrop-filter: blur(10px)`,
  hairline bottom. **Nothing else is fixed:** no floating buttons, no chat
  bubbles, no scroll progress bars.
- The look is *vertical and breathing*, not dense.

**Gap to flag for the console:** an operator panel is a **dense dashboard**,
which is the opposite of "vertical and breathing" with 80px section padding
and a 1060px column. Density is the thing to negotiate first. A reasonable
reading: keep the brand's radii, warm borders, warm shadows and type roles,
but compress the spacing scale by roughly one step throughout and let the
content column run full width.

---

## 5. Motion

**Slow and gentle. No bouncy springs, no parallax, no scale-everything-on-scroll.**

```
--mm-ease        cubic-bezier(0.4, 0, 0.2, 1)
--mm-ease-soft   cubic-bezier(0.25, 1, 0.5, 1)
--mm-dur-fast    0.2s
--mm-dur-med     0.45s
--mm-dur-reveal  0.7s
--mm-float-period 6s    gentle mushroom hover (translateY 0 to -8px)
--mm-spore-period 18s   falling-spore particle
```

- Reveal on scroll: translateY 28px + opacity 0 to 1 over 0.7s ease-out, at
  15 % intersection.
- Hover lifts: -3px to -6px translateY with a soft warm shadow.
- Buttons: hover translateY(-3px) + warm shadow; press settles to -1px +
  scale(0.99).
- Cards: hover translateY(-5px), shadow softens warmer.
- Nav links: background fills mushroom-brown, text turns cream, a small pill
  highlight.
- "Mushroom pulse" for coming-soon moments: 2.5s ease-in-out, scale 1 to
  1.06, drop-shadow shifting terracotta to gold.

**Gap to flag for the console:** 0.7s reveals and 6s float cycles are wrong
for a panel whose whole job is to report state changes the instant they
happen. Reserve the slow motion for decoration; keep every state-carrying
transition at `--mm-dur-fast` (0.2s) or shorter, and consider none at all on
the live LED strip.

---

## 6. Imagery and iconography

- **Mushroom PNG illustrations are the brand's icon set.** Cartoon-naturalist:
  warm tans and browns, occasional rosy red caps, soft ink outlines, no harsh
  black, no photorealism. Children's mycology field guide.
- **NEVER hand-draw new mushroom SVGs.** Copy the PNGs.
- Decor usage: scattered into section corners at **opacity 0.1 to 0.35**,
  rotated, sometimes flipped, as wallpaper. Never as primary content, never
  full-bleed.
- **Wave dividers** between differently-coloured sections: a gentle 48px SVG
  sine wave in two layered tones.
- Assets are served from the design-assets CDN
  (`https://design-assets.musicalmycology.org/assets/`). Each `day_*` asset
  has a `night_*` counterpart for theme-aware surfaces, **which is directly
  relevant to a dark console.** The current decorative set:
  `day_faeriecrown` (ornate showstopper, rare), `day_corpusviola`
  (multi-stem cluster), `day_gildedhaze` (single classic cap, corner
  ornament), `dayvein` (small mini-vignette), `day_angelring` (tall narrow
  silhouette), `day_glimmeringoculi` (small secondary).
  `shroom_tan_solo.png` is the only illustration still committed locally, in
  `assets/`.
- **Logos** are PNG only, from the CDN: `logo_day_angelring.png` /
  `logo_night_devilring.png` (wordmark lockup), `foundation_logo_day.png` /
  `foundation_logo_night.png`.
- **Functional icons:** the brand has none. The system recommends **Lucide**
  (1.5 to 2px round stroke, MIT) as a substitution and flags it as
  needing confirmation. The console needs functional icons (play, stop,
  fire, link, warning, chevron), so this is a decision the overhaul forces.
- The hamburger in the existing nav is hand-rolled: three mushroom-brown
  26x3px rounded bars, 5px gaps. Match that weight for any inline
  iconography.
- When a small "thing-icon" is needed, use a 40 to 60px mushroom illustration
  rather than a glyph. That is the brand-correct call.

---

## 7. Files to hand over

| Path (under `~/projects/mm-documents/Musical Mycology Design System/`) | What it is |
|---|---|
| `colors_and_type.css` | **Import this first.** Every token as `--mm-*`, plus `.mm-h1` / `.mm-btn` / `.mm-card` / `.mm-eyebrow` / `.mm-subtitle-bar` utilities |
| `README.md` | Full brand context: content fundamentals, visual foundations, iconography policy |
| `SKILL.md` | Agent Skill manifest (`musical-mycology-design`) |
| `fonts/` | Londrina Solid (100/300/400/900) and Atkinson Hyperlegible TTFs, for offline rendering |
| `assets/shroom_tan_solo.png` | The one locally committed illustration |
| `preview/` | One HTML card per token group: colors, type, spacing, shadows, radii, buttons, cards, nav, wave, logo, voice, motion |
| `ui_kits/website/` | High-fidelity JSX recreation of musicalmycology.org |
| `slides/` | Deck templates: title, section divider, two-column, list, quote, big-stat, closing |

---

## 8. Open decisions this overhaul forces

These are all places where the brand as written does not yet cover what an
operator panel needs. Each needs an explicit call.

1. **Light or dark surface.** See section 0. Recommendation: dark, built on
   `--mm-mushroom` / `--mm-dark-brown` with `--mm-gold` as the accent.
2. **A status palette.** No success / warning / error / running colours
   exist. Promoting sage and rose out of "rare" is an extension.
3. **Density.** 80px section padding and a 1060px column versus a dense
   dashboard.
4. **Functional icon set.** Lucide is recommended by the system and flagged
   as unconfirmed.
5. **Monospace.** The console leans hard on mono (cue scripts, the event
   log, manifest JSON, controller values). It is not a brand face and has no
   token beyond `--mm-font-mono`. Worth choosing one deliberately.
6. **Motion budget.** Brand motion is 0.7s and 6s; state reporting wants
   0.2s or instant.
7. **Decoration in an operator tool.** Corner mushrooms at 0.1 to 0.35
   opacity are the brand's signature texture, and they compete with an 864
   pixel live LED strip for the same visual attention. Probably: identity in
   the header only, and nothing decorative below it.

---

*Compiled 2026-08-25 from `~/projects/mm-documents/Musical Mycology Design System/`
(design system v1, April 2026). Paired with `docs/console-frontend-audit.md`.*
