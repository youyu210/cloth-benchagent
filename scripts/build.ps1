$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskCmake = 'D:\tools\MicrosoftVisualStudio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$taskNinja = 'D:\tools\MicrosoftVisualStudio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe'
$taskVs = 'D:\tools\MicrosoftVisualStudio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
$taskLog = Join-Path $taskRoot 'build\build.log'
$taskCommand = 'call "{0}" && set NVCC_PREPEND_FLAGS=-allow-unsupported-compiler && "{1}" -S "{2}\simulator" -B "{2}\build\native" -G Ninja -DCMAKE_MAKE_PROGRAM="{3}" -DCMAKE_BUILD_TYPE=Release -DEigen3_DIR="{2}\dependencies\cmake\Eigen3" -Dnlohmann_json_DIR="{2}\dependencies\cmake\nlohmann_json" -DGLEW_DIR="{2}\dependencies\cmake\GLEW" -DGLUT_INCLUDE_DIR="{2}\dependencies\include" -DGLUT_glut_LIBRARY_RELEASE="{2}\dependencies\lib\freeglut.lib" && "{1}" --build "{2}\build\native" --parallel 2' -f $taskVs,$taskCmake,$taskRoot,$taskNinja
$env:TEMP = Join-Path $taskRoot 'build\tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& $env:ComSpec /d /s /c $taskCommand 2>&1 | Tee-Object -FilePath $taskLog
if ($LASTEXITCODE -ne 0) { throw "Native build failed; see $taskLog" }
Copy-Item -LiteralPath 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin\cusparse64_12.dll','C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin\nvJitLink_120_0.dll' -Destination "$taskRoot\build\bin" -Force
Copy-Item -LiteralPath "$taskRoot\build\native\gipc.exe" -Destination "$taskRoot\build\bin\gipc.exe" -Force
& "$taskRoot\.venv\Scripts\python.exe" "$taskRoot\scripts\seal_build.py"
if ($LASTEXITCODE -ne 0) { throw 'Build sealing failed' }
