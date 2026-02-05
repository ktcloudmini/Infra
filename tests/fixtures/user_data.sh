#!/bin/bash
set -ex

# 1. 패키지 설치 
apt-get update -y
apt-get install -y python3 python3-pip stress python3-flask

# 2. 작업 디렉토리 설정
mkdir -p /home/ubuntu/app
cd /home/ubuntu/app

# 3. Flask App 작성
cat > app.py <<'EOF'
from flask import Flask, request
import socket
import time
import math
import subprocess
import sys

app = Flask(__name__)

is_alive = True

@app.route("/")
def index():
    # 좀비 모드면 사용자에게 500 에러 반환
    if not is_alive:
        return f"CRITICAL ERROR: Service on {socket.gethostname()} is BROKEN!\n", 500
    return f"Hello! Host: {socket.gethostname()}\n", 200

@app.route("/health")
def health():
    # 좀비 모드면 ALB에게 500 에러 반환 (Unhealthy)
    if not is_alive:
        return "Unhealthy (Zombie Mode)", 500
    return "OK", 200

# CPU load - prevent webserver stop by subprocess
def spawn_cpu_burn(seconds):
    code = (
        "import time, math;"
        f"end = time.time() + {seconds};"
        "x = 0.0;"
        "while time.time() < end: x = math.sqrt(x + 1.2345)"
    )
    subprocess.Popen([sys.executable, "-c", code])

@app.route("/work")
def work():
    sec = int(request.args.get("sec", 5))
    spawn_cpu_burn(sec)
    return f"CPU burning started for {sec} seconds!\n", 200

@app.route("/kill")
def kill():
    global is_alive
    # 프로세스를 끄지 않고 상태 변수만 변경
    is_alive = False
    return f"Instance {socket.gethostname()} entering ZOMBIE mode.\n", 200

@app.route("/revive")
def revive():
    global is_alive
    # 테스트 초기화용 (다시 살리기)
    is_alive = True
    return f"Instance {socket.gethostname()} revived!\n", 200

if __name__ == "__main__":
    # threaded=True: 요청마다 쓰레드를 생성하므로 Locust 동시 접속 처리 가능
    app.run(host="0.0.0.0", port=8080, threaded=True)
EOF

# 권한 변경
chown -R ubuntu:ubuntu /home/ubuntu/app

# 5. Systemd 서비스 등록 (실행 명령어가 python으로 변경됨)
cat > /etc/systemd/system/miniapp.service <<'SERVICE'
[Unit]
Description=Mini Web App
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/app
ExecStart=/usr/bin/python3 app.py
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

# 서비스 시작
systemctl daemon-reload
systemctl enable --now miniapp.service