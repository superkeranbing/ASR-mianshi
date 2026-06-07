# 听记面试 (ASR-Mianshi) — AI 面试录音转写与分析平台

基于 AI 的面试录音转写、分析、提升一体化平台。支持浏览器实时录音、音频文件导入、智能语音转写、说话人分离、AI 面试分析与提升报告生成。

## 功能概览

| 模块 | 功能 |
|------|------|
| 录音管理 | 浏览器实时录音、音频文件导入（MP3/WAV/M4A）、波形可视化播放 |
| ASR 转写 | 中英文识别、说话人分离（VAD + ERes2NetV2 聚类）、标点恢复、时间戳对齐 |
| AI 纪要 | LLM 自动生成对话摘要和问答对提取 |
| 面试分析 | 提取问答对、自动分类、生成最佳答案、评分、输出提升计划 |
| 简历解析 | 上传 PDF/DOCX，自动提取结构化信息 |
| 知识点卡片 | LLM 从面试内容中提取技术知识点，生成学习卡片 |
| 多格式导出 | TXT、DOCX、PDF、SRT 字幕 |

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 19、TypeScript、Vite 8、TailwindCSS 4、Wavesurfer.js 7、Zustand 5、Lucide React |
| 后端 | Python 3.12、FastAPI、Celery、SQLAlchemy 2.0 |
| 数据库 | SQLite（开发）/ PostgreSQL 16 + pgvector（生产） |
| 缓存/队列 | Redis 7 |
| 文件存储 | 本地文件系统（开发）/ MinIO S3（生产） |
| ASR 引擎 | FunASR（Paraformer-large + ERes2NetV2 + FSMN-VAD 自定义管道） |
| LLM | DeepSeek / 通义千问 / OpenAI 兼容 API |
| 部署 | Docker、Docker Compose |

## 快速开始

### 前置要求

- Python >= 3.12
- Node.js >= 22
- Docker 和 Docker Compose（用于启动基础设施服务）

### 1. 启动基础设施（Docker）

```bash
docker compose -f docker/docker-compose.dev.yml up -d postgres redis minio
```

### 2. 启动后端

```bash
cd backend

# 配置环境变量（首次）
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（必填）

# 安装依赖（首次）
pip install -r requirements.txt

# 运行数据库迁移（首次）
alembic upgrade head

# 启动后端
python run.py
```

`python run.py` 会同时启动 FastAPI（端口 8000）和 Celery Worker。如果只需要 API：

```bash
python run.py --api-only
```

验证后端：`curl http://localhost:8000/api/health`

API 文档：http://localhost:8000/api/docs

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端运行在 http://localhost:5173，Vite 开发服务器自动将 `/api` 和 `/ws` 请求代理到后端。

### Windows 一键启动

项目根目录下的 `start.bat` 可以自动启动 Docker 服务、安装 Python 依赖并运行后端：

```bash
start.bat
```

然后手动启动前端：`cd frontend && npm run dev`

## Docker 全量部署

所有服务一并启动（适合生产或快速体验）：

```bash
# 先配置环境变量（LLM_API_KEY、JWT_SECRET 等）
# 编辑 docker/docker-compose.yml

docker compose -f docker/docker-compose.yml up -d --build
```

启动后：

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost |
| 后端 API | http://localhost/api/ |
| API 文档 | http://localhost/api/docs |

**生产环境必须修改的变量**（在 `docker/docker-compose.yml` 中设置）：

