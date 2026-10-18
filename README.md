# 🌐 Tailscale Node Monitoring & Awtrix NG Integration (v2.10.3)

Ein leichtgewichtiges, selbstgehostetes Monitoring-System für Tailscale-Netzwerke mit automatischer Verbindungsweg-Erkennung (Tailscale vs. Public IP), Telegram-Alarmierung, SQLite-Historie und Live-Statusanzeige auf einer **Awtrix NG (32x8 LED-Matrix)**.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-brightgreen.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)
![Awtrix NG](https://img.shields.io/badge/Awtrix_NG-Berry-orange.svg)

---

## ✨ Features

- 📊 **Dynamic Master Dashboard:** FastAPI-basiertes Web-Dashboard mit Live-Metriken (CPU, RAM, Disk, Uptime) und Ping-Ergebnissen.
- 🔀 **Smart Route Tracking:** Erkennt automatisch, ob ein Agent über die Tailscale-IP oder die öffentliche IP antwortet.
- 📉 **Chart.js Historie:** Interaktive Verlaufsdiagramme für Systemauslastung und Latenzen.
- ⌚ **Awtrix NG Integration:** Benutzerdefiniertes Berry-Skript für 32x8 LED-Matrizen (`4 OK` / `1 ERR`).
- 🔔 **Telegram Alerts:** Benachrichtigungen bei Serverausfällen und Entwarnungen.
- 🧹 **Automatischer DB Cleanup:** Löscht historische Daten nach 120 Tagen und führt zur Speicherplatzersparnis ein `VACUUM` auf der SQLite-DB aus.

---

## 🛠️ System-Architektur

```text
┌──────────────────┐      Report (API)     ┌───────────────────────┐
│ Server Agent     │ ────────────────────> │ Monitoring Master     │
│ (Node 1..N)      │ Tailscale / Public IP │ (FastAPI + SQLite)    │
└──────────────────┘                       └───────────┬───────────┘
                                                       │
                                   ┌───────────────────┼───────────────────┐
                                   │                   │                   │
                                   ▼                   ▼                   ▼
                            ┌──────────────┐   ┌──────────────┐   ┌─────────────────┐
                            │ Web Dashboard│   │ Telegram Bot │   │ Awtrix NG Clock │
                            │ (Chart.js)   │   │ Alerts       │   │ (Berry App)     │
                            └──────────────┘   └──────────────┘   └─────────────────┘
#🚀 Schnellstart
1. Master Server aufsetzen (Docker Compose)
Klonen Sie das Repository und starten Sie den Master-Server:
git clone [https://github.com/philipplindner-media-network/tailscale-node-monitoring.git](https://github.com/philipplindner-media-network/tailscale-node-monitoring.git)
cd tailscale-node-monitoring

# Docker Container starten
docker compose up -d
Das Dashboard ist anschließend unter http://<SERVER-IP>:8090 erreichbar.

2. Agent auf Ziel-Servern installieren
Auf jedem zu überwachenden Server kann der Agent per Setup-Skript eingerichtet werden:
curl -sSL http://<MASTER-IP>:8090/setup-agent.sh | bash

3. Awtrix NG App installieren (Berry Script)
Öffne die Weboberfläche deiner Awtrix NG / Tasmota-Uhr.

Navigiere zu Filesystem / Console und erstelle eine neue Berry-App serverMonitor.be.

Füge folgenden Code ein und passe die Server-IP an:
# @desc Zeigt den Status aller Monitoring Server an (32x8 Display)

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

#🔒 Konfiguration & Sicherheit
Empfohlen wird, sensible Daten über eine .env-Datei oder Umgebungsvariablen einzubinden:
API_SECRET=DeinSicheresSecret
TELEGRAM_BOT_TOKEN=DeinBotToken
TELEGRAM_CHAT_ID=DeineChatID

#📄 Lizenz
Dieses Projekt steht unter der MIT License.
<Suggest>Soll ich dir ein Shell-Skript erstellen, das die Veröffentlichung auf GitHub automatisch per Befehl durchführt?</Suggest>
<Suggest>Möchtest du eine `README.md`-Datei als Download oder direkt per Agent im Repository anlegen?</Suggest>
</FollowUp>

<ElicitationsGroup>
</ElicitationsGroup>
