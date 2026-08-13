param(
    [string]$PythonPath = "python",
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

$FastApiDirectory = $PSScriptRoot
$RepositoryRoot = (Resolve-Path (Join-Path $FastApiDirectory "..\..")).Path
$DetectionModelPath = Join-Path $RepositoryRoot "deeplearning\.models\scrfd\scrfd_10g_bnkps.onnx"
$LandmarkerModelPath = Join-Path $RepositoryRoot "deeplearning\.models\mediapipe\face_landmarker.task"

try {
    $ResolvedPython = (& $PythonPath -c "import sys; print(sys.executable)").Trim()
} catch {
    Write-Error "Python was not found. Activate the Conda environment or pass python.exe with -PythonPath."
    exit 1
}

if (-not $ResolvedPython -or -not (Test-Path -LiteralPath $ResolvedPython)) {
    Write-Error "The Python executable is unavailable: $PythonPath"
    exit 1
}

try {
    & $ResolvedPython -c "import fastapi, uvicorn, cv2, mediapipe, onnxruntime" 2>$null
} catch {
    Write-Error "Required packages are missing. Install both webapps/fastapi and deeplearning requirements first."
    exit 1
}

if (-not (Test-Path -LiteralPath $DetectionModelPath)) {
    Write-Error "SCRFD model was not found: $DetectionModelPath"
    exit 1
}
if (-not (Test-Path -LiteralPath $LandmarkerModelPath)) {
    Write-Error "MediaPipe Face Landmarker model was not found: $LandmarkerModelPath"
    exit 1
}

$env:FACE_DETECTION_MODEL_PATH = $DetectionModelPath
$env:FACE_LANDMARKER_MODEL_PATH = $LandmarkerModelPath

$ReloadArguments = @()
if (-not $NoReload) {
    $ReloadArguments = @("--reload")
}

$AiArguments = @(
    "-m", "uvicorn", "deeplearning.app:app",
    "--host", "127.0.0.1",
    "--port", "8100"
) + $ReloadArguments

$WebArguments = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1",
    "--port", "8000"
) + $ReloadArguments

Write-Host "Starting face analyzer: http://127.0.0.1:8100"
$AiProcess = Start-Process `
    -FilePath $ResolvedPython `
    -ArgumentList $AiArguments `
    -WorkingDirectory $RepositoryRoot `
    -PassThru

Start-Sleep -Seconds 2
if ($AiProcess.HasExited) {
    Write-Error "The face analyzer exited during startup. Check the Python environment and model configuration."
    exit 1
}

Write-Host "Starting FastAPI web server: http://127.0.0.1:8000"
$WebProcess = Start-Process `
    -FilePath $ResolvedPython `
    -ArgumentList $WebArguments `
    -WorkingDirectory $FastApiDirectory `
    -PassThru

Start-Sleep -Seconds 2
if ($WebProcess.HasExited) {
    if (-not $AiProcess.HasExited) {
        Stop-Process -Id $AiProcess.Id
    }
    Write-Error "The FastAPI server exited during startup. Check .env and the Python environment."
    exit 1
}

Write-Host ""
Write-Host "Both servers are running. Open http://127.0.0.1:8000 in a browser."
Write-Host "Press Enter in this window to stop both servers."
[void](Read-Host)

foreach ($Process in @($WebProcess, $AiProcess)) {
    if (-not $Process.HasExited) {
        Stop-Process -Id $Process.Id
    }
}

Write-Host "Face enrollment servers stopped."