- `JWT_SECRET`：用 `openssl rand -hex 32` 生成
- `LLM_API_KEY`：你的 API Key
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`：更换默认密码
- `CORS_ORIGINS`：线上域名
- `DEBUG`：设为 `"false"`

## 项目结构

```
asr-mianshi/
├── frontend/                    # React 19 SPA
│   ├── src/
│   │   ├── components/          # 通用 UI 组件（AudioPlayer、Layout 等）
│   │   ├── pages/               # 页面（Home、Recording、Interview、Report、History、Login）
│   │   ├── services/            # API 请求层
│   │   ├── stores/              # Zustand 状态管理
│   │   ├── hooks/               # 自定义 Hooks（useAudioRecorder）
│   │   └── types/               # TypeScript 类型定义
│   ├── Dockerfile               # 生产构建 + Nginx 镜像
│   ├── Dockerfile.split         # 前后端分离部署镜像
│   ├── nginx.conf               # Nginx 配置（SPA + API 代理）
│   └── nginx.split.conf         # 分离部署 Nginx 模板
│
├── backend/                     # Python FastAPI
│   ├── app/
│   │   ├── api/                 # 路由（auth、asr、recording、interview、export、resume、websocket）
│   │   ├── core/                # 基础设施（database、security、storage、celery_app）
│   │   ├── models/              # SQLAlchemy ORM 模型（user、recording、transcript、interview、resume）
│   │   ├── schemas/             # Pydantic 请求/响应模型
│   │   ├── services/            # 业务逻辑（asr_engine、llm_service、resume_parser）
│   │   ├── tasks/               # Celery 异步任务（asr_tasks、interview_tasks）
│   │   └── utils/               # 工具函数
│   ├── alembic/                 # 数据库迁移
│   ├── uploads/                 # 本地文件存储（audio/、resumes/、exports/）
│   ├── .env.example             # 环境变量模板
│   └── run.py                   # 开发启动器（FastAPI + Celery Worker）
│
├── docker/                      # Docker Compose 编排
│   ├── docker-compose.yml           # 生产全量部署
│   ├── docker-compose.dev.yml       # 开发环境（基础设施 + 热重载）
│   ├── docker-compose.backend.yml   # 分离部署：后端
│   ├── docker-compose.frontend.yml  # 分离部署：前端
│   ├── nginx/nginx.conf            # 网关配置
│   └── minio/init.sh               # MinIO 初始化
│
├── docs/
│   └── deployment.md            # 详细部署指南
│
├── README.md
├── DEVELOPMENT_PLAN.md          # 架构设计文档
├── start.bat                    # Windows 一键启动脚本
└── .gitignore
```

## API 速查

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录 |
| POST | `/api/recordings/upload` | 上传音频文件 |
| GET | `/api/recordings` | 录音列表 |
| GET | `/api/recordings/{id}` | 录音详情（含转写文本） |
| GET | `/api/recordings/{id}/audio` | 流式播放音频 |
| GET | `/api/recordings/{id}/summary` | 对话摘要（LLM 生成） |
| GET | `/api/recordings/{id}/qa` | 问答对提取 |
| DELETE | `/api/recordings/{id}` | 删除录音 |
| POST | `/api/asr/{id}/transcribe` | 触发离线转写 |
| GET | `/api/asr/{id}/status` | 查询转写状态 |
| WS | `/ws/asr/stream` | 实时流式转写 |
| POST | `/api/resumes/upload` | 上传简历 |
| GET | `/api/resumes` | 简历列表 |
| DELETE | `/api/resumes/{id}` | 删除简历 |
| POST | `/api/interview/analyze` | 开始面试分析 |
| GET | `/api/interview/reports` | 分析报告列表 |
| GET | `/api/interview/reports/{id}` | 报告详情 |
| GET | `/api/export/{id}/{format}` | 导出转写文本（txt/docx/pdf/srt） |
| GET | `/api/export/report/{id}/pdf` | 导出报告 PDF |
| PUT | `/api/recordings/transcripts/{id}` | 编辑转写文本 |

交互式 API 文档：http://localhost:8000/api/docs

## 环境变量

所有可用变量见 [backend/.env.example](backend/.env.example)。关键变量：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | 是 | 见 .env.example | PostgreSQL 连接（async 引擎） |
| `REDIS_URL` | 是 | `redis://localhost:6379/0` | Redis 连接 |
| `LLM_API_KEY` | **是** | — | LLM API Key |
| `LLM_BASE_URL` | 否 | `https://api.deepseek.com/v1` | OpenAI 兼容 API 地址 |
| `LLM_MODEL` | 否 | `deepseek-chat` | 模型名称 |
| `JWT_SECRET` | **是** | — | JWT 签名密钥 |
| `DEBUG` | 否 | `true` | 调试模式开关 |
| `CORS_ORIGINS` | 否 | `http://localhost:5173,...` | 允许的跨域来源 |

## ASR 引擎说明

项目使用 FunASR 自定义管道，将 VAD、说话人聚类、ASR 三个模型拆开独立控制：

- **VAD**：`iic/speech_fsmn_vad_zh-cn-16k-common-pytorch` — 语音活动检测
- **说话人聚类**：`iic/speech_eres2netv2_sv_zh-cn_16k-common` + 谱聚类 — ERes2NetV2 提取 192 维 embedding，自动确定说话人数
- **ASR**：`iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch` — 识别 + 标点恢复

支持通过 `ASR_BACKEND` 环境变量切换：`funasr`（默认）| `whisper`（需安装 faster-whisper）| `mock`（开发测试）

## 文档

- [部署指南](docs/deployment.md) — Docker 部署、前后端分离部署、故障排查
- [开发设计文档](DEVELOPMENT_PLAN.md) — 架构设计、数据库设计、API 规范、路线图
- [API 文档](http://localhost:8000/api/docs) — Swagger 交互式文档（需启动后端）

## License

[MIT](LICENSE)
