# Design System — SkillForge

## Product Context
- **What this is:** Industrial operations supervisor cockpit for manufacturing (CNC, textile, FMCG)
- **Who it's for:** Production supervisors and engineers at Karawang/Cikarang factories
- **Space/industry:** Industrial Operations / Manufacturing
- **Project type:** Dashboard / internal tool (data-dense, task-focused)

## Aesthetic Direction
- **Direction:** Technical/Minimal
- **Decoration level:** Minimal - typography does all the work
- **Mood:** Clean, fast, no-nonsense - like a surgeon's workstation. Zero decoration, maximum clarity.
- **Reference sites:** Linear, Vercel, GitHub - data-first interfaces

## Typography
- **Display/Hero:** Inter - clean, highly legible system font
- **Body:** Inter - same as display for consistency, tight line-height
- **UI/Labels:** Inter - bold for hierarchy
- **Data/Tables:** Inter (tabular-nums) - numbers align properly
- **Code:** JetBrains Mono - monospace for technical content
- **CDN:** Google Fonts (Inter)
- **Scale:** 14px base (compact for data density)

## Color
- **Approach:** Cool grays + electric blue accent
- **Primary:** #2563eb (electric blue) - for primary actions, links, highlights
- **Secondary:** #475569 (slate) - for secondary elements
- **Neutrals:** 
  - Background: #0f1115 (very dark, almost black)
  - Surface: #181b21 (cards, containers)
  - Border: #2d3748 (subtle borders)
  - Text: #e2e8f0 (primary), #94a3b8 (secondary), #64748b (tertiary)
- **Semantic:** success #10b981, warning #f59e0b, error #ef4444, info #3b82f6
- **Dark mode:** Native - surfaces are already dark, use lighter grays for elevated elements

## Spacing
- **Base unit:** 8px
- **Density:** Compact (data-dense interface)
- **Scale:** xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64) 4xl(96)

## Layout
- **Approach:** Grid-disciplined
- **Grid:** 12 columns at desktop, fluid at mobile
- **Max content width:** 1280px
- **Border radius:** sm(4px) md(8px) lg(12px) xl(16px) full(9999px)
- **Card padding:** 24px (lg), 16px (md), 12px (compact)

## Motion
- **Approach:** Minimal-functional
- **Easing:** enter(ease-out) exit(ease-in) move(ease-in-out)
- **Duration:** micro(100ms) short(150ms) medium(250ms) long(400ms)
- **Animated properties:** opacity, transform only

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-10-05 | Technical/Minimal aesthetic | Engineers need data density, not decoration |
| 2026-10-05 | Inter typography | Clean, familiar, excellent legibility |
| 2026-10-05 | Cool grays + blue accent | Professional, technical feel |
| 2026-10-05 | 8px spacing base | Compact but readable data layout |
| 2026-10-05 | Minimal motion | Speed first - animation only when needed |
