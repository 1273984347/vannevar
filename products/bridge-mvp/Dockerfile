# Bridge MVP Dockerfile — 生产部署镜像
# 用法：docker build -t vannevar-bridge ./products/bridge-mvp
# P0-1 修复：使用非特权用户运行容器，避免 root 权限风险
# P1-4 修复：固定 Python patch 版本，避免浮动标签不兼容
# V7 修复：多阶段构建，编译工具（gcc/libffi-dev）不进入运行时镜像

# ===== Stage 1: builder（安装编译工具 + 编译依赖）=====
FROM python:3.12.10-slim AS builder

WORKDIR /app

# 编译依赖（bcrypt / cffi 需要 gcc + libffi-dev），仅在此阶段存在
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .
# 安装到 /install 前缀，便于 Stage 2 精确复制
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ===== Stage 2: runtime（精简运行时镜像，无编译工具）=====
FROM python:3.12.10-slim

WORKDIR /app

# 复制编译好的 Python 依赖（site-packages + bin）
COPY --from=builder /install /usr/local

# 运行时系统依赖：curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制应用代码
COPY . .

# 创建数据目录 + 非特权用户
RUN mkdir -p /app/data \
    && useradd -r -u 1000 -d /app -s /sbin/nologin bridge \
    && chown -R bridge:bridge /app

# 生产环境配置
ENV BRIDGE_DEBUG=0
ENV BRIDGE_DATABASE_URL=sqlite:///data/bridge.db
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# 健康检查（用 curl 替代 python -c，更轻量）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 切换非特权用户
USER bridge

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
