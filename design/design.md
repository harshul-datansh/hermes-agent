# Datansh PM-OS — Design System

**Source of truth:** `https://erp.datansh.com` (Datansh ERP), inspected live on 2026-08-05
via an authenticated session. Every value below was read out of the running app's
CSSOM (`getComputedStyle` on `:root` + matched stylesheet rules), not eyeballed from a
screenshot.

**Goal:** the PM-OS dashboard must be visually indistinguishable from the ERP. Same
shell, same tokens, same control sizes, same density. A user moving between
`erp.datansh.com` and PM-OS should not notice a product boundary.

---

## 1. Brand identity

| Property | Value |
|---|---|
| Wordmark | `DATANSH` — "DATA" in `--ink` (`#0b0e16`), "NSH" in `--brand` (`#3578bd`) |
| Primary brand blue | `#3578bd` |
| Deep brand navy | `#0e2a4d` |
| Voice | Enterprise-calm. Sentence case. Short subtitle under every page title. |
| Tone of headings | Tight tracking (`-0.025em`), weight 600–680, never 700+ |

The ERP never uses saturated fills for large areas. Colour appears in: the brand blue
for primary actions and active nav, and low-alpha status tints for pills. Everything
else is grey-blue neutrals.

---

## 2. Token layer

The ERP runs **two stacked token layers**. Copy both.

### 2a. shadcn/Tailwind-v4 layer (oklch)

Used by shadcn primitives (`bg-background`, `text-foreground`, `border-input`, …).
`<body class="min-h-screen bg-background text-foreground antialiased">`,
`<html class="light">` / `<html class="dark">`.

```css
:root {
  --background: oklch(99% .003 240);
  --foreground: oklch(18% .02 260);
  --card: oklch(100% 0 0);
  --card-foreground: oklch(18% .02 260);
  --popover: oklch(100% 0 0);
  --popover-foreground: oklch(18% .02 260);
  --primary: oklch(21% .07 256);
  --primary-foreground: oklch(99% 0 0);
  --secondary: oklch(96% .01 250);
  --secondary-foreground: oklch(25% .04 260);
  --muted: #515b6c;
  --muted-foreground: oklch(44% .025 262);
  --accent: oklch(95% .025 252);
  --accent-foreground: oklch(25% .06 256);
  --destructive: oklch(58% .22 25);
  --destructive-foreground: oklch(99% 0 0);
  --border: oklch(90% .01 250);
  --input: oklch(89% .012 250);
  --ring: oklch(64% .13 252);
  --radius: .75rem;

  --chart-1: oklch(60% .14 252);
  --chart-2: oklch(30% .08 256);
  --chart-3: oklch(75% .15 75);
  --chart-4: oklch(55% .22 25);
  --chart-5: oklch(65% .17 165);

  --sidebar: oklch(99% .005 250);
  --sidebar-foreground: oklch(25% .02 260);
  --sidebar-primary: oklch(21% .07 256);
  --sidebar-primary-foreground: oklch(99% 0 0);
  --sidebar-accent: oklch(95% .02 252);
  --sidebar-accent-foreground: oklch(25% .04 260);
  --sidebar-border: oklch(92% .008 250);
  --sidebar-ring: oklch(64% .13 252);
}

.dark {
  --background: oklch(16% .02 260);
  --foreground: oklch(98% 0 0);
  --card: oklch(20% .02 260);
  --card-foreground: oklch(98% 0 0);
  --popover: oklch(20% .02 260);
  --popover-foreground: oklch(98% 0 0);
  --primary: oklch(70% .12 252);
  --primary-foreground: oklch(18% .02 260);
  --secondary: oklch(27% .02 260);
  --secondary-foreground: oklch(98% 0 0);
  --muted: #aeb8c7;
  --muted-foreground: oklch(70% .02 260);
  --accent: oklch(27% .04 252);
  --accent-foreground: oklch(98% 0 0);
  --destructive: oklch(55% .2 25);
  --destructive-foreground: oklch(98% 0 0);
  --border: oklch(30% .02 260);
  --input: oklch(30% .02 260);
  --ring: oklch(64% .13 252);

  --chart-1: oklch(66% .14 252);
  --chart-2: oklch(55% .1 256);
  --chart-3: oklch(78% .15 75);
  --chart-4: oklch(66% .2 25);
  --chart-5: oklch(70% .16 165);

  --sidebar: oklch(18% .02 260);
  --sidebar-foreground: oklch(95% 0 0);
  --sidebar-primary: oklch(70% .12 252);
  --sidebar-primary-foreground: oklch(18% .02 260);
  --sidebar-accent: oklch(27% .04 260);
  --sidebar-accent-foreground: oklch(98% 0 0);
  --sidebar-border: oklch(30% .02 260);
  --sidebar-ring: oklch(64% .13 252);
}
```

