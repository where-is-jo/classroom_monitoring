$ErrorActionPreference = "Stop"

$modelRoot = Join-Path $PSScriptRoot ".models\buffalo_l"
$modelPath = Join-Path $modelRoot "w600k_r50.onnx"
if (Test-Path -LiteralPath $modelPath -PathType Leaf) {
    Write-Host "Face recognition model is already prepared: $modelPath"
    exit 0
}

$archivePath = Join-Path $PSScriptRoot ".models\buffalo_l.zip"
$expectedSha256 = "80FFE37D8A5940D59A7384C201A2A38D4741F2F3C51EEF46EBB28218A7B0CA2F"
New-Item -ItemType Directory -Force (Split-Path -Parent $archivePath) | Out-Null
curl.exe -L --fail --retry 2 --output $archivePath "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

$actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
if ($actualSha256 -ne $expectedSha256) {
    throw "InsightFace model archive checksum mismatch."
}

New-Item -ItemType Directory -Force $modelRoot | Out-Null
Expand-Archive -LiteralPath $archivePath -DestinationPath $modelRoot -Force
Remove-Item -LiteralPath $archivePath
if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) {
    throw "Recognition model was not found after extraction."
}
Write-Host "Face recognition model prepared: $modelPath"
