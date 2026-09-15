# Phase 11: Sector-neutral relative value

`SectorRelativeValue.evaluate(observations, universe, master, state, inventory, at, mode)`
returns combined intents and reason-coded exclusion/construction reports. All `relative_value`
settings are unoptimized research/paper starting values. No broker, order, portfolio allocation,
production position book or profitability claim is introduced.

## Inputs and ranking

Each `RelativeValueObservation` contains a current Phase 5 `MomentumObservation` and synchronized
stock/NIFTY bars. Require the complete long-universe cross-section at `at`; missing/duplicate
members block evaluation. Future snapshots are ignored. Validate universe/master date, generation
time, identity uniqueness, snapshot linkage, feature context/hash and release/data provenance.
A context's sector instrument identifier must match the master sector identifier; providers must
use consistent sector codes. Sector membership uses the effective master and known feature context
for the evaluated day, never today's membership applied retrospectively.

Exclude inactive, suspended, surveillance, ambiguous, illiquid, inventory-reserved or beta-invalid
instruments with reasons. The remaining eligible population must meet minimum sector size (four).
Liquidity uses versioned spread, quote-age, ADV and turnover features; missing values reject.
Short legs also require short-universe permission and F&O eligibility.

Strength is `w * stock.relative_sector_30m + (1-w) * stock.relative_market_30m`, initially w=.5.
Rank separately within sectors using tie-aware percentiles and configured tails (25% initially).
Break selection ties by stable security ID. Require minimum strength separation between the
weakest leader and strongest laggard. Equal values cannot create a trade. PAIR selects one per
side; BASKET selects `members_per_side` (two initially). Insufficient tails reject rather than
silently shrinking the basket. The engine creates at most one combined candidate per sector.

## Training and exposure math

The rolling baseline uses 24 simple returns, requiring 25 synchronized prior five-minute bars
in the current session. The training endpoint equals `at - 5m`; both bars and receipts must be
available at that cutoff. The current decision bar and later receipts cannot affect the fit.
No overnight returns, missing-interval interpolation or future fills are used. History must be
contiguous and session aligned; decision timestamps must be canonical five-minute closes.

OLS with intercept estimates stock returns on NIFTY returns. Screen beta bounds and relative
split-half drift; zero regressor variance rejects. All included estimates must use an identical
hashed NIFTY training snapshot. Fits retain method, time bounds, sample count and data hashes.
This short intraday window is a research baseline, not proof of a reliable beta estimate.

For equal-within-side baskets, BL and BS are mean leg betas. Long gross L=BS/(BL+BS), short
gross S=BL/(BL+BS), hedge ratio S/L=BL/BS. Each leg receives its side's gross divided by n.
Signed net/gross is L-S; signed beta/gross is L*BL-S*BS. Validate both against configured
tolerances (15% net and .02 beta per unit gross initially). Infeasible net exposure rejects.
Example: BL=1.09, BS=1 gives L=1/2.09, S=1.09/2.09, zero fitted beta and about -4.31% net.
There is no sizing-to-recover-P&L logic. Same-sector membership does not guarantee neutrality
to every sector factor, and estimated beta neutrality does not guarantee realized neutrality.

## Stable identity and inventory

`security_id` derives UUID5 from normalized exchange/segment/ISIN, independent of token or symbol.
Basket IDs derive from sorted constituent IDs, independent of direction and order. Duplicate
listing identities in a master reject. Cross-listed segments remain distinct. Corporate-action
ISIN changes require upstream identity reconciliation; continuity is not invented by the alpha.

`RelativeValueInventory` must be explicitly complete and fresh (30 seconds initially), even
when empty. Reservations include active and pending stat-arb/RV exposure. Every overlapping
security is excluded in either direction. `stat_arb_inventory` converts caller-supplied active/
pending Phase 9 intents through their archived entry-date master; it cannot infer active positions
from event history. Unknown tokens or future/later mappings raise errors. Keep both legs reserved
until reconciliation confirms release, including partial exits. Mark incomplete inventory as such.

This read-only check is not a concurrent reservation service. Later ensemble/portfolio execution
must reserve candidates atomically and recheck inventory, actual rounded quantities and independent
risk. No broker submission or automatic release occurs here.

## Output and validation

`RelativeValueIntent` carries both sides, hedge ratio, residuals/ranks, beta fits, combined exposure
reference, calendar flatten cutoff, paired execution requirements and provenance. All legs share
one sector and one durable event. The existing projection uses `RV:<basket UUID>` and rank separation
as descriptive score, not confidence. Publication is idempotent; conflicts or critical projection
failure prevent dispatch and halt the journal. No SQL migration is needed.

State/router permission and fresh healthy risk/system evidence are required; RISK_OFF and
OPEN_SHOCK always veto. LIVE is unconditionally blocked. Independent promotion still requires
chronological OOS evidence after realistic multi-leg costs, slippage, partial fills, parameter
stability, selection-bias diagnostics and paper validation.

Tests cover known-beta weight arithmetic, pair/basket ranking, ties, missing cross-sections,
point-in-time membership, causal/stable estimation, token-independent overlap, exposure validation
and atomic PostgreSQL publication. Run offline suites with `python -m pytest -m "not postgres"`;
run all suites and coverage with `python scripts/test_postgres.py`.
