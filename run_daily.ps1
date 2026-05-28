Set-Location $PSScriptRoot
python -m app.pipeline --config .\config.toml --env .\.env
