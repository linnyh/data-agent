# 数枢 DataPivot — 多阶段构建:前端构建 → Python 运行时(单容器,前端+API 同源)

# ---------- 阶段 1:前端构建 ----------
FROM node:22-alpine AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---------- 阶段 2:Python 运行时 ----------
FROM python:3.12-slim

# uv(锁文件安装)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 依赖层(利用构建缓存:lock 不变则不重装)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 源码 + 前端产物
COPY src/ ./src/
COPY --from=web /app/web/dist ./web/dist

# 数据目录(挂 volume 持久化会话/checkpoint/用户库)
ENV DATA_AGENT_DATA_DIR=/app/data
ENV DATA_AGENT_PORT=8000
EXPOSE 8000

# 模型配置通过容器环境变量注入:
#   MODEL_API_URL / MODEL_API_KEY / MODEL_NAME
CMD ["uv", "run", "python", "-m", "data_agent.infrastructure.main"]