### 2b. Semantic layer (hex) — **this is the one feature UI uses**

The ERP's own components (`.hnav`, `.widget`, `.metric`, `.tbl`, `.btn`, `.seg`) are
written against these, and feature markup binds to them through Tailwind arbitrary
values: `text-[color:var(--ink-2)]`, `border-[color:var(--line)]`.

```css
:root {
  /* brand */
  --brand:        #3578bd;
  --brand-50:     #eef4fb;
  --brand-100:    #d8e7f6;
  --brand-600:    #2f6fac;
  --brand-700:    #0e2a4d;

  /* ink */
  --ink:          #0b0e16;   /* headings, primary text */
  --ink-2:        #273043;   /* body text, table cells */
  --muted:        #515b6c;   /* labels, secondary, nav idle */

  /* lines */
  --line:         #dbe1ea;   /* card + table borders */
  --line-2:       #edf0f5;   /* internal dividers, row separators */
  --line-strong:  #c5cdda;   /* table header underline */
  --btn-edge:     #9aa6b8;   /* secondary button border */

  /* surfaces */
  --surface:         #fff;      /* cards, nav, topbar */
  --surface-2:       #f6f8fc;
  --surface-sunken:  #eef2f8;   /* table headers, segmented control track */
  --surface-hover:   #eef4fd;
  --canvas:          #eef1f7;   /* app background */
  --app-bg:          linear-gradient(160deg, #eef1f7, #e7ebf2);

  /* status */
  --success: #2f9e74;
  --warning: #b67a1e;
  --danger:  #cf4d44;
  --info:    #3a73c4;

  /* radii */
  --r-sm:   6px;    /* buttons, inputs, nav items, segmented */
  --r-md:   8px;
  --r-lg:   12px;   /* cards, widgets, metrics */
  --r-xl:   16px;
  --r-pill: 999px;  /* status pills, avatar button */

  /* elevation */
  --sh-xs:   0 1px 2px #1018280d;
  --sh-sm:   0 1px 2px #1018280f, 0 1px 3px #1018280d;
  --sh-card: 0 1px 2px #1018280a, 0 6px 16px -6px #1018281a;
  --sh-md:   0 4px 16px -2px #1018281a, 0 2px 6px #1018280d;
  --sh-lg:   0 14px 38px -6px #10182829, 0 6px 14px #10182814;

  /* metrics */
  --control-h: 36px;   /* every button, input and select */
}
```

> **Rule:** new PM-OS components are written against layer 2b. Only reach for 2a when
> you are dropping in an unmodified shadcn primitive.

---

## 3. Typography

```css
--font:      "Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
--font-mono: "IBM Plex Mono", ui-monospace, monospace;   /* codes, ids, diffs */
```

Base: `14px / 1.45` (`20.3px` line-height), weight 400, colour `--ink` (`#0b0e16`).

