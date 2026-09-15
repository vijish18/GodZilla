"""Receipt-ordered router with immediate veto and conservative transition permissions."""

from datetime import datetime

from godzilla.core.health import HealthStatus
from godzilla.features.models import FeatureSnapshot, content_hash
from godzilla.market_data.calendar import CalendarCoverageError, ExchangeCalendar
from godzilla.market_data.metrics import Metrics
from godzilla.market_data.models import utc
from godzilla.market_data.quality import session_bounds
from godzilla.market_state.models import (
    AlphaFamily,
    MarketState,
    MarketStateEvidence,
    RouterSettings,
    RoutingHealth,
    StateDecision,
)
from godzilla.market_state.rules import EvidenceBuilder, classify, permissions


class MarketStateRouter:
    def __init__(
        self, settings: RouterSettings, calendar: ExchangeCalendar, timezone: str, metrics: Metrics
    ) -> None:
        self.settings, self.calendar, self.timezone, self.metrics = (
            settings,
            calendar,
            timezone,
            metrics,
        )
        self._last: StateDecision | None = None
        self._identity: str | None = None
        self._pending: MarketState | None = None
        self._count = 0
        self._feature_time: datetime | None = None

    def route(
        self, snapshot: FeatureSnapshot, health: RoutingHealth, at: datetime
    ) -> StateDecision:
        at = utc(at)
        identity = snapshot.snapshot_hash() + content_hash(health) + at.isoformat()
        last, cfg = self._last, self.settings
        if last and at < last.timestamp:
            raise ValueError("router input time regressed; replay in chronological order")
        if last and identity == self._identity:
            return last
        if last and at == last.timestamp:
            raise ValueError("conflicting router input at same timestamp")
        e = EvidenceBuilder(snapshot)
        safe = (
            e.record(
                "health.system_status",
                float(health.system_status),
                "eq",
                float(HealthStatus.HEALTHY),
            ),
            e.record(
                "health.risk_status", float(health.risk_status), "eq", float(HealthStatus.HEALTHY)
            ),
            e.record("health.blocks_new_entries", float(health.blocks_new_entries), "eq", 0),
            e.record("health.age_seconds", (at - health.observed_at).total_seconds(), "ge", 0),
            e.record(
                "health.age_seconds",
                (at - health.observed_at).total_seconds(),
                "le",
                cfg.health_max_age_seconds,
            ),
            e.record("feature.age_seconds", (at - snapshot.timestamp).total_seconds(), "ge", 0),
            e.record(
                "feature.age_seconds",
                (at - snapshot.timestamp).total_seconds(),
                "le",
                cfg.feature_max_age_seconds,
            ),
        )
        try:
            bounds = session_bounds(self.calendar, at, self.timezone)
        except CalendarCoverageError:
            bounds = None
        opening = bounds[0] if bounds else None
        in_session = bool(bounds and bounds[0] <= snapshot.timestamp <= at < bounds[1])
        e.record("session.confirmed_active", float(in_session), "eq", 1)
        candidate = (
            classify(e, cfg, (at - opening).total_seconds()) if opening else MarketState.RISK_OFF
        )
        reasons = ["RULE_" + candidate.value]
        if not all(safe) or not in_session:
            candidate = MarketState.RISK_OFF
            reasons.append("SAFETY_VETO")
        new_session = last is None or opening != last.evidence.session_open
        previous = last.state if last else MarketState.RISK_OFF
        state = MarketState.RISK_OFF if new_session else previous
        since = at if new_session else last.state_since if last else at
        gap = (
            last is not None
            and (at - last.timestamp).total_seconds() > cfg.max_confirmation_gap_seconds
        )
        if new_session or gap:
            self._pending, self._count = None, 0
        fresh_observation = self._feature_time is None or snapshot.timestamp > self._feature_time
        if candidate != self._pending:
            self._pending, self._count = candidate, 0
        if fresh_observation:
            self._count += 1
        immediate = candidate in (
            MarketState.RISK_OFF,
            MarketState.OPEN_SHOCK,
            MarketState.HIGH_VOL,
        )
        dwell = (at - since).total_seconds()
        e.record("transition.confirmations", self._count, "ge", cfg.confirmation_count)
        e.record("transition.dwell_seconds", dwell, "ge", cfg.min_duration_seconds)
        if candidate != state and (
            immediate
            or (self._count >= cfg.confirmation_count and dwell >= cfg.min_duration_seconds)
        ):
            state, since = candidate, at
        elif candidate != state:
            reasons.append("TRANSITION_PENDING")
            self.metrics.increment("market_state.suppressed_transitions")
        families, sides, multiplier = permissions(state, cfg)
        wanted, wanted_sides, wanted_multiplier = permissions(candidate, cfg)
        # Pending transitions cannot retain permissions that current evidence disallows.
        families = tuple(f for f in families if f in wanted)
        sides = tuple(side for side in sides if side in wanted_sides)
        if not sides:
            families = tuple(
                f
                for f in families
                if f
                not in (
                    AlphaFamily.CROSS_SECTIONAL_MOMENTUM,
                    AlphaFamily.MOMENTUM_BREAKOUT,
                )
            )
        multiplier = min(multiplier, wanted_multiplier) if families else 0
        if last and state != last.state:
            self.metrics.increment("market_state.transitions")
            self.metrics.increment(f"market_state.transition.{last.state.value}.{state.value}")
            self.metrics.observe(
                "market_state.duration_seconds", (at - last.state_since).total_seconds()
            )
            if (at - last.state_since).total_seconds() < cfg.flicker_window_seconds:
                self.metrics.increment("market_state.flicker_transitions")
        self.metrics.increment("market_state.decisions")
        self.metrics.increment("market_state.state." + state.value)
        decision = StateDecision(
            timestamp=at,
            state=state,
            candidate=candidate,
            previous_state=previous,
            state_since=since,
            confirmation_count=self._count,
            allowed_alpha_families=families,
            directional_sides=sides,
            gross_risk_multiplier=multiplier,
            evidence=MarketStateEvidence(
                feature_snapshot_hash=snapshot.snapshot_hash(),
                feature_set_hash=snapshot.feature_set_hash,
                router_version_hash=cfg.version_hash(),
                thresholds=tuple(e.checks),
                health=health,
                session_open=opening,
                reasons=tuple(reasons),
            ),
            settings=cfg,
            code_commit=snapshot.code_commit,
            config_hash=snapshot.config_hash,
            data_snapshot=snapshot.data_snapshot,
            prior_decision_hash=last.decision_hash() if last else None,
        )
        self._last, self._identity = decision, identity
        self._feature_time = (
            max(snapshot.timestamp, self._feature_time)
            if self._feature_time
            else snapshot.timestamp
        )
        return decision
