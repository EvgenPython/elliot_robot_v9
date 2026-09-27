from __future__ import annotations

import math


TRADE_ACTION_DEAL = 1
TRADE_ACTION_PENDING = 5
TRADE_ACTION_REMOVE = 8

ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TYPE_BUY_LIMIT = 2
ORDER_TYPE_SELL_LIMIT = 3
ORDER_TYPE_BUY_STOP = 4
ORDER_TYPE_SELL_STOP = 5

TRADE_RETCODE_DONE = 10009


class ReplayExecutionAmbiguous(RuntimeError):
    """
    Broker state could not be reconciled after an ambiguous
    execution transport failure.

    Fail closed: never blindly repeat order_send().
    """
    pass


class ReplayExecutor:
    """Execution layer used only with SimulatorClient.

    It never connects to real MT5.

    Claude chooses WAIT / READY_LONG / READY_SHORT and supplies
    entry / stop / target.

    Python only:
    - validates executable geometry;
    - applies configured risk sizing;
    - maps the Claude entry to market/limit/stop order mechanics;
    - prevents duplicate exposure.
    """

    def __init__(
        self,
        simulator,
        logger,
        symbol: str,
        settings: dict,
    ):
        self.simulator = simulator
        self.logger = logger
        self.symbol = symbol

        cfg = settings.get("execution", {})

        self.enabled = bool(
            cfg.get("replay_enabled", True)
        )

        self.risk_fraction = float(
            cfg.get("risk_fraction", 0.0025)
        )

        self.minimum_rr = float(
            cfg.get("minimum_rr", 2.0)
        )

        self.magic = int(
            cfg.get("replay_magic", 90502)
        )

    def _log(self, event: str, data: dict):
        self.logger.event(
            "execution",
            event,
            data,
        )

    @staticmethod
    def _volume_down(
        raw_volume: float,
        info: dict,
    ) -> float | None:

        vmin = float(
            info.get("volume_min") or 0.01
        )
        vmax = float(
            info.get("volume_max") or 100.0
        )
        step = float(
            info.get("volume_step") or 0.01
        )

        if (
            raw_volume <= 0
            or step <= 0
            or raw_volume + 1e-12 < vmin
        ):
            return None

        capped = min(
            raw_volume,
            vmax,
        )

        steps = math.floor(
            ((capped - vmin) / step) + 1e-12
        )

        volume = (
            vmin
            + steps * step
        )

        volume = min(
            volume,
            vmax,
        )

        return round(
            volume,
            8,
        )

    @staticmethod
    def _candidate_summary(candidate: dict) -> dict:
        decision = candidate["decision"]

        return {
            "timeframe": candidate["timeframe"],
            "action": decision.action,
            "entry": decision.entry,
            "stop": decision.stop,
            "target": decision.target,
        }

    def _owned_pending_for_timeframe(
        self,
        timeframe: str,
    ) -> list[dict]:
        """
        Return only WaveFrame replay pending orders belonging to this timeframe.

        Ownership is determined by BOTH:
        - replay magic;
        - deterministic WaveFrame comment prefix.

        A decision on one timeframe must never cancel another timeframe's order.
        """
        prefix = f"WF_REPLAY_{timeframe}_"

        result = []

        for order in self.simulator.orders_get(
            self.symbol
        ):
            magic = int(
                order.get("magic") or 0
            )

            comment = str(
                order.get("comment") or ""
            )

            if (
                magic == self.magic
                and comment.startswith(prefix)
            ):
                result.append(order)

        return result

    @staticmethod
    def _same_price(
        left,
        right,
    ) -> bool:
        if left is None or right is None:
            return left is None and right is None

        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=1e-9,
        )

    def _pending_matches_decision(
        self,
        order: dict,
        timeframe: str,
        decision,
    ) -> bool:
        """
        A pending order is considered unchanged only when Claude's
        direction AND full entry/SL/TP geometry are unchanged.
        """
        expected_comment = (
            f"WF_REPLAY_{timeframe}_"
            f"{decision.action}"
        )

        if str(
            order.get("comment") or ""
        ) != expected_comment:
            return False

        order_type = int(
            order.get("type")
        )

        if decision.action == "READY_LONG":
            allowed_types = {
                ORDER_TYPE_BUY_LIMIT,
                ORDER_TYPE_BUY_STOP,
            }
        elif decision.action == "READY_SHORT":
            allowed_types = {
                ORDER_TYPE_SELL_LIMIT,
                ORDER_TYPE_SELL_STOP,
            }
        else:
            return False

        if order_type not in allowed_types:
            return False

        return (
            self._same_price(
                order.get("price"),
                decision.entry,
            )
            and self._same_price(
                order.get("sl"),
                decision.stop,
            )
            and self._same_price(
                order.get("tp"),
                decision.target,
            )
        )

    def _matching_exposure_after_send(
        self,
        timeframe: str,
        decision,
        order_action: int,
        order_type: int,
        entry: float,
        stop: float,
        target: float,
        volume: float,
    ) -> list[dict]:
        """
        Reconcile an ambiguous order_send() by reading broker state.

        Important invariant:
        execute_candidate() checked that no symbol exposure existed
        immediately before order_send(). Therefore a matching exposure
        appearing now is evidence that the ambiguous request reached
        the broker.

        No retry is performed here.
        """
        expected_comment = (
            f"WF_REPLAY_{timeframe}_"
            f"{decision.action}"
        )

        matches = []

        if order_action == TRADE_ACTION_PENDING:
            items = self.simulator.orders_get(
                self.symbol
            )

            for item in items:
                if int(
                    item.get("magic") or 0
                ) != self.magic:
                    continue

                if str(
                    item.get("comment") or ""
                ) != expected_comment:
                    continue

                if int(
                    item.get("type")
                ) != int(order_type):
                    continue

                if not self._same_price(
                    item.get("price"),
                    entry,
                ):
                    continue

                if not self._same_price(
                    item.get("sl"),
                    stop,
                ):
                    continue

                if not self._same_price(
                    item.get("tp"),
                    target,
                ):
                    continue

                if not self._same_price(
                    item.get("volume"),
                    volume,
                ):
                    continue

                matches.append(
                    {
                        "kind": "PENDING",
                        "item": item,
                    }
                )

        else:
            items = self.simulator.positions_get(
                self.symbol
            )

            for item in items:
                if int(
                    item.get("magic") or 0
                ) != self.magic:
                    continue

                if str(
                    item.get("comment") or ""
                ) != expected_comment:
                    continue

                if int(
                    item.get("type")
                ) != int(order_type):
                    continue

                if not self._same_price(
                    item.get("sl"),
                    stop,
                ):
                    continue

                if not self._same_price(
                    item.get("tp"),
                    target,
                ):
                    continue

                if not self._same_price(
                    item.get("volume"),
                    volume,
                ):
                    continue

                # Market fill price can legitimately differ from
                # Claude's nominal entry due to bid/ask/slippage,
                # therefore price_open is deliberately not matched.
                matches.append(
                    {
                        "kind": "POSITION",
                        "item": item,
                    }
                )

        return matches

    def _reconcile_order_send_exception(
        self,
        *,
        timeframe: str,
        decision,
        direction: str,
        order_action: int,
        order_type: int,
        order_kind: str,
        entry: float,
        executable_entry: float,
        stop: float,
        target: float,
        volume: float,
        request: dict,
        error: Exception,
    ) -> dict:
        """
        An order_send transport exception does NOT mean the broker
        rejected the order.

        Re-read broker state exactly once.

        1 matching exposure:
            broker accepted it -> recover success.

        0 matching exposures:
            not confirmed -> do NOT retry automatically.

        >1 matching exposures:
            invariant violation -> hard stop.

        Broker state itself unavailable:
            true ambiguity -> hard stop.
        """
        try:
            matches = (
                self._matching_exposure_after_send(
                    timeframe=timeframe,
                    decision=decision,
                    order_action=order_action,
                    order_type=order_type,
                    entry=entry,
                    stop=stop,
                    target=target,
                    volume=volume,
                )
            )

        except Exception as reconcile_error:
            data = {
                "status":
                    "ORDER_SEND_STATE_UNKNOWN",
                "timeframe": timeframe,
                "action": decision.action,
                "request": request,
                "send_error":
                    repr(error),
                "reconcile_error":
                    repr(reconcile_error),
                "retry": False,
            }

            self._log(
                "ORDER_SEND_STATE_UNKNOWN",
                data,
            )

            raise ReplayExecutionAmbiguous(
                "order_send failed and broker state "
                "could not be reconciled; automatic retry "
                "is forbidden"
            ) from reconcile_error

        if len(matches) == 1:
            recovered = matches[0]

            data = {
                "status":
                    "ORDER_ACCEPTED_RECONCILED",
                "timeframe": timeframe,
                "action": decision.action,
                "direction": direction,
                "order_kind": order_kind,
                "claude_entry": entry,
                "executable_entry":
                    executable_entry,
                "stop": stop,
                "target": target,
                "volume": volume,
                "request": request,
                "send_error": repr(error),
                "retry": False,
                "recovered_kind":
                    recovered["kind"],
                "recovered":
                    recovered["item"],
            }

            self._log(
                "ORDER_SEND_RECONCILED",
                data,
            )

            return data

        if len(matches) == 0:
            data = {
                "status":
                    "ORDER_SEND_NOT_CONFIRMED",
                "timeframe": timeframe,
                "action": decision.action,
                "direction": direction,
                "order_kind": order_kind,
                "claude_entry": entry,
                "stop": stop,
                "target": target,
                "volume": volume,
                "request": request,
                "send_error": repr(error),
                "retry": False,
            }

            self._log(
                "ORDER_SEND_NOT_CONFIRMED",
                data,
            )

            return data

        data = {
            "status":
                "ORDER_SEND_MULTIPLE_MATCHES",
            "timeframe": timeframe,
            "action": decision.action,
            "request": request,
            "send_error": repr(error),
            "matches": matches,
            "retry": False,
        }

        self._log(
            "ORDER_SEND_MULTIPLE_MATCHES",
            data,
        )

        raise ReplayExecutionAmbiguous(
            "multiple matching broker exposures found "
            "after ambiguous order_send"
        )

    def _cancel_pending(
        self,
        order: dict,
        timeframe: str,
        reason: str,
    ) -> dict:
        """
        Cancel one simulator pending order.

        Simulator currently validates volume before dispatching the
        REMOVE action, therefore the existing order volume is included
        in the replay request.
        """
        request = {
            "action": TRADE_ACTION_REMOVE,
            "symbol": self.symbol,
            "order": int(order["ticket"]),
            "volume": float(
                order.get("volume") or 0.01
            ),
        }

        try:
            result = self.simulator.order_send(
                request
            )

        except Exception as error:
            # REMOVE may already have succeeded even though
            # its HTTP response was lost.
            try:
                remaining = (
                    self.simulator.orders_get(
                        self.symbol
                    )
                )

            except Exception as reconcile_error:
                data = {
                    "status":
                        "PENDING_CANCEL_STATE_UNKNOWN",
                    "timeframe": timeframe,
                    "reason": reason,
                    "ticket": int(order["ticket"]),
                    "old_order": order,
                    "request": request,
                    "send_error": repr(error),
                    "reconcile_error":
                        repr(reconcile_error),
                    "retry": False,
                }

                self._log(
                    "PENDING_CANCEL_STATE_UNKNOWN",
                    data,
                )

                raise ReplayExecutionAmbiguous(
                    "pending cancel response lost and "
                    "broker state cannot be checked"
                ) from reconcile_error

            still_exists = any(
                int(x.get("ticket") or 0)
                == int(order["ticket"])
                for x in remaining
            )

            if not still_exists:
                data = {
                    "status":
                        "PENDING_CANCELLED",
                    "timeframe": timeframe,
                    "reason": reason,
                    "ticket": int(order["ticket"]),
                    "old_order": order,
                    "request": request,
                    "result": None,
                    "send_error": repr(error),
                    "reconciled": True,
                    "retry": False,
                }

                self._log(
                    "PENDING_CANCELLED_RECONCILED",
                    data,
                )

                return data

            data = {
                "status":
                    "PENDING_CANCEL_NOT_CONFIRMED",
                "timeframe": timeframe,
                "reason": reason,
                "ticket": int(order["ticket"]),
                "old_order": order,
                "request": request,
                "result": None,
                "send_error": repr(error),
                "reconciled": True,
                "retry": False,
            }

            self._log(
                "PENDING_CANCEL_NOT_CONFIRMED",
                data,
            )

            return data

        success = (
            int(result.get("retcode") or 0)
            == TRADE_RETCODE_DONE
        )

        data = {
            "status": (
                "PENDING_CANCELLED"
                if success
                else "PENDING_CANCEL_FAILED"
            ),
            "timeframe": timeframe,
            "reason": reason,
            "ticket": int(order["ticket"]),
            "old_order": order,
            "request": request,
            "result": result,
        }

        self._log(
            (
                "PENDING_CANCELLED"
                if success
                else "PENDING_CANCEL_FAILED"
            ),
            data,
        )

        return data

    def _cancel_timeframe_pending(
        self,
        timeframe: str,
        reason: str,
    ) -> list[dict]:
        results = []

        for order in self._owned_pending_for_timeframe(
            timeframe
        ):
            results.append(
                self._cancel_pending(
                    order,
                    timeframe,
                    reason,
                )
            )

        return results

    def execute_decisions(
        self,
        decisions: list[dict],
    ) -> list[dict]:
        """
        Reconcile ALL fresh Claude decisions with existing replay orders.

        WAIT:
            cancel pending order(s) for that SAME timeframe only.

        READY_* with identical existing pending:
            leave it unchanged.

        READY_* with changed geometry:
            cancel old same-timeframe pending, then submit replacement.

        Decisions from another timeframe:
            never cancel this timeframe's pending.

        Multiple simultaneous READY decisions:
            Python does not choose between them. Any old pending orders
            owned by those READY timeframes are cancelled so an old setup
            cannot become an implicit winner.
        """
        if not self.enabled:
            if decisions:
                self._log(
                    "REPLAY_EXECUTION_DISABLED",
                    {
                        "decisions": [
                            self._candidate_summary(x)
                            for x in decisions
                        ]
                    },
                )

            return []

        if not decisions:
            return []

        results = []
        ready = []

        # WAIT is an explicit fresh Claude decision.
        # It invalidates an unfilled pending setup from the same timeframe,
        # but does NOT close an already-open position.
        for candidate in decisions:
            timeframe = str(
                candidate["timeframe"]
            )

            decision = candidate["decision"]

            if decision.action == "WAIT":
                results.extend(
                    self._cancel_timeframe_pending(
                        timeframe,
                        reason="CLAUDE_WAIT",
                    )
                )

            elif decision.action in {
                "READY_LONG",
                "READY_SHORT",
            }:
                ready.append(candidate)

        if not ready:
            return results

        # Never let Python pick one of two independent Claude READY decisions.
        if len(ready) > 1:
            # Also remove any old pending setups belonging to these
            # timeframes. Keeping one would implicitly select that setup.
            for candidate in ready:
                timeframe = str(
                    candidate["timeframe"]
                )

                results.extend(
                    self._cancel_timeframe_pending(
                        timeframe,
                        reason=(
                            "AMBIGUOUS_MULTIPLE_READY"
                        ),
                    )
                )

            data = {
                "status":
                    "AMBIGUOUS_MULTIPLE_READY",
                "candidates": [
                    self._candidate_summary(x)
                    for x in ready
                ],
            }

            self._log(
                "MULTIPLE_READY_NO_ARBITRATION",
                data,
            )

            results.append(data)

            return results

        candidate = ready[0]

        timeframe = str(
            candidate["timeframe"]
        )

        decision = candidate["decision"]

        existing = (
            self._owned_pending_for_timeframe(
                timeframe
            )
        )

        # Exactly the same Claude setup is already waiting.
        # Do not cancel/recreate it and do not duplicate it.
        if (
            len(existing) == 1
            and self._pending_matches_decision(
                existing[0],
                timeframe,
                decision,
            )
        ):
            data = {
                "status": "PENDING_UNCHANGED",
                "timeframe": timeframe,
                "action": decision.action,
                "ticket": int(
                    existing[0]["ticket"]
                ),
                "order": existing[0],
            }

            self._log(
                "PENDING_UNCHANGED",
                data,
            )

            results.append(data)

            return results

        # Same timeframe has an old/different setup.
        # Remove it before attempting replacement.
        if existing:
            cancelled = []

            for order in existing:
                item = self._cancel_pending(
                    order,
                    timeframe,
                    reason="CLAUDE_READY_REPLACED",
                )

                cancelled.append(item)
                results.append(item)

            if any(
                x["status"]
                != "PENDING_CANCELLED"
                for x in cancelled
            ):
                data = {
                    "status":
                        "PENDING_REPLACE_ABORTED",
                    "timeframe": timeframe,
                    "action": decision.action,
                    "reason":
                        "OLD_PENDING_CANCEL_FAILED",
                }

                self._log(
                    "PENDING_REPLACE_ABORTED",
                    data,
                )

                results.append(data)

                return results

        # execute_candidate still enforces:
        # - no pyramiding;
        # - no other existing symbol exposure;
        # - geometry;
        # - RR;
        # - risk sizing.
        results.append(
            self.execute_candidate(
                candidate
            )
        )

        return results

    def execute_candidates(
        self,
        candidates: list[dict],
    ) -> list[dict]:

        if not self.enabled:
            if candidates:
                self._log(
                    "REPLAY_EXECUTION_DISABLED",
                    {
                        "candidates": [
                            self._candidate_summary(x)
                            for x in candidates
                        ]
                    },
                )

            return []

        if not candidates:
            return []

        # Never let Python rank two independent Claude READY decisions.
        # If more than one timeframe produces READY on the same checkpoint,
        # execution is deliberately withheld and the ambiguity is logged.
        if len(candidates) > 1:
            data = {
                "status": "AMBIGUOUS_MULTIPLE_READY",
                "candidates": [
                    self._candidate_summary(x)
                    for x in candidates
                ],
            }

            self._log(
                "MULTIPLE_READY_NO_ARBITRATION",
                data,
            )

            return [data]

        return [
            self.execute_candidate(
                candidates[0]
            )
        ]

    def execute_candidate(
        self,
        candidate: dict,
    ) -> dict:

        timeframe = str(
            candidate["timeframe"]
        )

        decision = candidate["decision"]

        if decision.action not in {
            "READY_LONG",
            "READY_SHORT",
        }:
            return {
                "status": "NO_TRADE_INTENT",
                "timeframe": timeframe,
                "action": decision.action,
            }

        # One symbol = one exposure.
        # No pyramiding and no duplicate pending orders during replay.
        positions = self.simulator.positions_get(
            self.symbol
        )

        orders = self.simulator.orders_get(
            self.symbol
        )

        if positions or orders:
            data = {
                "status": "EXISTING_EXPOSURE",
                "timeframe": timeframe,
                "action": decision.action,
                "positions": len(positions),
                "orders": len(orders),
            }

            self._log(
                "TRADE_NOT_OPENED_EXISTING_EXPOSURE",
                data,
            )

            return data

        entry = float(decision.entry)
        stop = float(decision.stop)
        target = float(decision.target)

        direction = (
            "LONG"
            if decision.action == "READY_LONG"
            else "SHORT"
        )

        # First validate Claude's own requested geometry.
        if direction == "LONG":
            geometry_ok = (
                stop < entry < target
            )
        else:
            geometry_ok = (
                target < entry < stop
            )

        if not geometry_ok:
            data = {
                "status": "INVALID_GEOMETRY",
                "timeframe": timeframe,
                "action": decision.action,
                "entry": entry,
                "stop": stop,
                "target": target,
            }

            self._log(
                "TRADE_NOT_OPENED_INVALID_GEOMETRY",
                data,
            )

            return data

        info = self.simulator.symbol_info(
            self.symbol
        )

        tick = self.simulator.symbol_info_tick(
            self.symbol
        )

        account = self.simulator.account_info()

        if not tick:
            data = {
                "status": "NO_SIMULATED_TICK",
                "timeframe": timeframe,
            }

            self._log(
                "TRADE_NOT_OPENED_NO_TICK",
                data,
            )

            return data

        point = float(
            info.get("point") or 0.01
        )

        tick_size = float(
            info.get("trade_tick_size")
            or point
        )

        tick_value = float(
            info.get("trade_tick_value")
            or info.get("trade_tick_value_loss")
            or 0.0
        )

        if (
            tick_size <= 0
            or tick_value <= 0
        ):
            data = {
                "status": "INVALID_SYMBOL_RISK_METADATA",
                "timeframe": timeframe,
                "tick_size": tick_size,
                "tick_value": tick_value,
            }

            self._log(
                "TRADE_NOT_OPENED_INVALID_SYMBOL_METADATA",
                data,
            )

            return data

        bid = float(tick["bid"])
        ask = float(tick["ask"])

        tolerance = max(
            point,
            tick_size,
        )

        if direction == "LONG":
            market_price = ask

            if abs(entry - ask) <= tolerance:
                order_action = TRADE_ACTION_DEAL
                order_type = ORDER_TYPE_BUY
                executable_entry = ask
                order_kind = "BUY_MARKET"

            elif entry > ask:
                order_action = TRADE_ACTION_PENDING
                order_type = ORDER_TYPE_BUY_STOP
                executable_entry = entry
                order_kind = "BUY_STOP"

            else:
                order_action = TRADE_ACTION_PENDING
                order_type = ORDER_TYPE_BUY_LIMIT
                executable_entry = entry
                order_kind = "BUY_LIMIT"

        else:
            market_price = bid

            if abs(entry - bid) <= tolerance:
                order_action = TRADE_ACTION_DEAL
                order_type = ORDER_TYPE_SELL
                executable_entry = bid
                order_kind = "SELL_MARKET"

            elif entry < bid:
                order_action = TRADE_ACTION_PENDING
                order_type = ORDER_TYPE_SELL_STOP
                executable_entry = entry
                order_kind = "SELL_STOP"

            else:
                order_action = TRADE_ACTION_PENDING
                order_type = ORDER_TYPE_SELL_LIMIT
                executable_entry = entry
                order_kind = "SELL_LIMIT"

        # For a market fill use the actual simulated bid/ask when
        # calculating risk and RR, not Claude's nominal entry.
        if direction == "LONG":
            execution_geometry_ok = (
                stop
                < executable_entry
                < target
            )
        else:
            execution_geometry_ok = (
                target
                < executable_entry
                < stop
            )

        if not execution_geometry_ok:
            data = {
                "status": "MARKET_MOVED_OUTSIDE_GEOMETRY",
                "timeframe": timeframe,
                "action": decision.action,
                "claude_entry": entry,
                "executable_entry": executable_entry,
                "market_price": market_price,
                "stop": stop,
                "target": target,
            }

            self._log(
                "TRADE_NOT_OPENED_MARKET_MOVED",
                data,
            )

            return data

        risk_distance = abs(
            executable_entry - stop
        )

        reward_distance = abs(
            target - executable_entry
        )

        if risk_distance <= 0:
            data = {
                "status": "ZERO_RISK_DISTANCE",
                "timeframe": timeframe,
            }

            self._log(
                "TRADE_NOT_OPENED_ZERO_RISK",
                data,
            )

            return data

        rr = (
            reward_distance
            / risk_distance
        )

        if rr + 1e-12 < self.minimum_rr:
            data = {
                "status": "RR_BELOW_CONFIGURED_MINIMUM",
                "timeframe": timeframe,
                "action": decision.action,
                "rr": rr,
                "minimum_rr": self.minimum_rr,
                "entry": executable_entry,
                "stop": stop,
                "target": target,
            }

            self._log(
                "TRADE_NOT_OPENED_RR",
                data,
            )

            return data

        balance = float(
            account.get("balance") or 0.0
        )

        risk_budget = (
            balance
            * self.risk_fraction
        )

        risk_per_lot = (
            risk_distance
            / tick_size
            * tick_value
        )

        if risk_per_lot <= 0:
            data = {
                "status": "INVALID_RISK_PER_LOT",
                "timeframe": timeframe,
            }

            self._log(
                "TRADE_NOT_OPENED_INVALID_RISK_PER_LOT",
                data,
            )

            return data

        raw_volume = (
            risk_budget
            / risk_per_lot
        )

        volume = self._volume_down(
            raw_volume,
            info,
        )

        if volume is None:
            data = {
                "status": "VOLUME_BELOW_MINIMUM",
                "timeframe": timeframe,
                "raw_volume": raw_volume,
                "risk_budget": risk_budget,
                "risk_per_lot": risk_per_lot,
                "volume_min": info.get("volume_min"),
            }

            self._log(
                "TRADE_NOT_OPENED_VOLUME_TOO_SMALL",
                data,
            )

            return data

        estimated_risk = (
            risk_per_lot
            * volume
        )

        request = {
            "action": order_action,
            "symbol": self.symbol,
            "type": order_type,
            "volume": volume,
            "sl": stop,
            "tp": target,
            "magic": self.magic,
            "comment": (
                f"WF_REPLAY_{timeframe}_"
                f"{decision.action}"
            ),
        }

        if order_action == TRADE_ACTION_PENDING:
            request["price"] = entry

        try:
            result = self.simulator.order_send(
                request
            )

        except Exception as error:
            return (
                self._reconcile_order_send_exception(
                    timeframe=timeframe,
                    decision=decision,
                    direction=direction,
                    order_action=order_action,
                    order_type=order_type,
                    order_kind=order_kind,
                    entry=entry,
                    executable_entry=
                        executable_entry,
                    stop=stop,
                    target=target,
                    volume=volume,
                    request=request,
                    error=error,
                )
            )

        success = (
            int(result.get("retcode") or 0)
            == TRADE_RETCODE_DONE
        )

        data = {
            "status": (
                "ORDER_ACCEPTED"
                if success
                else "ORDER_REJECTED"
            ),
            "timeframe": timeframe,
            "action": decision.action,
            "direction": direction,
            "order_kind": order_kind,
            "claude_entry": entry,
            "executable_entry": executable_entry,
            "stop": stop,
            "target": target,
            "rr": rr,
            "balance_before": balance,
            "risk_fraction": self.risk_fraction,
            "risk_budget": risk_budget,
            "risk_per_lot": risk_per_lot,
            "raw_volume": raw_volume,
            "volume": volume,
            "estimated_risk": estimated_risk,
            "request": request,
            "result": result,
        }

        self._log(
            (
                "REPLAY_ORDER_ACCEPTED"
                if success
                else "REPLAY_ORDER_REJECTED"
            ),
            data,
        )

        return data
