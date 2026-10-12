# Runs resume_all.cmd while asking Windows not to sleep (like a video player does).
# No power settings are changed; the request ends when this script exits.
Add-Type -Namespace LocalAI -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint f);'
[LocalAI.Power]::SetThreadExecutionState([Convert]::ToUInt32("80000001", 16)) | Out-Null   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
try {
    & cmd.exe /c C:\LocalAI\scripts\install\resume_all.cmd
} finally {
    [LocalAI.Power]::SetThreadExecutionState([Convert]::ToUInt32("80000000", 16)) | Out-Null
}
