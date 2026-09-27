# Elliot Robot V9.1 — Elliott + Claude + Market Memory + Anthropic Cache

Это чистый research-first проект. До отдельного causal backtest и DEMO forward test live execution не включается.

## Принципы

1. **Решение LONG / SHORT / WAIT принимает только Claude.**
2. Python и библиотеки (`smartmoneyconcepts`, `trendln`, NumPy/SciPy) дают Claude только доказательства/контекст.
3. Никакая библиотека не имеет права открыть, закрыть или заблокировать сделку.
4. Нет дневных лимитов Claude, event slots, hard/soft budget gates. Стоимость только измеряется и логируется.
5. `Market Memory` — источник истины. Anthropic prompt cache — только дешёвая оптимизация. Cache miss восстанавливается из Market Memory.
6. Все pivots причинные: pivot становится известен только после правых подтверждающих свечей (`confirmed_at`).
7. Все действия и бездействия логируются: **No silent decisions**.

## Аналитический стек

- OUR CAUSAL STRUCTURE ENGINE — confirmed pivots, HH/HL/LH/LL.
- smartmoneyconcepts — BOS, CHoCH, liquidity **только поверх наших causal pivots**.
- trendln — support/resistance/trend lines.
- NumPy / SciPy — prominence, regression/channel geometry.
- Claude — Elliott interpretation и единственное торговое решение.

## Market Memory + cache

Первичный/периодический rebase может передавать расширенный контекст. После этого робот хранит компактную `state/market_memory/current.json` и отправляет Claude в основном delta: новые свечи + изменившиеся evidence. Stable prefix помечается `cache_control` для Anthropic.

Если Anthropic cache исчез — ничего не теряется: prefix строится заново из `Market Memory`, cache создаётся снова, а событие и token usage пишутся в лог.

## Надёжность Claude

- raw response сохраняется;
- локально применяется `json-repair`;
- если не хватает полей — повторно запрашиваются только отсутствующие поля;
- если формальный Elliott-count нарушает hard rules — Python сообщает нарушения Claude и просит Claude самому пересчитать count;
- нет permanent circuit breaker: transient ошибки повторяются с backoff, auth/billing ошибки — с длинным backoff;
- API key никогда не пишется в логи.

## Логи

`logs/` содержит отдельные потоки:

- `market/`
- `evidence/`
- `decisions/` — в том числе `called=false` и причина
- `memory/`
- `cache/`
- `claude/requests/`
- `claude/responses/`
- `claude/parsed/`
- `cost/`
- `trade_funnel/`
- `errors/`

Команды аудита:

```powershell
.\.venv\Scripts\python.exe .\audit_report.py --days 7
.\.venv\Scripts\python.exe .\export_audit.py --days 7
.\.venv\Scripts\python.exe .\replay.py --date 2026-09-30
```

## Установка локально

```powershell
.\setup.ps1
.\run_tests.ps1
```

Для реального API позже:

```powershell
Copy-Item .\.env.example .\.env
notepad .\.env
```

## Сейчас

Сервер и live execution пока не запускаем. Следующий этап — причинный backtest/replay, который должен доказать отсутствие look-ahead и корректность Market Memory/delta/caching логики до DEMO.
