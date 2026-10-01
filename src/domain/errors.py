"""Domain errors. Overwrites and unsupported pairs are failures, not warnings."""

from __future__ import annotations


class RVError(Exception):
    """Base error for the relative-value engine."""


class UnsupportedPairError(RVError):
    """Raised when a caller asks the engine to trade anything other than EURGBP."""


class PointInTimeError(RVError):
    """Raised when a timestamp or query would leak future information."""


class DataValidationError(RVError):
    """Raised when a record violates the append-only point-in-time contract."""


class InsufficientHistoryError(RVError):
    """Raised when a model is asked to fit with fewer than the configured observations."""
