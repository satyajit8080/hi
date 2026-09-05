# Interface design notes

## Subject

A trading terminal for people who are being asked to trust a signal engine with money. The primary
job of the interface is not to look impressive — it is to make the evidence legible, including the
parts that are unflattering.

That framing drives every choice below. Where a normal SaaS product would reach for reassurance, this
one reaches for a number and its sample size.

## Colour

| Token | Hex | Role |
|---|---|---|
| `base.900` | `#0A0E14` | Page. Cold graphite, deliberately not a tinted near-black. |
| `base.800` | `#0F141C` | Panel surface |
| `base.700` | `#151C26` | Raised rows, grid lines |
| `base.600` / `500` | `#1D2733` / `#283444` | Controls, scroll thumbs |
| `edge` | `#22303F` | Every border and rule |
| `ink.100 → 400` | `#E8EDF4 → #5C6979` | Text, four steps of emphasis |
| `long` | `#3DD68C` | Bid, long, win |
| `short` | `#FF5C6C` | Ask, short, loss |
| `warn` | `#F2B441` | **Degraded data only** |

Two semantic accents, because in a book, a ladder and a P&L column those colours carry meaning
rather than decoration. Amber is reserved exclusively for data-quality problems, so a single amber
element anywhere on screen means one thing and is never diluted by using it for emphasis.

The base is a cold blue-grey rather than `#0B0B0B` or `#111`: on a bright monitor, mint and coral
read cleanly against it, and the tinted-near-black look is a generated-design tell.

## Type

Inter for prose, JetBrains Mono for every number.

Monospace here is functional, not stylistic — tabular figures stop prices from reflowing as they
tick, which is the difference between a readable ladder and a jittering one. The `.num` utility
carries `font-variant-numeric: tabular-nums` and is applied to every figure in the product.

Scale is compressed on purpose: `micro` 10.5px and `tick` 11.5px do most of the work, with size
reserved for the two or three numbers that actually deserve it. A terminal is dense; giving every
label 14px would push the useful content below the fold.

## Structure

The unit is a **panel**, not a card: a bordered surface with a labelled header rule, no shadow, no
elevation, no rounded-corner softness (3px, effectively square). Panels butt against each other in
grids separated by hairlines. Nothing floats.

The stat strips on the landing and performance pages use `gap-px` over an `edge`-coloured background,
so the dividing lines are the grid gaps themselves rather than added borders — dense, and structurally
honest about the fact that these are columns of one table.

## Motion

One animation exists: `pulse-soft`, 2.6s, opacity 1 → 0.35, on live indicators and skeletons. Slow
and low-amplitude, because a terminal that throbs is a terminal people stop reading. No entrance
transitions, no hover lifts, no scroll reveals. `prefers-reduced-motion` cuts even this.

## Decisions worth naming

**The hero is the ledger, not a promise.** The landing page opens by running a live chain
verification and printing the result, the signal count and the current chain head. The claim is "you
can check"; the most direct way to make it is to check on page load. A gradient headline with a big
number would have been the default treatment and would have said nothing.

**Drivers and context are visually separated.** In the "Why this signal" panel, factors that moved
the score render with contribution bars; on-chain and whale factors render below a rule, greyed, with
an explicit `weight 0.000` chip. Blending them into one list would be exactly the theatre the product
exists to avoid — and the backend independently refuses to publish if a context code carries weight.

**The book is drawn as a book.** Asks descend into the spread from above, bids sit below, size
renders as a proportional fill behind the numbers. A trader reads relative depth from the fills
without parsing a single figure.

**Losses have their own section.** On the performance page, a dedicated losing-signals table sits
above the fold-out, with MAE alongside the result. Most signal products bury this. Surfacing it is
the strongest available signal that the rest of the numbers are real.

**Empty and degraded states carry direction, not apology.** "No open signals right now — the engine
publishes only when a setup clears its gates. Quiet periods are normal and are not a fault." The
DATA DELAYED panel explains what broke, shows venue count and feed lag, and says plainly that showing
nothing beats showing something wrong.

## Accessibility floor

Keyboard focus is a 2px mint ring at 2px offset, never removed. Charts carry `role="img"` with
descriptive labels, and every figure they encode is also available as text nearby. Direction is never
communicated by colour alone — LONG/SHORT is always spelled out next to the tint. Layouts collapse to
a single column below `lg`, with the ticker rail scrolling horizontally rather than wrapping.
