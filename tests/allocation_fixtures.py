from datetime import timedelta
from decimal import Decimal
from itertools import combinations
from uuid import UUID

from godzilla.config.modes import TradingMode
from godzilla.ensemble.health import measure_health
from godzilla.ensemble.models import (
    AlphaHealthSettings,
    AlphaId,
    AlphaRegistry,
    CandidateLeg,
    NormalizedCandidate,
)
from godzilla.features.models import content_hash
from godzilla.market_state.models import AlphaFamily
from godzilla.portfolio.allocator import AllocationInputs
from godzilla.portfolio.models import (
    AllocatorSettings,
    CandidateRiskEstimate,
    CorrelationEstimate,
    CorrelationSnapshot,
    EnsembleDecision,
    LiquidityCapacity,
    PortfolioSnapshot,
)
from tests.momentum_fixtures import momentum_inputs


def allocation_inputs(checker, count=4, basket=False):
    _, _, _, state, at = momentum_inputs(checker)
    registry = AlphaRegistry()
    state = state.model_copy(update={"allowed_alpha_families": tuple(AlphaFamily)})
    candidates = []
    ids = []
    for index in range(count):
        legs = []
        for leg in range(2 if basket else 1):
            entity = UUID(int=100 + 2 * index + leg)
            ids.append(entity)
            legs.append(
                CandidateLeg(
                    entity_id=entity,
                    instrument_id=str(entity),
                    sector="S" + str(index % 2),
                    signed_weight=Decimal("0.5") * (1 if leg == 0 else -1)
                    if basket
                    else Decimal(1 if index % 2 == 0 else -1),
                )
            )
        candidates.append(
            NormalizedCandidate(
                signal_id=UUID(int=index + 1),
                alpha_id=tuple(AlphaId)[index % 5],
                timestamp=at,
                strength=Decimal("0.8"),
                legs=tuple(legs),
                raw_intent_hash="a" * 64,
                registry_hash=content_hash(registry),
            )
        )
    health = tuple(
        measure_health(
            a,
            (),
            at,
            AlphaHealthSettings(),
            code_commit=state.code_commit,
            config_hash=state.config_hash,
            data_snapshot=state.data_snapshot,
        )
        for a in AlphaId
    )
    return AllocationInputs(
        ensemble=EnsembleDecision(
            decision_id=UUID(int=999),
            timestamp=at,
            candidates=tuple(candidates),
            rejected=(),
            reasons=(),
            input_hash="b" * 64,
        ),
        registry=registry,
        health=health,
        portfolio=PortfolioSnapshot(
            snapshot_id="empty",
            timestamp=at,
            equity=Decimal(1000000),
            complete=True,
            blocks_new_entries=False,
        ),
        liquidity=tuple(
            LiquidityCapacity(
                entity_id=entity,
                timestamp=at,
                valid_until=at + timedelta(seconds=30),
                price=Decimal(100),
                adv_shares=Decimal(10000000),
                daily_turnover=Decimal(1000000000),
                max_notional=Decimal(1000000),
                source_snapshot="liquidity-fixture",
                eligible=True,
            )
            for entity in ids
        ),
        estimates=tuple(
            CandidateRiskEstimate(
                signal_id=c.signal_id,
                timestamp=at,
                loss_per_gross=Decimal("0.01"),
                source_snapshot="risk-proxy-not-approval",
            )
            for c in candidates
        ),
        correlations=CorrelationSnapshot(
            timestamp=at,
            training_end=at - timedelta(minutes=5),
            estimates=tuple(
                CorrelationEstimate(left=a, right=b, value=Decimal("0.8"))
                for a, b in combinations(ids, 2)
            ),
            source_snapshot="prior-correlations",
        ),
        state=state,
        settings=AllocatorSettings(),
        at=at,
        mode=TradingMode.RESEARCH,
    )
