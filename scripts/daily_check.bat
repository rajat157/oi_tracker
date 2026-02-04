@echo off
REM Daily monitoring script for OI Tracker
REM Run this every morning to check system status

echo ================================================================================
echo OI TRACKER - DAILY STATUS CHECK
echo ================================================================================
echo.
echo Date: %date% %time%
echo.

REM Check if system is running
tasklist /FI "IMAGENAME eq python.exe" 2>NUL | find /I /N "python.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [OK] Python processes running
) else (
    echo [WARNING] No Python processes found - Is the system running?
)

echo.
echo ================================================================================
echo CALL-ONLY PERFORMANCE (Last 7 days)
echo ================================================================================
echo.

REM Change to project root directory and run script
cd /d "%~dp0.."
python scripts/monitor_call_performance.py --days 7

echo.
echo ================================================================================
echo SELF-LEARNER STATUS
echo ================================================================================
echo.

python -c "from database import get_latest_analysis; import json; analysis = get_latest_analysis(); sl = analysis.get('self_learning', {}); print(f'Last Update: {analysis.get(\"timestamp\", \"N/A\")[:19]}'); print(f'Is Paused:   {sl.get(\"is_paused\", \"N/A\")} {\"[System NOT trading]\" if sl.get(\"is_paused\") else \"[System ACTIVE]\"}'); print(f'EMA Accuracy: {sl.get(\"ema_accuracy\", 0):.1f}%% (needs >50%% to unpause)'); print(f'Verdict:     {analysis.get(\"verdict\", \"N/A\")}'); print(f'Confidence:  {analysis.get(\"signal_confidence\", 0):.0f}%%')"

echo.
echo ================================================================================
echo TODAY'S ACTIVITY
echo ================================================================================
echo.

python -c "import sqlite3; from datetime import datetime; conn = sqlite3.connect('oi_tracker.db'); cursor = conn.cursor(); today = datetime.now().strftime('%%Y-%%m-%%d'); cursor.execute('SELECT created_at, direction, verdict_at_creation, status FROM trade_setups WHERE created_at LIKE ?', (f'{today}%%',)); trades = cursor.fetchall(); print(f'Trades created today: {len(trades)}'); [print(f'  {t[0][11:19]} | {t[1]:8} | {t[2]:25} | {t[3]}') for t in trades] if trades else print('  (No trades yet - check back later)'); conn.close()"

echo.
echo ================================================================================
echo NEXT ACTIONS
echo ================================================================================
echo.
echo 1. If win rate is 40%%+  : Good - Continue monitoring
echo 2. If win rate is under 35%% : Investigate signal quality
echo 3. If no trades for 3+ days : Check filters (too strict?)
echo 4. If self-learner paused : Normal - Will unpause when accuracy improves
echo.
echo Run this script again tomorrow for updated status.
echo.

pause