| Role | Size | Weight | Tracking | Colour |
|---|---|---|---|---|
| Page title (`.page-title`) | 22px | 680 | `-0.025em` | `--ink` |
| Page subtitle (`.page-sub`) | 13.5px | 400 | normal | `--muted` |
| Widget/card title (`.widget-title`) | 14px | 600 | normal | `--ink` |
| Nav item (`.hnav-item`) | 13px | 500 | normal | `--muted` → `--brand` when active |
| Button (`.btn`) | 13px | 500 | normal | per variant |
| Segmented button (`.seg button`) | 12.5px | 500 | normal | `--muted` → `--ink` active |
| Table header (`.tbl thead th`) | 11px | 600 | `0.04em`, UPPERCASE | `--muted` |
| Table cell (`.tbl tbody td`) | 14px | 400 | normal | `--ink-2` |
| Status pill | 11.5px | 600 | `0.0575px` | status ink |
| Metric value (`.m-top`) | ~22px | 600 | `-0.02em` | `--ink` |
| Metric label (`.m-lbl`) | 12px | 500 | normal | `--muted` |

Marketing/landing headline runs `92.8px / 600 / -1.02px` — do not use inside the app.

---

## 4. App shell

Three columns, full viewport height, no page-level scroll.

```
┌────┬──────────┬───────────────────────────────────────────────┐
│ 40 │   232    │  topbar 58px: workspace chip · view chip ·     │
│ px │   px     │  search · theme · bell · profile              │
│    │          ├───────────────────────────────────────────────┤
│icon│ section  │  page-title / page-sub          [primary btn] │
│rail│ nav      │  filter row                                   │
│    │          │  content                                      │
└────┴──────────┴───────────────────────────────────────────────┘
```

```css
.shell         { display:flex; height:100vh; overflow:hidden; background:var(--app-bg,var(--canvas)); }
.shell-nav     { flex:0 0 auto; display:flex; }
.shell-main    { display:flex; flex-direction:column; flex:1 1 0; min-width:0; }
.shell-content { flex:1 1 0; overflow:auto; scroll-behavior:smooth; }
```

### Icon rail — 40px

Module switcher. Vertical stack of icon + 9px label. Active item gets `--brand-50`
background and `--brand` icon/label. ERP modules: Employee, Attendance, Leave &
Holidays, Requests & Approvals, Performance, Compensation & Payroll, Talent & Growth,
Announcements, Insights, Assets, **Projects**, Timesheet, **Tasks**.

### Section nav — 232px (`.hnav`)

```css
.hnav        { width:232px; flex:0 0 auto; display:flex; flex-direction:column;
               background:var(--surface); border-right:1px solid var(--line); }
.hnav-head   { display:flex; align-items:center; justify-content:space-between;
               padding:14px 14px 12px; border-bottom:1px solid var(--line-2); }
.hnav-badge  { width:32px; height:32px; display:flex; align-items:center;
               justify-content:center; border-radius:9px;
               background:var(--brand-50); color:var(--brand); flex:0 0 auto; }
.hnav-list   { flex:1 1 0; display:flex; flex-direction:column; gap:2px;
               padding:10px; overflow:auto; }
.hnav-item   { display:flex; align-items:center; gap:10px; padding:8px 10px;
               border-radius:var(--r-sm); font-size:13px; font-weight:500;
               color:var(--muted); transition:background .14s, color .14s; }
.hnav-item:hover   { background:var(--surface-sunken); color:var(--ink-2); }
.hnav-item.active  { background:var(--brand-50); color:var(--brand); }
.hnav-item.active svg { color:var(--brand); }
.hnav-foot   { padding:12px; border-top:1px solid var(--line-2); }
```

The nav head carries a 32px rounded badge with the module glyph, the module name
(14px/600) and a 11px `--muted` descriptor beneath it — e.g. **Tasks** / "Internal task
tracking", **Projects** / "Project configuration".

### Topbar — 58px

`background:#fff; border-bottom:1px solid var(--line); padding:0 16px; gap:14px;`

