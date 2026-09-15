"""One transaction per accepted event, including projections and optional state."""

import hashlib
import json

from sqlalchemy import Engine, create_engine, or_, select, text
from sqlalchemy.orm import Session

from godzilla.core.events.models import (
    BarClose,
    EventEnvelope,
    FeatureUpdate,
    MarketStateUpdate,
    QuoteUpdate,
    SystemStateSnapshot,
)
from godzilla.core.events.serialization import event_hash
from godzilla.features.models import FeatureSnapshot
from godzilla.market_data.instruments import InstrumentMasterSnapshot
from godzilla.market_state.models import StateDecision
from godzilla.storage.models import (
    AuditEventRow,
    Bar1mRow,
    Bar5mRow,
    FeatureSnapshotRow,
    FeatureVersionRow,
    InstrumentRow,
    InstrumentVersionRow,
    MarketStateRow,
    ProvenanceVersionRow,
    QuoteRow,
    SystemStateRow,
)


class IdempotencyConflict(ValueError):
    pass


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if engine.dialect.name != "postgresql":
        engine.dispose()
        raise ValueError("durable storage requires PostgreSQL")
    return engine


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PostgresRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def append(
        self, event: EventEnvelope, key: str, *, state: SystemStateSnapshot | None = None
    ) -> bool:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("idempotency key must be a SHA256 hex digest")
        if state and (
            state.last_event_id != event.event_id or state.updated_at != event.received_at
        ):
            raise ValueError("state must reference this event and its receipt timestamp")
        digest = event_hash(event)
        with Session(self.engine) as session, session.begin():
            # Serialize append/commit order across processes, not just within a Python lock.
            session.execute(text("SELECT pg_advisory_xact_lock(471104)"))
            previous = session.scalar(
                select(AuditEventRow).where(
                    or_(
                        AuditEventRow.event_id == event.event_id,
                        AuditEventRow.idempotency_key == key,
                    )
                )
            )
            if previous is not None:
                if previous.payload_hash != digest or previous.idempotency_key != key:
                    raise IdempotencyConflict("event identity reused with different content")
                if state is not None:
                    # State is part of the transaction identity, never an update on redelivery.
                    stored_state = previous.envelope.get("_state_projection")
                    if stored_state != state.model_dump(mode="json"):
                        raise IdempotencyConflict("duplicate event has different state projection")
                elif "_state_projection" in previous.envelope:
                    raise IdempotencyConflict("duplicate omitted original state projection")
                return False
            tail = session.scalar(
                select(AuditEventRow).order_by(AuditEventRow.sequence.desc()).limit(1)
            )
            if tail and event.received_at < tail.received_at:
                raise ValueError(
                    "receipt order regression; capture late events with actual receipt time"
                )
            provenance = event.provenance.model_dump(mode="json")
            provenance_id = _hash(provenance)
            if session.get(ProvenanceVersionRow, provenance_id) is None:
                session.add(ProvenanceVersionRow(version_id=provenance_id, **provenance))
                session.flush()
            previous_hash = tail.chain_hash if tail else "0" * 64
            envelope = event.model_dump(mode="json")
            if state is not None:
                envelope["_state_projection"] = state.model_dump(mode="json")
            chain_hash = _hash([previous_hash, key, envelope])
            row = AuditEventRow(
                event_id=event.event_id,
                correlation_id=event.correlation_id,
                idempotency_key=key,
                event_type=event.payload.kind,
                occurred_at=event.occurred_at,
                received_at=event.received_at,
                source=event.source,
                schema_version=event.schema_version,
                provenance_id=provenance_id,
                payload_hash=digest,
                previous_hash=previous_hash,
                chain_hash=chain_hash,
                envelope=envelope,
            )
            session.add(row)
            session.flush()
            self._project(session, event)
            if state is not None:
                session.merge(
                    SystemStateRow(**state.model_dump(), payload=state.model_dump(mode="json"))
                )
        return True

    @staticmethod
    def _project(session: Session, event: EventEnvelope) -> None:
        if isinstance(event.payload, MarketStateUpdate) and event.payload.decision is not None:
            decision = event.payload.decision
            session.add(
                MarketStateRow(
                    decision_hash=decision.decision_hash(),
                    event_id=event.event_id,
                    timestamp=decision.timestamp,
                    state=decision.state.value,
                    config_version=decision.evidence.router_version_hash,
                    payload=decision.model_dump(mode="json"),
                )
            )
        if isinstance(event.payload, FeatureUpdate) and event.payload.snapshot is not None:
            snapshot, definition = event.payload.snapshot, event.payload.definition
            if definition is None:
                raise ValueError("missing feature definition")
            if session.get(FeatureVersionRow, snapshot.feature_set_hash) is None:
                session.add(
                    FeatureVersionRow(
                        version_hash=snapshot.feature_set_hash,
                        name=definition.name,
                        definition=definition.model_dump(mode="json"),
                    )
                )
                session.flush()
            session.add(
                FeatureSnapshotRow(
                    snapshot_hash=snapshot.snapshot_hash(),
                    event_id=event.event_id,
                    feature_set_hash=snapshot.feature_set_hash,
                    entity=snapshot.entity,
                    timestamp=snapshot.timestamp,
                    input_hash=snapshot.input_hash,
                    payload=snapshot.model_dump(mode="json"),
                )
            )
        if isinstance(event.payload, BarClose):
            bar = event.payload.bar
            model = Bar1mRow if bar.interval_minutes == 1 else Bar5mRow
            session.add(
                model(
                    event_id=event.event_id,
                    instrument_id=bar.instrument_id,
                    bar_start=bar.start,
                    bar_end=bar.end,
                    source=bar.source,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    complete=bar.complete,
                    quality_flags=sorted(flag.value for flag in bar.quality_flags),
                    payload=bar.model_dump(mode="json"),
                )
            )
        elif isinstance(event.payload, QuoteUpdate):
            quote = event.payload.quote
            session.add(
                QuoteRow(
                    event_id=event.event_id,
                    instrument_id=quote.instrument_id,
                    timestamp=quote.timestamp,
                    source=quote.source,
                    bid=quote.bid,
                    ask=quote.ask,
                    last=quote.last,
                    payload=quote.model_dump(mode="json"),
                )
            )

    def read_events(self, after_sequence: int = 0) -> tuple[EventEnvelope, ...]:
        with Session(self.engine) as session:
            rows = session.scalars(select(AuditEventRow).order_by(AuditEventRow.sequence)).all()
            previous_hash = "0" * 64
            events: list[EventEnvelope] = []
            for row in rows:
                if row.previous_hash != previous_hash or row.chain_hash != _hash(
                    [previous_hash, row.idempotency_key, row.envelope]
                ):
                    raise ValueError("audit chain integrity failure")
                envelope = {
                    key: value for key, value in row.envelope.items() if key != "_state_projection"
                }
                event = EventEnvelope.model_validate(envelope)
                if row.payload_hash != event_hash(event):
                    raise ValueError("event digest mismatch")
                previous_hash = row.chain_hash
                if row.sequence > after_sequence:
                    events.append(event)
            return tuple(events)

    def get_state(self, scope: str) -> SystemStateSnapshot | None:
        with Session(self.engine) as session:
            row = session.get(SystemStateRow, scope)
            return SystemStateSnapshot.model_validate(row.payload) if row else None

    def save_master(self, snapshot: InstrumentMasterSnapshot) -> None:
        if snapshot.generated_at.tzinfo is None or snapshot.generated_at.utcoffset() is None:
            raise ValueError("instrument snapshot receipt must be timezone-aware")
        digest = _hash(snapshot.model_dump(mode="json"))
        with Session(self.engine) as session, session.begin():
            session.execute(text("SELECT pg_advisory_xact_lock(471104)"))
            previous = session.get(InstrumentVersionRow, snapshot.snapshot_id)
            if previous:
                if previous.content_hash != digest:
                    raise IdempotencyConflict("instrument snapshot ID content conflict")
                return
            session.add(
                InstrumentVersionRow(
                    snapshot_id=snapshot.snapshot_id,
                    version=snapshot.version,
                    content_hash=digest,
                    as_of=snapshot.as_of,
                    generated_at=snapshot.generated_at,
                    source_reference=snapshot.source_reference,
                    payload=snapshot.model_dump(mode="json"),
                )
            )
            session.flush()
            for instrument in snapshot.instruments:
                session.add(
                    InstrumentRow(
                        snapshot_id=snapshot.snapshot_id,
                        token=instrument.token,
                        symbol=instrument.symbol,
                        isin=instrument.isin,
                        exchange=instrument.exchange,
                        segment=instrument.segment,
                        tick_size=instrument.tick_size,
                        status=instrument.status,
                        fo_eligible=instrument.fo_eligible,
                        sector=instrument.sector,
                        effective_from=instrument.effective_from,
                        effective_to=instrument.effective_to,
                        payload=instrument.model_dump(mode="json"),
                    )
                )

    def get_master(self, snapshot_id: str) -> InstrumentMasterSnapshot | None:
        with Session(self.engine) as session:
            row = session.get(InstrumentVersionRow, snapshot_id)
            if row is None:
                return None
            if _hash(row.payload) != row.content_hash:
                raise ValueError("instrument snapshot integrity failure")
            return InstrumentMasterSnapshot.model_validate(row.payload)

    def get_feature_snapshot(self, snapshot_hash: str) -> FeatureSnapshot | None:
        with Session(self.engine) as session:
            row = session.get(FeatureSnapshotRow, snapshot_hash)
            if row is None:
                return None
            snapshot = FeatureSnapshot.model_validate(row.payload)
            if snapshot.snapshot_hash() != snapshot_hash:
                raise ValueError("feature snapshot integrity failure")
            return snapshot

    def get_market_state(self, decision_hash: str) -> StateDecision | None:
        with Session(self.engine) as session:
            row = session.get(MarketStateRow, decision_hash)
            if row is None:
                return None
            decision = StateDecision.model_validate(row.payload)
            if decision.decision_hash() != decision_hash:
                raise ValueError("market-state snapshot integrity failure")
            return decision
