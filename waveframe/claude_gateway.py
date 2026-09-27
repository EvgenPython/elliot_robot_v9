from __future__ import annotations

import json
import os
import time
import uuid
from typing import Callable

try:
    from json_repair import repair_json as _repair_json
except ImportError:
    def _repair_json(text, return_objects=False):
        # Development fallback for custom/mock transports.
        return text

from pydantic import ValidationError

from .models import ClaudeDecision
from .logging import AuditLogger
from .cache import CacheManager
from .costs import estimate, SimulatedLiveCostTracker
from .prompts import update_prompt
from .elliott_validator import validate_elliott


class ClaudeCycleError(RuntimeError):
    """One analysis cycle could not produce a trustworthy Claude decision."""


class ClaudeSchemaExhausted(ClaudeCycleError):
    pass


class ClaudeElliottExhausted(ClaudeCycleError):
    pass


class ClaudeGateway:
    """Claude decision gateway.

    Design invariants:
    - Claude remains the only trading decider.
    - Structured Outputs constrain transport JSON to ClaudeDecision schema.
    - Python validates schema and Elliott hard rules but never invents a trade.
    - No daily call cap, budget cap, slot quota or cost authorization gate exists.
    - Technical retry loops are finite so one broken cycle cannot spend forever.
    """

    def __init__(
        self,
        root=".",
        model="claude-sonnet-5",
        max_tokens=2200,
        cache_ttl="1h",
        effort="medium",
        logger=None,
        transport: Callable | None = None,
        max_transport_failures=3,
        max_schema_repairs=1,
        max_elliott_revisions=2,
    ):
        self.root = root
        self.model = model
        self.max_tokens = max_tokens
        self.cache_ttl = cache_ttl
        self.effort = effort

        self.logger = logger or AuditLogger(root)
        self.cache = CacheManager()
        self.transport = transport

        # True only for the real Anthropic SDK transport.
        # Custom transports used by tests are not billable API calls.
        self.api_called = self.transport is None

        self.simulated_live_cost = SimulatedLiveCostTracker()

        # These are NOT daily/budget gates.
        # They only prevent one technically broken analysis cycle from retrying forever.
        self.max_transport_failures = int(max_transport_failures)
        self.max_schema_repairs = int(max_schema_repairs)
        self.max_elliott_revisions = int(max_elliott_revisions)

    def _sdk_transport(self, stable_prefix: str, dynamic_prompt: str):
        from anthropic import Anthropic, transform_schema

        client = Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY")
        )

        system_block = {
            "type": "text",
            "text": stable_prefix,
            "cache_control": {
                "type": "ephemeral",
                "ttl": self.cache_ttl,
            },
        }

        # Convert our real Pydantic model into Anthropic-compatible JSON Schema.
        # We deliberately use messages.create rather than messages.parse here:
        # raw Message/usage/stop_reason must remain available for cost/audit logging
        # even if local semantic Pydantic validation later rejects the decision.
        schema = transform_schema(
            ClaudeDecision.model_json_schema()
        )

        msg = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[system_block],
            messages=[
                {
                    "role": "user",
                    "content": dynamic_prompt,
                }
            ],
            output_config={
                "effort": self.effort,
                "format": {
                    "type": "json_schema",
                    "schema": schema,
                },
            },
        )

        text = "".join(
            getattr(block, "text", "")
            for block in msg.content
            if getattr(block, "type", None) == "text"
        )

        usage_obj = getattr(msg, "usage", None)

        usage = {
            key: int(getattr(usage_obj, key, 0) or 0)
            for key in [
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ]
        }

        return {
            "text": text,
            "usage": usage,
            "request_id": getattr(msg, "id", None),
            "model": getattr(msg, "model", self.model),
            "stop_reason": getattr(msg, "stop_reason", None),
            "structured_output": True,
        }

    @staticmethod
    def _fatal_api_error(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)

        # Repeating these with the same request cannot fix them.
        if status in {400, 401, 402, 403, 404, 422}:
            return True

        message = str(exc).lower()

        fatal_markers = (
            "invalid x-api-key",
            "authentication_error",
            "permission",
            "credit balance",
            "insufficient credit",
            "billing",
            "invalid api key",
            "model not found",
        )

        return any(marker in message for marker in fatal_markers)

    def _log_paid_response(
        self,
        cycle_id: str,
        attempt: int,
        stable_prefix: str,
        raw: dict,
    ) -> None:
        usage = dict(raw.get("usage") or {})
        model_name = str(raw.get("model") or self.model)

        actual_cost = estimate(
            model_name,
            usage,
            self.cache_ttl,
        )

        sim_cost = self.simulated_live_cost.calculate(
            stable_prefix=stable_prefix,
            sim_time=self.logger._market_now(),
            model=model_name,
            usage=usage,
            cache_ttl=self.cache_ttl,
        )

        response_log = {
            **raw,
            "estimated_cost_usd": actual_cost,
            "actual_api_cost_usd": actual_cost,
            "actual_cache_status": self.cache.cache_status(usage),
            "simulated_live_cost_usd": sim_cost["cost_usd"],
            "simulated_live_cache_status": sim_cost["cache_status"],
            "simulated_live_usage": sim_cost["usage"],
        }

        self.logger.claude_response(
            f"{cycle_id}_{attempt:03d}",
            response_log,
        )

        self.logger.event(
            "cost",
            "CLAUDE_COST",
            {
                "cycle_id": cycle_id,
                "attempt": attempt,
                "model": model_name,
                "usage": usage,
                "estimated_cost_usd": actual_cost,
                "actual_api_cost_usd": actual_cost,
                "actual_cache_status": self.cache.cache_status(usage),
                "simulated_live_cost_usd": sim_cost["cost_usd"],
                "simulated_live_cache_status": sim_cost["cache_status"],
                "simulated_live_usage": sim_cost["usage"],
                "simulated_live_prefix_hash": sim_cost["prefix_hash"],
                "simulated_live_previous_use": sim_cost["previous_sim_use"],
                "sim_time": sim_cost["sim_time"],
            },
        )

    def ask_until_valid(
        self,
        stable_prefix: str,
        delta: dict,
        sleep: Callable[[float], None] = time.sleep,
        on_valid_decision: Callable[
            [ClaudeDecision],
            None,
        ] | None = None,
    ) -> ClaudeDecision:

        cycle_id = str(uuid.uuid4())

        missing = None
        issues = None
        merged = {}

        attempt = 0
        transport_failures = 0
        schema_repairs = 0
        elliott_revisions = 0

        # Absolute cycle bound in addition to category-specific bounds.
        max_total_attempts = (
            1
            + self.max_transport_failures
            + self.max_schema_repairs
            + self.max_elliott_revisions
        )

        while attempt < max_total_attempts:
            attempt += 1

            dynamic = update_prompt(
                delta,
                missing,
                issues,
            )

            request = {
                "cycle_id": cycle_id,
                "attempt": attempt,
                "model": self.model,
                "cache_ttl": self.cache_ttl,
                "stable_prefix": stable_prefix,
                "dynamic_prompt": dynamic,
                "repair_missing_fields": missing,
                "elliott_issues": issues,
                "structured_output": self.transport is None,
            }

            self.logger.claude_request(
                f"{cycle_id}_{attempt:03d}",
                request,
            )

            try:
                raw = (
                    self.transport or self._sdk_transport
                )(
                    stable_prefix,
                    dynamic,
                )

            except Exception as exc:
                name = type(exc).__name__
                message = str(exc)

                self.logger.event(
                    "errors",
                    "CLAUDE_ATTEMPT_FAILED",
                    {
                        "cycle_id": cycle_id,
                        "attempt": attempt,
                        "type": name,
                        "error": message,
                    },
                )

                if self._fatal_api_error(exc):
                    self.logger.event(
                        "errors",
                        "CLAUDE_FATAL_ERROR",
                        {
                            "cycle_id": cycle_id,
                            "attempt": attempt,
                            "type": name,
                            "error": message,
                            "retry": False,
                        },
                    )
                    raise

                transport_failures += 1

                if transport_failures > self.max_transport_failures:
                    self.logger.event(
                        "errors",
                        "CLAUDE_TRANSPORT_RETRIES_EXHAUSTED",
                        {
                            "cycle_id": cycle_id,
                            "attempt": attempt,
                            "transport_failures": transport_failures,
                        },
                    )

                    raise ClaudeCycleError(
                        "Claude transport retry limit exhausted for one analysis cycle"
                    ) from exc

                delay = min(
                    60,
                    2 ** min(transport_failures, 6),
                )

                sleep(delay)
                continue

            # From this point onward the API response exists and may be billable.
            # Always log cost BEFORE attempting local JSON/Pydantic validation.
            self._log_paid_response(
                cycle_id=cycle_id,
                attempt=attempt,
                stable_prefix=stable_prefix,
                raw=raw,
            )

            stop_reason = raw.get("stop_reason")

            if stop_reason == "max_tokens":
                self.logger.event(
                    "errors",
                    "CLAUDE_OUTPUT_TRUNCATED",
                    {
                        "cycle_id": cycle_id,
                        "attempt": attempt,
                        "max_tokens": self.max_tokens,
                        "retry": False,
                    },
                )

                raise ClaudeCycleError(
                    f"Claude structured output hit max_tokens={self.max_tokens}; "
                    "automatic same-size retry disabled"
                )

            if stop_reason == "refusal":
                self.logger.event(
                    "errors",
                    "CLAUDE_REFUSAL",
                    {
                        "cycle_id": cycle_id,
                        "attempt": attempt,
                        "retry": False,
                    },
                )

                raise ClaudeCycleError(
                    "Claude refused the structured-output request"
                )

            text = str(
                raw.get("text", "")
            )

            try:
                if raw.get("structured_output"):
                    # Anthropic has already constrained this to our JSON Schema.
                    obj = json.loads(text)

                    if not isinstance(obj, dict):
                        raise ValueError(
                            "Claude structured JSON root must be object"
                        )

                    candidate = obj

                else:
                    # Backward-compatible path for tests/custom transports.
                    repaired = _repair_json(
                        text,
                        return_objects=False,
                    )

                    obj = json.loads(repaired)

                    if not isinstance(obj, dict):
                        raise ValueError(
                            "Claude JSON root must be object"
                        )

                    merged.update(obj)

                    required = set(
                        ClaudeDecision.model_fields
                    )

                    missing_fields = sorted(
                        key
                        for key in required
                        if (
                            key not in merged
                            and ClaudeDecision.model_fields[key].is_required()
                        )
                    )

                    if missing_fields:
                        schema_repairs += 1
                        missing = missing_fields
                        issues = None

                        self.logger.event(
                            "claude",
                            "RESPONSE_PARTIAL",
                            {
                                "cycle_id": cycle_id,
                                "attempt": attempt,
                                "received": sorted(obj),
                                "missing": missing_fields,
                                "schema_repairs": schema_repairs,
                            },
                        )

                        if schema_repairs > self.max_schema_repairs:
                            raise ClaudeSchemaExhausted(
                                "Claude partial-response repair limit exhausted"
                            )

                        sleep(
                            min(
                                2 * schema_repairs,
                                10,
                            )
                        )
                        continue

                    candidate = dict(merged)

                decision = ClaudeDecision.model_validate(
                    candidate
                )

            except ClaudeSchemaExhausted:
                raise

            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                schema_repairs += 1

                fields = []

                if isinstance(exc, ValidationError):
                    fields = sorted(
                        {
                            str(error["loc"][0])
                            for error in exc.errors()
                            if error.get("loc")
                        }
                    )

                self.logger.event(
                    "claude",
                    "SCHEMA_REPAIR_REQUIRED",
                    {
                        "cycle_id": cycle_id,
                        "attempt": attempt,
                        "schema_repairs": schema_repairs,
                        "fields": fields,
                        "error": str(exc),
                        "errors": (
                            exc.errors()
                            if isinstance(exc, ValidationError)
                            else []
                        ),
                    },
                )

                if schema_repairs > self.max_schema_repairs:
                    self.logger.event(
                        "errors",
                        "CLAUDE_SCHEMA_REPAIRS_EXHAUSTED",
                        {
                            "cycle_id": cycle_id,
                            "attempt": attempt,
                            "schema_repairs": schema_repairs,
                        },
                    )

                    raise ClaudeSchemaExhausted(
                        "Claude schema repair limit exhausted"
                    ) from exc

                missing = fields or None

                issues = [
                    "Previous decision failed local ClaudeDecision validation. "
                    f"Correct the decision and return the complete schema. Error: {exc}"
                ]

                merged = {}

                sleep(
                    min(
                        2 * schema_repairs,
                        10,
                    )
                )
                continue

            # JSON/schema is valid. Now check Elliott hard rules.
            elliott_issues = validate_elliott(
                decision.primary_count
            )

            if elliott_issues:
                elliott_revisions += 1

                self.logger.event(
                    "claude",
                    "ELLIOTT_REVISION_REQUIRED",
                    {
                        "cycle_id": cycle_id,
                        "attempt": attempt,
                        "revision": elliott_revisions,
                        "issues": elliott_issues,
                    },
                )

                if elliott_revisions > self.max_elliott_revisions:
                    self.logger.event(
                        "errors",
                        "CLAUDE_ELLIOTT_REVISIONS_EXHAUSTED",
                        {
                            "cycle_id": cycle_id,
                            "attempt": attempt,
                            "revisions": elliott_revisions,
                            "issues": elliott_issues,
                        },
                    )

                    raise ClaudeElliottExhausted(
                        "Claude Elliott revision limit exhausted"
                    )

                # Claude revises its own interpretation.
                # Python does NOT choose WAIT/LONG/SHORT.
                missing = None
                issues = elliott_issues
                merged = {}

                sleep(
                    min(
                        2 * elliott_revisions,
                        10,
                    )
                )
                continue

            # Recovery invariant:
            # once a decision has passed BOTH schema validation
            # and Elliott hard-rule validation, persist it before
            # returning control to Orchestrator.
            #
            # If durable persistence fails, propagate the error.
            # Never call Claude again merely because local journal
            # storage failed.
            if on_valid_decision is not None:
                on_valid_decision(
                    decision
                )

            self.logger.claude_parsed(
                cycle_id,
                decision.model_dump(
                    mode="json"
                ),
            )

            self.logger.event(
                "claude",
                "DECISION_VALID",
                {
                    "cycle_id": cycle_id,
                    "attempts": attempt,
                    "transport_failures": transport_failures,
                    "schema_repairs": schema_repairs,
                    "elliott_revisions": elliott_revisions,
                    "action": decision.action,
                },
            )

            return decision

        self.logger.event(
            "errors",
            "CLAUDE_CYCLE_ATTEMPTS_EXHAUSTED",
            {
                "cycle_id": cycle_id,
                "attempts": attempt,
                "max_total_attempts": max_total_attempts,
                "transport_failures": transport_failures,
                "schema_repairs": schema_repairs,
                "elliott_revisions": elliott_revisions,
            },
        )

        raise ClaudeCycleError(
            "Claude analysis cycle exhausted its technical attempt bound"
        )
