# Audit contract

Для любой закрытой H4/H1/M30/M15 должен существовать журнал, позволяющий ответить:

- какую свечу увидел робот;
- какие causal pivots были уже подтверждены;
- что вернули SMC/trendln/geometry;
- изменился ли EvidencePack;
- вызывался ли Claude; если нет — почему;
- какой exact sanitized prompt ушёл;
- какой raw response пришёл;
- какой parsed response получился;
- какие поля ремонтировались;
- был cache HIT/MISS/REBUILT;
- сколько input/output/cache tokens и сколько USD;
- какая версия Market Memory была до и после;
- какое торговое действие вернул Claude.

Любой код-путь "ничего не делаем" обязан оставить `CLAUDE_CALL_DECISION called=false` с причиной.
