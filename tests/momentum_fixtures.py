from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from godzilla.alphas.cross_sectional_momentum import CrossSectionalMomentum
from godzilla.alphas.momentum_models import MomentumObservation, MomentumSettings
from godzilla.features.models import FeatureValue, InstrumentRef, MissingReason, content_hash
from godzilla.market_data.instruments import (
    Instrument,
    InstrumentMasterSnapshot,
    InstrumentStatus,
    PriceBand,
)
from godzilla.market_data.metrics import MemoryMetrics
from godzilla.market_state.models import RouterSettings
from godzilla.market_state.router import MarketStateRouter
from godzilla.universe.builder import UniverseSnapshot
from tests.feature_fixtures import feature_context
from tests.state_fixtures import healthy, state_snapshot


def momentum_inputs(checker, direction=1, tied=False):
    state_features = state_snapshot(
        start="2026-09-15T13:00:00+05:30",
        changes={
            "market.vwap_distance": direction * 0.003,
            "market.slope_15m": direction * 0.001,
            "market.slope_30m": direction * 0.001,
            "market.breadth_advancing": 0.7 if direction > 0 else 0.3,
            "market.breadth_declining": 0.3 if direction > 0 else 0.7,
        },
    )
    at = state_features.timestamp
    state = MarketStateRouter(
        RouterSettings(confirmation_count=1, min_duration_seconds=0),
        checker.calendar,
        checker.timezone,
        MemoryMetrics(),
    ).route(state_features, healthy(state_features), at)
    instruments = tuple(
        Instrument(
            symbol=f"STOCK{i:02}",
            token=f"T{i:02}",
            isin=f"ISIN{i}",
            exchange="NSE",
            segment="CM",
            tick_size=Decimal("0.05"),
            status=InstrumentStatus.ACTIVE,
            fo_eligible=True,
            sector="SECTOR",
            price_band=PriceBand(),
            effective_from=at.date(),
            effective_to=at.date(),
        )
        for i in range(10)
    )
    master = InstrumentMasterSnapshot(
        snapshot_id="master-v1",
        version="1",
        exchange="NSE",
        as_of=at.date(),
        generated_at=at - timedelta(hours=4),
        source_reference="synthetic",
        instruments=instruments,
    )
    universe = UniverseSnapshot(
        snapshot_id=UUID(int=7),
        as_of=at.date(),
        generated_at=master.generated_at,
        instrument_snapshot_id=master.snapshot_id,
        compliance_profile_id="fixture",
        long_tokens=tuple(i.token for i in instruments),
        short_tokens=tuple(i.token for i in instruments),
        exclusions=(),
    )
    observations = []
    for i, instrument in enumerate(instruments):
        value = 0.01 if tied else (i - 4.5) * 0.01
        context = feature_context().model_copy(
            update={
                "entity": InstrumentRef(instrument_id=instrument.token, source="fixture"),
                "sector": InstrumentRef(instrument_id="SECTOR", source="fixture"),
                "nifty": InstrumentRef(instrument_id="NIFTY", source="fixture"),
                "universe_version": str(universe.snapshot_id),
            }
        )
        values = {
            "stock.return_30m": value,
            "stock.return_60m": value * 2,
            "stock.return_120m": value * 3,
            "sector.return_30m": 0,
            "market.return_30m": 0,
            "stock.return_5m": 0.001,
            "sector.return_5m": 0,
            "market.return_5m": 0,
            "stock.vwap_distance": direction * 0.01,
            "stock.ema_20_slope": direction * 0.001,
            "sector.vwap_distance": direction * 0.005,
            "liquidity.spread_bps": 3,
            "liquidity.quote_age": 1,
            "liquidity.adv": 200000,
            "liquidity.average_daily_turnover": 20000000,
        }
        snapshot = state_features.model_copy(
            update={
                "entity": instrument.token,
                "context_hash": content_hash(context),
                "values": tuple(FeatureValue(name=k, value=v) for k, v in sorted(values.items())),
            }
        )
        observations.append(MomentumObservation(snapshot=snapshot, context=context))
    return tuple(observations), universe, master, state, at


def changed(observation, values):
    old = {item.name: item for item in observation.snapshot.values}
    old.update(
        {
            key: FeatureValue(
                name=key, value=value, reason=MissingReason.MISSING_INPUT if value is None else None
            )
            for key, value in values.items()
        }
    )
    return observation.model_copy(
        update={
            "snapshot": observation.snapshot.model_copy(
                update={"values": tuple(old[key] for key in sorted(old))}
            )
        }
    )


def momentum_result(checker, direction=1, **settings):
    inputs = momentum_inputs(checker, direction)
    return CrossSectionalMomentum(MomentumSettings(**settings)).evaluate(*inputs)
