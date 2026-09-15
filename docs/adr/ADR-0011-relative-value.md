# ADR-0011: Sector relative-value research

Status: Accepted for Phase 11, following Architecture v1.0 section 10. No change to the
mandatory alpha -> ensemble -> portfolio -> independent risk -> execution boundary.

Rank a configured weighted average of existing causal 30-minute stock-versus-sector and
stock-versus-NIFTY features within each eligible sector. These are relative returns, not
regression residuals; the separate beta estimator models exposure. Use tie-aware ranks,
stable-ID tie breaking, disjoint tails and minimum raw strength separation. Produce one
pair or one small basket per sector, with equal weights within each side before hedging.

Estimate simple-return OLS beta to the same NIFTY history with an intercept. Use only prior
completed/received canonical bars, excluding the decision bar. Reuse Phase 9's tested OLS
math without invoking cointegration diagnostics or adding dependencies. Reject unstable,
degenerate, nonpositive/out-of-range or incomplete fits.

For positive side betas BL and BS, long gross is BS/(BL+BS), short gross BL/(BL+BS).
This zeros fitted NIFTY beta. Reject if signed net/gross exceeds tolerance; do not relax
the beta hedge to achieve net neutrality. Weights are unit-gross research references, not
shares, capital allocation or risk approval. Realized neutrality is not guaranteed.

Use UUID5 listing identities from normalized exchange, segment and ISIN. Tokens/symbols
remain mapping attributes. Explicit complete timestamped inventory reserves all active/pending
stat-arb securities, including partial exits. Resolve legacy pair intents through their
entry-date master. Missing mappings/inventory block entries; any same-security overlap is
excluded regardless of side. No automatic netting or inventory release is allowed.

RelativeValueIntent carries all legs in one event and existing alpha JSONB projection.
No migration is necessary. Later portfolio/execution phases must atomically reserve concurrent
decisions and recheck inventory, rounded quantities and exposures. Settings and engine block
LIVE until independent validation is implemented.
