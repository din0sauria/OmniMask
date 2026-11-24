$root = Join-Path $PSScriptRoot "models"

# 音频/视频语音转写（必选）：faster-whisper-base
uv run --no-project --default-index "https://mirrors.aliyun.com/pypi/simple/" --with modelscope modelscope download --model Systran/faster-whisper-base --local_dir (Join-Path $root "faster-whisper-base")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 本地模型增强（可选）：HaS 文本 NER / HaS 图像分割
uv run --no-project --default-index "https://mirrors.aliyun.com/pypi/simple/" --with modelscope modelscope download --model TencentXuanwu/HaS_Text_0209_0.6B_Q8 --local_dir (Join-Path $root "HaS_Text_0209_0.6B_Q8")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv run --no-project --default-index "https://mirrors.aliyun.com/pypi/simple/" --with modelscope modelscope download --model TencentXuanwu/HaS_Image_0209_FP32 --local_dir (Join-Path $root "HaS_Image_0209_FP32")
