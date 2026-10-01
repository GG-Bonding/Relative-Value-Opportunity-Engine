# Opportunity lifecycle

States: `DISCOVERED`, `WATCHING`, `READY`, `ENTERED`, `CONVERGING`, `EXIT_READY`, `EXITED`, `INVALIDATED`, `EXPIRED`, `REJECTED`.

Terminal states do not transition. The legal graph is `opportunities.status.transition`. An illegal move raises.

## What an opportunity stores

Identity, pair, direction, discovery time, market price, fair value, mispricing in price and in z, fundamental score and its attribution, expected fundamentals, regime, alpha state and health, positioning, crowding, the opportunity score and its components, expected edge, cost, and net edge, entry strategy and zone, target zone, expected holding period, the trade thesis, invalidation conditions, and status.

## Decision states

`LONG`, `SHORT`, `NO_TRADE`, `WATCH`, `WAIT_PULLBACK`, `WAIT_BREAKOUT`, `WAIT_CONFIRMATION`, `MODEL_UNCERTAIN`, `ALPHA_DECAYING`, `DATA_DEGRADED`.

The gates run in a fixed order: data, model error, liquidity, regime break, event dislocation, alpha decay, dead alpha, reaction lag, unvalidated alpha, regime compatibility, fundamental conflict, minimum gap, net edge, crowding, chase, breakout, and only then a tradable mispricing. A strong fundamental short whose gap has already closed is not a short.

## Entry and exit

Direction is the fade of a positive residual (EUR rich versus fair value is `SHORT`). Entry strategy is market, limit pullback, breakout confirmation, or wait. The zone width comes from the live gap, the pullback fraction, and the spread. It is not an oscillator level.

Exit reasons: mispricing closed, thesis invalidated, alpha decayed, time expired, stop, target reached, regime change, liquidity event. Target reached uses the current fair value. A gap that has shrunk because fair value moved is an exit even if the original target is untouched.

## Score

The opportunity score is a weighted sum of the components that are present, renormalized when a component is missing. It is not a product of hard-coded multipliers, and a high score does not override a failed gate.
