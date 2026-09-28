"""Trend test, segmentation and baseline methods against known answers."""
import numpy as np
import pandas as pd
from scipy.stats import kendalltau

from analytics.baselines import moving_average, naive_scale, sba, seasonal_naive, ses
from analytics.config import AnalysisConfig
from analytics.segmentation import demand_patterns
from analytics.trends import mann_kendall, sen_slope


def test_mann_kendall_matches_kendall_tau():
    rng = np.random.default_rng(3)
    x = rng.poisson(3, 30).astype(float) + np.arange(30) * 0.1        # includes ties
    s, z, p = mann_kendall(x)
    tau, p_ref = kendalltau(np.arange(30), x, method="asymptotic")
    assert np.sign(s) == np.sign(tau)
    assert abs(p - p_ref) < 0.03          # small gap from the continuity correction


def test_trend_on_clear_and_flat_series():
    assert mann_kendall(np.arange(20, dtype=float))[2] < 0.001
    assert mann_kendall(np.full(20, 4.0))[2] == 1.0
    assert sen_slope(3 + 2.5 * np.arange(15)) == 2.5


def test_demand_pattern_classes():
    cfg = AnalysisConfig()
    U = np.array([
        [10, 11, 9, 10, 12, 10, 9, 11],      # smooth
        [2, 30, 1, 25, 3, 40, 2, 35],        # erratic: every week, sizes vary
        [0, 0, 5, 0, 0, 5, 0, 5],            # intermittent: gaps, similar sizes
        [0, 0, 50, 0, 0, 2, 0, 90],          # lumpy: gaps and variable sizes
        [0, 0, 0, 0, 0, 0, 0, 7],            # one sale only
    ], dtype=float)
    assert list(demand_patterns(U, cfg)["pattern"]) == ["smooth", "erratic", "intermittent", "lumpy", "insufficient"]


def test_ses_matches_statsmodels():
    from statsmodels.tsa.holtwinters import SimpleExpSmoothing
    y = np.array([12, 15, 11, 14, 18, 16, 13, 17, 19, 15], dtype=float)
    ours = ses(y[None, :], len(y) - 1, 1, alphas=(0.3,))[0, 0]
    ref = SimpleExpSmoothing(y, initialization_method="known", initial_level=y[0]).fit(smoothing_level=0.3, optimized=False)
    assert abs(ours - ref.forecast(1)[0]) < 1e-9


def test_simple_baselines():
    U = np.array([np.arange(60, dtype=float)])
    assert seasonal_naive(U, 59, 2)[0].tolist() == [8.0, 9.0]              # same week last year
    assert moving_average(U, 59, 1, 4)[0, 0] == np.mean([56, 57, 58, 59])
    assert naive_scale(U, 59)[0] == 1.0
    intermittent = np.array([[0, 0, 6, 0, 0, 6, 0, 0, 6]], dtype=float)
    f = sba(intermittent, 8, 1, alpha=0.1)[0, 0]
    assert abs(f - 0.95 * 6 / 3) < 0.05                                      # size 6 every 3 weeks, bias-corrected
