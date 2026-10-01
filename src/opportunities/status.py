"""Legal opportunity transitions. Terminal states stay terminal."""

from __future__ import annotations

from domain.enums import OpportunityStatus

_NEXT: dict[OpportunityStatus, set[OpportunityStatus]] = {
    OpportunityStatus.DISCOVERED: {
        OpportunityStatus.WATCHING,
        OpportunityStatus.READY,
        OpportunityStatus.REJECTED,
        OpportunityStatus.EXPIRED,
    },
    OpportunityStatus.WATCHING: {
        OpportunityStatus.READY,
        OpportunityStatus.REJECTED,
        OpportunityStatus.EXPIRED,
        OpportunityStatus.INVALIDATED,
    },
    OpportunityStatus.READY: {
        OpportunityStatus.ENTERED,
        OpportunityStatus.EXPIRED,
        OpportunityStatus.REJECTED,
        OpportunityStatus.INVALIDATED,
    },
    OpportunityStatus.ENTERED: {
        OpportunityStatus.CONVERGING,
        OpportunityStatus.EXIT_READY,
        OpportunityStatus.INVALIDATED,
    },
    OpportunityStatus.CONVERGING: {
        OpportunityStatus.EXIT_READY,
        OpportunityStatus.INVALIDATED,
        OpportunityStatus.ENTERED,
    },
    OpportunityStatus.EXIT_READY: {OpportunityStatus.EXITED},
    OpportunityStatus.EXITED: set(),
    OpportunityStatus.INVALIDATED: set(),
    OpportunityStatus.EXPIRED: set(),
    OpportunityStatus.REJECTED: set(),
}


def transition(current: OpportunityStatus, new: OpportunityStatus) -> OpportunityStatus:
    if current is new:
        return current
    if new not in _NEXT[current]:
        raise ValueError(f"cannot move an opportunity from {current.value} to {new.value}")
    return new
