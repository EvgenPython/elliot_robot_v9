import unittest
from datetime import datetime, timezone
from waveframe.clock import ReplayClock


class ReplayClockTests(unittest.TestCase):
    def test_clock_uses_simulator_time_not_wall_time(self):
        c = ReplayClock()
        x = c.set("2026-09-25T09:30:00+00:00")
        self.assertEqual(c.now(), datetime(2026, 9, 25, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(x, c.now())


if __name__ == "__main__":
    unittest.main()
