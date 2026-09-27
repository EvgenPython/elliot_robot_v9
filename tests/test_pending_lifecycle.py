import unittest

from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.replay_execution import (
    ReplayExecutor,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    TRADE_RETCODE_DONE,
    ORDER_TYPE_BUY_STOP,
)


MAGIC = 90502


class FakeLogger:
    def __init__(self):
        self.events = []

    def event(self, category, event, data):
        self.events.append(
            (category, event, data)
        )


class FakeSimulator:
    def __init__(self, orders=None):
        self.orders = list(orders or [])
        self.positions = []
        self.requests = []
        self.next_ticket = 100

    def orders_get(self, symbol=None):
        return [
            dict(x)
            for x in self.orders
            if symbol is None
            or x["symbol"] == symbol
        ]

    def positions_get(self, symbol=None):
        return [
            dict(x)
            for x in self.positions
            if symbol is None
            or x["symbol"] == symbol
        ]

    def account_info(self):
        return {
            "balance": 100000.0,
        }

    def symbol_info(self, symbol):
        return {
            "point": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
        }

    def symbol_info_tick(self, symbol):
        return {
            "bid": 100.00,
            "ask": 100.25,
        }

    def order_send(self, request):
        request = dict(request)
        self.requests.append(request)

        if request["action"] == TRADE_ACTION_REMOVE:
            ticket = int(request["order"])

            before = len(self.orders)

            self.orders = [
                x for x in self.orders
                if int(x["ticket"]) != ticket
            ]

            if len(self.orders) == before:
                return {
                    "retcode": 10013,
                    "comment": "order not found",
                }

            return {
                "retcode": TRADE_RETCODE_DONE,
                "comment": "done",
                "order": ticket,
            }

        if request["action"] == TRADE_ACTION_PENDING:
            ticket = self.next_ticket
            self.next_ticket += 1

            self.orders.append(
                {
                    "ticket": ticket,
                    "symbol": request["symbol"],
                    "type": request["type"],
                    "volume": request["volume"],
                    "price": request["price"],
                    "sl": request["sl"],
                    "tp": request["tp"],
                    "magic": request["magic"],
                    "comment": request["comment"],
                }
            )

            return {
                "retcode": TRADE_RETCODE_DONE,
                "comment": "done",
                "order": ticket,
                "price": request["price"],
                "volume": request["volume"],
            }

        return {
            "retcode": TRADE_RETCODE_DONE,
            "comment": "done",
            "order": self.next_ticket,
        }


def make_decision(
    action,
    entry=None,
    stop=None,
    target=None,
):
    direction = (
        "bullish"
        if action == "READY_LONG"
        else (
            "bearish"
            if action == "READY_SHORT"
            else "neutral"
        )
    )

    count = ElliottCount(
        degree="test",
        direction=direction,
        current_wave="test",
        legs=[],
        invalidation=None,
        summary="test",
    )

    return ClaudeDecision(
        action=action,
        primary_count=count,
        alternate_count=None,
        structure_assessment="test",
        support_resistance_assessment="test",
        channel_assessment="test",
        pattern_assessment=[],
        evidence_disagreements=[],
        watch_conditions=[],
        entry=entry,
        stop=stop,
        target=target,
        confidence="low",
        rationale_brief="test",
        evidence_interpretation="test",
    )


def pending(
    ticket,
    timeframe,
    entry=101.0,
    stop=99.0,
    target=105.0,
):
    return {
        "ticket": ticket,
        "symbol": "XAUUSD",
        "type": ORDER_TYPE_BUY_STOP,
        "volume": 1.25,
        "price": entry,
        "sl": stop,
        "tp": target,
        "magic": MAGIC,
        "comment":
            f"WF_REPLAY_{timeframe}_READY_LONG",
    }


def executor(sim):
    return ReplayExecutor(
        simulator=sim,
        logger=FakeLogger(),
        symbol="XAUUSD",
        settings={
            "execution": {
                "replay_enabled": True,
                "risk_fraction": 0.0025,
                "minimum_rr": 2.0,
                "replay_magic": MAGIC,
            }
        },
    )


class PendingLifecycleTests(unittest.TestCase):

    def test_wait_cancels_same_timeframe_only(self):
        sim = FakeSimulator(
            [
                pending(1, "M15"),
                pending(2, "H1"),
            ]
        )

        ex = executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision":
                        make_decision("WAIT"),
                }
            ]
        )

        self.assertEqual(
            ["PENDING_CANCELLED"],
            [x["status"] for x in result],
        )

        self.assertEqual(
            [2],
            [x["ticket"] for x in sim.orders],
        )

        self.assertEqual(
            [TRADE_ACTION_REMOVE],
            [x["action"] for x in sim.requests],
        )

    def test_same_ready_does_not_duplicate_pending(self):
        sim = FakeSimulator(
            [
                pending(
                    1,
                    "M15",
                    entry=101.0,
                    stop=99.0,
                    target=105.0,
                )
            ]
        )

        ex = executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision":
                        make_decision(
                            "READY_LONG",
                            101.0,
                            99.0,
                            105.0,
                        ),
                }
            ]
        )

        self.assertEqual(
            "PENDING_UNCHANGED",
            result[-1]["status"],
        )

        self.assertEqual(1, len(sim.orders))
        self.assertEqual([], sim.requests)

    def test_changed_ready_cancels_and_replaces(self):
        sim = FakeSimulator(
            [
                pending(
                    1,
                    "M15",
                    entry=101.0,
                    stop=99.0,
                    target=105.0,
                )
            ]
        )

        ex = executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision":
                        make_decision(
                            "READY_LONG",
                            102.0,
                            100.0,
                            106.0,
                        ),
                }
            ]
        )

        statuses = [
            x["status"]
            for x in result
        ]

        self.assertEqual(
            [
                "PENDING_CANCELLED",
                "ORDER_ACCEPTED",
            ],
            statuses,
        )

        self.assertEqual(
            [
                TRADE_ACTION_REMOVE,
                TRADE_ACTION_PENDING,
            ],
            [x["action"] for x in sim.requests],
        )

        self.assertEqual(1, len(sim.orders))
        self.assertEqual(
            102.0,
            sim.orders[0]["price"],
        )

    def test_other_timeframe_wait_does_not_touch_pending(self):
        sim = FakeSimulator(
            [
                pending(7, "H1")
            ]
        )

        ex = executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision":
                        make_decision("WAIT"),
                }
            ]
        )

        self.assertEqual([], result)
        self.assertEqual(1, len(sim.orders))
        self.assertEqual(7, sim.orders[0]["ticket"])
        self.assertEqual([], sim.requests)


if __name__ == "__main__":
    unittest.main()
