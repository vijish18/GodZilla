"""Adapters for all five alphas, stable-identity conflicts, no probability claims."""

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from godzilla.alphas.pairs_models import PairSignalIntent
from godzilla.alphas.relative_value_models import RelativeValueIntent, security_id
from godzilla.core.events.models import SignalIntent
from godzilla.ensemble.models import AlphaId, AlphaRegistry, CandidateLeg, NormalizedCandidate
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.instruments import InstrumentMasterSnapshot, InstrumentStatus
from godzilla.market_data.models import utc
from godzilla.market_state.models import StateDecision
from godzilla.portfolio.models import EnsembleDecision, EnsembleRejection
from godzilla.universe.builder import UniverseSnapshot

AlphaIntent = SignalIntent | PairSignalIntent | RelativeValueIntent


class _Inputs(FeatureModel):
    intents: tuple[AlphaIntent, ...]
    registry: AlphaRegistry
    master: InstrumentMasterSnapshot
    universe: UniverseSnapshot
    state: StateDecision


def normalize_intents(
    intents: tuple[AlphaIntent, ...],
    registry: AlphaRegistry,
    master: InstrumentMasterSnapshot,
    universe: UniverseSnapshot,
    state: StateDecision,
    at: datetime,
) -> EnsembleDecision:
    at = utc(at)
    ordered = tuple(sorted(intents, key=lambda x: (str(x.signal_id), content_hash(x))))
    digest = content_hash(
        _Inputs(intents=ordered, registry=registry, master=master, universe=universe, state=state)
    )
    decision_id = uuid5(NAMESPACE_URL, "godzilla:ensemble:" + digest)

    def blocked(reason: str) -> EnsembleDecision:
        return EnsembleDecision(
            decision_id=decision_id,
            timestamp=at,
            candidates=(),
            rejected=tuple(sorted({x.signal_id for x in intents}, key=str)),
            reasons=(reason,),
            input_hash=digest,
            rejection_reasons=tuple(
                EnsembleRejection(signal_id=i, reasons=(reason,))
                for i in sorted({x.signal_id for x in intents}, key=str)
            ),
        )

    day = at.astimezone(ZoneInfo("Asia/Kolkata")).date()
    mapping = {i.token: i for i in master.instruments}
    if (
        master.as_of != day
        or universe.as_of != day
        or utc(master.generated_at) > at
        or utc(universe.generated_at) > at
        or universe.instrument_snapshot_id != master.snapshot_id
        or len(mapping) != len(master.instruments)
        or len({security_id(x) for x in mapping.values()}) != len(mapping)
        or state.timestamp != at
    ):
        return blocked("NORMALIZATION_METADATA_UNAVAILABLE")
    unique: dict[str, AlphaIntent] = {}
    for intent in ordered:
        key = str(intent.signal_id)
        if key in unique and unique[key] != intent:
            return blocked("CONFLICTING_SIGNAL_ID")
        unique[key] = intent
    rejected, candidates = [], []
    rejection_reasons: dict[UUID, str] = {}
    for intent in unique.values():
        try:
            alpha = AlphaId(intent.alpha_id)
            registration = registry.registration(alpha)
            if not registration.enabled:
                raise ValueError("alpha disabled")
            if isinstance(intent, SignalIntent):
                detail = intent.evidence
                if detail is None or intent.score is None:
                    raise ValueError("scored evidence required")
                timestamp, score = detail.timestamp, intent.score
                tokens: tuple[tuple[str, Decimal], ...] = (
                    (intent.instrument_id, Decimal(1) if intent.side == "LONG" else Decimal(-1)),
                )
                provenance = (detail.code_commit, detail.config_hash, detail.data_snapshot)
                universe_hash = detail.universe_hash
                if detail.instrument_master_hash != content_hash(master):
                    raise ValueError("master mismatch")
                state_hash = detail.state_hash
            else:
                timestamp = intent.timestamp
                if isinstance(intent, PairSignalIntent):
                    if intent.action != "ENTER":
                        raise ValueError("exit intents are not new allocations")
                    score = min(1, abs(intent.risk_reference.current_z) / intent.settings.stop_z)
                else:
                    score = intent.score
                    if intent.master_hash != content_hash(master):
                        raise ValueError("master mismatch")
                tokens = tuple(
                    (
                        x.instrument_id,
                        Decimal(str(x.gross_fraction)) * (1 if x.side == "LONG" else -1),
                    )
                    for x in intent.legs
                )
                provenance = (intent.code_commit, intent.config_hash, intent.data_snapshot)
                universe_hash, state_hash = intent.universe_hash, intent.state_hash
            if (
                timestamp != at
                or provenance != (state.code_commit, state.config_hash, state.data_snapshot)
                or universe_hash != content_hash(universe)
                or state_hash != state.decision_hash()
            ):
                raise ValueError("causal provenance mismatch")
            legs = []
            total = sum(abs(w) for _, w in tokens)
            for token, weight in tokens:
                instrument = mapping[token]
                if (
                    token not in universe.long_tokens
                    or instrument.status is not InstrumentStatus.ACTIVE
                    or not instrument.active_on(day)
                    or instrument.surveillance
                    or instrument.data_ambiguous
                    or (
                        weight < 0
                        and (token not in universe.short_tokens or not instrument.fo_eligible)
                    )
                ):
                    raise ValueError("ineligible leg")
                legs.append(
                    CandidateLeg(
                        entity_id=security_id(instrument),
                        instrument_id=token,
                        sector=instrument.sector,
                        signed_weight=weight / total,
                    )
                )
            strength = Decimal(
                str(
                    max(
                        0,
                        min(
                            1,
                            (score - registration.score_floor)
                            / (registration.score_ceiling - registration.score_floor),
                        ),
                    )
                )
            )
            candidates.append(
                NormalizedCandidate(
                    signal_id=intent.signal_id,
                    alpha_id=alpha,
                    timestamp=at,
                    strength=strength,
                    legs=tuple(legs),
                    raw_intent_hash=content_hash(intent),
                    registry_hash=content_hash(registry),
                )
            )
        except (ValueError, KeyError) as error:
            rejected.append(intent.signal_id)
            rejection_reasons[intent.signal_id] = str(error)
    # Opposite directions block all involved bundles, including every hedge leg.
    conflicted: set[UUID] = set()
    for left in candidates:
        for right in candidates:
            if left.signal_id != right.signal_id and any(
                a.entity_id == b.entity_id and a.signed_weight * b.signed_weight < 0
                for a in left.legs
                for b in right.legs
            ):
                conflicted.update((left.signal_id, right.signal_id))
    selected: list[NormalizedCandidate] = []
    used: set[UUID] = set()
    for candidate in sorted(candidates, key=lambda x: (-x.strength, str(x.signal_id))):
        entities = {x.entity_id for x in candidate.legs}
        if candidate.signal_id in conflicted or entities & used:
            rejected.append(candidate.signal_id)
            rejection_reasons[candidate.signal_id] = (
                "OPPOSITE_DIRECTION_CONFLICT"
                if candidate.signal_id in conflicted
                else "SAME_SECURITY_DUPLICATE"
            )
            continue
        selected.append(candidate)
        used.update(entities)
    return EnsembleDecision(
        decision_id=decision_id,
        timestamp=at,
        candidates=tuple(selected),
        rejected=tuple(sorted(set(rejected), key=str)),
        reasons=("NORMALIZED_CONFLICTS_RESOLVED",),
        input_hash=digest,
        rejection_reasons=tuple(
            EnsembleRejection(signal_id=i, reasons=(rejection_reasons[i],))
            for i in sorted(set(rejected), key=str)
        ),
    )
