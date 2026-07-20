import unittest

from langchain_core.tools import ToolException

from backend.tools.chart_plan_tool import _validate_range_shape


class ChartRangeLimitTests(unittest.TestCase):
    def test_july_hourly_single_series_uses_full_natural_days(self):
        result = _validate_range_shape(
            station_count=1,
            source_count=1,
            granularity="hourly",
            period_start="2026-07-01",
            period_end="2026-07-31",
        )
        self.assertEqual(result["period_days"], 31)
        self.assertEqual(result["points_per_series"], 744)
        self.assertEqual(result["total_points"], 744)
        self.assertEqual(result["capability_id"], "time_series_trend")

    def test_range_over_744_points_is_rejected_before_query(self):
        with self.assertRaises(ToolException):
            _validate_range_shape(
                station_count=1,
                source_count=1,
                granularity="hourly",
                period_start="2026-07-01",
                period_end="2026-08-01",
            )

    def test_multi_station_uses_one_source_and_dynamic_series_count(self):
        result = _validate_range_shape(
            station_count=4,
            source_count=1,
            granularity="hourly",
            period_start="2026-07-01",
            period_end="2026-07-31",
        )
        self.assertEqual(result["capability_id"], "time_series_compare")
        self.assertEqual(result["series_count"], 4)
        self.assertEqual(result["total_points"], 2976)

    def test_multi_station_mixed_sources_are_rejected(self):
        with self.assertRaises(ToolException):
            _validate_range_shape(
                station_count=2,
                source_count=2,
                granularity="hourly",
                period_start="2026-07-01",
                period_end="2026-07-02",
            )


if __name__ == "__main__":
    unittest.main()
