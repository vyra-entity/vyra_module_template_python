# `.module/` — Modul-Metadaten und Abhängigkeiten

Dieses Verzeichnis liegt unter `/workspace/.module/` im Container bzw. im Modul-Workspace
während der Entwicklung. Es enthält die Laufzeit-Konfiguration, Modul-Identität und
deklarative Abhängigkeiten, die beim Docker-Image-Build installiert werden.

---

## `module_data.yaml`

Kanonical Modul-Manifest. Wird beim Start gelesen (`_load_module_data()`,
`_load_module_config()`) und bei Bedarf automatisch aktualisiert.

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `name` | string | Technischer Modulname (z. B. `v2_modulemanager`). Wird als `package_name` für VyraEntity verwendet. |
| `uuid` | string | Eindeutige Instanz-ID (32 Zeichen, hex). Bestimmt Container-Namen und Interface-Pfade. |
| `version` | string | Semantische Version (z. B. `0.1.0`). |
| `blueprints` | string | Blueprint-Typ des Moduls (z. B. `basic`, `usermanager`). |
| `description` | string | Kurzbeschreibung des Moduls. |
| `author` | string | Autor oder Team (optional). |
| `alias` | string | Anzeige-Alias (optional). |
| `display_name` | string | Lesbarer Anzeigename (optional, wird bei Startup beibehalten). |

**Beispiel:**

```yaml
name: v2_modulemanager
uuid: 733256b82d6b48a48bc52b5ec73ebfff
version: 0.1.0
blueprints: basic
description: VYRA Module Manager
author: ""
alias: ""
```

---

## `module_params.yaml`

Laufzeit- und Deployment-Parameter. Wird vom Container Manager, State Manager und
VyraEntity gelesen. Die Abschnitte `security` und `simulation` werden in
`_load_module_config()` in die Entity-Konfiguration übernommen.

### `permissions`

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `visible` | bool | Modul in UI/API-Listen sichtbar. |
| `removable` | bool | Modul darf per API/UI entfernt werden. |
| `updatable` | bool | Modul darf aktualisiert werden. |
| `suspendable` | bool | Modul darf in den Ruhezustand versetzt werden. |
| `multi_instance` | bool | Mehrere Instanzen desselben Moduls erlaubt. |
| `protected` | bool | Erfordert erhöhte Berechtigungen für Operationen. |

### `behavior`

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `auto_start` | bool | Modul beim Systemstart automatisch starten. |
| `restart_on_failure` | bool | Bei Fehler neu starten (optional). |
| `health_check.enabled` | bool | Docker-Healthcheck aktivieren. |
| `health_check.interval_seconds` | int | Prüfintervall in Sekunden. |
| `health_check.timeout_seconds` | int | Timeout pro Prüfung. |
| `health_check.retries` | int | Anzahl Wiederholungen vor „unhealthy“. |
| `health_check.start_period_seconds` | int | Karenzzeit nach Start. |

### `state_manager`

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `broadcast_interval_hz` | float | State-Broadcast-Frequenz über VyraEntity StateFeeder. |
| `max_state_history_size` | int | Größe des State-History-Puffers. |
| `max_error_history_size` | int | Größe des Error-History-Puffers. |
| `debug_transitions` | bool | State-Übergänge auf DEBUG-Level loggen. |

### `security`

Konfiguration für `VyraEntity._init_security_manager()` (vyra_base).

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `enabled` | bool | SecurityManager aktivieren. |
| `max_level` | int | Maximales Sicherheitslevel (1–5, z. B. `4` = HMAC). |
| `session_duration` | int | Session-Dauer in Sekunden (Standard: 3600). |
| `ca_key_path` | string\|null | Pfad zum CA-Key (Level 5). |
| `ca_cert_path` | string\|null | Pfad zum CA-Zertifikat (Level 5). |
| `module_passwords` | map | Passwörter pro Modul-ID (Level 3+). |
| `module_access.level_4.username` | string | Benutzername für Basic Auth (Level 4). |
| `module_access.level_4.password` | string | Passwort für Basic Auth (Level 4). |
| `module_access.level_5.username` | string | Benutzername für Zertifikats-Auth (Level 5). |
| `module_access.level_5.password` | string | Passwort für Zertifikats-Auth (Level 5). |

