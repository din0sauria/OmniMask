# OmniMask

面向多模态数据的隐私脱敏系统。当前已打通 **本地路径 → 检测 → 审核 → 脱敏 → 导出** 完整链路，覆盖：

- **代码密钥 / 敏感串**（复用 keyleak 规则库）
- **文本 PII**：手机号、身份证、邮箱、银行卡、姓名、地址
- **图像**：人脸、二维码、OCR 文本 PII、打码
- **音频 / 视频**：语音转写 PII、敏感片段静音、变声、抽帧检测

这是本地应用：后端直接读取你填写的文件/目录路径，**不复制、不移动、不修改源文件**。
脱敏结果在导出时即时生成到 ZIP，任务目录只保存元数据和检测结果。

## 技术栈

- 后端：Python 3.12 + FastAPI + uv
- 前端：Next.js 16（App Router，React 19）+ TypeScript
- 检测：直接复用 `keyleak/` 的规则库与过滤链路

## 目录

```
OmniMask/
├── backend/                 # FastAPI 服务
│   └── app/
│       ├── api/             # tasks / policies 路由
│       ├── core/            # 配置、本地存储
│       ├── models/          # Pydantic 模型
│       └── services/        # ingest / detect / mask / policy / report
├── frontend/                # Next.js 工作台
├── keyleak/                 # 原有密钥检测模块（规则库 + 过滤逻辑）
└── data/                    # 运行期元数据（自动创建，不入库）
    ├── policies.json        # 自定义策略
    └── tasks/<task_id>/
        ├── meta.json        # 路径、文件列表、策略快照、统计
        └── findings.json    # 检测结果与审核状态
```

源文件始终留在原目录；脱敏和还原 ZIP 在下载时生成到系统临时目录，响应结束后自动删除。

## 启动

### 1. 后端

