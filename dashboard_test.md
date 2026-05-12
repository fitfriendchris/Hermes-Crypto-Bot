# TELEGRAM DASHBOARD BUTTON NAVIGATION TEST

## Bot Status
- Bot PID: 92768 (running)
- Dashboard PID: 94109 (running)
- Both operational

## Button Layout (Main Menu)
```
Row 1: [📊 STATUS] [📈 PnL]
Row 2: [📋 POSITIONS] [📜 TRADES]
Row 3: [🏥 HEALTH] [🔄 REFRESH]
Row 4: [🟢 START BOT] [🔴 STOP BOT]
Row 5: [🧹 CLEAN RESTART] [⚙️ CONFIG]
Row 6: [💰 WITHDRAW]
```

## Navigation Flow
1. **/start** → Shows main menu with all buttons
2. **📊 STATUS** → Portfolio overview + back to menu
3. **📈 PnL** → Profit/loss report + back to menu
4. **📋 POSITIONS** → Open positions + back to menu
5. **📜 TRADES** → Trade history + back to menu
6. **🏥 HEALTH** → Health check → submenu with [CHECK AGAIN] [CLEAN RESTART] [FULL STATUS] [DASH RESTART] [BACK TO MENU]
7. **🔄 REFRESH** → Updates status display
8. **🟢 START BOT** → Starts bot (if stopped)
9. **🔴 STOP BOT** → Stops bot
10. **🧹 CLEAN RESTART** → Clean restart sequence
11. **⚙️ CONFIG** → Shows config + back to menu
12. **💰 WITHDRAW** → Shows wallet info + withdraw instructions

## Health Submenu
```
Row 1: [🔍 CHECK AGAIN] [🧹 CLEAN RESTART]
Row 2: [📊 FULL STATUS] [🔄 DASH RESTART]
Row 3: [◀️ BACK TO MENU]
```

## Back Button Navigation
- All status screens have [🏠 MENU] button to return to main menu
- Health screen has [◀️ BACK TO MENU] button

## Commands
- `/start` - Open command center
- `/status` - Quick status
- `/positions` - Open positions
- `/trades` - Trade history
- `/pnl` - P&L report
- `/health` - Health check
- `/config` - Config view
- `/withdraw` - Withdraw info
- `/restart` - Clean restart
- `/menu` - Back to menu
