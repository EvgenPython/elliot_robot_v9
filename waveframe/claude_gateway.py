from __future__ import annotations
import json, os, time, uuid
from typing import Callable
try:
    from json_repair import repair_json as _repair_json
except ImportError:
    def _repair_json(text, return_objects=False):
        # Development fallback when optional dependency is not installed.
        # Production setup installs json-repair; fallback only accepts valid JSON.
        return text
from pydantic import ValidationError
from .models import ClaudeDecision
from .logging import AuditLogger
from .cache import CacheManager
from .costs import estimate
from .prompts import update_prompt
from .elliott_validator import validate_elliott

class ClaudeGateway:
    """Reliable Claude JSON pipeline with prompt caching and field-level repair.

    There is deliberately NO daily cost cap, slot limit or event quota. Costs are
    observed and logged; they never authorize or deny analysis.
    """
    def __init__(self, root=".", model="claude-sonnet-5", max_tokens=2200, cache_ttl="1h", logger=None, transport:Callable|None=None):
        self.root=root; self.model=model; self.max_tokens=max_tokens; self.cache_ttl=cache_ttl
        self.logger=logger or AuditLogger(root); self.cache=CacheManager(); self.transport=transport

    def _sdk_transport(self, stable_prefix: str, dynamic_prompt: str):
        from anthropic import Anthropic
        client=Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        system_block={"type":"text","text":stable_prefix,"cache_control":{"type":"ephemeral","ttl":self.cache_ttl}}
        msg=client.messages.create(model=self.model,max_tokens=self.max_tokens,system=[system_block],messages=[{"role":"user","content":dynamic_prompt}])
        text="".join(getattr(b,"text","") for b in msg.content if getattr(b,"type",None)=="text")
        u=getattr(msg,"usage",None)
        usage={k:int(getattr(u,k,0) or 0) for k in ["input_tokens","output_tokens","cache_creation_input_tokens","cache_read_input_tokens"]}
        return {"text":text,"usage":usage,"request_id":getattr(msg,"id",None),"model":getattr(msg,"model",self.model)}

    def ask_until_valid(self, stable_prefix: str, delta: dict, sleep:Callable[[float],None]=time.sleep) -> ClaudeDecision:
        cycle_id=str(uuid.uuid4()); missing=None; issues=None; merged={}; attempt=0
        while True:
            attempt+=1
            dynamic=update_prompt(delta,missing,issues)
            request={"cycle_id":cycle_id,"attempt":attempt,"model":self.model,"cache_ttl":self.cache_ttl,"stable_prefix":stable_prefix,"dynamic_prompt":dynamic,"repair_missing_fields":missing,"elliott_issues":issues}
            self.logger.claude_request(f"{cycle_id}_{attempt:03d}",request)
            try:
                raw=(self.transport or self._sdk_transport)(stable_prefix,dynamic)
                text=str(raw.get("text", "")); usage=dict(raw.get("usage") or {})
                cost=estimate(str(raw.get("model") or self.model),usage,self.cache_ttl)
                self.logger.claude_response(f"{cycle_id}_{attempt:03d}",{**raw,"estimated_cost_usd":cost,"cache_status":self.cache.cache_status(usage)})
                self.logger.event("cost","CLAUDE_COST",{"cycle_id":cycle_id,"attempt":attempt,"model":raw.get("model",self.model),"usage":usage,"estimated_cost_usd":cost,"cache_status":self.cache.cache_status(usage)})
                obj=json.loads(_repair_json(text,return_objects=False))
                if not isinstance(obj,dict): raise ValueError("Claude JSON root must be object")
                merged.update(obj)
                required=set(ClaudeDecision.model_fields)
                missing=sorted(k for k in required if k not in merged and ClaudeDecision.model_fields[k].is_required())
                if missing:
                    self.logger.event("claude","RESPONSE_PARTIAL",{"cycle_id":cycle_id,"attempt":attempt,"received":sorted(obj),"missing":missing})
                    issues=None; sleep(min(2*attempt,10)); continue
                try:
                    decision=ClaudeDecision.model_validate(merged)
                except ValidationError as e:
                    # Request the fields implicated by validation, not the whole market again.
                    fields=sorted({str(err.get("loc",["unknown"])[0]) for err in e.errors()})
                    missing=fields
                    self.logger.event("claude","SCHEMA_REPAIR_REQUIRED",{"cycle_id":cycle_id,"attempt":attempt,"fields":fields,"errors":e.errors()})
                    sleep(min(2*attempt,10)); continue
                issues=validate_elliott(decision.primary_count)
                if issues:
                    self.logger.event("claude","ELLIOTT_REVISION_REQUIRED",{"cycle_id":cycle_id,"attempt":attempt,"issues":issues})
                    # Ask Claude to revise its own decision/count. Do not let Python choose a trade.
                    missing=None; merged={}; sleep(min(2*attempt,10)); continue
                self.logger.claude_parsed(cycle_id,decision.model_dump(mode="json"))
                self.logger.event("claude","DECISION_VALID",{"cycle_id":cycle_id,"attempts":attempt,"action":decision.action})
                return decision
            except Exception as e:
                name=type(e).__name__; msg=str(e)
                self.logger.event("errors","CLAUDE_ATTEMPT_FAILED",{"cycle_id":cycle_id,"attempt":attempt,"type":name,"error":msg})
                # No permanent circuit breaker. Authentication/billing errors retry slowly;
                # transient failures retry with bounded exponential backoff.
                lower=msg.lower()
                delay=300 if any(x in lower for x in ["401","402","403","credit","billing","authentication","api key"]) else min(60,2**min(attempt,6))
                sleep(delay)