```sh
cd backend
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

首次检测会把 keyleak 规则库与过滤词表加载到内存，约需数秒。

### 2. 前端

```sh
cd frontend
npm install
npm run dev
```

打开 <http://localhost:3000>。前端通过 Next rewrites 把 `/api/*` 代理到 `http://127.0.0.1:8000`；如后端地址不同，设置环境变量 `BACKEND_URL`。

生产构建：

```sh
cd frontend
npm run build
npm run start
```

## 使用流程

1. 工作台填写任务名称、操作人、数据用途；默认值：任务名取路径名、操作人取当前系统用户、用途为「内部代码安全检查」、策略为标准脱敏。
2. 在「本地文件或目录」处直接输入绝对路径，或点击「浏览…」通过只读弹窗选择本机文件/目录（支持盘符、上一级、路径跳转和筛选）。最近使用的路径会被记住。
3. 选择策略，创建任务并自动检测。
4. 在任务页逐条接受 / 忽略 / 调整处理方式，右侧列表与左侧高亮联动。
5. 执行脱敏，查看脱敏预览（预览基于内存计算，不写源文件）。
6. 导出 ZIP：包含 `masked/`、`report.json`、`report.md`；可逆策略可单独下载映射表，并可一键还原。

## 检测说明

- 规则：`keyleak/data/regex/merged_rules.json`（1124 条），按文件原文逐条正则匹配。
- 过滤：复用 keyleak 的 `key_value_filter_single`、`combine_substr`、`check_multi_reason`、`filter_strings`（多语言字符串提取），并缓存其反复加载的词表。
- 输出：文件、起止行列、命中内容、规则名、类型、置信度、风险等级。
- 目录导入会跳过 `.git`、`node_modules`、`__pycache__`、`.venv` 等目录；最多 500 个文件，单个文件上限 5 MB。
- 检测阶段跳过二进制文件与大于 1 MB 的文件。
- LLM 辅助检测（`keyleak/llmdetect.py`）未启用，避免引入 Qwen 与 GPU 依赖。

## 模态与可选依赖

任务会按文件类型自动判定模态：`.txt/.csv/.md/.log/.html` → 文本 PII，`.jpg/.png` 等 → 图像，`.mp3/.wav` 等 → 音频，`.mp4/.mkv` 等 → 视频，其余文本文件 → 代码密钥。

- **文本 PII**：内置正则（手机/身份证/邮箱/银行卡含校验、姓名/地址轻量规则）开箱即用；可选 HaS_Text NER 增强。
- **图像**：人脸（OpenCV Haar）、二维码（OpenCV）、OCR（rapidocr）→ 文本 PII；可选 HaS_Image YOLO 分割增强（21 类）。需安装媒体依赖。
- **音频**：ASR（faster-whisper，首次自动下载模型）→ 文本 PII → 静音；变声（pitch / robot）。
- **视频**：抽帧走图像检测 + 音轨走音频检测（需 ffmpeg）。

安装可选媒体依赖：

```sh
cd backend
uv sync --group media
```

未安装时这些模态会优雅降级：检测返回提示信息，代码与文本 PII 不受影响。

**本地模型增强**（可选）：模型权重统一放 `models/`（已 git 忽略），用 `downmodels.ps1`（modelscope）下载到
`models/HaS_Text_0209_0.6B_Q8/has_text_model.gguf` 与 `models/HaS_Image_0209_FP32/sensitive_seg_best.pt`。
在策略里打开「使用本地模型增强检测」（`use_model`）后，文本走 HaS_Text NER、图像走 HaS_Image YOLO 分割，未下载模型时自动跳过。
Windows 上 `llama-cpp-python` 无 PyPI 预编译包，已把 CPU wheel 固化为 `backend/vendor/` 下的本地依赖。

## 策略

| 内置策略 | 行为 |
|---|---|
| 标准脱敏 | 保留首尾 4 字符，中间打码 |
| 彻底删除 | 直接删除命中内容 |
| 可逆代号替换 | 替换为 `__OMNI_SECRET_0001__` 形式代号，映射表留在任务目录 |
| 严格模式 | 只处理置信度 ≥ 0.7 的结果 |
| 文本 PII 标准脱敏 | 检测手机/身份证/邮箱/银行卡/姓名/地址并打码 |
| 媒体脱敏 | 图像人脸/二维码打码、音频敏感片段静音 |

处理方式：**打码**（保留首尾）/ **删除** / **代号替换** / **加密**（对称可解密）/ **数字偏移**（加偏移并取整）。

自定义策略支持：处理方式、保留字符数、可逆开关、置信度阈值、文本 PII 类型开关、加密密钥、数字偏移量、音频变声、本地模型增强开关、忽略规则系列、自定义正则（Python `re` 语法，单捕获组时只处理该组）。

## API 摘要

统一响应 `{code, msg, data}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/env` | 默认值：系统用户、主目录、工作目录 |
| GET | `/api/fs?path=` | 只读目录浏览（路径弹窗使用） |
| GET | `/api/tasks` | 任务列表 |
| POST | `/api/tasks` | 新建任务（JSON：`source_path`、`name`、`policy_id`、`operator`、`purpose`） |
| GET | `/api/tasks/{id}` | 任务详情 + 结果统计 |
| DELETE | `/api/tasks/{id}` | 删除任务及文件 |
| POST | `/api/tasks/{id}/detect` | 检测（可传 `policy_id` 切换策略） |
| GET | `/api/tasks/{id}/findings` | 检测结果 |
| GET | `/api/tasks/{id}/files/{file_id}` | 原始 / 脱敏预览 |
| POST | `/api/tasks/{id}/review` | 审核（接受 / 忽略 / 修改方式） |
| POST | `/api/tasks/{id}/mask` | 计算脱敏结果（不写源文件） |
| GET | `/api/tasks/{id}/mapping` | 下载映射表（JSON，实时生成） |
| POST | `/api/tasks/{id}/restore` | 使用映射表还原 |
| GET | `/api/tasks/{id}/restore/download` | 下载还原包（临时生成） |
| GET | `/api/tasks/{id}/export` | 导出 ZIP（`inline=1` 供前端 fetch，`include_mapping=true` 打包映射表） |
| GET | `/api/tasks/{id}/report` | 报告 JSON |
| GET/POST/PUT/DELETE | `/api/policies` | 策略 CRUD |

## 说明

- 目录浏览接口为只读（列目录/文件，不读写内容）；服务只应绑定 `127.0.0.1`，不要暴露到公网。
- 任务是同步处理的；样本仓库（10 个文件）检测约 3–5 秒，首次检测需加载 keyleak 规则与词表。
- 源文件目录不会被写入。任务目录只保存 `meta.json` / `findings.json`，其中包含命中的明文密钥，请仅在受控机器上运行并注意清理。
- 若检测后源文件被修改，预览和导出会基于当前文件内容重新定位命中位置；找不到的内容会被跳过。
- 后续模态（文本 PII / 图像 / 音视频）在主链路稳定后按 `detect(input, policy) -> findings` / `mask(input, findings, policy) -> output` 的同样接口扩展。
