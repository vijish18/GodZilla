from datetime import timedelta
from decimal import Decimal

import pytest
from tests.momentum_fixtures import changed
from tests.pairs_fixtures import pairs_inputs
from tests.relative_value_fixtures import relative_value_inputs, relative_value_result

from godzilla.alphas.relative_value_beta import estimate_beta, neutral_weights
from godzilla.alphas.relative_value_inventory import stat_arb_inventory
from godzilla.alphas.relative_value_models import (
    RelativeValueIntent,
    RelativeValueInventory,
    RelativeValueSettings,
    ReservedSecurity,
    security_id,
)
from godzilla.alphas.sector_relative_value import SectorRelativeValue
from godzilla.config.loader import load_settings
from godzilla.config.models import Settings
from godzilla.config.modes import TradingMode
from godzilla.features.models import content_hash


def test_pair_and_basket_rank_and_beta_math(checker):
    pair = relative_value_result(checker).intents[0]
    assert [(x.instrument_id, x.side) for x in pair.legs] == [("T09", "LONG"), ("T00", "SHORT")]
    assert pair.legs[0].beta.beta == pytest.approx(1.09)
    assert pair.legs[1].beta.beta == pytest.approx(1)
    assert pair.hedge_ratio == pytest.approx(1.09)
    assert pair.legs[0].gross_fraction == pytest.approx(1 / 2.09)
    assert pair.risk_reference.signed_beta_gross == pytest.approx(0, abs=1e-12)
    assert pair.risk_reference.signed_net_gross == pytest.approx((1 - 1.09) / 2.09)
    assert RelativeValueIntent.model_validate_json(pair.model_dump_json()) == pair
    basket = relative_value_result(checker, construction="BASKET").intents[0]
    assert [(x.instrument_id, x.side) for x in basket.legs] == [
        ("T09", "LONG"),
        ("T08", "LONG"),
        ("T00", "SHORT"),
        ("T01", "SHORT"),
    ]
    assert sum(x.gross_fraction for x in basket.legs) == pytest.approx(1)
    assert basket.risk_reference.signed_beta_gross == pytest.approx(0, abs=1e-12)
    assert basket.hedge_ratio == pytest.approx(1.085 / 1.005)
    assert neutral_weights(1, 1, RelativeValueSettings()) == (0.5, 0.5)
    with pytest.raises(ValueError, match="NET_EXPOSURE_INFEASIBLE"):
        neutral_weights(0.5, 2, RelativeValueSettings())
    with pytest.raises(ValueError, match="BETA_OUT_OF_RANGE"):
        neutral_weights(-1, 1, RelativeValueSettings())


def test_input_order_ties_and_missing_sector_data(checker):
    args = list(relative_value_inputs(checker))
    engine = SectorRelativeValue(RelativeValueSettings(), checker.calendar)
    expected = engine.evaluate(*args)
    args[0] = tuple(reversed(args[0]))
    assert engine.evaluate(*args) == expected
    args[0] = args[0][1:]
    assert engine.evaluate(*args).reasons == ("CROSS_SECTION_OR_MASTER_AMBIGUOUS",)
    args = list(relative_value_inputs(checker))
    args[0] = tuple(
        o.model_copy(
            update={
                "current": changed(
                    o.current, {"stock.relative_sector_30m": 0, "stock.relative_market_30m": 0}
                )
            }
        )
        for o in args[0]
    )
    assert not engine.evaluate(*args).intents
    assert engine.evaluate(*args).reports[-1].reasons == ("TAILS_OR_RESIDUAL_GAP_INSUFFICIENT",)


