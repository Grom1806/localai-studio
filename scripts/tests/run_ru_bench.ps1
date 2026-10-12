# Russian TTS benchmark (generate + score) for all engines; keeps Windows awake while it runs.
param([string]$Engines = "qwen,omnivoice,chatterbox,fish")
Add-Type -Namespace LocalAI -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[LocalAI.Power]::SetThreadExecutionState([Convert]::ToUInt32("80000001", 16)) | Out-Null
$env:PYTHONIOENCODING = "utf-8"
try {
    & C:\LocalAI\envs\env-api\Scripts\python.exe -u C:\LocalAI\scripts\tests\ru_bench_generate.py $Engines *> C:\LocalAI\logs\ru_bench_gen.log
    & C:\LocalAI\envs\env-media\Scripts\python.exe -u C:\LocalAI\scripts\tests\ru_bench_score.py $Engines *> C:\LocalAI\logs\ru_bench_score.log
} finally {
    [LocalAI.Power]::SetThreadExecutionState([Convert]::ToUInt32("80000000", 16)) | Out-Null
}
