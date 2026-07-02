"""Feature engineering — compose causal indicators into per-candle matrices.

The feature matrix is the foundation for forward-return backtesting and
hypothesis search: one row per candle, every feature computed strictly from
past-and-current data (no look-ahead). See ``feature_matrix``.
"""

from app.analysis.features.feature_matrix import FeatureRow, build_feature_matrix

__all__ = ["FeatureRow", "build_feature_matrix"]