def test_point_in_time_sector_membership_and_provenance(checker):
    args = list(relative_value_inputs(checker))
    engine = SectorRelativeValue(RelativeValueSettings(), checker.calendar)
    master = args[2]
    args[2] = master.model_copy(
        update={
            "instruments": tuple(
                i.model_copy(update={"sector": "NEW_SECTOR"}) if i.token == "T09" else i
                for i in master.instruments
            )
        }
    )
    result = engine.evaluate(*args)
    assert any(
        r.entity == "T09" and r.reasons == ("SECTOR_CONTEXT_OR_PROVENANCE_MISMATCH",)
        for r in result.reports
    )
    assert all(x.instrument_id != "T09" for intent in result.intents for x in intent.legs)
    args[2] = master.model_copy(update={"generated_at": args[5] + timedelta(seconds=1)})
    assert engine.evaluate(*args).reasons == ("CROSS_SECTION_OR_MASTER_AMBIGUOUS",)
    args = list(relative_value_inputs(checker))
    first = args[0][-1]
    context = first.current.context.model_copy(update={"known_at": args[5] + timedelta(seconds=1)})
    current = first.current.model_copy(
        update={
            "context": context,
            "snapshot": first.current.snapshot.model_copy(
                update={"context_hash": content_hash(context)}
            ),
        }
    )
    args[0] = (*args[0][:-1], first.model_copy(update={"current": current}))
    assert any(
        r.reasons == ("SECTOR_CONTEXT_OR_PROVENANCE_MISMATCH",)
        for r in engine.evaluate(*args).reports
    )


def test_stable_identity_and_inventory_overlap_across_token_change(checker):
    args = list(relative_value_inputs(checker))
    engine = SectorRelativeValue(RelativeValueSettings(), checker.calendar)
    original = args[2].instruments[-1]
    renamed = original.model_copy(update={"token": "RENAMED_TOKEN", "symbol": "RENAMED_SYMBOL"})
    assert security_id(original) == security_id(renamed)
    args[4] = RelativeValueInventory(
        snapshot_id="active-pair",
        observed_at=args[5],
        complete=True,
        reservations=(
            ReservedSecurity(
                entity_id=security_id(renamed),
                owner_alpha="pairs_stat_arb",
                reference_id="pending-pair",
            ),
        ),
    )
    result = engine.evaluate(*args)
    assert all(
        x.entity_id != security_id(original) for intent in result.intents for x in intent.legs
    )
    assert any(r.reasons == ("STAT_ARB_OR_RV_INVENTORY_OVERLAP",) for r in result.reports)
    args[4] = args[4].model_copy(update={"complete": False})
    assert engine.evaluate(*args).reasons == ("INVENTORY_UNAVAILABLE",)
    args[4] = args[4].model_copy(
        update={"complete": True, "observed_at": args[5] - timedelta(minutes=1)}
    )
    assert engine.evaluate(*args).reasons == ("INVENTORY_UNAVAILABLE",)


def test_training_only_estimation_excludes_trigger_and_late_data(checker):
    args = relative_value_inputs(checker)
    item = args[0][0]
    opening = args[3].evidence.session_open
    cfg = RelativeValueSettings()

    def fit(history):
        return estimate_beta(
            history, item.current.context.entity, item.current.context.nifty, args[5], opening, cfg
        )

    original = fit(item.history)
    trigger = item.history[-1]
    extreme = trigger.model_copy(
        update={
            "stock": trigger.stock.model_copy(
                update={"close": Decimal(5000), "high": Decimal(5001)}
            )
        }
    )
    assert fit((*item.history[:-1], extreme)) == original
    assert original.training_end == args[5] - timedelta(minutes=5)
    with pytest.raises(ValueError, match="BETA_WARMUP"):
        fit(item.history[2:])
    missing = (*item.history[:10], *item.history[11:], item.history[-3])
    with pytest.raises(ValueError, match="BETA_HISTORY_GAP_OR_DUPLICATE"):
        fit(missing)
    delayed = item.history[-2].model_copy(
        update={"stock": item.history[-2].stock.model_copy(update={"received_at": args[5]})}
    )
    with pytest.raises(ValueError):
        fit((*item.history[:-2], delayed, trigger))


def test_liquidity_short_routes_and_live_block(checker):
    args = list(relative_value_inputs(checker))
    engine = SectorRelativeValue(RelativeValueSettings(), checker.calendar)
    args[-1] = TradingMode.LIVE
    assert engine.evaluate(*args).reasons == ("LIVE_VALIDATION_REQUIRED",)
    args[-1] = TradingMode.RESEARCH
    args[1] = args[1].model_copy(update={"short_tokens": ()})
    assert not engine.evaluate(*args).intents
    args = list(relative_value_inputs(checker))
    args[0] = tuple(
        o.model_copy(update={"current": changed(o.current, {"liquidity.spread_bps": 100})})
        for o in args[0]
    )
    result = engine.evaluate(*args)
    assert not result.intents
    assert all(r.reasons == ("LIQUIDITY_REJECTED",) for r in result.reports)
    args = list(relative_value_inputs(checker))
    args[3] = args[3].model_copy(update={"gross_risk_multiplier": 0})
    assert engine.evaluate(*args).reasons == ("STATE_OR_HEALTH_BLOCKED",)


