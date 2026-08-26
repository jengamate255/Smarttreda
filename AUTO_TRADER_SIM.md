# SIM Auto Trader

The unattended runner is deliberately restricted to the `FundingPips2-SIM`
account (`11911448`). It evaluates the validated EURUSD Med and JP225 setups
once per completed H1 bar. SPX500 and BTCUSD are excluded.

## Install

Run from an ordinary PowerShell window:

```powershell
.\manage_auto_trader.ps1 -Action Install
```

The Windows task `MT5AutoTraderSIM` runs every five minutes. The strategy
itself prevents duplicate evaluation within the same H1 bar.

## Safety gates

- SIM server and expected account login are required.
- EURUSD: H1 zones, minimum strength 70, 15-30 pip SL, 80-100 pip TP.
- JP225: H1+H4 zones, minimum strength 40, 20-30 pip SL, 80-100 pip TP.
- Minimum RR is 1:3 and confidence is at least 90%.
- Compliance must be normal; news, liquidity, spread, daily-loss, drawdown,
  position-count, and correlation gates must pass.
- A completed H1 confirmation candle is required.
- The runner fails closed when the news provider is unavailable.

## Monitor

- Log: `logs\auto_trader.log`
- State: `mt5_mcp\memory\auto_trader_state.json`
- Dashboard: `http://127.0.0.1:8050`

## Disable immediately

```powershell
.\manage_auto_trader.ps1 -Action Disable
```

This disables the scheduled task and creates the kill-switch file. Re-enable
only after reviewing the log:

```powershell
.\manage_auto_trader.ps1 -Action Enable
```

The runner does not trade live accounts. It refuses any connected server that
does not contain `SIM`.
