from uuid import UUID

import pytest
from tests.breakout_fixtures import breakout_inputs
from tests.momentum_fixtures import momentum_inputs
from tests.pairs_fixtures import pairs_inputs
from tests.relative_value_fixtures import relative_value_inputs
from tests.vwap_fixtures import vwap_inputs

from godzilla.alphas.breakout_models import BreakoutSettings
from godzilla.alphas.cross_sectional_momentum import CrossSectionalMomentum
from godzilla.alphas.momentum_breakout import MomentumBreakout
from godzilla.alphas.momentum_models import MomentumSettings
from godzilla.alphas.pairs_engine import PairsEngine
from godzilla.alphas.pairs_models import PairsSettings
from godzilla.alphas.pairs_statistics import StatsmodelsDiagnostics
from godzilla.alphas.relative_value_models import RelativeValueSettings
from godzilla.alphas.sector_relative_value import SectorRelativeValue
from godzilla.alphas.vwap_mean_reversion import VwapMeanReversion
from godzilla.alphas.vwap_models import VwapSettings
from godzilla.ensemble.models import AlphaRegistry
from godzilla.ensemble.normalize import normalize_intents


@pytest.mark.parametrize("family", ["momentum", "breakout", "pairs", "vwap", "rv"])
def test_each_actual_alpha_adapts_without_losing_legs(checker, family):
    if family == "momentum":
        args = momentum_inputs(checker)
        intents = CrossSectionalMomentum(MomentumSettings()).evaluate(*args).intents
        _, universe, master, state, at = args
    elif family == "breakout":
        args = breakout_inputs(checker)
        intents = MomentumBreakout(BreakoutSettings(), checker.calendar).evaluate(*args).intents
        _, universe, master, state, at = args
    elif family == "pairs":
        args = pairs_inputs(checker)
        intents = (
            PairsEngine(PairsSettings(), StatsmodelsDiagnostics(), checker.calendar)
            .evaluate(*args)
            .intent,
        )
        universe, master, state, at = args[3:7]
    elif family == "rv":
        args = relative_value_inputs(checker)
        intents = (
            SectorRelativeValue(RelativeValueSettings(), checker.calendar).evaluate(*args).intents
        )
        universe, master, state, at = args[1], args[2], args[3], args[5]
    else:
        args = vwap_inputs(checker)
        intents = (VwapMeanReversion(VwapSettings(), checker.calendar).evaluate(*args).intent,)
        universe, master, state, at = args[2:6]
    result = normalize_intents(intents, AlphaRegistry(), master, universe, state, at)
    assert result.candidates and not result.rejected
    assert all(c.score_semantics == "ORDINAL_STRENGTH_NOT_PROBABILITY" for c in result.candidates)
    assert len(result.candidates[0].legs) == (2 if family in ("pairs", "rv") else 1)


def test_opposite_conflicts_block_both_and_same_side_deduplicates(checker):
    args = momentum_inputs(checker)
    original = CrossSectionalMomentum(MomentumSettings()).evaluate(*args).intents[0]
    _, universe, master, state, at = args
    registry = AlphaRegistry()
    same = original.model_copy(update={"signal_id": UUID(int=5)})
    result = normalize_intents((same, original, original), registry, master, universe, state, at)
    assert len(result.candidates) == 1 and len(result.rejected) == 1
    assert result == normalize_intents(
        (original, same, original), registry, master, universe, state, at
    )
    opposite = original.model_copy(
        update={
            "signal_id": UUID(int=6),
            "side": "SHORT",
            "score": original.evidence.ranked.short_score,
            "evidence": original.evidence.model_copy(
                update={"side": "SHORT", "score": original.evidence.ranked.short_score}
            ),
        }
    )
    result = normalize_intents((original, opposite), registry, master, universe, state, at)
    assert not result.candidates and len(result.rejected) == 2
    ambiguous = original.model_copy(update={"reasons": ("different-reason",)})
    assert normalize_intents(
        (original, ambiguous), registry, master, universe, state, at
    ).reasons == ("CONFLICTING_SIGNAL_ID",)


def test_normalization_rejects_unscored_or_mismatched_provenance(checker):
    args = momentum_inputs(checker)
    original = CrossSectionalMomentum(MomentumSettings()).evaluate(*args).intents[0]
    _, universe, master, state, at = args
    for bad in (
        original.model_copy(update={"score": None, "evidence": None}),
        original.model_copy(
            update={"evidence": original.evidence.model_copy(update={"config_hash": "f" * 64})}
        ),
    ):
        assert not normalize_intents(
            (bad,), AlphaRegistry(), master, universe, state, at
        ).candidates
