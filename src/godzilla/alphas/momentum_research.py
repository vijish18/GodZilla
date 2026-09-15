"""Label-isolated, equal-weight quantile diagnostics before sizing or execution costs."""

from datetime import datetime
from statistics import fmean

from pydantic import Field, field_validator, model_validator

from godzilla.alphas.cross_sectional_momentum import MomentumResult
from godzilla.alphas.momentum_models import MomentumSettings
from godzilla.features.models import FeatureModel, content_hash
from godzilla.market_data.models import utc
from godzilla.market_state.research import ExperimentIdentity


class ForwardReturn(FeatureModel):
    instrument_id: str
    decision_at: datetime
    measured_at: datetime
    return_fraction: float = Field(gt=-1)

    _aware = field_validator("decision_at", "measured_at")(utc)

    @model_validator(mode="after")
    def forward_only(self) -> "ForwardReturn":
        if self.measured_at <= self.decision_at:
            raise ValueError("forward outcome must follow decision")
        return self


class QuantileDiagnostic(FeatureModel):
    quantile: int
    count: int
    mean_strength: float | None
    mean_forward_return: float | None


class MomentumDiagnostics(FeatureModel):
    experiment: ExperimentIdentity
    parameters: MomentumSettings
    result_hash: str
    labels_hash: str
    quantiles: tuple[QuantileDiagnostic, ...]
    gross_long_tail_return: float | None
    gross_short_tail_return: float | None
    gross_long_short_return: float | None
    notice: str = "Equal-weight descriptive returns; no fills, costs, sizing or promotion evidence."


class _Labels(FeatureModel):
    labels: tuple[ForwardReturn, ...]


def quantile_diagnostics(
    result: MomentumResult,
    labels: tuple[ForwardReturn, ...],
    experiment: ExperimentIdentity,
    available_at: datetime,
    quantiles: int = 10,
) -> MomentumDiagnostics:
    if quantiles < 2 or not result.scores:
        raise ValueError("diagnostics require scores and at least two quantiles")
    if (
        experiment.code_commit != result.code_commit
        or experiment.data_snapshot != result.data_snapshot
    ):
        raise ValueError("experiment provenance differs from ranked inputs")
    available_at = utc(available_at)
    indexed = {label.instrument_id: label for label in labels}
    if (
        len(indexed) != len(labels)
        or set(indexed) != {s.instrument_id for s in result.scores}
        or any(
            label.decision_at != result.timestamp or label.measured_at > available_at
            for label in labels
        )
        or len({label.measured_at for label in labels}) != 1
    ):
        raise ValueError("labels must cover the cross-section at one fully observed horizon")
    buckets = []
    for q in range(1, quantiles + 1):
        members = tuple(
            s for s in result.scores if min(quantiles, int(s.percentile * quantiles) + 1) == q
        )
        buckets.append(
            QuantileDiagnostic(
                quantile=q,
                count=len(members),
                mean_strength=fmean(s.strength for s in members) if members else None,
                mean_forward_return=fmean(indexed[s.instrument_id].return_fraction for s in members)
                if members
                else None,
            )
        )
    long = buckets[-1].mean_forward_return
    bottom = buckets[0].mean_forward_return
    short = -bottom if bottom is not None else None
    return MomentumDiagnostics(
        experiment=experiment,
        parameters=result.settings,
        result_hash=content_hash(result),
        labels_hash=content_hash(
            _Labels(labels=tuple(sorted(labels, key=lambda label: label.instrument_id)))
        ),
        quantiles=tuple(buckets),
        gross_long_tail_return=long,
        gross_short_tail_return=short,
        gross_long_short_return=(long + short) / 2
        if long is not None and short is not None
        else None,
    )
