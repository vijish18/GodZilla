import math
import random
from datetime import datetime, timedelta
from decimal import Decimal

from godzilla.alphas.pairs_models import (
    PairCandidate,
    PairLiquidity,
    PairObservation,
    PairsSettings,
)
from godzilla.alphas.pairs_statistics import StatsmodelsDiagnostics, fit_pair
from godzilla.config.modes import TradingMode
from godzilla.features.models import InstrumentRef
from godzilla.market_data.models import Bar, Quote
from godzilla.market_data.quality import session_bounds
from godzilla.market_state.models import AlphaFamily, MarketState
from tests.momentum_fixtures import momentum_inputs


def pairs_inputs(checker, *, cointegrated=True, z=2.5, seed=7):
    # The fixed synthetic seed is a fixture, never a production selection parameter.
    cfg = PairsSettings()
    _, universe, master, state, _ = momentum_inputs(checker)
    starts = []
    day = datetime.fromisoformat("2026-09-08T09:15:00+05:30")
    while len(starts) < cfg.training_bars + 1:
        bounds = session_bounds(checker.calendar, day, checker.timezone)
        if bounds:
            cursor = bounds[0]
            while (
                cursor + timedelta(minutes=5) <= bounds[1] and len(starts) < cfg.training_bars + 1
            ):
                starts.append(cursor)
                cursor += timedelta(minutes=5)
        day += timedelta(days=1)
    rng = random.Random(seed)
    right, residual, independent = 4.5, 0.0, 4.5
    observations = []
    for start in starts:
        right += rng.gauss(0, 0.01)
        residual = 0.6 * residual + rng.gauss(0, 0.001)
        independent += rng.gauss(0, 0.01)
        left = 0.1 + 1.1 * right + residual if cointegrated else independent

        def bar(token, value, start=start):
            price = Decimal(str(math.exp(value)))
            return Bar(
                instrument_id=token,
                source="fixture",
                start=start,
                end=start + timedelta(minutes=5),
                received_at=start + timedelta(minutes=5),
                interval_minutes=5,
                open=price,
                close=price,
                high=price + 1,
                low=price - 1,
                volume=Decimal(1000),
            )

        observations.append(PairObservation(left=bar("T00", left), right=bar("T01", right)))
    fit = fit_pair(tuple(observations[:-1]), cfg, StatsmodelsDiagnostics())
    if cointegrated:
        current = observations[-1]
        target = (
            fit.spread_mean + z * fit.spread_std + fit.beta * math.log(float(current.right.close))
        )
        price = Decimal(str(math.exp(target)))
        observations[-1] = current.model_copy(
            update={
                "left": current.left.model_copy(
                    update={"open": price, "close": price, "high": price + 1, "low": price - 1}
                )
            }
        )
    at = observations[-1].left.end
    localday = at.date()
    generated = at - timedelta(hours=5)
    universe = universe.model_copy(update={"as_of": localday, "generated_at": generated})
    master = master.model_copy(
        update={
            "as_of": localday,
            "generated_at": generated,
            "instruments": tuple(
                i.model_copy(update={"effective_from": localday, "effective_to": localday})
                for i in master.instruments
            ),
        }
    )
    state = state.model_copy(
        update={
            "timestamp": at,
            "state_since": at,
            "evidence": state.evidence.model_copy(
                update={"health": state.evidence.health.model_copy(update={"observed_at": at})}
            ),
            "state": MarketState.CHOP,
            "allowed_alpha_families": (AlphaFamily.PAIRS_STAT_ARB,),
            "directional_sides": (),
        }
    )
    candidate = PairCandidate(
        pair_id="T00-T01-v1",
        left=InstrumentRef(instrument_id="T00", source="fixture"),
        right=InstrumentRef(instrument_id="T01", source="fixture"),
        economic_tags=("same-sector",),
        left_sector="SECTOR",
        right_sector="SECTOR",
        rationale="Synthetic peer relationship",
        source_reference="synthetic-v1",
        known_at=starts[0] - timedelta(days=1),
        reviewed_at=starts[0] - timedelta(days=1),
        effective_from=starts[0] - timedelta(days=1),
        effective_to=at + timedelta(days=1),
        corporate_actions_clear=True,
        adjustment_snapshot="synthetic-clean-v1",
    )
    liquidity = []
    for bar in (observations[-1].left, observations[-1].right):
        liquidity.append(
            PairLiquidity(
                quote=Quote(
                    instrument_id=bar.instrument_id,
                    source="fixture",
                    timestamp=at,
                    received_at=at,
                    bid=bar.close - Decimal("0.01"),
                    ask=bar.close + Decimal("0.01"),
                    last=bar.close,
                ),
                adv=200000,
                daily_turnover=20000000,
                known_at=at,
                snapshot_id="liq-fixture",
            )
        )
    return (
        candidate,
        tuple(observations),
        tuple(liquidity),
        universe,
        master,
        state,
        at,
        TradingMode.RESEARCH,
    )
