$root = Join-Path $PSScriptRoot "models"

uv run --no-project --default-index "https://mirrors.aliyun.com/pypi/simple/" --with modelscope modelscope download --model TencentXuanwu/HaS_Text_0209_0.6B_Q8 --local_dir (Join-Path $root "HaS_Text_0209_0.6B_Q8")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --no-project --default-index "https://mirrors.aliyun.com/pypi/simple/" --with modelscope modelscope download --model TencentXuanwu/HaS_Image_0209_FP32 --local_dir (Join-Path $root "HaS_Image_0209_FP32")
