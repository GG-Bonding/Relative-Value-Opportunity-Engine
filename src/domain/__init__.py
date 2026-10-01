"""Domain types for the EURGBP relative-value engine."""

from domain.enums import (
    AlphaLifecycle,
    DecisionState,
    Direction,
    ExitReason,
    ExperimentConclusion,
    MechanismKind,
    MispricingClass,
    OpportunityStatus,
    RegimeName,
)
from domain.errors import (
    DataValidationError,
    InsufficientHistoryError,
    PointInTimeError,
    RVError,
    UnsupportedPairError,
)

__all__ = [
    "AlphaLifecycle",
    "DataValidationError",
    "DecisionState",
    "Direction",
    "ExitReason",
    "ExperimentConclusion",
    "InsufficientHistoryError",
    "MechanismKind",
    "MispricingClass",
    "OpportunityStatus",
    "PointInTimeError",
    "RVError",
    "RegimeName",
    "UnsupportedPairError",
]
