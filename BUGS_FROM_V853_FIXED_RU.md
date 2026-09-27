# Что V9.1 принципиально не переносит из V8.5.3

- Нет `MAX_M30_EVENT_DECISIONS_PER_DAY=2`.
- Нет `TARGET_DAILY_USD`, `SOFT_DAILY_USD`, `EVENT_HARD_CEILING_USD` как gate.
- Нет H1 entry disable из-за M30-primary.
- Нет permanent AI breaker после 400/401/402/403.
- Нет generic M30 movement как замены точному watch Claude.
- Watch Claude хранится machine-readable и Python проверяет именно его уровень.
- Review/cost теперь основаны на каждом фактическом Claude usage record.
- Market Memory переживает рестарт; cache не является источником истины.
- Library evidence не может проголосовать за/против сделки.
- Causal pivots имеют `confirmed_at`, чтобы не использовать future bars.
