# 安崽ETF

个人 A 股 / 场内 ETF 分析助手：iOS 全屏 PWA + FastAPI 后端 + Agent（后续）。

## 结构

```
apps/web   Next.js PWA 前端（features / layout / lib/api）
apps/api   FastAPI 后端（core / database / api/routes / providers）
docs/      需求、iOS 全屏、架构、交互规范
scripts/   本地一键启动
```

分层说明见 [docs/架构.md](docs/架构.md)。

## 本地启动

### 一键

```powershell
.\scripts\dev.ps1
```

### 1. 后端

```powershell
cd apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8515
```

API 文档：http://127.0.0.1:8515/docs  
管理后台（配置 LLM / 密码 / 行情源等，不走 PWA）：http://127.0.0.1:8515/admin

### 2. 前端

```powershell
cd apps/web
npm install
copy .env.example .env.local
npm run dev
```

打开：http://127.0.0.1:3515（本机）或 `http://<电脑局域网IP>:3515`

端口约定：前端 **3515**，后端 **8515**（均监听 `0.0.0.0`）。根目录也可：`npm run dev:web` / `npm run dev:api`。

### 反代

公网入口：`https://anzai.605081.xyz:16666`  
反代请指向本机前端 `http://127.0.0.1:3515`（API 走前端 `/backend` 同源代理到 8515）。

### iOS 全屏

用 iPhone Safari 打开反代地址或局域网地址 → 分享 → 添加到主屏幕。  
设计说明见 [docs/iOS全屏设计.md](docs/iOS全屏设计.md)。

## 说明

分析与建议仅供个人参考，不构成投资建议。行情可能延迟或使用降级占位价。
