"""Adapt explicitly active/pending stat-arb intents into conservative stable-ID reservations."""

from datetime import datetime
from zoneinfo import ZoneInfo

from godzilla.alphas.pairs_models import PairSignalIntent
from godzilla.alphas.relative_value_models import (
    RelativeValueInventory,
    ReservedSecurity,
    security_id,
)
from godzilla.market_data.instruments import InstrumentMasterSnapshot
from godzilla.market_data.models import utc


def stat_arb_inventory(
    active_or_pending: tuple[PairSignalIntent, ...],
    master: InstrumentMasterSnapshot,
    observed_at: datetime,
    snapshot_id: str,
    *,
    complete: bool = False,
) -> RelativeValueInventory:
    observed_at = utc(observed_at)
    mapping = {i.token: i for i in master.instruments}
    if len(mapping) != len(master.instruments) or utc(master.generated_at) > observed_at:
        raise ValueError("inventory instrument mapping unavailable")
    reservations = []
    for intent in active_or_pending:
        if intent.timestamp > observed_at:
            raise ValueError("inventory cannot contain future intents")
        if (
            master.as_of != intent.timestamp.astimezone(ZoneInfo("Asia/Kolkata")).date()
            or utc(master.generated_at) > intent.timestamp
        ):
            raise ValueError("inventory requires the entry-date master, not a later token mapping")
        for leg in intent.legs:
            if leg.instrument_id not in mapping:
                raise ValueError("inventory token cannot be resolved; entries must block")
            reservations.append(
                ReservedSecurity(
                    entity_id=security_id(mapping[leg.instrument_id]),
                    owner_alpha="pairs_stat_arb",
                    reference_id=str(intent.signal_id),
                )
            )
    return RelativeValueInventory(
        snapshot_id=snapshot_id,
        observed_at=observed_at,
        complete=complete,
        reservations=tuple(sorted(reservations, key=lambda r: (str(r.entity_id), r.reference_id))),
    )
