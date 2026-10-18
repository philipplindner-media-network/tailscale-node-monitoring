#!/bin/bash
set -e

PRIMARY_SERVER_URL="http://xxx.xxx.xxx.xxx:8090"
FALLBACK_SERVER_URL="http://xxx.xxx.xxxx.xxx:8090"
API_SECRET="xxx"
INSTALL_DIR="/opt/tailscale-agent"

echo "=== 1. Systempakete & Tailscale prüfen/installieren ==="
if ! command -v tailscale &> /dev/null; then
    echo "[+] Installiere Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    echo "[!] Bitte verbinde Tailscale jetzt mit 'sudo tailscale up'"
    sudo tailscale up
fi

sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv iputils-ping

echo "=== 2. Projektordner & Python Venv einrichten ==="
sudo mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install psutil httpx python-dotenv

echo "=== 3. Konfigurationsdatei erstellen ==="
cat <<EOF | sudo tee config.env
PRIMARY_SERVER_URL=$PRIMARY_SERVER_URL
FALLBACK_SERVER_URL=$FALLBACK_SERVER_URL
API_SECRET=$API_SECRET
EOF

echo "=== 4. Agent Skript mit IP-Fallback erstellen ==="
cat <<'EOF' | sudo tee agent.py
import os
import time
import subprocess
import platform
import psutil
import httpx
from dotenv import load_dotenv

load_dotenv("/opt/tailscale-agent/config.env")

PRIMARY_SERVER_URL = os.getenv("PRIMARY_SERVER_URL", "http://100.106.160.23:8090")
FALLBACK_SERVER_URL = os.getenv("FALLBACK_SERVER_URL", "http://78.46.80.46:8090")
API_SECRET = os.getenv("API_SECRET", "ServerMonitorTA2026")
NODE_NAME = platform.node()

def get_tailscale_ip() -> str:
    try:
        res = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return "N/A"

def get_public_ip() -> str:
    try:
        with httpx.Client(timeout=3.0) as client:
            res = client.get("https://api.ipify.org")
            if res.status_code == 200:
                return res.text.strip()
    except Exception:
        pass
    return "N/A"

def send_request_with_fallback(client: httpx.Client, endpoint: str, payload: dict, headers: dict) -> httpx.Response:
    """Versucht zuerst Tailscale IP, bei Fehler Fallback auf öffentliche IP."""
    # 1. Versuch: Tailscale IP
    try:
        url = f"{PRIMARY_SERVER_URL}{endpoint}"
        res = client.post(url, json=payload, headers=headers)
        if res.status_code == 200:
            return res
    except Exception as e:
        print(f"[!] Primary Tailscale URL ({PRIMARY_SERVER_URL}) nicht erreichbar: {e}")

    # 2. Versuch: Öffentliche IP (Fallback)
    print(f"[*] Weiche auf Fallback-URL ({FALLBACK_SERVER_URL}) aus...")
    url = f"{FALLBACK_SERVER_URL}{endpoint}"
    res = client.post(url, json=payload, headers=headers)
    return res

def ping_host(host: str) -> dict:
    param = "-n" if platform.system().lower() == "windows" else "-c"
    command = ["ping", param, "1", "-W", "2", host]
    
    start_time = time.time()
    res = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    latency_ms = round((time.time() - start_time) * 1000, 2)
    
    return {
        "target": host,
        "alive": res.returncode == 0,
        "latency_ms": latency_ms if res.returncode == 0 else None
    }

def collect_metrics() -> dict:
    return {
        "cpu_usage": psutil.cpu_percent(interval=1),
        "ram_usage": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent,
        "uptime_seconds": int(time.time() - psutil.boot_time())
    }

def register_and_fetch_config(client: httpx.Client, headers: dict) -> dict:
    payload = {
        "node_name": NODE_NAME,
        "tailscale_ip": get_tailscale_ip(),
        "public_ip": get_public_ip()
    }
    
    response = send_request_with_fallback(client, "/api/register", payload, headers)
    response.raise_for_status()
    data = response.json()
    print(f"[+] Registrierung erfolgreich. Config: {data['config']}")
    return data["config"]

def main():
    headers = {"X-API-Secret": API_SECRET}
    
    with httpx.Client(timeout=5.0) as client:
        config = None
        while not config:
            try:
                print("[*] Verbinde zum Hauptserver für Erstkontakt...")
                config = register_and_fetch_config(client, headers)
            except Exception as e:
                print(f"[!] Erstkontakt fehlgeschlagen ({e}). Erneuter Versuch in 10s...")
                time.sleep(10)

        targets_to_ping = config.get("targets_to_ping", [])
        interval = config.get("interval", 30)

        while True:
            metrics = collect_metrics()
            ping_results = [ping_host(target) for target in targets_to_ping]
            
            report = {
                "node_name": NODE_NAME,
                "metrics": metrics,
                "neighbors": ping_results
            }
            
            try:
                res = send_request_with_fallback(client, "/api/report", report, headers)
                if res.status_code == 200:
                    data = res.json()
                    if "config" in data:
                        targets_to_ping = data["config"].get("targets_to_ping", targets_to_ping)
                        interval = data["config"].get("interval", interval)
                    print(f"[{time.strftime('%H:%M:%S')}] Bericht erfolgreich gesendet.")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] Server-Antwort: {res.status_code}")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] Fehler beim Senden (beide IPs fehlgeschlagen): {e}")
                
            time.sleep(interval)

if __name__ == "__main__":
    main()
EOF

echo "=== 5. Systemd Service einrichten & starten ==="
cat <<EOF | sudo tee /etc/systemd/system/tailscale-agent.service
[Unit]
Description=Tailscale Monitoring Agent
After=network.target tailscaled.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now tailscale-agent

echo "=== ✅ Fertig! Der Agent läuft jetzt im Hintergrund mit IP-Fallback. ==="
