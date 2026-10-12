@echo off
:waittools
findstr /C:"DONE" C:\LocalAI\logs\download_tools.log >nul || (timeout /t 20 >nul & goto waittools)
C:\LocalAI\envs\env-api\Scripts\python.exe -u C:\LocalAI\scripts\download_models.py qwen21 > C:\LocalAI\logs\download_qwen21.log 2>&1
C:\LocalAI\envs\env-api\Scripts\python.exe -u C:\LocalAI\scripts\download_models.py bernini > C:\LocalAI\logs\download_bernini.log 2>&1

