# Vannevar Bridge 部署指南

## 快速开始（Docker Compose）

> 以下命令均在**仓库根目录**执行（`.env.example` 与 `docker-compose.yml` 都在仓库根目录）。

### 1. 准备环境配置

```bash
cp .env.example .env
# 编辑 .env，修改管理员密码和 JWT 密钥
```

生成 JWT 密钥：
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> `.env` 中 `BRIDGE_ADMIN_PASS` 与 `BRIDGE_JWT_SECRET` **两项都必须填**：compose 文件用
> `${VAR:?提示}` 强制校验，缺任意一项 `docker-compose up` 会在启动前就报错退出。
> `BRIDGE_ADMIN_PASS` 的生成方式：`python -c "import secrets; print(secrets.token_urlsafe(16))"`。

### 2. 启动服务

```bash
docker-compose up -d
```

### 3. 验证服务

```bash
curl http://localhost:8000/health
# 期望返回：{"ok": true}
```

### 4. 访问管理后台

- 管理后台：http://localhost:8000/
- API 文档：http://localhost:8000/docs（仅 dev 模式）
- 默认凭据：admin / （.env 中设置的 `BRIDGE_ADMIN_PASS`）

---

## 手动部署（无 Docker）

### 1. 安装依赖

```bash
cd products/bridge-mvp
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
export BRIDGE_DEBUG=0
export BRIDGE_DATABASE_URL=sqlite:///data/bridge.db
export BRIDGE_ADMIN_PASS=your_secure_password
export BRIDGE_JWT_SECRET=your_jwt_secret
```

### 3. 启动服务

**方式 A：直接用 uvicorn（推荐用于生产，参数显式可控）**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

**方式 B：用 `main.py` 直接启动（读取环境变量）**

```bash
export BRIDGE_BIND_ADDR=0.0.0.0   # 监听地址，默认 127.0.0.1
export BRIDGE_PORT=8000            # 端口，默认 8000
python main.py
```

> `BRIDGE_BIND_ADDR` 与 `BRIDGE_PORT` 仅在方式 B（`python main.py`）下生效；方式 A 由 uvicorn 的 `--host` / `--port` 决定。详见下方「监听地址与端口」。

---

## 监听地址与端口

`BRIDGE_PORT` 与 `BRIDGE_BIND_ADDR` **只在 `python main.py` 直接启动时生效**，启动方式决定最终监听地址：

| 启动方式 | host / port 由谁决定 |
|---|---|
| `python main.py` | 环境变量 `BRIDGE_BIND_ADDR`（默认 `127.0.0.1`）、`BRIDGE_PORT`（默认 `8000`） |
| `uvicorn main:app --host ... --port ...` | **uvicorn 的命令行参数**；此时这两个环境变量被忽略 |
| `docker-compose up -d` | compose 的端口映射 `${BRIDGE_BIND_ADDR:-127.0.0.1}:${BRIDGE_PORT:-8000}:8000`，容器内固定监听 8000 |

> 原因见 `products/bridge-mvp/main.py` 的 `if __name__ == "__main__":` 分支：只有该分支读取这两个
> 环境变量并传给 `uvicorn.run(host=..., port=...)`。用 uvicorn 直接起进程时，`main.py` 的
> `__main__` 分支根本不会执行，环境变量自然不生效。
>
> `BRIDGE_PORT` 取值非法（非整数，或不在 1-65535）时，`python main.py` 会往 stderr 打印错误并以
> **退出码 2** 终止，不会带着非法端口继续跑。

---

## 扩展安装

### Focus（上下文管理）

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `products/focus-mvp` 目录

### Muse（灵感管理）

同上，选择 `products/muse-mvp` 目录。

---

## AI 配置（可选）

Bridge 支持可选的 AI 增强，未配置时使用规则引擎。

### OpenAI 兼容 API

```env
BRIDGE_AI_BASE_URL=https://api.openai.com/v1/chat/completions
BRIDGE_AI_API_KEY=sk-your-key
BRIDGE_AI_MODEL=gpt-4o-mini
```

### 自定义端点

任何兼容 OpenAI Chat Completions API 的服务均可：
```env
BRIDGE_AI_BASE_URL=http://localhost:11434/v1/chat/completions
BRIDGE_AI_API_KEY=ollama
BRIDGE_AI_MODEL=llama3
```

---

## 数据备份

### 备份工具

备份逻辑在 `products/bridge-mvp/backup.py`，对外接口如下：

| 函数 | 签名 | 行为 |
|---|---|---|
| `backup_db()` | **无参数** | 把 `DB_PATH`（默认 `products/bridge-mvp/bridge_mvp.db`）备份到**与数据库文件同级的 `backups/` 目录**，文件名 `bridge_mvp_YYYYMMDD_HHMMSS.db`，返回备份文件路径；失败返回 `None` |
| `list_backups()` | 无参数 | 列出 `backups/` 下的可用备份 |
| `restore_db(name)` | 备份文件名 | 从指定备份恢复 |

> ⚠️ `database.py` 里**没有** `backup_database` 这个函数。早期文档里的
> `from database import backup_database` 会直接 `ImportError`，请改用上面的 `backup.py`。

### SQLite 备份

**本地 / 源码部署**（`python main.py` 启动）：

```bash
cd products/bridge-mvp
python -c "from backup import backup_db; print(backup_db())"
```

