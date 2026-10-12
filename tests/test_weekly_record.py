import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dkcapital.weekly_record import close_time, record_due, weekly_record


class WeeklyRecordTests(unittest.TestCase):
    def test_ny_close_dst(self):
        self.assertEqual(close_time(datetime(2026, 10, 16, 22, 0, tzinfo=UTC)).hour, 21)
        self.assertEqual(close_time(datetime(2026, 12, 11, 23, 0, tzinfo=UTC)).hour, 22)

    def test_friday_close_grace(self):
        closing = datetime(2026, 10, 16, 21, tzinfo=UTC)
        self.assertFalse(record_due(closing + timedelta(minutes=4)))
        self.assertTrue(record_due(closing + timedelta(minutes=5)))
        self.assertFalse(record_due(closing + timedelta(days=3)))

    def test_rejects_snapshot_not_at_close(self):
        closing = datetime(2026, 10, 16, 21, tzinfo=UTC)
        with self.assertRaises(ValueError):
            weekly_record({"generated_at": (closing - timedelta(hours=2)).isoformat()},
                          now=closing + timedelta(minutes=5),
                          pip_size=0.01, starting_balance=None)


if __name__ == "__main__":
    unittest.main()
