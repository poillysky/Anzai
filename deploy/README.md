# 安崽 NAS — 单镜像 poillysky/anzai:1.0.1（API+Web）

路径：`/vol1/1000/Docker/anzai`，`restart: always`。

## 导出本机数据（文件夹，无压缩）

```powershell
.\scripts\pack-nas-data.ps1
```

得到 `deploy/dist/anzai/` → 整目录拷到 `/vol1/1000/Docker/anzai`。

## 启动

```bash
cd /vol1/1000/Docker/anzai
docker compose pull
docker compose up -d
# PWA
curl -sI http://127.0.0.1:3515/
# API / admin
curl -s http://127.0.0.1:8515/health
```

端口：`3515` 前端，`8515` 后端（含 `/admin`）。前端 `/backend` 在容器内转到本机 8515。
