$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskCmake='D:\tools\MicrosoftVisualStudio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$taskNinja='D:\tools\MicrosoftVisualStudio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe'
$taskVs='D:\tools\MicrosoftVisualStudio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
$taskCommand='call "{0}" && set NVCC_PREPEND_FLAGS=-allow-unsupported-compiler && "{1}" -S "{2}\versions\paper-original-source" -B "{2}\build\paper-original" -G Ninja -DCMAKE_MAKE_PROGRAM="{3}" -DCMAKE_CUDA_ARCHITECTURES=52 -DCMAKE_BUILD_TYPE=Release -DEigen3_DIR="{2}\dependencies\cmake\Eigen3" -Dnlohmann_json_DIR="{2}\dependencies\cmake\nlohmann_json" -DGLEW_DIR="{2}\dependencies\cmake\GLEW" -DGLUT_INCLUDE_DIR="{2}\dependencies\include" -DGLUT_glut_LIBRARY_RELEASE="{2}\dependencies\lib\freeglut.lib" "-DCMAKE_CXX_FLAGS=/utf-8" "-DCMAKE_CUDA_FLAGS_RELEASE=-Xcompiler=-O2,-Ob2,/utf-8 -DNDEBUG" && "{1}" --build "{2}\build\paper-original" --parallel 4' -f $taskVs,$taskCmake,$taskRoot,$taskNinja
$env:TEMP=Join-Path $taskRoot 'build\tmp'
$env:TMP=$env:TEMP
& $env:ComSpec /d /s /c $taskCommand *> "$taskRoot\build\paper-original-final.log"
$result=$LASTEXITCODE
Get-Content "$taskRoot\build\paper-original-final.log" -Tail 10
exit $result
