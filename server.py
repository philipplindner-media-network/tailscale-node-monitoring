import sqlite3
import json
import datetime
from datetime import timezone
import asyncio
from typing import List, Optional
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import httpx

app = FastAPI(title="Tailscale Dynamic Master Dashboard")
templates = Jinja2Templates(directory="templates")

DB_FILE = "/app/data/monitoring.db"

# --- DEINE KONFIGURATION ---
API_SECRET = "xxxx"
TELEGRAM_BOT_TOKEN = "DEIN_TELEGRAM"
TELEGRAM_CHAT_ID = "DEIN_TELEGRAM"
OFFLINE_THRESHOLD_MINUTES = 2
RETENTION_DAYS = 120  # Löschedatum für alte Logs (120 Tage = ~4 Monate)

alerted_nodes = set()

def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or "DEIN_TELEGRAM" in TELEGRAM_BOT_TOKEN:
        print(f"[Telegram Mock] {message}")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        httpx.post(url, json=payload, timeout=5.0)
    except Exception as e:
        print(f"[!] Telegram-Fehler: {e}")

async def check_offline_agents_loop():
    while True:
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT node_name, display_name, last_seen, tailscale_ip FROM nodes")
                nodes = cursor.fetchall()

                now = datetime.datetime.now(timezone.utc)
                for node in nodes:
                    node_name = node["node_name"]
                    display_name = node["display_name"] or node_name
                    last_seen_str = node["last_seen"]

                    if not last_seen_str:
                        continue

                    if not last_seen_str.endswith('Z') and '+' not in last_seen_str:
                        last_seen_str_parsed = last_seen_str + 'Z'
                    else:
                        last_seen_str_parsed = last_seen_str

                    last_seen = datetime.datetime.fromisoformat(last_seen_str_parsed.replace('Z', '+00:00'))
                    minutes_since_seen = (now - last_seen).total_seconds() / 60

                    if minutes_since_seen > OFFLINE_THRESHOLD_MINUTES:
                        if node_name not in alerted_nodes:
                            msg = f"⚠️ *MONITORING ALARM*\n\nServer *{display_name}* ({node['tailscale_ip']}) antwortet nicht mehr!\nZuletzt gesehen: {last_seen_str}"
                            send_telegram_alert(msg)
                            alerted_nodes.add(node_name)
                    else:
                        if node_name in alerted_nodes:
                            msg = f"✅ *MONITORING ENTWARNUNG*\n\nServer *{display_name}* ist wieder erreichbar."
                            send_telegram_alert(msg)
                            alerted_nodes.remove(node_name)
        except Exception as e:
            print(f"[!] Fehler im Alert Loop: {e}")

        await asyncio.sleep(30)

async def cleanup_old_data_loop():
    """Löscht täglich alte Metriken und Pings (älter als RETENTION_DAYS) zur Speicherplatzersparnis."""
    while True:
        try:
            cutoff_date = (datetime.datetime.now(timezone.utc) - datetime.timedelta(days=RETENTION_DAYS)).isoformat()
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM system_metrics WHERE timestamp < ?", (cutoff_date,))
                deleted_metrics = cursor.rowcount
                cursor.execute("DELETE FROM ping_history WHERE timestamp < ?", (cutoff_date,))
                deleted_pings = cursor.rowcount
                conn.commit()
                
                # Führt Speicherplatzfreigabe aus, falls Daten gelöscht wurden
                if deleted_metrics > 0 or deleted_pings > 0:
                    cursor.execute("VACUUM")
                    print(f"[DB Cleanup] Gelöscht: {deleted_metrics} Metriken, {deleted_pings} Ping-Records (älter als {RETENTION_DAYS} Tage). DB komprimiert.")
        except Exception as e:
            print(f"[!] Fehler beim DB Cleanup: {e}")
            
        # Prüft einmal alle 24 Stunden (86400 Sekunden)
        await asyncio.sleep(86400)

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                node_name TEXT PRIMARY KEY,
                display_name TEXT,
                tailscale_ip TEXT,
                public_ip TEXT,
                targets_to_ping TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP,
                last_connection_type TEXT
            )
        """)
        # Migrationen für bestehende Tabellen
        try:
            cursor.execute("ALTER TABLE nodes ADD COLUMN display_name TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            cursor.execute("ALTER TABLE nodes ADD COLUMN last_connection_type TEXT")
        except sqlite3.OperationalError:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_name TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                cpu_usage REAL, ram_usage REAL, disk_usage REAL, uptime_seconds INTEGER,
                FOREIGN KEY(node_name) REFERENCES nodes(node_name)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ping_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_name TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                target TEXT, alive INTEGER, latency_ms REAL,
                FOREIGN KEY(node_name) REFERENCES nodes(node_name)
            )
        """)
        conn.commit()

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(check_offline_agents_loop())
    asyncio.create_task(cleanup_old_data_loop())