### `simulation`

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `enabled` | bool | Simulationsmodus aktivieren. |
| `name` | string | Name der Simulation. |
| `description` | string | Beschreibung der Simulation. |

### `resources`

Optionale Docker-Ressourcenlimits (`cpu_limit`, `memory_limit`, `cpu_reservation`,
`memory_reservation`). `null` = kein Limit.

### `labels`

Docker-Deploy-Labels. `labels.modulemanager.module_id` wird vom Container Manager
bei der Installation automatisch gesetzt — nicht manuell ändern.

---

## Abhängigkeiten — Installations-Pipeline

Die folgenden Dateien deklarieren zusätzliche Abhängigkeiten. Sie werden beim
**Docker-Image-Build** (`Dockerfile`, Builder-Stage) in dieser Reihenfolge verarbeitet:

| Datei | Installer | Beschreibung |
|-------|-----------|--------------|
| `requirements.txt` | **Python (pip)** | Python-Pakete, ein Eintrag pro Zeile (`package==version`). Wird via `pip install -r .module/requirements.txt` installiert (Builder- und Runtime-Stage). |
| `pre-install.sh` | **Dynamic Installer** | Beliebiges Bash-Skript für individuelle Setup-Schritte (z. B. zusätzliche APT-Repositories, Toolchain-Vorbereitung). Wird via `bash .module/pre-install.sh` ausgeführt. |
| `system-packages.txt` | **System (apt)** | Debian/Ubuntu-Pakete, ein Paketname pro Zeile. Kommentare mit `#`. Installation via `apt-get install`. |
| `npm-packages.txt` | **NPM (global)** | NPM-Pakete für globale CLI-Tools, ein Paket pro Zeile (z. B. `vite`, `nodemon`). Wird global installiert, wenn im Build-Pipeline-Schritt vorhanden. |
| `cargo-packages.txt` | **Cargo (Rust)** | Cargo-Binary-Tools, ein Crate pro Zeile (`crate@version`). Nur in Rust-Modulen. Installation via `cargo install`. |

### Format-Beispiele

**requirements.txt**
```
fastapi==0.115.0
redis==5.0.1
# Kommentare mit #
```

**system-packages.txt**
```
netcat-openbsd
nginx
# python3-dev
```

**npm-packages.txt**
```
vite
nodemon
# typescript
```

**pre-install.sh**
```bash
#!/bin/bash
echo "Setting up custom repositories..."
# Beliebige Setup-Logik
```

### Docker-Build-Ablauf

```
COPY .module/ → /workspace/.module/
    ↓
pip install -r .module/requirements.txt     (Python-Module)
    ↓
bash .module/pre-install.sh                 (falls vorhanden)
    ↓
apt-get install < .module/system-packages.txt
    ↓
npm install -g < .module/npm-packages.txt   (falls konfiguriert)
cargo install < .module/cargo-packages.txt  (Rust-Module, falls konfiguriert)
    ↓
poetry install / cargo build / colcon build …
```

Nach dem Build liegt `.module/` im Runtime-Image unter `/workspace/.module/` und
wird vom Entrypoint (`vyra_entrypoint.sh`) und der Modul-Runtime gelesen.

---

## Weitere Dateien in `.module/`

| Datei | Zweck |
|-------|-------|
| `module_dependencies.yaml` | Deklarierte Abhängigkeiten (Runtime, Hardware, Services). Wird vom Container Manager vor dem Start geprüft. |
| `module_dependencies.example.yaml` | Vorlage und Dokumentation für `module_dependencies.yaml`. |
| `plugin_interfaces.yaml` | Plugin-Interface-Registrierung (falls Plugins verwendet werden). |
