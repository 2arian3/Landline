"""The Matcher (docs/SCORING.md): code, not a model. Same inputs, same order."""
from landline.scoring.features import FeatureMatrix
from landline.scoring.rank import Ranked, Ranking, explain, rank
from landline.scoring.weights import feature_key, weights_for

__all__ = ["FeatureMatrix", "Ranked", "Ranking", "explain", "feature_key", "rank", "weights_for"]
