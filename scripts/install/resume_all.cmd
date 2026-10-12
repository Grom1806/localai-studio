@echo off
rem Resumes the paused work in order: Bernini-R, InfiniteTalk, HunyuanVideo 1.5 downloads, then the Fish S2 Pro env.
set PY=C:\LocalAI\envs\env-api\Scripts\python.exe
%PY% -u C:\LocalAI\scripts\download_models.py bernini > C:\LocalAI\logs\download_bernini.log 2>&1
%PY% -u C:\LocalAI\scripts\download_models.py infinitetalk > C:\LocalAI\logs\download_infinitetalk.log 2>&1
%PY% -u C:\LocalAI\scripts\download_models.py hunyuan15 > C:\LocalAI\logs\download_hunyuan15.log 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File C:\LocalAI\scripts\install\install_fish.ps1 > C:\LocalAI\logs\install_fish.log 2>&1
echo ALLDONE> C:\LocalAI\logs\resume_all.done
