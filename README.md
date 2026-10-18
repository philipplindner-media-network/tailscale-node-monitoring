# 🌐 Tailscale Node Monitoring & Awtrix NG Integration (v2.10.3)

Ein leichtgewichtiges, selbst gehostetes Monitoring-System für Tailscale-Netzwerke mit automatischer Verbindungsweg-Erkennung (Tailscale-IP vs. öffentliche IP), Telegram-Alarmierung, SQLite-Historie und Awtrix-NG-LED-Matrix-Integration.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-brightgreen.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)
![Awtrix NG](https://img.shields.io/badge/Awtrix_NG-Berry-orange.svg)

---

## ✨ Features

- 📊 Dynamic Master Dashboard: FastAPI-basiertes Web-Dashboard mit Live-Metriken (CPU, RAM, Disk, Uptime) und Ping-Ergebnissen.
- 🔀 Smart Route Tracking: Erkennt automatisch, ob ein Agent über die Tailscale-IP oder die öffentliche IP antwortet.
- 📉 Chart.js-Historie: Interaktive Verlaufsdiagramme für Systemauslastung und Latenzen.
- ⌚ Awtrix NG Integration: Benutzerdefiniertes Berry-Skript für 32x8-LED-Matrizen (`4 OK` / `1 ERR`).
- 🔔 Telegram Alerts: Benachrichtigungen bei Serverausfällen und Entwarnungen.
- 🧹 Automatischer DB-Cleanup: Löscht historische Daten nach 120 Tagen und führt bei Bedarf ein `VACUUM` auf der SQLite-Datenbank aus.

---

## 🛠️ System-Architektur

```text
┌──────────────────┐      Report (API)     ┌───────────────────────┐
│ Server Agent     │ ────────────────────> │ Monitoring Master     │
│ (Node 1..N)      │ Tailscale / Public IP │ (FastAPI + SQLite)     │
└──────────────────┘                       └───────────┬───────────┘
                                                        │
                                    ┌───────────────────┼───────────────────┐
                                    │                   │                   │
                                    ▼                   ▼                   ▼
                             ┌──────────────┐   ┌──────────────┐   ┌─────────────────┐
                             │ Web Dashboard│   │ Telegram Bot │   │ Awtrix NG Clock │
                             │ (Chart.js)   │   │ Alerts       │   │ (Berry App)     │
                             └──────────────┘   └──────────────┘   └─────────────────┘
```

---

## 🚀 Schnellstart

### 1. Master-Server aufsetzen (Docker Compose)

Repository klonen und den Master-Server starten:

```bash
git clone https://github.com/philipplindner-media-network/tailscale-node-monitoring.git
cd tailscale-node-monitoring
docker compose up -d
```

Das Dashboard ist anschließend unter `http://<SERVER-IP>:8090` erreichbar.

### 2. Agent auf Zielservern installieren

Auf jedem zu überwachenden Server kann der Agent über das Setup-Skript eingerichtet werden:

```bash
curl -sSL http://<MASTER-IP>:8090/setup-agent.sh | bash
```

### 3. Awtrix NG App installieren (Berry Script)

Öffne die Weboberfläche deiner Awtrix-NG-/Tasmota-Uhr.

Navigiere zu `Filesystem / Console` und erstelle eine neue Berry-App namens `serverMonitor.be`.

Füge den folgenden Code ein und passe die Server-IP an:

```berry
# @desc Zeigt den Status aller Monitoring-Server an (32x8 Display)

class ServerMonitorApp
  var status_text
  var text_color
  var timer

  def init()
    self.status_text = "..."
    self.text_color = 0x00A0FF
    self.timer = 0
    print("[Awtrix-Monitor] Starte App...")
    self.update_data()
  end

  def update_data()
    http.get("http://<MASTER-IP>:8090/api/awtrixng_all_server_status", / res, err -> self.on_http_response(res, err))
  end

  def on_http_response(res, err)
    if err == 200 || err == 0 || err == nil
      if res != nil
        var data = json.load(res)
        if data != nil
          if data.contains("text")
            self.status_text = data["text"]
          else
            self.status_text = "ERR"
          end

          if data.contains("all_online") && data["all_online"] == true
            self.text_color = 0x00FF00  # Grün
          else
            self.text_color = 0xFF0000  # Rot
          end
          return
        end
      end
    end

    self.status_text = "N/C"
    self.text_color = 0xFF9900  # Orange
  end

  def draw()
    clear()
    text(1, 6, self.status_text, self.text_color)
  end

  def loop()
    self.timer = self.timer + 1
    if self.timer >= 300
      self.timer = 0
      self.update_data()
    end
  end
end

return ServerMonitorApp()
```

---

## 🔒 Konfiguration & Sicherheit

Es wird empfohlen, sensible Daten über eine `.env`-Datei oder Umgebungsvariablen einzubinden:

```env
API_SECRET=DeinSicheresSecret
TELEGRAM_BOT_TOKEN=DeinBotToken
TELEGRAM_CHAT_ID=DeineChatID
```

Wichtige Hinweise:

- `API_SECRET` schützt die API-Endpunkte vor unbefugtem Zugriff.
- Telegram-Token und Chat-ID sollten niemals direkt im Code hinterlegt werden.
- Für produktive Umgebungen empfiehlt sich ein separates Netzwerk-Setup mit Tailscale und eingeschränkten Firewall-Regeln.

### Konfigurations-Punkte für server.py und setup-agent.sh

Damit Server und Agent korrekt miteinander kommunizieren, müssen einige Umgebungsvariablen konsistent gesetzt werden.

1) Variablen für `server.py` (Master)

- `API_SECRET` — Das gemeinsame geheime Token, das der Master beim Verifizieren von Agent-Anfragen verwendet.
- `TELEGRAM_BOT_TOKEN` — Token des Telegram-Bots, über den Alerts gesendet werden.
- `TELEGRAM_CHAT_ID` — Ziel-Chat/Channel für Telegram-Alerts.

Beispiel `.env` für den Master:

```env
API_SECRET=MeinSuperSecretToken
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=987654321
```

2) Variablen für `setup-agent.sh` / Agent (auf dem zu überwachenden Host)

- `PRIMARY_SERVER_URL` — Die Tailscale-IP-Adresse des Master-Servers (z. B. `http://100.64.x.y:8090`).
- `FALLBACK_SERVER_URL` — Die öffentliche IP/URL des Master-Servers (z. B. `http://203.0.113.5:8090`).
- `API_SECRET` — Muss identisch mit `API_SECRET` aus `server.py` sein, sonst werden Agent-Reports verworfen.

Beispiel-Aufruf (als Umgebungsvariablen vor dem Install-Skript):

```bash
export PRIMARY_SERVER_URL="http://100.64.0.12:8090"
export FALLBACK_SERVER_URL="http://203.0.113.5:8090"
export API_SECRET="MeinSuperSecretToken"
curl -sSL http://<MASTER-IP>:8090/setup-agent.sh | bash
```

Hinweis: In der `setup-agent.sh` wird zuerst versucht, die Verbindung über `PRIMARY_SERVER_URL` (Tailscale) aufzubauen; falls das nicht funktioniert, wird `FALLBACK_SERVER_URL` (öffentliche IP) verwendet.

---

## 📄 Lizenz

Dieses Projekt steht unter der MIT-Lizenz.

---

## 📌 Hinweis

Dieses Repository ist für den Einsatz als selbst gehostetes Monitoring-Tool für Tailscale-Umgebungen gedacht. Die Einrichtung und Konfiguration kann je nach Infrastruktur und Netzwerk-Topologie variieren.