Left→right: workspace chip (square avatar with initial + name + tiny approval status),
a `.seg`-style view chip ("Manager view"), a wide search input with a `⌘K` key hint,
then right-aligned: theme toggle, notification bell (`.bell-dot`), profile button.

```css
.profile-btn { display:flex; align-items:center; gap:6px; padding:3px 5px 3px 3px;
               border:none; background:none; border-radius:var(--r-pill);
               transition:background .14s; }
.profile-btn:hover { background:var(--surface-sunken); }
```

---

## 5. Components

### Card / widget

```css
.widget       { background:var(--surface); border:1px solid var(--line);
                border-radius:var(--r-lg); box-shadow:var(--sh-card); overflow:hidden; }
.widget-head  { display:flex; align-items:center; justify-content:space-between;
                padding:14px 16px; border-bottom:1px solid var(--line); }
.widget-title { font-size:14px; font-weight:600; }
.widget-body  { padding:14px 16px; }
.widget-body.flush { padding:4px 0; }   /* when the body is a table */
```

### Metric tile

Used for the dashboard KPI row (`My Team 5`, `Active 3`, `Pending Documents 16`…).
Rendered as `<a class="metric link">` so the whole tile is a link.

```css
.metric { display:flex; flex-direction:column; gap:6px; padding:12px 14px;
          background:var(--surface); border:1px solid var(--line);
          border-radius:var(--r-lg); box-shadow:var(--sh-card);
          transition:box-shadow .16s, border-color .16s, transform .16s; }
.metric.link       { cursor:pointer; }
.metric.link:hover { box-shadow:var(--sh-md); border-color:var(--line-strong); }
.metric .m-top { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.metric .m-ico { display:flex; align-items:center; border-radius:var(--r-sm); }
.metric .m-lbl { font-size:12px; font-weight:500; color:var(--muted); }
```

Layout: tinted square icon (28px, `--r-sm`, status-tinted background) on the left of
`.m-top`, big number right-aligned, label underneath. Grid is `repeat(auto-fit,
minmax(150px, 1fr))` with a 12px gap.

### Buttons

```css
.btn { height:var(--control-h); display:inline-flex; align-items:center;
       justify-content:center; gap:7px; padding:0 13px; border-radius:var(--r-sm);
       border:1px solid transparent; font-size:13px; font-weight:500;
       white-space:nowrap; user-select:none;
       transition:background .15s, border-color .15s, box-shadow .15s, color .15s; }
.btn:active        { transform:translateY(.5px); }
.btn:disabled      { opacity:.55; cursor:not-allowed; box-shadow:none; }
.btn:focus-visible { outline:2px solid var(--brand); outline-offset:1px; }

.btn-primary          { background:var(--brand); color:#fff; box-shadow:var(--sh-xs); }
.btn-primary:hover    { background:var(--brand-700); }
.btn-secondary        { background:var(--surface-sunken); color:var(--ink);
                        border-color:var(--btn-edge); box-shadow:var(--sh-xs); }
.btn-secondary:hover  { background:var(--surface-hover); border-color:var(--ink-2); }
.btn-outline          { background:var(--surface); border-color:var(--line); }
```

Primary buttons live top-right of the page header and carry a leading 14px icon
(`+ New project`, `+ New item`, `Approval Center`).

### Status pill

```css
.pill { display:inline-flex; align-items:center; gap:5px; height:22px; padding:0 9px;
        border-radius:var(--r-pill); font-size:11.5px; font-weight:600;
        letter-spacing:.0575px; border:1px solid transparent; }
```

Fill is the status colour at **12% alpha**; text is the status colour at full
strength. Optional 5px leading dot.

