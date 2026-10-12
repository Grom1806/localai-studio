@echo off
:wait
findstr /C:"DONE" C:\LocalAI\logs\download_bernini.log >nul 2>&1 || (timeout /t 30 >nul & goto wait)
C:\LocalAI\envs\env-api\Scripts\python.exe -u C:\LocalAI\scripts\download_models.py infinitetalk > C:\LocalAI\logs\download_infinitetalk.log 2>&1

