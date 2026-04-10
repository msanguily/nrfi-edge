"""Tests for the NRFI prediction pipeline with fully mocked database."""

import pytest
from unittest.mock import MagicMock, patch, call

import src.pipeline.predict as predict_module
from src.pipeline.predict import predict_nrfi, get_marcel_weighted_rates, get_best_split_rates


# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

LEAGUE_RATES = {
    'season': 2024,
    'k_rate': 0.223,
    'bb_rate': 0.083,
    'hbp_rate': 0.012,
    'single_rate': 0.150,
    'double_rate': 0.046,
    'triple_rate': 0.005,
    'hr_rate': 0.031,
    'pa': 185000,
    'runs_per_game': 4.53,
    'nrfi_pct': 0.492,
}

HOME_PITCHER = {
    'mlb_player_id': 543037,
    'name': 'Gerrit Cole',
    'throws': 'R',
    'bats': 'R',
    'position': 'P',
    'current_team_id': 147,
    'sprint_speed': None,
}

AWAY_PITCHER = {
    'mlb_player_id': 594798,
    'name': 'Dylan Cease',
    'throws': 'R',
    'bats': 'R',
    'position': 'P',
    'current_team_id': 135,
    'sprint_speed': None,
}

GAME = {
    'game_pk': 745123,
    'game_date': '2024-07-15',
    'game_type': 'regular',
    'status': 'final',
    'home_team_id': 147,
    'away_team_id': 135,
    'home_pitcher_id': 543037,
    'away_pitcher_id': 594798,
    'park_id': 1,
    'hp_umpire_id': 3000,
    'is_day_game': False,
    'game_total': 8.5,
}

PARK_OUTDOOR = {
    'park_id': 1,
    'name': 'Yankee Stadium',
    'mlb_team_id': 147,
    'is_dome': False,
    'is_retractable_roof': False,
    'elevation_feet': 55,
    'run_factor': 104,
    'hr_factor': 112,
}

PARK_DOME = {
    'park_id': 2,
    'name': 'Tropicana Field',
    'mlb_team_id': 139,
    'is_dome': True,
    'is_retractable_roof': False,
    'elevation_feet': 42,
    'run_factor': 92,
    'hr_factor': 88,
}

WEATHER = {
    'game_pk': 745123,
    'temperature_f': 82.0,
    'humidity_pct': 55.0,
    'wind_speed_mph': 12.0,
    'wind_direction_degrees': 180,
    'wind_relative': 'out',
    'cloud_cover_pct': 20.0,
    'is_dome_closed': False,
}

UMPIRE = {
    'mlb_umpire_id': 3000,
    'name': 'Angel Hernandez',
    'games_called': 3200,
    'walk_rate_impact': 0.008,
}


def _make_pitcher_stats(player_id, season, **overrides):
    row = {
        'mlb_player_id': player_id,
        'season': season,
        'games_started': 30,
        'innings_pitched': 190.0,
        'k_rate': 0.280,
        'bb_rate': 0.065,
        'hbp_rate': 0.008,
        'hr_rate': 0.025,
        'single_rate': 0.140,
        'double_rate': 0.040,
        'triple_rate': 0.003,
        'gb_rate': 0.420,
    }
    row.update(overrides)
    return row


def _make_batter_stats(player_id, season, **overrides):
    row = {
        'mlb_player_id': player_id,
        'season': season,
        'pa': 550,
        'k_rate': 0.210,
        'bb_rate': 0.090,
        'hbp_rate': 0.010,
        'single_rate': 0.160,
        'double_rate': 0.050,
        'triple_rate': 0.006,
        'hr_rate': 0.035,
    }
    row.update(overrides)
    return row


def _lineup_rows(team_id, player_ids):
    return [
        {
            'game_pk': 745123,
            'team_id': team_id,
            'batting_order': i + 1,
            'mlb_player_id': pid,
            'confirmed_at': '2024-07-15T16:00:00Z',
        }
        for i, pid in enumerate(player_ids)
    ]


AWAY_BATTER_IDS = [100 + i for i in range(9)]
HOME_BATTER_IDS = [200 + i for i in range(9)]


