import unittest
from datetime import datetime, timedelta, timezone

from waveframe.costs import SimulatedLiveCostTracker


class SimulatedLiveCostTests(unittest.TestCase):
    def test_first_historical_use_rebuilds_even_if_actual_api_hit(self):
        tracker = SimulatedLiveCostTracker()
        t = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)

        result = tracker.calculate(
            stable_prefix="same-prefix",
            sim_time=t,
            model="claude-sonnet-5",
            usage={
                "input_tokens": 100,
                "output_tokens": 10,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 0,
            },
            cache_ttl="1h",
        )

        self.assertEqual(result["cache_status"], "CREATED_OR_REBUILT")
        self.assertEqual(result["usage"]["cache_read_input_tokens"], 0)
        self.assertEqual(result["usage"]["cache_creation_input_tokens"], 1000)

    def test_historical_hit_refreshes_ttl(self):
        tracker = SimulatedLiveCostTracker()
        t = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
        usage = {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 0,
        }

        tracker.calculate(
            stable_prefix="same-prefix",
            sim_time=t,
            model="claude-sonnet-5",
            usage=usage,
            cache_ttl="1h",
        )

        hit1 = tracker.calculate(
            stable_prefix="same-prefix",
            sim_time=t + timedelta(minutes=50),
            model="claude-sonnet-5",
            usage=usage,
            cache_ttl="1h",
        )

        hit2 = tracker.calculate(
            stable_prefix="same-prefix",
            sim_time=t + timedelta(minutes=100),
            model="claude-sonnet-5",
            usage=usage,
            cache_ttl="1h",
        )

        self.assertEqual(hit1["cache_status"], "HIT")
        self.assertEqual(hit2["cache_status"], "HIT")

    def test_historical_expiry_rebuilds(self):
        tracker = SimulatedLiveCostTracker()
        t = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
        usage = {
            "input_tokens": 0,
            "output_tokens": 10,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 0,
        }

        tracker.calculate(
            stable_prefix="same-prefix",
            sim_time=t,
            model="claude-sonnet-5",
            usage=usage,
            cache_ttl="1h",
        )

        result = tracker.calculate(
            stable_prefix="same-prefix",
            sim_time=t + timedelta(minutes=61),
            model="claude-sonnet-5",
            usage=usage,
            cache_ttl="1h",
        )

        self.assertEqual(result["cache_status"], "CREATED_OR_REBUILT")


if __name__ == "__main__":
    unittest.main()
