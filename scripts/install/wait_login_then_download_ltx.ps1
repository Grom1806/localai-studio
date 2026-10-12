# Waits (up to 24 h) for the Hugging Face login created by HF-Login.bat, then downloads LTX-2.5.
$tok = "C:\LocalAI\cache\hf\token"
for ($i = 0; $i -lt 2880 -and -not (Test-Path $tok); $i++) { Start-Sleep 30 }
if (Test-Path $tok) {
    & C:\LocalAI\envs\env-api\Scripts\python.exe -u C:\LocalAI\scripts\download_models.py video *> C:\LocalAI\logs\download_ltx25.log
}