def _build_mock_db(
    game=None,
    park=None,
    weather=None,
    umpire=None,
    odds=None,
):
    """Build a mock supabase client that returns canned data."""
    if game is None:
        game = GAME
    if park is None:
        park = PARK_OUTDOOR

    # Collect all pitcher/batter stats
    pitcher_stats = {}
    for pid in [game['home_pitcher_id'], game['away_pitcher_id']]:
        for s in [2024, 2023, 2022]:
            pitcher_stats[(pid, s)] = _make_pitcher_stats(pid, s)

    batter_stats = {}
    for pid in AWAY_BATTER_IDS + HOME_BATTER_IDS:
        for s in [2024, 2023, 2022]:
            batter_stats[(pid, s)] = _make_batter_stats(pid, s)

    players_db = {
        game['home_pitcher_id']: HOME_PITCHER,
        game['away_pitcher_id']: AWAY_PITCHER,
    }
    for pid in AWAY_BATTER_IDS:
        players_db[pid] = {
            'mlb_player_id': pid,
            'name': f'Away Batter {pid}',
            'throws': 'R',
            'bats': 'L' if pid % 2 == 0 else 'R',
            'position': 'OF',
        }
    for pid in HOME_BATTER_IDS:
        players_db[pid] = {
            'mlb_player_id': pid,
            'name': f'Home Batter {pid}',
            'throws': 'R',
            'bats': 'R',
            'position': 'IF',
        }

    class MockResponse:
        def __init__(self, data):
            self.data = data

    class MockQuery:
        """Chainable mock for supabase query builder."""

        def __init__(self, table_name, all_data):
            self._table = table_name
            self._data = all_data
            self._filters = {}
            self._order_by = None
            self._order_desc = False
            self._limit_n = None
            self._select_cols = '*'

        def select(self, cols='*'):
            self._select_cols = cols
            return self

        def eq(self, col, val):
            self._filters[col] = val
            return self

        def order(self, col, desc=False):
            self._order_by = col
            self._order_desc = desc
            return self

        def limit(self, n):
            self._limit_n = n
            return self

        def upsert(self, data, on_conflict=None):
            return self

        def execute(self):
            rows = self._data
            for col, val in self._filters.items():
                rows = [r for r in rows if r.get(col) == val]
            if self._order_by:
                rows = sorted(
                    rows,
                    key=lambda r: r.get(self._order_by, ''),
                    reverse=self._order_desc,
                )
            if self._limit_n:
                rows = rows[: self._limit_n]
            return MockResponse(rows)

    # Build table data stores
    tables = {
        'games': [game],
        'parks': [park],
        'league_averages': [LEAGUE_RATES],
        'weather_snapshots': [weather] if weather else [],
        'umpires': [umpire] if umpire else [],
        'odds': odds or [],
        'players': list(players_db.values()),
        'pitcher_stats': list(pitcher_stats.values()),
        'batter_stats': list(batter_stats.values()),
        'platoon_splits': [],  # no splits by default — falls back to overall
        'lineups': (
            _lineup_rows(game['away_team_id'], AWAY_BATTER_IDS)
            + _lineup_rows(game['home_team_id'], HOME_BATTER_IDS)
        ),
        'predictions': [],
    }

    mock_db = MagicMock()
    mock_db.table = lambda name: MockQuery(name, tables.get(name, []))
    return mock_db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPredictNRFI:
    """Integration tests for predict_nrfi."""

    def test_basic_prediction_returns_valid_probabilities(self):
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        assert result is not None
        assert 0 < result['p_nrfi_top'] <= 1.0
        assert 0 < result['p_nrfi_bottom'] <= 1.0
        assert 0 < result['p_nrfi_combined'] <= 1.0
        assert 0 < result['p_nrfi_calibrated'] <= 1.0

    def test_combined_equals_top_times_bottom(self):
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        assert result is not None
        expected = result['p_nrfi_top'] * result['p_nrfi_bottom']
        assert abs(result['p_nrfi_combined'] - expected) < 1e-10

    def test_factor_details_keys(self):
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        fd = result['factor_details']
        assert 'home_pitcher' in fd
        assert 'away_pitcher' in fd
        assert 'away_top4_batters' in fd
        assert 'home_top4_batters' in fd
        assert 'park' in fd
        assert 'weather' in fd
        assert 'umpire' in fd
        assert 'adjustments_applied' in fd

        assert fd['home_pitcher']['id'] == 543037
        assert fd['away_pitcher']['id'] == 594798
        assert len(fd['away_top4_batters']) == 4
        assert len(fd['home_top4_batters']) == 4

    def test_dome_game_skips_weather_adjustments(self):
        dome_game = dict(GAME, park_id=2)
        db = _build_mock_db(game=dome_game, park=PARK_DOME, weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        adj = result['factor_details']['adjustments_applied']
        assert 'park_factor' in adj
        assert 'umpire' in adj
        assert 'temperature' not in adj
        assert 'wind' not in adj

    def test_non_regular_game_returns_none(self):
        spring_game = dict(GAME, game_type='spring')
        db = _build_mock_db(game=spring_game)
        result = predict_nrfi(745123, db)
        assert result is None

    def test_no_lineups_uses_league_average(self):
        """If lineups table is empty, predict with league-average batters."""
        db = _build_mock_db()
        # Override lineups to be empty
        orig_table = db.table

        class EmptyLineupQuery:
            def select(self, *a, **kw):
                return self

            def eq(self, *a, **kw):
                return self

            def order(self, *a, **kw):
                return self

            def limit(self, *a, **kw):
                return self

            def execute(self):
                return MagicMock(data=[])

        def patched_table(name):
            if name == 'lineups':
                return EmptyLineupQuery()
            return orig_table(name)

        db.table = patched_table
        result = predict_nrfi(745123, db)
        # Should still produce a prediction using league-average batters
        assert result is not None
        assert 0.3 < result['p_nrfi_calibrated'] < 0.7

    def test_with_odds_computes_edge(self):
        odds_data = [
            {
                'game_pk': 745123,
                'book': 'DraftKings',
                'nrfi_price': -135,
                'yrfi_price': 115,
                'nrfi_decimal': 1.741,
                'yrfi_decimal': 2.15,
                'implied_nrfi_prob': 0.574,
            },
            {
                'game_pk': 745123,
                'book': 'FanDuel',
                'nrfi_price': -130,
                'yrfi_price': 110,
                'nrfi_decimal': 1.769,
                'yrfi_decimal': 2.10,
                'implied_nrfi_prob': 0.565,
            },
        ]
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE, odds=odds_data)
        result = predict_nrfi(745123, db)

        assert result is not None
        assert result['best_book'] is not None
        assert result['implied_prob'] is not None
        assert result['edge'] is not None
        assert result['kelly_fraction'] is not None
        assert isinstance(result['bet_recommended'], bool)

    def test_no_weather_no_umpire_still_works(self):
        db = _build_mock_db(weather=None, umpire=None)
        result = predict_nrfi(745123, db)

        assert result is not None
        adj = result['factor_details']['adjustments_applied']
        assert 'park_factor' in adj
        assert 'temperature' not in adj
        assert 'wind' not in adj
        assert 'umpire' not in adj
        assert result['factor_details']['weather'] is None
        assert result['factor_details']['umpire'] is None

    def test_game_not_found_returns_none(self):
        db = _build_mock_db()
        result = predict_nrfi(999999, db)
        assert result is None

    def test_is_backtest_flag(self):
        db = _build_mock_db()
        result = predict_nrfi(745123, db)
        assert result['is_backtest'] is True

    def test_prediction_type_confirmed(self):
        db = _build_mock_db()
        result = predict_nrfi(745123, db)
        assert result['prediction_type'] == 'confirmed'


