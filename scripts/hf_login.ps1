# Hugging Face login for LocalAI (needed once for gated models: LTX-2.5, Gemma, pyannote).
# The token is stored only on this PC in C:\LocalAI\cache\hf\token.
$env:HF_HOME = "C:\LocalAI\cache\hf"
$hf = "C:\LocalAI\apps\bin\hf.exe"

Write-Host ""
Write-Host "  LocalAI - Hugging Face login" -ForegroundColor Magenta
Write-Host "  Before continuing, make sure you clicked 'Agree and access repository' (while signed in) on:"
Write-Host "    https://huggingface.co/Lightricks/LTX-2.5                       (video)"
Write-Host "    https://huggingface.co/google/gemma-4-12B-it-qat-q4_0-gguf      (local LLM)"
Write-Host "    https://huggingface.co/pyannote/speaker-diarization-community-1 (optional, multi-speaker dubbing)"
Write-Host ""
Write-Host "  1 = Log in with my browser (easiest)"
Write-Host "  2 = Paste a token I created at huggingface.co/settings/tokens"
$choice = Read-Host "  Choose 1 or 2"

if ($choice -eq "2") {
    $sec = Read-Host "  Paste your token (hidden) and press Enter" -AsSecureString
    $tok = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
    & $hf auth login --token $tok.Trim() --force --no-add-to-git-credential
    $tok = $null
} else {
    Write-Host "  A browser page will open (or a link is shown). Approve the login there, then come back here."
    & $hf auth login --force --no-add-to-git-credential
}

Write-Host ""
if (Test-Path "$env:HF_HOME\token") {
    Write-Host "  Logged in as:" -ForegroundColor Green
    & $hf auth whoami
    Write-Host "  Done. Gated models can now be downloaded (the installer continues by itself)." -ForegroundColor Green
} else {
    Write-Host "  Login did not complete. Please try again (or use option 2)." -ForegroundColor Red
}
Read-Host "  Press Enter to close"
