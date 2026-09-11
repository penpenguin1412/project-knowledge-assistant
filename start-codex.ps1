$ErrorActionPreference = 'Stop'
$codexCommand = if ($env:CODEX_BIN) { $env:CODEX_BIN } else { 'codex' }
& $codexCommand login status
if ($LASTEXITCODE -ne 0) { throw 'Run codex login first.' }
$env:LLM_PROVIDER = 'codex'
$env:LLM_MODEL = 'gpt-6-astra'
$env:LLM_ENABLED = '1'
& (Join-Path $PSScriptRoot 'start.ps1')