> - `backup_db()` **不接收参数**，备份目录固定为数据库文件同级的 `backups/`，并只保留最近
>   `BRIDGE_MAX_BACKUPS`（默认 7）份。
> - **数据库文件不存在时会直接跳过并返回 `None`**（不会创建空库，也不会抛错）。
>   首次启动、库文件还没生成时跑这条命令是空操作。

**Docker 部署**：

容器内 `BRIDGE_DATABASE_URL=sqlite:///data/bridge.db`，真正在用的数据库是 `/app/data/bridge.db`；
而 `backup_db()` 只认 `database.py` 的 `DB_PATH`（镜像内为 `/app/bridge_mvp.db`，而 `.dockerignore`
已排除 `*.db`，该文件在容器中并不存在）。**所以在容器里直接调用 `backup_db()` 是空操作**，
要备份容器真正在用的库，请对实际库文件执行 SQLite 在线备份：

```bash
docker exec vannevar-bridge python -c "import os,sqlite3; os.makedirs('/app/data/backups',exist_ok=True); s=sqlite3.connect('/app/data/bridge.db'); d=sqlite3.connect('/app/data/backups/bridge-backup.db'); s.backup(d); d.close(); s.close()"
```

> `/app/data` 是 compose 声明的数据卷（可写），备份目录由上面的 `os.makedirs` 自动创建。
> 该命令走 SQLite 的在线备份 API，WAL 模式下也不会拷到不一致的半截文件。

### 数据卷备份

```bash
docker run --rm -v vannevar_bridge-data:/data -v $(pwd):/backup \
    alpine tar czf /backup/bridge-backup-$(date +%Y%m%d).tar.gz /data
```

### 自动化备份

建议通过宿主机 cron 定时执行数据库备份，防止单点数据丢失。
用 `cat` + heredoc 写 cron 文件，避免 `echo` 与命令内引号互相打架：

```bash
cat > /tmp/bridge-backup.cron <<'CRON'
0 3 * * * docker exec vannevar-bridge python -c "import os,sqlite3; os.makedirs('/app/data/backups',exist_ok=True); s=sqlite3.connect('/app/data/bridge.db'); d=sqlite3.connect('/app/data/backups/bridge-backup.db'); s.backup(d); d.close(); s.close()"
CRON
crontab /tmp/bridge-backup.cron
```

若不需要容器内的一致性备份、只想把整个数据卷归档，可改用下面这条（配合上面的「数据卷备份」）：

```bash
cat > /tmp/bridge-volume-backup.cron <<'CRON'
30 3 * * * docker run --rm -v vannevar_bridge-data:/data -v /backups:/backup alpine tar czf /backup/bridge-$(date +\%Y\%m\%d).tar.gz /data
CRON
crontab /tmp/bridge-volume-backup.cron
```

> 注意：cron 表达式中 `%` 需转义为 `\%`（见上面第二条）。备份文件应定期清理旧版本或上传到异地存储。
> 两条 cron 择一使用即可，`crontab <文件>` 会**覆盖**已有的 crontab。

---

## 监控

### 健康检查

```bash
curl http://localhost:8000/health
```

### 日志查看

```bash
docker-compose logs -f bridge
```

### 性能指标

Bridge 内置性能测试：
```bash
cd products/bridge-mvp
py -m pytest tests/test_performance.py -v
```

### 扩展端测试说明（V17 限制）

> **V17 限制**：Focus / Muse 扩展端核心模块（sync.js、auth.js、auto-sync.js、local-model.js 等）当前无自动化单元测试。
> 后端 Bridge 的测试位于 `products/bridge-mvp/tests/`，需安装 `requirements-dev.txt` 后运行。
> 扩展端测试待后续引入 Jest / Vitest 测试框架后补充，改动较大不在本次修复范围。

---

## 升级

### Docker 升级

```bash
git pull
docker-compose down
docker-compose build
docker-compose up -d
```

### 数据库迁移

Bridge 支持自动迁移，启动时自动执行：
```bash
docker-compose logs bridge | grep "migration"
```

---

## 安全建议

1. **修改默认密码**：首次启动后立即修改 admin 密码
2. **设置 JWT 密钥**：生产环境必须设置 `BRIDGE_JWT_SECRET`
3. **关闭 Debug 模式**：`BRIDGE_DEBUG=0`
4. **使用 HTTPS**：通过 Nginx 反向代理配置 SSL（见下方配置示例）
5. **定期备份**：至少每周备份一次数据
6. **限制网络访问**：仅允许信任网络访问 8000 端口

---

## Nginx 反向代理配置（HTTPS）

生产环境推荐通过 Nginx 反向代理提供 HTTPS：

```nginx
server {
    listen 443 ssl http2;
    server_name bridge.example.com;

    ssl_certificate /etc/ssl/certs/bridge.crt;
    ssl_certificate_key /etc/ssl/private/bridge.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    # 安全头
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;

    # 代理到 Bridge
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式响应支持
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}

# HTTP 重定向到 HTTPS
server {
    listen 80;
    server_name bridge.example.com;
    return 301 https://$host$request_uri;
}
```

### Nginx 配置要点

- `proxy_buffering off`：SSE 流式响应必须关闭缓冲
- `proxy_read_timeout 300s`：长连接超时 5 分钟
- `ssl_protocols TLSv1.2 TLSv1.3`：禁用不安全的旧协议
- `X-Frame-Options DENY`：防止点击劫持
