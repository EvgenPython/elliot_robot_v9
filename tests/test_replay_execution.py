import unittest

from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.replay_execution import (
    ReplayExecutor,
)


class FakeLogger:

    def __init__(self):
        self.events = []

    def event(
        self,
        category,
        event,
        data,
    ):
        self.events.append(
            (
                category,
                event,
                data,
            )
        )


class FakeSimulator:

    def __init__(self):
        self.requests = []
        self.positions = []
        self.orders = []

    def positions_get(
        self,
        symbol=None,
    ):
        return list(
            self.positions
        )

    def orders_get(
        self,
        symbol=None,
    ):
        return list(
            self.orders
        )

    def symbol_info(
        self,
        symbol,
    ):
        return {
            "point": 0.01,
            "trade_tick_size": 0.01,
            "trade_tick_value": 1.0,
            "trade_tick_value_loss": 1.0,
            "volume_min": 0.01,
            "volume_max": 100.0,
            "volume_step": 0.01,
        }

    def symbol_info_tick(
        self,
        symbol,
    ):
        return {
            "bid": 100.00,
            "ask": 100.25,
        }

    def account_info(self):
        return {
            "balance": 100000.0,
            "equity": 100000.0,
        }

    def order_send(
        self,
        request,
    ):
        self.requests.append(
            dict(request)
        )

        return {
            "retcode": 10009,
            "comment": "done",
            "order": 1,
            "deal": 1,
        }


def count():
    return ElliottCount(
        degree="minor",
        direction="bullish",
        current_wave="3",
        legs=[],
        invalidation=None,
        summary="test",
    )


def ready(
    action,
    entry,
    stop,
    target,
):
    return ClaudeDecision(
        action=action,
        primary_count=count(),
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
        confidence="medium",
        rationale_brief="test",
        evidence_interpretation="test",
    )


class ReplayExecutionTests(
    unittest.TestCase
):

    def build(self):
        sim = FakeSimulator()
        log = FakeLogger()

        ex = ReplayExecutor(
            simulator=sim,
            logger=log,
            symbol="XAUUSD",
            settings={
                "execution": {
                    "replay_enabled": True,
                    "risk_fraction": 0.0025,
                    "minimum_rr": 2.0,
                }
            },
        )

        return ex, sim, log

    def test_long_buy_stop_and_risk_sizing(self):
        ex, sim, _ = self.build()

        d = ready(
            "READY_LONG",
            101.00,
            99.00,
            105.00,
        )

        result = ex.execute_candidates(
            [
                {
                    "timeframe": "M15",
                    "decision": d,
                }
            ]
        )[0]

        self.assertEqual(
            result["status"],
            "ORDER_ACCEPTED",
        )

        self.assertEqual(
            result["order_kind"],
            "BUY_STOP",
        )

        # Risk distance = $2.
        # $2 / $0.01 * $1 = $200 risk per 1 lot.
        # $250 / $200 = 1.25 lot.
        self.assertAlmostEqual(
            result["volume"],
            1.25,
            places=8,
        )

        self.assertAlmostEqual(
            result["estimated_risk"],
            250.0,
            places=6,
        )

        self.assertEqual(
            sim.requests[0]["type"],
            4,
        )

        self.assertEqual(
            sim.requests[0]["price"],
            101.00,
        )

    def test_existing_position_prevents_second_trade(self):
        ex, sim, _ = self.build()

        sim.positions = [
            {
                "ticket": 1,
                "symbol": "XAUUSD",
            }
        ]

        d = ready(
            "READY_LONG",
            101.00,
            99.00,
            105.00,
        )

        result = ex.execute_candidates(
            [
                {
                    "timeframe": "M15",
                    "decision": d,
                }
            ]
        )[0]

        self.assertEqual(
            result["status"],
            "EXISTING_EXPOSURE",
        )

        self.assertEqual(
            len(sim.requests),
            0,
        )

    def test_multiple_ready_is_not_arbitrated_by_python(self):
        ex, sim, _ = self.build()

        a = ready(
            "READY_LONG",
            101.00,
            99.00,
            105.00,
        )

        b = ready(
            "READY_SHORT",
            99.00,
            101.00,
            95.00,
        )

        result = ex.execute_candidates(
            [
                {
                    "timeframe": "H1",
                    "decision": a,
                },
                {
                    "timeframe": "M15",
                    "decision": b,
                },
            ]
        )[0]

        self.assertEqual(
            result["status"],
            "AMBIGUOUS_MULTIPLE_READY",
        )

        self.assertEqual(
            len(sim.requests),
            0,
        )

    def test_low_rr_is_executed_because_claude_is_decider(self):
        ex, sim, log = self.build()

        # RR = 1.5, intentionally below the old
        # Python minimum_rr=2.0 threshold.
        d = ready(
            "READY_LONG",
            101.00,
            99.00,
            104.00,
        )

        result = ex.execute_candidates(
            [
                {
                    "timeframe": "M15",
                    "decision": d,
                }
            ]
        )[0]

        self.assertEqual(
            result["status"],
            "ORDER_ACCEPTED",
        )

        self.assertAlmostEqual(
            result["rr"],
            1.5,
            places=8,
        )

        self.assertEqual(
            len(sim.requests),
            1,
        )

        rr_events = [
            data
            for category, event, data
            in log.events
            if event == "CLAUDE_RR_OBSERVED"
        ]

        self.assertEqual(
            len(rr_events),
            1,
        )

        self.assertAlmostEqual(
            rr_events[0]["rr"],
            1.5,
            places=8,
        )


if __name__ == "__main__":
    unittest.main()
