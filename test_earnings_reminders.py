import contextlib
import datetime as dt
import io
import unittest
from unittest.mock import patch
import earnings_reminders as er


class DeliveryGoogle:
    def __init__(self):
        self.messages = {}
        self.sent = 0

    def find_sent(self, subject):
        return self.messages.get(subject)

    def send(self, to, subject, body):
        self.sent += 1
        data = {"id": str(self.sent), "labelIds": ["SENT"]}
        self.messages[subject] = data
        return data

    def mark_telegram(self, message_id, label):
        for data in self.messages.values():
            if data["id"] == message_id:
                data["labelIds"].append(label)


class DeliveryTelegram:
    def __init__(self):
        self.fail = False
        self.sent = 0

    def send(self, body):
        if self.fail:
            raise RuntimeError("failed")
        self.sent += 1


class Tests(unittest.TestCase):
    def event(self, symbol, date):
        return dict(symbol=symbol, name=symbol, date=date, time="Saat açıklanmamış", status="Tahmini", source="https://example.org")

    def test_portfolio_uses_header_and_company_not_other_cells(self):
        rows = [["OLD"], ["SEMBOL", None, "ŞİRKET ADI"], ["TEKNOLOJİ ŞİRKETLERİ(20)"], ["RBRK", None, "Rubrik"], [], ["MU", None, "Micron"], ["TOPLAM", None, ""]]
        self.assertEqual(er.portfolio(rows), {"RBRK": "Rubrik", "MU": "Micron"})
        with self.assertRaises(RuntimeError):
            er.portfolio([["MU", None, "Micron"]])

    def test_three_day_reminders_catch_late_announcements(self):
        today = dt.date(2026, 10, 13)
        events = [self.event("OLD", "2026-10-12"), self.event("TODAY", "2026-10-13"), self.event("TWO", "2026-10-15"), self.event("THREE", "2026-10-16"), self.event("FOUR", "2026-10-17")]
        plans = er.make_plans(events, {}, today)
        self.assertEqual([p[0] for p in plans], ["reminder:TODAY:2026-10-13", "reminder:TWO:2026-10-15", "reminder:THREE:2026-10-16"])

    def test_weekly_covers_monday_to_sunday_not_next_week(self):
        today = dt.date(2026, 10, 19)
        events = [self.event("VICR", "2026-10-20"), self.event("NOK", "2026-10-22"), self.event("AMD", "2026-10-27")]
        plans = er.make_plans(events, {"VICR": "Vicor", "NOK": "Nokia", "AMD": "AMD", "MU": "Micron"}, today)
        weekly = next(p for p in plans if p[0].startswith("weekly:"))
        self.assertIn("NOK — 22.10.2026", weekly[2])
        self.assertNotIn("AMD — 27.10.2026", weekly[2])
        self.assertIn("AMD: 27.10.2026", weekly[2])
        self.assertIn("Yaklaşan tarihi henüz bulunamayanlar: MU", weekly[2])

    def test_delivery_retry_does_not_resend_successful_channel(self):
        google, telegram = DeliveryGoogle(), DeliveryTelegram()
        plan = ("reminder:MU:2026-12-17", "Bilanço", "body")
        telegram.fail = True
        with self.assertRaises(RuntimeError):
            er.deliver(google, telegram, "self@example.org", "TG", plan)
        self.assertEqual(google.sent, 1)
        telegram.fail = False
        er.deliver(google, telegram, "self@example.org", "TG", plan)
        er.deliver(google, telegram, "self@example.org", "TG", plan)
        self.assertEqual((google.sent, telegram.sent), (1, 1))

    def test_date_change_has_new_notification_identity(self):
        google, telegram = DeliveryGoogle(), DeliveryTelegram()
        er.deliver(google, telegram, "self@example.org", "TG", ("reminder:MU:2026-12-17", "Bilanço", "body"))
        er.deliver(google, telegram, "self@example.org", "TG", ("reminder:MU:2026-12-18", "Bilanço", "body"))
        self.assertEqual((google.sent, telegram.sent), (2, 2))

    def test_empty_calendar_response_is_not_silent_success(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"data": None, "status": {"rCode": 200}}
        with patch.object(er, "request", return_value=response):
            with self.assertRaises(RuntimeError):
                er.calendar_day(dt.date(2026, 10, 15), {"MU"})

    def test_secondary_past_date_is_ignored(self):
        response = unittest.mock.Mock()
        response.text = "<table><tr><td>Earnings Date</td><td>Nov 10, 2026</td></tr></table>"
        with patch.object(er, "request", return_value=response):
            self.assertEqual(er.secondary("IFNNY", dt.date(2026, 10, 13))["date"], "2026-11-10")
            self.assertIsNone(er.secondary("IFNNY", dt.date(2026, 11, 11)))

    def test_long_telegram_body_preserves_all_content(self):
        body = "entry\n" * 2000
        parts = er.chunks(body)
        self.assertEqual("".join(parts), body)
        self.assertTrue(all(len(part) <= 3800 for part in parts))


if __name__ == "__main__":
    unittest.main()
