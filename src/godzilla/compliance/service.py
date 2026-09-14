"""Fail-closed compliance validation at startup."""

from dataclasses import dataclass

from godzilla.compliance.models import ComplianceProfile
from godzilla.config.modes import TradingMode
from godzilla.core.clock import Clock


class ComplianceViolation(RuntimeError):
    """Raised when the requested operating mode lacks valid compliance metadata."""


@dataclass(frozen=True)
class ComplianceGate:
    clock: Clock

    def validate(self, profile: ComplianceProfile, mode: TradingMode) -> None:
        if mode not in {TradingMode.PAPER, TradingMode.LIVE}:
            return
        issues = (*profile.structural_issues(mode), *profile.temporal_issues(self.clock.now()))
        if issues:
            raise ComplianceViolation(
                f"{mode.value} startup blocked by compliance: " + ", ".join(issues)
            )
