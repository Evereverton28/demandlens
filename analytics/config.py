"""Central configuration for the DemandLens analytics engine.

Every threshold used in the analysis lives here so it can be cited in the
report and changed in one place.
"""
from dataclasses import dataclass, field


@dataclass
class AnalysisConfig:
    # --- time structure -------------------------------------------------
    horizons: int = 4                 # forecast 1..4 weeks ahead
    backtest_weeks: int = 24          # evaluation window at the end of the data
    origin_step: int = 4              # a new forecast origin every 4 weeks -> 6 origins
    min_train_weeks: int = 30         # weeks of history needed before the first origin
    min_history_weeks: int = 26       # products younger than this use a baseline, not the ML model
    min_feature_weeks: int = 4        # training rows need at least this much product history
    active_window_weeks: int = 26     # a product is "active" if it sold within this window

    # --- descriptive analytics -----------------------------------------
    segmentation_window: int = 52     # weeks used for ABC / XYZ / demand pattern
    abc_cutoffs: tuple = (0.80, 0.95) # cumulative revenue share boundaries for A and B
    xyz_cutoffs: tuple = (0.5, 1.0)   # coefficient of variation boundaries for X and Y
    adi_cutoff: float = 1.32          # Syntetos, Boylan & Croston (2005)
    cv2_cutoff: float = 0.49
    trend_window: int = 26            # weeks tested by Mann-Kendall
    trend_alpha: float = 0.05
    velocity_window: int = 12
    dormant_weeks: int = 13           # no sale for this long -> dormant

    # --- baselines -----------------------------------------------------
    moving_average_window: int = 8
    croston_alpha: float = 0.1
    ses_alphas: tuple = (0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8)

    # --- gradient boosting ------------------------------------------------
    gbm_params: dict = field(default_factory=lambda: dict(
        max_iter=150, learning_rate=0.1, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0,
    ))
    train_window_weeks: int = 52      # train on the most recent year of forecast origins
    random_state: int = 42

    # --- anomalies ------------------------------------------------------
    anomaly_z: float = 3.5            # Iglewicz & Hoaglin (1993)
    anomaly_p90_multiple: float = 2.0 # a spike must reach at least twice a busy (P90) week
    anomaly_high_multiple: float = 5.0  # ... and is "high" severity at five times
    anomaly_min_units: float = 3.0    # ignore deviations smaller than this many units
    anomaly_min_points: int = 8
    txn_window_weeks: int = 12        # transaction outliers are checked in this recent window
    txn_min_history: int = 10
    injection_count: int = 200

    # --- decision support ---------------------------------------------
    runout_cap_days: int = 365
    increase_cover_weeks: float = 4.0
    review_no_sale_weeks: int = 8
