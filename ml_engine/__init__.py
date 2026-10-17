"""ML pattern-engineering research platform.

Answers: do technical-analysis patterns contain a repeatable statistical
edge, under which conditions, and does it survive unseen data?

Leakage-free by construction: every detector emits events with explicit
confirmation lags; labels are quarantined from features; validation is
chronological walk-forward.
"""

__version__ = "0.1.0"

FEATURE_VERSION = "fv1"
DATASET_VERSION = "dv1"
LABEL_VERSION = "lv1"
