@echo off
rem Starter fuer kai-ln-budget.ps1 (liegt im PATH, damit "kai-ln-budget 5000" ueberall geht).
powershell -NoProfile -ExecutionPolicy Bypass -File "%USERPROFILE%\KAI-mirror\scripts\kai-ln-budget.ps1" %*
