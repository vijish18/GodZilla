"""Journal first: health, ensemble and proposed targets have no execution authority."""

from collections.abc import Callable
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from godzilla.core.events.journal import EventJournal
from godzilla.core.events.models import Decision, EventEnvelope, Health, Provenance
from godzilla.core.events.serialization import idempotency_key
from godzilla.ensemble.models import AlphaHealthSnapshot
from godzilla.portfolio.models import EnsembleDecision, PortfolioDecision


def publish_health(
    journal: EventJournal,
    snapshot: AlphaHealthSnapshot,
    correlation_id: UUID,
    received_at: datetime,
    emit: Callable[[EventEnvelope], None] | None = None,
) -> bool:
    identity = f"{snapshot.alpha_id}:{snapshot.timestamp.isoformat()}"
    event = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:alpha-health:" + identity),
        correlation_id=correlation_id,
        occurred_at=snapshot.timestamp,
        received_at=received_at,
        source="alpha-health",
        payload=Health(
            component=snapshot.alpha_id.value,
            status="HEALTHY",
            blocking=False,
            reason="RESEARCH_PERFORMANCE_NOT_SYSTEM_HEALTH",
            alpha_snapshot=snapshot,
        ),
        provenance=Provenance(
            code_commit=snapshot.code_commit,
            config_hash=snapshot.config_hash,
            data_snapshot=snapshot.data_snapshot,
            feature_version=snapshot.settings.version,
        ),
    )
    return journal.publish(event, idempotency_key("alpha-health", identity), emit=emit)


def publish_decision(
    journal: EventJournal,
    decision: EnsembleDecision | PortfolioDecision,
    provenance: Provenance,
    correlation_id: UUID,
    received_at: datetime,
    emit: Callable[[EventEnvelope], None] | None = None,
) -> bool:
    is_portfolio = isinstance(decision, PortfolioDecision)
    payload = Decision(
        kind="PORTFOLIO_DECISION" if is_portfolio else "ENSEMBLE_DECISION",
        decision_id=decision.decision_id,
        intent_id=decision.decision_id,
        accepted=False,
        reasons=decision.reasons,
        portfolio=decision if isinstance(decision, PortfolioDecision) else None,
        ensemble=decision if isinstance(decision, EnsembleDecision) else None,
    )
    event = EventEnvelope(
        event_id=uuid5(NAMESPACE_URL, "godzilla:allocation-event:" + str(decision.decision_id)),
        correlation_id=correlation_id,
        occurred_at=decision.timestamp,
        received_at=received_at,
        source="portfolio" if is_portfolio else "ensemble",
        payload=payload,
        provenance=provenance,
    )
    return journal.publish(
        event, idempotency_key("allocation", str(decision.decision_id)), emit=emit
    )
