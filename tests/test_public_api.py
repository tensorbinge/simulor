from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def test_root_module_imports_without_optional_dependencies() -> None:
    sys.modules.pop("simulor", None)

    simulor = importlib.import_module("simulor")

    assert hasattr(simulor, "Engine")
    assert hasattr(simulor, "BacktestResult")
    assert hasattr(simulor, "CsvFeed")
    assert hasattr(simulor, "SimulatedBroker")


def test_pipeline_artifacts_are_public() -> None:
    simulor = importlib.import_module("simulor")
    types = importlib.import_module("simulor.types")
    alpha = importlib.import_module("simulor.alpha")

    for name in ("Signal", "SignalDirection", "Target", "TargetKind", "TargetSource"):
        assert name in simulor.__all__
        assert hasattr(simulor, name)

    assert types.Target is simulor.Target
    assert types.TargetKind is simulor.TargetKind
    assert types.TargetSource is simulor.TargetSource
    assert alpha.SignalDirection is simulor.SignalDirection


def test_public_subpackages_expose_canonical_symbols() -> None:
    simulor = importlib.import_module("simulor")
    models = importlib.import_module("simulor.models")
    data = importlib.import_module("simulor.data")
    execution = importlib.import_module("simulor.execution")
    live = importlib.import_module("simulor.live")

    assert hasattr(models, "AlphaModel")
    assert hasattr(models, "ExecutionModel")
    assert hasattr(data, "CsvFeed")
    assert hasattr(execution, "SimulatedBroker")
    assert hasattr(live, "__all__")
    assert "Longbridge" not in simulor.__all__
    assert "LongbridgeConnector" not in simulor.__all__
    assert "Longbridge" not in live.__all__
    assert "LongbridgeConnector" not in live.__all__