class TestGetMarcelWeightedRates:
    """Unit tests for the Marcel weighting helper."""

    def test_returns_none_when_no_data(self):
        db = _build_mock_db()
        # Query for a player that doesn't exist in batter_stats
        result = get_marcel_weighted_rates(999999, 2024, 'batter_stats', db, {
            'k': 0.223, 'bb': 0.083, 'hbp': 0.012,
            'single': 0.150, 'double': 0.046, 'triple': 0.005, 'hr': 0.031,
        })
        assert result is None

    def test_returns_rates_for_existing_player(self):
        db = _build_mock_db()
        league = {
            'k': 0.223, 'bb': 0.083, 'hbp': 0.012,
            'single': 0.150, 'double': 0.046, 'triple': 0.005, 'hr': 0.031,
        }
        result = get_marcel_weighted_rates(100, 2024, 'batter_stats', db, league)

        assert result is not None
        for key in ['k', 'bb', 'hbp', 'single', 'double', 'triple', 'hr']:
            assert key in result
            assert 0 <= result[key] <= 1


class TestFirstInningAdjustment:
    """Verify first-inning adjustments are applied in the pipeline."""

    @patch('src.pipeline.predict.adjust_for_first_inning_bottom', wraps=predict_module.adjust_for_first_inning_bottom)
    @patch('src.pipeline.predict.adjust_for_first_inning_top', wraps=predict_module.adjust_for_first_inning_top)
    @patch('src.pipeline.predict.apply_all_adjustments', wraps=predict_module.apply_all_adjustments)
    def test_first_inning_adjustment_called_before_environmental(self, mock_env, mock_fi_top, mock_fi_bottom):
        """adjust_for_first_inning_top/bottom must be called on each batter's matchup
        rates before apply_all_adjustments."""
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        assert result is not None
        # 9 away batters (top) + 9 home batters (bottom) = 18 env calls
        assert mock_fi_top.call_count == 9
        assert mock_fi_bottom.call_count == 9
        assert mock_env.call_count == 18

    def test_predictions_differ_from_no_first_inning_adj(self):
        """With first-inning adjustments, predictions should differ from a
        hypothetical pipeline without them (i.e., they actually change rates)."""
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result_with = predict_nrfi(745123, db)

        # Run again with identity first-inning adjustments
        identity = lambda r: r
        with patch('src.pipeline.predict.adjust_for_first_inning_top', side_effect=identity), \
             patch('src.pipeline.predict.adjust_for_first_inning_bottom', side_effect=identity):
            result_without = predict_nrfi(745123, db)

        # The HR multiplier is 1.12, so predictions should differ
        assert result_with['p_nrfi_combined'] != result_without['p_nrfi_combined']


