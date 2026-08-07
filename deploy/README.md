# Anzai NAS deploy

Single image: poillysky/anzai:1.0.2 (API + Web)

Path: /vol1/1000/Docker/anzai
Restart policy: always

## Export local data (folder, no zip)

`powershell
.\scripts\pack-nas-data.ps1
`

Copy deploy/dist/anzai/ to /vol1/1000/Docker/anzai.

## Start

`ash
cd /vol1/1000/Docker/anzai
docker compose pull
docker compose up -d
curl -sI http://127.0.0.1:3515/
curl -s http://127.0.0.1:8515/health
`

Ports: 3515 PWA, 8515 API/admin. Frontend /backend proxies to in-container 8515.

Do not use compose env_file for .env - mount the file only so admin knowledge DB saves stick.
