# ADR-0009: Research-first causal pairs

Status: Accepted for Phase 9; no change to the architecture's execution or risk boundaries.

Use positive log-price OLS with an intercept, fitting exactly the configured number of
completed historical bars before the evaluated bar. Statistical diagnostics are injected
through `PairDiagnostics`. Pin statsmodels to 0.14.6 for Engle–Granger cointegration and
ADF p-values; implementing calibrated statistical tests locally would add avoidable risk.
Use a configured fixed lag and no automatic lag search. The adapter records the installed
statsmodels, NumPy, SciPy and pandas versions. Transitive dependencies are not a lockfile.
The [official cointegration API](https://www.statsmodels.org/stable/generated/statsmodels.tsa.stattools.coint.html)
documents the I(1) assumption and lag controls. Level-series ADF checks screen that assumption;
passing these tests is not proof of a stable economic relationship or profitability.

Freeze the entry fit for open-position z-scores and hedge references. Refit prior bars only
to monitor relationship deterioration. Gross fractions describe a beta hedge, not quantities,
exact dollar neutrality, market-factor neutrality or independent risk approval.

Publish both legs in one immutable event and one existing `alpha_signals` JSONB projection.
No migration is necessary. Projection failure rolls back the audit write and prevents dispatch.
Existing single-stock serialization stays compatible. Broker atomicity is explicitly unknown:
future execution must validate the whole pair through portfolio and independent risk, reconcile
ambiguous submissions, and handle partial-leg exposure.

LIVE is rejected both in configuration activation and unconditionally by the engine. This
phase provides no broker, allocator, production position book or executable risk decisions.
