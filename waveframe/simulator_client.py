from __future__ import annotations

import time

import httpx
import pandas as pd


class SimulatorClient:
    """Causal HTTP bridge to WaveFrame Market Simulator."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        timeout: float = 3600.0,
    ):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout)

    def close(self):
        self.http.close()

    def health(self) -> dict:
        r = self.http.get(self.base + "/health")
        r.raise_for_status()
        return r.json()

    def create_replay(
        self,
        start: str,
        end: str,
        run_id: str | None = None,
    ) -> dict:
        r = self.http.post(
            self.base + "/control/create",
            json={
                "start": start,
                "end": end,
                "run_id": run_id,
            },
        )
        r.raise_for_status()
        return r.json()

    def restore_replay(
        self,
        run_id: str,
    ) -> dict:
        r = self.http.post(
            self.base + "/control/restore",
            json={"run_id": run_id},
        )
        r.raise_for_status()
        return r.json()

    def start(self) -> dict:
        r = self.http.post(self.base + "/control/start")
        r.raise_for_status()
        return r.json()

    def status(self) -> dict:
        r = self.http.get(self.base + "/control/status")
        r.raise_for_status()
        return r.json()

    def report(self) -> dict:
        r = self.http.get(self.base + "/report")
        r.raise_for_status()
        return r.json()

    def poll_event(self, after_seq: int = 0) -> dict | None:
        r = self.http.get(
            self.base + "/events/poll",
            params={"after_seq": after_seq},
        )
        r.raise_for_status()
        return r.json().get("event")

    def wait_event(
        self,
        after_seq: int = 0,
        poll_seconds: float = 0.02,
    ) -> dict | None:
        while True:
            event = self.poll_event(after_seq)

            if event is not None:
                return event

            status = self.status()

            if status.get("status") in {
                "FINISHED",
                "STOPPED",
                "ERROR",
            }:
                return None

            time.sleep(poll_seconds)

    def analysis_started(
        self,
        seq: int,
        cycle_id: str | None = None,
    ) -> dict:
        r = self.http.post(
            self.base + "/robot/analysis_started",
            json={
                "seq": seq,
                "cycle_id": cycle_id,
            },
        )
        r.raise_for_status()
        return r.json()

    def ack(
        self,
        seq: int,
        claude_called: bool = False,
        cycle_id: str | None = None,
        decision: str | None = None,
    ) -> dict:
        r = self.http.post(
            self.base + "/robot/ack",
            json={
                "seq": seq,
                "claude_called": bool(claude_called),
                "cycle_id": cycle_id,
                "decision": decision,
            },
        )
        r.raise_for_status()
        return r.json()

    def rates(
        self,
        symbol: str,
        timeframe: str,
        count: int = 300,
        start_pos: int = 0,
    ) -> pd.DataFrame:
        r = self.http.get(
            self.base + "/mt5/copy_rates_from_pos",
            params={
                "symbol": symbol,
                "timeframe": timeframe,
                "start_pos": int(start_pos),
                "count": int(count),
            },
        )
        r.raise_for_status()

        rows = r.json().get("rates") or []

        if not rows:
            return pd.DataFrame(
                columns=[
                    "time",
                    "open_time",
                    "close_time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "tick_volume",
                    "spread",
                    "real_volume",
                ]
            )

        df = pd.DataFrame(rows)
        df["open_time"] = pd.to_datetime(
            df["open_time"],
            utc=True,
        )
        df["close_time"] = pd.to_datetime(
            df["close_time"],
            utc=True,
        )

        return (
            df.sort_values("open_time")
            .reset_index(drop=True)
        )

    def account_info(self) -> dict:
        r = self.http.get(
            self.base + "/mt5/account_info"
        )
        r.raise_for_status()
        return r.json()

    def symbol_info(self, symbol: str) -> dict:
        r = self.http.get(
            self.base + "/mt5/symbol_info",
            params={"symbol": symbol},
        )
        r.raise_for_status()
        return r.json()

    def symbol_info_tick(self, symbol: str) -> dict | None:
        r = self.http.get(
            self.base + "/mt5/symbol_info_tick",
            params={"symbol": symbol},
        )
        r.raise_for_status()
        return r.json()

    def positions_get(
        self,
        symbol: str | None = None,
    ) -> list[dict]:
        r = self.http.get(
            self.base + "/mt5/positions_get",
            params={"symbol": symbol} if symbol else {},
        )
        r.raise_for_status()
        return r.json()

    def orders_get(
        self,
        symbol: str | None = None,
    ) -> list[dict]:
        r = self.http.get(
            self.base + "/mt5/orders_get",
            params={"symbol": symbol} if symbol else {},
        )
        r.raise_for_status()
        return r.json()

    def history_deals_get(self) -> list[dict]:
        r = self.http.get(
            self.base + "/mt5/history_deals_get"
        )
        r.raise_for_status()
        return r.json()

    def order_send(self, request: dict) -> dict:
        r = self.http.post(
            self.base + "/mt5/order_send",
            json={"request": dict(request)},
        )
        r.raise_for_status()
        return r.json()