| Status | Text | Background |
|---|---|---|
| Active / Done | `#2f9e74` | `rgba(47,158,116,.12)` |
| In progress | `#3a73c4` | `rgba(58,115,196,.12)` |
| Blocked / Overdue | `#cf4d44` | `rgba(207,77,68,.12)` |
| Pending / Awaiting approval | `#b67a1e` | `rgba(182,122,30,.12)` |
| Neutral / To do | `#515b6c` | `rgba(81,91,108,.12)` |

Measured from the live "Overdue" pill: `80×22`, `bg rgba(212,87,78,.12)`,
`color rgb(207,77,68)`, `radius 999px`, `padding 0 9px`, `font 11.5px/600`.

### Table

```css
.tbl-wrap { width:100%; overflow:auto; }
.tbl thead th { position:sticky; top:0; z-index:2; text-align:left;
                padding:11px 14px; font-size:11px; font-weight:600;
                letter-spacing:.04em; text-transform:uppercase;
                color:var(--muted); background:var(--surface-sunken);
                border-bottom:1px solid var(--line-strong); white-space:nowrap; }
.tbl tbody td { padding:13px 14px; vertical-align:middle; color:var(--ink-2);
                border-bottom:1px solid var(--line-2); }
.tbl tbody tr.clickable        { cursor:pointer; }
.tbl tbody tr.clickable:hover  { background:var(--surface-hover); }
```

Tables sit inside `.widget > .widget-body.flush`. First column carries a small tinted
icon tile next to the label.

### Segmented control

```css
.seg { display:inline-flex; gap:2px; padding:3px; border-radius:var(--r-sm);
       background:var(--surface-sunken); }
.seg button { padding:5px 12px; border:none; background:none; border-radius:var(--r-sm);
              font-size:12.5px; font-weight:500; color:var(--muted);
              transition:background .12s, color .12s; }
.seg button:hover  { color:var(--ink-2); }
.seg button.active { background:var(--surface); color:var(--ink); box-shadow:var(--sh-xs); }
```

### Filter row

Directly under the page header, above the content: a search input with a leading
magnifier, then a run of native-styled selects (`All types`, `All statuses`,
`All accounts`, `All owners`), then a right-aligned count (`3 projects`,
`0 open · 0 overdue`, `Total 0 logged`). All controls are `--control-h` (36px), 8px gap.

### Empty state

A `.widget` with a single centred `--muted` 14px line: `Nothing assigned to you.` /
`Empty`. No illustration, no CTA inside the card.

---

## 6. Reference layouts observed in the ERP

### 6a. Manager Dashboard (`/hrms`)

`page-title` + `page-sub`, primary button top-right (`Approval Center`).
A "Employee Life Cycle" label, then a 7-across metric grid, then a second 3-across row.
Below: stacked `.widget`s — "Workforce Movement" (two-column split with uppercase
11px column labels and muted empty copy), "Upcoming actions" (list rows: title +
muted descriptor left, status pill right), "Announcements", "Birthdays & Work
Anniversaries".

### 6b. Projects (`/project`)

Table view: `PROJECT | CODE | TYPE | ACCOUNT | OWNER | DELIVERY MANAGER | ACCOUNT
MANAGER | STATUS | TASKS | TIMESHEET | BILLING`. Project cell = tinted icon tile +
name. Code is mono-ish (`PRJ-0003`). Status column uses the green Active pill.
`+ New project` top-right.

### 6c. Access & Settings (`/project/settings`) — **the RBAC pattern to copy**

A `.widget` titled "Module members" with subtitle *"Workspace admins have full access
without a membership. People involved in a project (owner, delivery manager, account
manager, viewers, resources) see their projects automatically."* and a
`+ Add member` secondary button top-right of the widget head.

Table: `MEMBER | EMAIL | ROLE | STATUS | ⋯`. **Role is an inline `<select>` on each
row** (`Project Admin`, `Viewer`) — role changes are made in-place, not in a modal.
Status is an Active pill. Trailing icon button per row.

Below it, a narrow half-width `.widget` "Project code format" with three labelled
inputs (Prefix / Pad width / Next number), a live preview line (`Next code: PRJ-0001`)
and a right-aligned `Save` primary button.

