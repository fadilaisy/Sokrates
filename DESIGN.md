# Design System — SkillForge

## Product Context
- **What this is:** Industrial operations supervisor cockpit for manufacturing (CNC, textile, FMCG)
- **Who it's for:** Production supervisors and engineers at Karawang/Cikarang factories
- **Space/industry:** Industrial Operations / Manufacturing
- **Project type:** Dashboard / internal tool (data-dense, task-focused)

## Aesthetic Direction
- **Direction:** Technical/Minimal
- **Decoration level:** Minimal - typography does all the work
- **Mood:** Light, industrial, soft (PRD). Clean and fast; status must be readable from ~2 m on a factory floor.
- **Reference sites:** Linear, Vercel, GitHub - data-first interfaces

## Typography
- **Display/Hero:** Inter - clean, highly legible system font
- **Body:** Inter - same as display for consistency, tight line-height
- **UI/Labels:** Inter - bold for hierarchy
- **Data/Tables:** Inter (tabular-nums) - numbers align properly
- **Code:** JetBrains Mono - monospace for technical content
- **CDN:** Google Fonts (Inter)
- **Scale:** 16px base, 14px minimum (factory-floor readability)

## Color
- **Approach:** Light surfaces, navy ink, saturated status colours (source: Figma "skills-forge-alt")
- **Ink / primary action:** #18324F (navy)
- **Page / sidebar:** #EFF2F6 · **Surface:** #FFFFFF · **Line:** #DEE5ED · **Muted text:** #5A6A80 · **Accent:** #486284
- **Machine status (always paired with a text label):** RUNNING #00623F · FAULT #A71920 · IDLE #334155 · MAINTENANCE / RECOVERING #8A4B00 · DISRUPTED #B45309
- **Feedback:** ok #10B981 on #ECFDF5 · danger #EF4444 on #FEF2F2 · warning #B45309 on #FFF7ED
- Tokens live in `frontend/src/index.css` (`--sf-*`).

## Industrial UX rules
- Minimum text 14px; status badges 18–22px bold; key numbers 26px+.
- Every action ≥ 48px tall, primary actions 56px (gloved use).
- CNC-01 → CNC-05 always left to right, matching the physical line.
- No nested menus; overlays (approval, ledger, chat) instead of extra pages.

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
| 2026-10-09 | Switched to light theme from Figma "skills-forge-alt" | PRD asks for "light, industrial, soft"; glare-heavy floors favour dark ink on light surfaces |
| 2026-10-09 | 14px minimum text, 48px minimum targets | 2 m readability and gloved touch |
