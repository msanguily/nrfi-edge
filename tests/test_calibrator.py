"""Tests for the NRFI isotonic regression calibrator."""

import tempfile
import os

import numpy as np
import pytest

from src.calibration.calibrator import (
    NRFICalibrator,
    compute_ece,
    compute_calibration_curve,
)


class TestNRFICalibrator:
    def test_well_calibrated_model(self):
        """Predictions all 0.7, 70% of outcomes are 1. Brier ~ 0.21, ECE ~ 0."""
        np.random.seed(42)
        n = 10000
        preds = np.full(n, 0.7)
        outcomes = np.zeros(n)
        outcomes[:7000] = 1.0
        np.random.shuffle(outcomes)

        cal = NRFICalibrator()
        metrics = cal.evaluate(preds, outcomes)

        # Theoretical Brier for p=0.7: p*(1-p)^2 + (1-p)*p^2 = 0.7*0.09 + 0.3*0.49 = 0.21
        assert abs(metrics['brier_score'] - 0.21) < 0.01
        assert metrics['ece'] < 0.02

    def test_overconfident_model(self):
        """Predictions 0.9 but only 70% hit. Calibrator should correct toward 0.7."""
        np.random.seed(42)
        n = 5000
        preds = np.full(n, 0.9)
        outcomes = np.zeros(n)
        outcomes[:3500] = 1.0
        np.random.shuffle(outcomes)

        cal = NRFICalibrator()
        cal.fit(preds, outcomes)

        calibrated = cal.calibrate(0.9)
        assert abs(calibrated - 0.7) < 0.05

    def test_save_load_round_trip(self):
        """Fit, save, load into new instance — calibrate(0.75) should match."""
        np.random.seed(42)
        n = 2000
        preds = np.random.uniform(0.5, 0.9, n)
        outcomes = (np.random.rand(n) < preds).astype(float)

        cal1 = NRFICalibrator()
        cal1.fit(preds, outcomes)
        val1 = cal1.calibrate(0.75)

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            tmppath = f.name

        try:
            cal1.save(tmppath)
            cal2 = NRFICalibrator()
            cal2.load(tmppath)
            val2 = cal2.calibrate(0.75)
            assert val1 == val2
            assert cal2.training_size == n
        finally:
            os.unlink(tmppath)

    def test_unfitted_calibrator_passthrough(self):
        """calibrate() returns raw probability when not fitted."""
        cal = NRFICalibrator()
        assert cal.calibrate(0.73) == 0.73
        batch = np.array([0.5, 0.6, 0.7])
        np.testing.assert_array_equal(cal.calibrate_batch(batch), batch)

    def test_save_unfitted_raises(self):
        """Saving an unfitted calibrator raises ValueError."""
        cal = NRFICalibrator()
        with pytest.raises(ValueError, match="Cannot save unfitted"):
            cal.save("/tmp/should_not_exist.json")


class TestECE:
    def test_perfectly_calibrated(self):
        """ECE should be near 0 for perfectly calibrated predictions."""
        np.random.seed(42)
        n = 10000
        preds = np.random.uniform(0.0, 1.0, n)
        outcomes = (np.random.rand(n) < preds).astype(float)
        ece = compute_ece(preds, outcomes)
        assert ece < 0.02

    def test_overconfident_ece(self):
        """Predictions always 0.9, actual rate 0.7 — ECE ~ 0.20."""
        n = 10000
        preds = np.full(n, 0.9)
        outcomes = np.zeros(n)
        outcomes[:7000] = 1.0
        ece = compute_ece(preds, outcomes)
        assert abs(ece - 0.20) < 0.03

    def test_quantile_strategy(self):
        """Quantile strategy should produce a valid non-negative result."""
        np.random.seed(42)
        n = 1000
        preds = np.random.uniform(0.5, 0.9, n)
        outcomes = (np.random.rand(n) < preds).astype(float)
        ece = compute_ece(preds, outcomes, strategy='quantile')
        assert 0.0 <= ece <= 1.0


class TestLogLossClipping:
    def test_no_infinity_or_nan(self):
        """Predictions of exactly 0.0 and 1.0 should not produce inf/nan."""
        preds = np.array([0.0, 1.0, 0.0, 1.0])
        outcomes = np.array([0.0, 1.0, 1.0, 0.0])
        cal = NRFICalibrator()
        metrics = cal.evaluate(preds, outcomes)
        assert np.isfinite(metrics['log_loss'])
        assert np.isfinite(metrics['brier_score'])


class TestEmptyBins:
    def test_empty_bins_handled(self):
        """All predictions in 0.6-0.8 — lower bins should be skipped without error."""
        np.random.seed(42)
        n = 1000
        preds = np.random.uniform(0.6, 0.8, n)
        outcomes = (np.random.rand(n) < preds).astype(float)

        ece = compute_ece(preds, outcomes)
        assert 0.0 <= ece <= 1.0

        curve = compute_calibration_curve(preds, outcomes)
        # Only bins covering 0.6-0.8 should have data (bins 6 and 7 out of 10)
        assert len(curve['bin_centers']) < 10
        assert all(c > 0 for c in curve['bin_counts'])


class TestCalibrationCurve:
    def test_basic_structure(self):
        """Calibration curve returns correct structure with matching lengths."""
        np.random.seed(42)
        n = 2000
        preds = np.random.uniform(0.0, 1.0, n)
        outcomes = (np.random.rand(n) < preds).astype(float)

        curve = compute_calibration_curve(preds, outcomes)
        assert set(curve.keys()) == {'bin_centers', 'bin_accuracies', 'bin_counts', 'bin_confidences'}
        n_bins = len(curve['bin_centers'])
        assert len(curve['bin_accuracies']) == n_bins
        assert len(curve['bin_counts']) == n_bins
        assert len(curve['bin_confidences']) == n_bins
        assert sum(curve['bin_counts']) == n
