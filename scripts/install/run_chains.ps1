# Two sequential chains: environments (chain A) and model downloads (chain B).
param([string]$Chain)
if ($Chain -eq "A") {
    & "$PSScriptRoot\install_comfyui_env.ps1" *> C:\LocalAI\logs\install_comfyui.log
    & "$PSScriptRoot\install_tts_envs.ps1" *> C:\LocalAI\logs\install_tts.log
} else {
    foreach ($g in "image", "tts", "video") {
        & C:\LocalAI\envs\env-api\Scripts\python.exe -u C:\LocalAI\scripts\download_models.py $g *> "C:\LocalAI\logs\download_$g.log"
    }
}