### 6d. Task Board (`/tasks/board`) — **the kanban target**

`page-title` "Task Board", `page-sub` "Drag cards across columns, or use each card's
move control." Top-right: `Columns` (secondary) + `+ New item` (primary).

Filter row: **project select** (`BeWizor (PRJ-001)`) → search (`Search title / code`)
→ `All types` → `All priorities`, right-aligned `Total 0 logged`.

Four columns: **To Do · In Progress · Blocked · Done**. Each column is a `.widget`
with a header row and a count chip on the right:

```html
<div class="flex items-center gap-2 border-b px-3 py-2.5 border-[color:var(--line)]">
  <div class="min-w-0 flex-1 truncate text-sm font-semibold text-[color:var(--ink-2)]">To Do</div>
  <span class="count">0</span>
</div>
```

Columns are equal-width flex children, ~12px gap, min-height ~56px body with a centred
muted `Empty`.

> Note the accessibility affordance: *"or use each card's move control"* — drag-and-drop
> is never the only way to move a card. PM-OS must keep a keyboard/menu move control.

### 6e. Tasks nav

`My Tasks · Board · Backlog · All Tasks · Team` — mirror this shape for PM-OS
(`Board · Backlog · My Queue · Approvals · Founder's Office · Members`).

---

## 7. Interaction rules

- **Transitions:** 120–160ms. Nav `.14s`, buttons `.15s`, metrics `.16s`, segmented `.12s`.
- **Hover elevation:** cards go `--sh-card` → `--sh-md` and `--line` → `--line-strong`.
  Never translate on hover; only `translateY(.5px)` on button `:active`.
- **Focus:** `outline: 2px solid var(--brand); outline-offset: 1px`. Never `outline:none`.
- **Density:** table rows 13px vertical padding, nav items 8px, card padding 14/16px.
  This is a dense enterprise app — do not loosen it.
- **Theme:** `<html class="light|dark">`. Toggle lives in the topbar. Every new token
  needs a `.dark` counterpart.
- **Empty states are quiet.** One muted sentence. No illustrations.

---

## 8. Applying this to the PM-OS plugin

The PM-OS dashboard plugin ships as a plain IIFE (no build step) — see
`plans/007-frontend-pmo-dashboard-PLAN.md`. Therefore:

1. Put the whole of §2b, §4 and §5 into `plugins/pmo/dashboard/dist/style.css` as
   literal CSS. No Tailwind build is available inside the plugin bundle.
2. Scope every selector under a `.pmo-root` wrapper so the plugin cannot restyle the
   host Hermes dashboard.
3. Where the host's plugin SDK gives you a shadcn primitive (`SDK.components.Button`),
   prefer a plain `<button class="btn btn-primary">` instead — the SDK primitives carry
   Hermes' own theme, not Datansh's.
4. Fonts: self-host `Plus Jakarta Sans` (400/500/600/680) and `IBM Plex Mono` (400/500)
   into `web/public/fonts-datansh/` and `@font-face` them from the plugin stylesheet.
   Do **not** hotlink Google Fonts — the dashboard must work offline.

### Colour mapping for PM-OS concepts

| PM-OS concept | Token |
|---|---|
| Board column: To Do | neutral pill, `--muted` |
| Board column: In Progress | `--info` |
| Board column: Blocked | `--danger` |
| Board column: Done | `--success` |
| Awaiting approval | `--warning` |
| PM agent identity chip | `--brand-50` bg / `--brand` text |
| Worker agent identity chip | `--surface-sunken` bg / `--ink-2` text |
| Founder's office (human) chip | `--brand-100` bg / `--brand-700` text |
| `@mention` inline token | `--brand-50` bg / `--brand-600` text, `--r-sm`, 500 weight |
| Escalated to human | `--warning` pill + `--warning` left border on the card |
