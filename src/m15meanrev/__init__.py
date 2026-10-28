"""EURGBP M15 mean reversion. Separate from the ridge fair-value engine.

The entry trigger is the z-score of the log return on the bar that just closed.
The daily price z-score is only a location filter, and a bar on date T sees the
z-score known at the previous trading day's close.
"""
