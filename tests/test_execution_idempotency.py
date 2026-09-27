import unittest

from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.replay_execution import (
    ReplayExecutor,
    TRADE_ACTION_DEAL,
    TRADE_ACTION_PENDING,
    TRADE_ACTION_REMOVE,
    ORDER_TYPE_BUY,
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


class AmbiguousSimulator:
    def __init__(self, mode):
        self.mode = mode
        self.orders = []
        self.positions = []
        self.send_calls = 0
        self.next_ticket = 1

    def account_info(self):
        return {"balance": 100000.0}

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

    def positions_get(self, symbol=None):
        return [
            dict(x)
            for x in self.positions
            if symbol is None
            or x["symbol"] == symbol
        ]

    def orders_get(self, symbol=None):
        return [
            dict(x)
            for x in self.orders
            if symbol is None
            or x["symbol"] == symbol
        ]

    def order_send(self, request):
        self.send_calls += 1
        request = dict(request)

        action = request["action"]

        if action == TRADE_ACTION_PENDING:
            if self.mode == "raise_before_accept":
                raise TimeoutError(
                    "request never reached broker"
                )

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

            if self.mode == "pending_accept_then_timeout":
                raise TimeoutError(
                    "response lost after broker accepted"
                )

            return {
                "retcode": 10009,
                "comment": "done",
                "order": ticket,
            }

        if action == TRADE_ACTION_DEAL:
            if self.mode == "raise_before_accept":
                raise TimeoutError(
                    "request never reached broker"
                )

            ticket = self.next_ticket
            self.next_ticket += 1

            self.positions.append(
                {
                    "ticket": ticket,
                    "symbol": request["symbol"],
                    "type": request["type"],
                    "volume": request["volume"],
                    "price_open": 100.25,
                    "sl": request["sl"],
                    "tp": request["tp"],
                    "magic": request["magic"],
                    "comment": request["comment"],
                    "profit": 0.0,
                }
            )

            if self.mode == "market_accept_then_timeout":
                raise TimeoutError(
                    "response lost after broker accepted"
                )

            return {
                "retcode": 10009,
                "comment": "done",
                "order": ticket,
            }

        if action == TRADE_ACTION_REMOVE:
            ticket = int(request["order"])

            self.orders = [
                x for x in self.orders
                if int(x["ticket"]) != ticket
            ]

            if self.mode == "remove_accept_then_timeout":
                raise TimeoutError(
                    "remove response lost"
                )

            return {
                "retcode": 10009,
                "comment": "done",
                "order": ticket,
            }

        raise RuntimeError("unexpected action")


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


def make_executor(sim):
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


class ExecutionIdempotencyTests(
    unittest.TestCase
):

    def test_pending_accept_then_response_lost_is_reconciled(self):
        sim = AmbiguousSimulator(
            "pending_accept_then_timeout"
        )

        ex = make_executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision": make_decision(
                        "READY_LONG",
                        101.0,
                        99.0,
                        105.0,
                    ),
                }
            ]
        )

        self.assertEqual(
            "ORDER_ACCEPTED_RECONCILED",
            result[-1]["status"],
        )

        self.assertEqual(1, sim.send_calls)
        self.assertEqual(1, len(sim.orders))
        self.assertEqual(0, len(sim.positions))

    def test_market_accept_then_response_lost_is_reconciled(self):
        sim = AmbiguousSimulator(
            "market_accept_then_timeout"
        )

        ex = make_executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision": make_decision(
                        "READY_LONG",
                        100.25,
                        98.25,
                        104.25,
                    ),
                }
            ]
        )

        self.assertEqual(
            "ORDER_ACCEPTED_RECONCILED",
            result[-1]["status"],
        )

        self.assertEqual(1, sim.send_calls)
        self.assertEqual(0, len(sim.orders))
        self.assertEqual(1, len(sim.positions))

    def test_request_not_accepted_is_never_blindly_retried(self):
        sim = AmbiguousSimulator(
            "raise_before_accept"
        )

        ex = make_executor(sim)

        result = ex.execute_decisions(
            [
                {
                    "timeframe": "M15",
                    "decision": make_decision(
                        "READY_LONG",
                        101.0,
                        99.0,
                        105.0,
                    ),
                }
            ]
        )

        self.assertEqual(
            "ORDER_SEND_NOT_CONFIRMED",
            result[-1]["status"],
        )

        # Critical invariant:
        # one send attempt only.
        self.assertEqual(1, sim.send_calls)
        self.assertEqual([], sim.orders)
        self.assertEqual([], sim.positions)

    def test_cancel_response_lost_is_reconciled(self):
        sim = AmbiguousSimulator(
            "remove_accept_then_timeout"
        )

        sim.orders.append(
            {
                "ticket": 77,
                "symbol": "XAUUSD",
                "type": ORDER_TYPE_BUY_STOP,
                "volume": 1.25,
                "price": 101.0,
                "sl": 99.0,
                "tp": 105.0,
                "magic": MAGIC,
                "comment":
                    "WF_REPLAY_M15_READY_LONG",
            }
        )

        ex = make_executor(sim)

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
            "PENDING_CANCELLED",
            result[-1]["status"],
        )

        self.assertTrue(
            result[-1].get("reconciled")
        )

        self.assertEqual([], sim.orders)
        self.assertEqual(1, sim.send_calls)


if __name__ == "__main__":
    unittest.main()