def test_config_and_intent_exposure_integrity(checker, monkeypatch):
    initial = load_settings("config")
    assert initial.relative_value == RelativeValueSettings()
    monkeypatch.setenv("GODZILLA_RELATIVE_VALUE__MAX_NET_GROSS", "0.1")
    assert load_settings("config").config_hash() != initial.config_hash()
    payload = initial.model_dump()
    payload["mode"] = "LIVE"
    payload["alphas"]["sector_relative_value"] = "ENABLED"
    with pytest.raises(ValueError, match="independent validation"):
        Settings.model_validate(payload)
    with pytest.raises(ValueError):
        RelativeValueSettings(live_enabled=True)
    intent = relative_value_result(checker).intents[0]
    payload = intent.model_dump()
    payload["legs"][0]["gross_fraction"] = 0.9
    with pytest.raises(ValueError, match="exposure"):
        RelativeValueIntent.model_validate(payload)


def test_stat_arb_inventory_adapter(checker):
    from godzilla.alphas.pairs_engine import PairsEngine
    from godzilla.alphas.pairs_models import PairsSettings
    from godzilla.alphas.pairs_statistics import StatsmodelsDiagnostics

    args = pairs_inputs(checker)
    intent = (
        PairsEngine(PairsSettings(), StatsmodelsDiagnostics(), checker.calendar)
        .evaluate(*args)
        .intent
    )
    inventory = stat_arb_inventory((intent,), args[4], args[6], "active-pairs", complete=True)
    assert len(inventory.reservations) == 2
    assert {r.entity_id for r in inventory.reservations} == {
        security_id(i) for i in args[4].instruments[:2]
    }
    assert not stat_arb_inventory((), args[4], args[6], "unknown").complete
    with pytest.raises(ValueError):
        stat_arb_inventory(
            (intent,), args[4].model_copy(update={"instruments": ()}), args[6], "bad", complete=True
        )


def test_beta_instability_and_inconsistent_market_snapshots(checker):
    args = list(relative_value_inputs(checker))
    item = args[0][0]
    price = Decimal(100)
    rows = []
    for index, row in enumerate(item.history):
        if index:
            change = row.market.close / item.history[index - 1].market.close - 1
            price *= 1 + (1 if index <= 12 else 2) * change
        rows.append(
            row.model_copy(
                update={
                    "stock": row.stock.model_copy(
                        update={"open": price, "close": price, "high": price + 1, "low": price - 1}
                    )
                }
            )
        )
    with pytest.raises(ValueError, match="BETA_UNSTABLE"):
        estimate_beta(
            tuple(rows),
            item.current.context.entity,
            item.current.context.nifty,
            args[5],
            args[3].evidence.session_open,
            RelativeValueSettings(),
        )
    rows = tuple(
        row.model_copy(
            update={
                "market": row.market.model_copy(
                    update={
                        "open": row.market.open * 2,
                        "high": row.market.high * 2,
                        "low": row.market.low * 2,
                        "close": row.market.close * 2,
                    }
                )
            }
        )
        for row in item.history
    )
    args[0] = (item.model_copy(update={"history": rows}), *args[0][1:])
    assert SectorRelativeValue(RelativeValueSettings(), checker.calendar).evaluate(
        *args
    ).reasons == ("INCONSISTENT_MARKET_TRAINING",)


def test_small_sector_and_net_limit_reject_without_relaxation(checker):
    assert not relative_value_result(checker, max_net_gross=0).intents
    result = relative_value_result(checker, minimum_sector_size=11)
    assert result.reports[-1].reasons == ("SECTOR_WARMUP",)
    result = relative_value_result(checker, construction="BASKET", members_per_side=4)
    assert not result.intents
    assert result.reports[-1].reasons == ("TAILS_OR_RESIDUAL_GAP_INSUFFICIENT",)
