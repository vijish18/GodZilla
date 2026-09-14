"""Local snapshot providers; network adapters implement the same protocols later."""

from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel

from godzilla.compliance.models import ComplianceProfile
from godzilla.market_data.calendar import CalendarSnapshot


class SnapshotLoadError(RuntimeError):
    pass


class CalendarProvider(Protocol):
    def load(self) -> CalendarSnapshot: ...


class ComplianceProvider(Protocol):
    def load(self) -> ComplianceProfile: ...


def load_yaml_model[SnapshotT: BaseModel](
    path: Path, model: type[SnapshotT], *, root_key: str | None = None
) -> SnapshotT:
    try:
        with path.open(encoding="utf-8") as stream:
            payload = yaml.safe_load(stream)
    except (OSError, yaml.YAMLError) as exc:
        raise SnapshotLoadError(f"unable to load snapshot {path}: {exc}") from exc
    if root_key is not None:
        if not isinstance(payload, dict) or root_key not in payload:
            raise SnapshotLoadError(f"snapshot {path} has no {root_key!r} root")
        payload = payload[root_key]
    return model.model_validate(payload)


class LocalCalendarProvider:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> CalendarSnapshot:
        return load_yaml_model(self.path, CalendarSnapshot)


class LocalComplianceProvider:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ComplianceProfile:
        return load_yaml_model(self.path, ComplianceProfile, root_key="compliance")
