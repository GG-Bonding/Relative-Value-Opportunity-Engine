# Point-in-time rules

This is the highest-priority constraint in the system.

## A row is never overwritten

A macro print keeps `revision_number`. A later revision is a new row. The unique key rejects a second insert of the same vintage. Replay of a date before the revision's `observed_at` can see only the earlier print.

## Visibility

An as-of query returns rows with `observed_at` less than or equal to the query. The macro vintage used in a feature is the greatest visible revision for that series and period, ordered by revision number, `observed_at`, and version.

## Surprise

```
surprise = (actual - consensus) / std(prior actual - prior consensus)
```

The standard deviation uses only earlier releases of the same series, with sample degrees of freedom. Fewer than eight prior releases leaves the surprise missing. A release that has not happened yet cannot change a past surprise.

## Clocks

Every timestamp is timezone-aware UTC, stored as a lexicographic string `YYYY-MM-DDTHH:MM:SS+00:00`. Naive datetimes are rejected. The four clocks are `effective_at`, `released_at`, `observed_at`, and `ingested_at`, plus source, version, and revision number.

## What the tests lock

- A revision inserted after T does not change the snapshot hash at T.
- A future rate does not change a past factor row.
- A missing positioning field is null on the snapshot. It is not filled with zero.
- Training for fair value at t uses rows strictly before t.
- A forward label of horizon h purges training labels that overlap the test block, plus an embargo. There is no random split.
