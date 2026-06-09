"""Verify package and key contracts are importable."""

import meritranker_data_ingestion
from meritranker_data_ingestion.schemas.extraction import (
    ExtractionPackageManifest,
    ExtractionStatus,
)
from meritranker_data_ingestion.services.marker_adapter import MarkerAdapter
from meritranker_data_ingestion.services.parser_adapter import ParserAdapter


def test_package_version() -> None:
    assert meritranker_data_ingestion.__version__ == "0.1.0"


def test_schema_imports() -> None:
    assert ExtractionStatus.PENDING.value == "pending"


def test_marker_adapter_satisfies_protocol() -> None:
    adapter = MarkerAdapter()
    assert isinstance(adapter, ParserAdapter)
    assert adapter.name == "marker"