class RegisterRequest(BaseModel):
    node_name: str
    tailscale_ip: Optional[str] = None
    public_ip: Optional[str] = None

class PingResult(BaseModel):
    target: str
    alive: bool
    latency_ms: Optional[float] = None

class SystemMetrics(BaseModel):
    cpu_usage: float
    ram_usage: float
    disk_usage: float
    uptime_seconds: int

class ReportPayload(BaseModel):
    node_name: str
    metrics: SystemMetrics
    neighbors: List[PingResult]

class TargetUpdatePayload(BaseModel):
    node_name: str
    display_name: Optional[str] = None
    targets_to_ping: List[str]

class AddNodePayload(BaseModel):
    node_name: str
    display_name: Optional[str] = None
    targets_to_ping: List[str]

@app.post("/api/register")
async def register_agent(payload: RegisterRequest, x_api_secret: Optional[str] = Header(None)):
    if x_api_secret != API_SECRET:
        raise HTTPException(status_code=403, detail="Ungültiges API-Secret")
    
    default_targets = ["1.1.1.1", "8.8.8.8"]
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT targets_to_ping FROM nodes WHERE node_name = ?", (payload.node_name,))
        row = cursor.fetchone()

        if row is None:
            targets_json = json.dumps(default_targets)
            cursor.execute("""
                INSERT INTO nodes (node_name, display_name, tailscale_ip, public_ip, targets_to_ping, last_seen)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (payload.node_name, payload.node_name, payload.tailscale_ip, payload.public_ip, targets_json))
            assigned_targets = default_targets
        else:
            cursor.execute("""
                UPDATE nodes 
                SET tailscale_ip = ?, public_ip = ?, last_seen = CURRENT_TIMESTAMP
                WHERE node_name = ?
            """, (payload.tailscale_ip, payload.public_ip, payload.node_name))
            assigned_targets = json.loads(row[0]) if row[0] else default_targets

        conn.commit()

    return {
        "status": "registered",
        "config": {
            "interval": 30,
            "targets_to_ping": assigned_targets
        }
    }

@app.post("/api/report")
async def receive_report(request: Request, payload: ReportPayload, x_api_secret: Optional[str] = Header(None)):
    if x_api_secret != API_SECRET:
        raise HTTPException(status_code=403, detail="Ungültiges API-Secret")
    
    now = datetime.datetime.now(timezone.utc).isoformat()
    
    # Ermitteln, über welche IP-Adresse der Agent angeklopft hat
    client_host = request.client.host if request.client else ""
    if client_host.startswith("100."):
        conn_type = "Tailscale"
    else:
        conn_type = "Public IP"
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT targets_to_ping FROM nodes WHERE node_name = ?", (payload.node_name,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Node nicht registriert.")

        assigned_targets = json.loads(row[0]) if row[0] else []
        
        # last_seen und last_connection_type in der DB aktualisieren
        cursor.execute("""
            UPDATE nodes 
            SET last_seen = ?, last_connection_type = ? 
            WHERE node_name = ?
        """, (now, conn_type, payload.node_name))

        m = payload.metrics
        cursor.execute("""
            INSERT INTO system_metrics (node_name, timestamp, cpu_usage, ram_usage, disk_usage, uptime_seconds)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (payload.node_name, now, m.cpu_usage, m.ram_usage, m.disk_usage, m.uptime_seconds))

        for p in payload.neighbors:
            cursor.execute("""
                INSERT INTO ping_history (node_name, timestamp, target, alive, latency_ms)
                VALUES (?, ?, ?, ?, ?)
            """, (payload.node_name, now, p.target, 1 if p.alive else 0, p.latency_ms))

        conn.commit()

    return {
        "status": "ok",
        "config": {
            "interval": 30,
            "targets_to_ping": assigned_targets
        }
    }

@app.post("/api/config/set_targets")
async def set_agent_targets(payload: TargetUpdatePayload):
    targets_json = json.dumps(payload.targets_to_ping)
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE nodes SET targets_to_ping = ?, display_name = ? WHERE node_name = ?", 
                       (targets_json, payload.display_name or payload.node_name, payload.node_name))
        conn.commit()
    return {"status": "updated"}

