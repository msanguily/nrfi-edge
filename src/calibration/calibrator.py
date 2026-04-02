"""
Isotonic regression calibration for NRFI model probabilities.

The Markov chain produces raw probabilities that may be systematically biased.
Isotonic regression is non-parametric and corrects any monotonic distortion,
unlike Platt scaling which assumes sigmoidal distortion.
"""

from sklearn.isotonic import IsotonicRegression
import numpy as np
import json


class NRFICalibrator:
    def __init__(self):
        self.model = IsotonicRegression(out_of_bounds='clip')
        self.is_fitted = False
        self.training_size = 0

    def fit(self, predicted_probs: np.ndarray, actual_outcomes: np.ndarray):
        """
        Train calibrator on historical predictions vs actual NRFI outcomes.
        predicted_probs: array of raw model P(NRFI) values
        actual_outcomes: array of 0/1 (1 = NRFI hit, 0 = run scored)
        """
        self.model.fit(predicted_probs, actual_outcomes)
        self.is_fitted = True
        self.training_size = len(predicted_probs)

    def calibrate(self, raw_prob: float) -> float:
        """
        Apply calibration to a single raw probability.
        If not fitted, return raw_prob unchanged.
        """
        if not self.is_fitted:
            return raw_prob
        return float(self.model.predict([raw_prob])[0])

    def calibrate_batch(self, raw_probs: np.ndarray) -> np.ndarray:
        """Apply calibration to array of probabilities."""
        if not self.is_fitted:
            return raw_probs
        return self.model.predict(raw_probs)

    def save(self, filepath: str):
        """
        Save fitted model to disk as JSON.
        Serialize the isotonic regression's X_thresholds_ and y_thresholds_ arrays.
        """
        if not self.is_fitted:
            raise ValueError("Cannot save unfitted calibrator")
        data = {
            'X_thresholds': self.model.X_thresholds_.tolist(),
            'y_thresholds': self.model.y_thresholds_.tolist(),
            'training_size': self.training_size,
        }
        with open(filepath, 'w') as f:
            json.dump(data, f)

    def load(self, filepath: str):
        """
        Load fitted model from disk.
        Reconstruct IsotonicRegression from saved thresholds.
        """
        with open(filepath, 'r') as f:
            data = json.load(f)
        X = np.array(data['X_thresholds'])
        y = np.array(data['y_thresholds'])
        self.model.fit(X, y)
        self.is_fitted = True
        self.training_size = data['training_size']

    def evaluate(self, predicted_probs: np.ndarray, actual_outcomes: np.ndarray) -> dict:
        """
        Compute evaluation metrics: Brier Score, Log Loss, ECE, and calibration curve.
        Clips probabilities to [0.001, 0.999] before computing log loss to avoid log(0).
        """
        predicted_probs = np.asarray(predicted_probs, dtype=float)
        actual_outcomes = np.asarray(actual_outcomes, dtype=float)

        # Brier Score: mean squared error between predicted and actual
        brier_score = float(np.mean((predicted_probs - actual_outcomes) ** 2))

        # Log Loss with clipping to avoid log(0)
        clipped = np.clip(predicted_probs, 0.001, 0.999)
        log_loss = float(-np.mean(
            actual_outcomes * np.log(clipped) + (1 - actual_outcomes) * np.log(1 - clipped)
        ))

        # ECE
        ece = compute_ece(predicted_probs, actual_outcomes)

        # Calibration curve
        cal_curve = compute_calibration_curve(predicted_probs, actual_outcomes)

        return {
            'brier_score': brier_score,
            'log_loss': log_loss,
            'ece': ece,
            'calibration_curve': cal_curve,
            'n_samples': len(predicted_probs),
        }


def compute_ece(predicted_probs, actual_outcomes, n_bins=10, strategy='uniform'):
    """
    Expected Calibration Error.

    ECE = sum over bins of: (|bin| / n) * |accuracy(bin) - confidence(bin)|

    strategy: 'uniform' for equal-width bins, 'quantile' for equal-frequency bins.
    Empty bins are skipped. Lower is better. 0 = perfectly calibrated.
    """
    predicted_probs = np.asarray(predicted_probs, dtype=float)
    actual_outcomes = np.asarray(actual_outcomes, dtype=float)
    n = len(predicted_probs)

    if n == 0:
        return 0.0

    if strategy == 'uniform':
        bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
        # Assign each prediction to a bin
        bin_indices = np.digitize(predicted_probs, bin_edges[1:-1], right=False)
    elif strategy == 'quantile':
        # Equal-frequency bins based on quantiles of predicted_probs
        quantiles = np.linspace(0.0, 1.0, n_bins + 1)
        bin_edges = np.quantile(predicted_probs, quantiles)
        bin_indices = np.digitize(predicted_probs, bin_edges[1:-1], right=False)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    ece = 0.0
    for b in range(n_bins):
        mask = bin_indices == b
        bin_count = mask.sum()
        if bin_count == 0:
            continue
        bin_accuracy = actual_outcomes[mask].mean()
        bin_confidence = predicted_probs[mask].mean()
        ece += (bin_count / n) * abs(bin_accuracy - bin_confidence)

    return float(ece)


def compute_calibration_curve(predicted_probs, actual_outcomes, n_bins=10, strategy='uniform'):
    """
    Compute calibration curve data.

    Returns dict with:
      - bin_centers: midpoints for each non-empty bin
      - bin_accuracies: actual NRFI rate in each bin
      - bin_counts: number of samples in each bin
      - bin_confidences: mean predicted probability in each bin

    Empty bins are skipped. Perfect calibration: bin_accuracies == bin_confidences.
    """
    predicted_probs = np.asarray(predicted_probs, dtype=float)
    actual_outcomes = np.asarray(actual_outcomes, dtype=float)
    n = len(predicted_probs)

    if strategy == 'uniform':
        bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
        bin_indices = np.digitize(predicted_probs, bin_edges[1:-1], right=False)
    elif strategy == 'quantile':
        quantiles = np.linspace(0.0, 1.0, n_bins + 1)
        bin_edges = np.quantile(predicted_probs, quantiles)
        bin_indices = np.digitize(predicted_probs, bin_edges[1:-1], right=False)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    centers = []
    accuracies = []
    counts = []
    confidences = []

    for b in range(n_bins):
        mask = bin_indices == b
        bin_count = mask.sum()
        if bin_count == 0:
            continue
        centers.append(float((bin_edges[b] + bin_edges[b + 1]) / 2))
        accuracies.append(float(actual_outcomes[mask].mean()))
        counts.append(int(bin_count))
        confidences.append(float(predicted_probs[mask].mean()))

    return {
        'bin_centers': centers,
        'bin_accuracies': accuracies,
        'bin_counts': counts,
        'bin_confidences': confidences,
    }
