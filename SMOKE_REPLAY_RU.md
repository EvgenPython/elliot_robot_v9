# Smoke replay без Claude

Сначала должен быть запущен WaveFrame Market Simulator на `http://127.0.0.1:8765`.

Команда ниже **НЕ вызывает Anthropic API**:

```powershell
.\.venv\Scripts\python.exe .\run_simulator_replay.py `
  --start "2026-09-25T08:00:00+00:00" `
  --end   "2026-09-25T10:00:00+00:00" `
  --ai stub
```

`ReplayClock` заставляет Market Memory и audit-логи жить в историческом времени симулятора, а не во времени Windows.

Каждый M5 checkpoint завершается ACK. H4/H1/M30/M15 анализируются только когда соответствующая свеча реально закрылась.

`--ai live` существует, но до отдельного разрешения пользователя его не запускать.