@app.post("/api/nodes/add")
async def add_node(payload: AddNodePayload):
    targets_json = json.dumps(payload.targets_to_ping)
    display_name = payload.display_name or payload.node_name
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO nodes (node_name, display_name, targets_to_ping)
            VALUES (?, ?, ?)
        """, (payload.node_name, display_name, targets_json))
        conn.commit()
    return {"status": "created"}

@app.delete("/api/nodes/{node_name}")
async def delete_node(node_name: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM system_metrics WHERE node_name = ?", (node_name,))
        cursor.execute("DELETE FROM ping_history WHERE node_name = ?", (node_name,))
        cursor.execute("DELETE FROM nodes WHERE node_name = ?", (node_name,))
        conn.commit()
    return {"status": "deleted"}

@app.get("/api/awtrixng_all_server_status")
async def get_awtrix_status():
    """Endpunkt für Awtrix Clock / Smart Display Integration (32x8-optimiert)."""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT last_seen FROM nodes")
        nodes = cursor.fetchall()

    total_nodes = len(nodes)
    offline_nodes = 0
    now = datetime.datetime.now(timezone.utc)

    for node in nodes:
        last_seen_str = node["last_seen"]
        if not last_seen_str:
            offline_nodes += 1
            continue

        if not last_seen_str.endswith('Z') and '+' not in last_seen_str:
            last_seen_str_parsed = last_seen_str + 'Z'
        else:
            last_seen_str_parsed = last_seen_str

        last_seen = datetime.datetime.fromisoformat(last_seen_str_parsed.replace('Z', '+00:00'))
        if (now - last_seen).total_seconds() / 60 > OFFLINE_THRESHOLD_MINUTES:
            offline_nodes += 1

    online_nodes = total_nodes - offline_nodes

    if offline_nodes == 0:
        return {
            "text": f"{total_nodes} OK",        # Extrem kurz: "4 OK"
            "color": "#00FF00",
            "icon": 82,
            "all_online": True,
            "offline_count": 0,
            "total_count": total_nodes
        }
    else:
        return {
            "text": f"{offline_nodes} ERR",      # Extrem kurz: "1 ERR"
            "color": "#FF0000",
            "icon": 1057,
            "all_online": False,
            "offline_count": offline_nodes,
            "total_count": total_nodes
        }

@app.get("/api/nodes")
async def list_nodes():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT node_name, display_name, tailscale_ip, public_ip, targets_to_ping, last_seen, last_connection_type FROM nodes")
        nodes = [dict(row) for row in cursor.fetchall()]

        for n in nodes:
            n["targets_to_ping"] = json.loads(n["targets_to_ping"]) if n["targets_to_ping"] else []

            cursor.execute("""
                SELECT cpu_usage, ram_usage, disk_usage, uptime_seconds 
                FROM system_metrics WHERE node_name = ? ORDER BY id DESC LIMIT 1
            """, (n["node_name"],))
            m_row = cursor.fetchone()
            n["latest_metrics"] = dict(m_row) if m_row else None

            if n["targets_to_ping"]:
                pings = []
                for target in n["targets_to_ping"]:
                    cursor.execute("""
                        SELECT target, alive, latency_ms FROM ping_history 
                        WHERE node_name = ? AND target = ? ORDER BY id DESC LIMIT 1
                    """, (n["node_name"], target))
                    p_row = cursor.fetchone()
                    if p_row:
                        pings.append(dict(p_row))
                n["latest_pings"] = pings
            else:
                n["latest_pings"] = []

    return nodes

@app.get("/api/history/{node_name}")
async def get_history(node_name: str, limit: int = 30):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT timestamp, cpu_usage, ram_usage, disk_usage 
            FROM system_metrics WHERE node_name = ? ORDER BY id DESC LIMIT ?
        """, (node_name, limit))
        metrics = [dict(row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT timestamp, target, alive, latency_ms 
            FROM ping_history WHERE node_name = ? ORDER BY id DESC LIMIT ?
        """, (node_name, limit * 5))
        pings = [dict(row) for row in cursor.fetchall()]

    return {"metrics": list(reversed(metrics)), "pings": list(reversed(pings))}

@app.get("/setup-agent.sh")
async def download_setup_script():
    """Erlaubt das Herunterladen des Agenten-Skripts via curl/wget."""
    return FileResponse("/app/setup-agent.sh", filename="setup-agent.sh", media_type="text/x-shellscript")

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")