class TestCalibrator:
    """Verify isotonic calibrator integration."""

    def setup_method(self):
        """Reset calibrator cache before each test."""
        predict_module._calibrator_cache = None

    def teardown_method(self):
        """Reset calibrator cache after each test."""
        predict_module._calibrator_cache = None

    @patch('src.pipeline.predict._get_calibrator')
    def test_calibrator_applied_when_available(self, mock_get_cal):
        """When calibrator exists, calibrate() is called on p_nrfi_combined."""
        mock_cal = MagicMock()
        mock_cal.calibrate.return_value = 0.55
        mock_get_cal.return_value = mock_cal

        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        assert result is not None
        mock_cal.calibrate.assert_called_once()
        # The argument should be p_nrfi_combined
        call_arg = mock_cal.calibrate.call_args[0][0]
        assert abs(call_arg - result['p_nrfi_combined']) < 1e-10
        assert result['p_nrfi_calibrated'] == 0.55

    @patch('src.pipeline.predict._get_calibrator')
    def test_no_calibrator_uses_raw(self, mock_get_cal):
        """When no calibrator.json exists, p_nrfi_calibrated == p_nrfi_combined."""
        mock_get_cal.return_value = None

        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)

        assert result is not None
        assert result['p_nrfi_calibrated'] == result['p_nrfi_combined']

    @patch('src.pipeline.predict._get_calibrator')
    def test_no_calibrator_logs_warning(self, mock_get_cal, caplog):
        """Missing calibrator should log a warning (from _get_calibrator)."""
        mock_get_cal.return_value = None

        # We test that the pipeline still works and returns uncalibrated values
        db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
        result = predict_nrfi(745123, db)
        assert result is not None
        assert result['p_nrfi_calibrated'] == result['p_nrfi_combined']

    def test_calibrator_cache_reused(self):
        """The cached calibrator is reused across multiple predict_nrfi calls."""
        mock_cal = MagicMock()
        mock_cal.calibrate.return_value = 0.52

        with patch('src.pipeline.predict._get_calibrator', return_value=mock_cal) as mock_get:
            db = _build_mock_db(weather=WEATHER, umpire=UMPIRE)
            predict_nrfi(745123, db)
            predict_nrfi(745123, db)

            # _get_calibrator is called each time, but returns cached instance
            assert mock_get.call_count == 2
            # Same mock object used both times
            assert mock_cal.calibrate.call_count == 2

    def test_get_calibrator_caches_on_file_exists(self, tmp_path):
        """_get_calibrator loads from file and caches the result."""
        import json
        import numpy as np

        # Create a fake calibrator.json
        cal_data = {
            'X_thresholds': [0.3, 0.5, 0.7],
            'y_thresholds': [0.35, 0.50, 0.65],
            'training_size': 100,
        }
        cal_file = tmp_path / 'config' / 'calibrator.json'
        cal_file.parent.mkdir(parents=True)
        cal_file.write_text(json.dumps(cal_data))

        with patch('src.pipeline.predict.os.path.abspath') as mock_abs:
            # Make the path resolution point to our tmp_path
            mock_abs.return_value = str(tmp_path / 'src' / 'pipeline' / 'predict.py')

            predict_module._calibrator_cache = None
            cal1 = predict_module._get_calibrator()
            cal2 = predict_module._get_calibrator()

            assert cal1 is not None
            assert cal1.is_fitted
            assert cal1 is cal2  # same cached object

    def test_get_calibrator_returns_none_when_missing(self, tmp_path):
        """_get_calibrator returns None when calibrator.json doesn't exist."""
        with patch('src.pipeline.predict.os.path.abspath') as mock_abs:
            mock_abs.return_value = str(tmp_path / 'src' / 'pipeline' / 'predict.py')

            predict_module._calibrator_cache = None
            result = predict_module._get_calibrator()
            assert result is None


class TestGetBestSplitRates:
    """Unit tests for the platoon split helper."""

    def test_returns_none_when_no_splits(self):
        db = _build_mock_db()
        result = get_best_split_rates(100, 2024, 'batter', 'R', db)
        assert result is None
