@echo off
REM ============================================================================
REM  ONE-TIME setup (run as Administrator). Creates the only persistent schedule:
REM  the PLANNER runs 6x/day. The planner is the single brain - it schedules and
REM  fires all generate/publish units (one at a time, specific --post-id). No daemon.
REM
REM  Why this file exists: programmatic `schtasks /Create` is DENIED from a
REM  non-elevated process on this machine, so this must be launched once with
REM  "Run as administrator". After that, Windows Task Scheduler runs it forever.
REM ============================================================================

SET "REPO=D:\Open Projects\Content Engine"
SET "PY=%REPO%\.venv\Scripts\python.exe"
SET "RJ=%REPO%\run_job.py"

schtasks /Create /TN "CE_plan_0"  /TR "\"%PY%\" \"%RJ%\" plan" /SC DAILY /ST 00:00 /RL HIGHEST /H /F
schtasks /Create /TN "CE_plan_4"  /TR "\"%PY%\" \"%RJ%\" plan" /SC DAILY /ST 04:00 /RL HIGHEST /H /F
schtasks /Create /TN "CE_plan_8"  /TR "\"%PY%\" \"%RJ%\" plan" /SC DAILY /ST 08:00 /RL HIGHEST /H /F
schtasks /Create /TN "CE_plan_12" /TR "\"%PY%\" \"%RJ%\" plan" /SC DAILY /ST 12:00 /RL HIGHEST /H /F
schtasks /Create /TN "CE_plan_16" /TR "\"%PY%\" \"%RJ%\" plan" /SC DAILY /ST 16:00 /RL HIGHEST /H /F
schtasks /Create /TN "CE_plan_20" /TR "\"%PY%\" \"%RJ%\" plan" /SC DAILY /ST 20:00 /RL HIGHEST /H /F

echo.
echo Created 6 daily CE_plan tasks (00:00, 04:00, 08:00, 12:00, 16:00, 20:00).
echo Verify with:  schtasks /Query /TN "CE_plan_0"
pause
