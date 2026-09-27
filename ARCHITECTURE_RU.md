# Архитектура V9.1

```text
MT5 / historical closed candles
        |
        v
Local Market Store (SQLite)
        |
        +--> Causal Structure Engine --> confirmed pivots / HH HL LH LL
        +--> smartmoneyconcepts adapter --> BOS / CHoCH / liquidity
        +--> trendln adapter --> S/R / trend lines
        +--> NumPy/SciPy --> prominence / channel geometry
                       |
                       v
                  EvidencePack
                       |
              delta vs Market Memory
                       |
           +-----------+-----------+
           |                       |
      no material delta       meaningful delta/watch/rebase
           |                       |
     LOG NO CALL                   v
                              Cache Manager
                                  |
                     stable Market Memory prefix
                     + dynamic new market delta
                                  |
                                  v
                                Claude
                         Elliott + LONG/SHORT/WAIT
                                  |
                    schema/JSON/Elliott validation
                                  |
                   missing/invalid? -> targeted repair
                                  |
                                  v
                           Market Memory update
                                  |
                                  v
                           trade intent (Claude only)
```

## Никаких скрытых блокировок

В runtime нет дневного бюджета, количества M30 вызовов, event slot, `PERMANENT_BLOCKED` и т.п. Решение не вызывать Claude допустимо только потому, что **нет новой информации**, и это решение обязано логироваться.

Технические ошибки API не превращаются в постоянную блокировку: stage продолжает retry с backoff. Это не гарантия доступности Anthropic; это гарантия, что робот сам не "запретит" себе анализ после N попыток.

## Causality

Ни один evidence-engine не получает будущую свечу относительно текущего replay prefix. `smartmoneyconcepts` не имеет права вызывать собственный `swing_highs_lows`; ему передаются наши pivots с `confirmed_at`. Это отдельно тестируется.

## Только Claude решает

Evidence может противоречить Claude. Это разрешено. Система не считает голосование библиотек торговым сигналом. `claude_trade_intent()` получает только `ClaudeDecision` и физически не имеет аргумента EvidencePack.
