from pathlib import Path

import pytest

from godzilla.market_data.providers import LocalComplianceProvider, SnapshotLoadError

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[2]


def test_local_compliance_provider_reads_layer_root() -> None:
    profile = LocalComplianceProvider(ROOT / "config/compliance.yaml").load()
    assert profile.profile_id == "india-nse-local-paper"


def test_local_compliance_provider_rejects_missing_root(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: fixture\n", encoding="utf-8")
    with pytest.raises(SnapshotLoadError, match="has no"):
        LocalComplianceProvider(path).load()
