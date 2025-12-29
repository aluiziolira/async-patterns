"""Tests for package-level exports."""

from __future__ import annotations


class TestPackageExports:
    """Test cases for top-level package imports."""

    def test_import_sync_engine(self) -> None:
        """SyncEngine should be importable from async_patterns."""
        from async_patterns import SyncEngine

        assert SyncEngine is not None

    def test_import_threaded_engine(self) -> None:
        """ThreadedEngine should be importable from async_patterns."""
        from async_patterns import ThreadedEngine

        assert ThreadedEngine is not None

    def test_import_request_result(self) -> None:
        """RequestResult should be importable from async_patterns."""
        from async_patterns import RequestResult

        assert RequestResult is not None

    def test_import_request_status(self) -> None:
        """RequestStatus should be importable from async_patterns."""
        from async_patterns import RequestStatus

        assert RequestStatus is not None

    def test_import_engine_result(self) -> None:
        """EngineResult should be importable from async_patterns."""
        from async_patterns import EngineResult

        assert EngineResult is not None

    def test_import_engine_protocol(self) -> None:
        """Engine protocol should be importable from async_patterns."""
        from async_patterns import Engine

        assert Engine is not None

    def test_all_exports_match_declared(self) -> None:
        """All items in __all__ should be importable."""
        import async_patterns

        for name in async_patterns.__all__:
            assert hasattr(async_patterns, name), f"{name} not found in async_patterns"
