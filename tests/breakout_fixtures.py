from datetime import timedelta
from decimal import Decimal

from godzilla.alphas.breakout_models import BreakoutObservation, BreakoutSettings
from godzilla.alphas.momentum_breakout import MomentumBreakout
from godzilla.features.models import FeatureSetVersion
from godzilla.market_data.models import Bar
from tests.momentum_fixtures import changed, momentum_inputs


def breakout_inputs(checker, direction=1):
    obs, universe, master, state, at = momentum_inputs(checker, direction)
    definition = FeatureSetVersion()
    state = state.model_copy(
        update={
            "evidence": state.evidence.model_copy(
                update={"feature_set_hash": definition.version_hash()}
            )
        }
    )
    observations = []
    for i, original in enumerate(obs):
        current = changed(
            original,
            {
                "stock.session_vwap": 99 if direction == 1 else 101,
                "stock.atr": 2,
                "stock.relative_volume": 2.5,
                "stock.relative_sector_30m": direction * (i + 1) * 0.002,
                "stock.relative_market_30m": direction * (i + 1) * 0.002,
            },
        )
        current = current.model_copy(
            update={
                "snapshot": current.snapshot.model_copy(
                    update={"feature_set_hash": definition.version_hash()}
                )
            }
        )
        reference = changed(
            current,
            {
                "stock.opening_range_high": 100,
                "stock.opening_range_low": 100,
                "stock.swing_high": 100,
                "stock.swing_low": 100,
            },
        )
        reference = reference.model_copy(
            update={
                "snapshot": reference.snapshot.model_copy(
                    update={"timestamp": at - timedelta(minutes=5)}
                )
            }
        )
        bar = Bar(
            instrument_id=current.snapshot.entity,
            source="fixture",
            received_at=at,
            start=at - timedelta(minutes=5),
            end=at,
            interval_minutes=5,
            open=Decimal("99.8") if direction == 1 else Decimal("100.2"),
            high=Decimal("100.5") if direction == 1 else Decimal("100.2"),
            low=Decimal("99.8") if direction == 1 else Decimal("99.5"),
            close=Decimal("100.4") if direction == 1 else Decimal("99.6"),
            volume=Decimal(100),
        )
        previous = bar.model_copy(
            update={
                "start": at - timedelta(minutes=10),
                "end": bar.start,
                "received_at": bar.start,
                "open": Decimal(100),
                "high": Decimal(101),
                "low": Decimal(99),
                "close": Decimal(100),
            }
        )
        observations.append(
            BreakoutObservation(
                current=current,
                reference=reference,
                bar=bar,
                previous_bar=previous,
                feature_definition=definition,
            )
        )
    return tuple(observations), universe, master, state, at


def breakout_result(checker, direction=1, **settings):
    return MomentumBreakout(BreakoutSettings(**settings), checker.calendar).evaluate(
        *breakout_inputs(checker, direction)
    )
