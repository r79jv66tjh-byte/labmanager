# LabManager

Zentrale für das Heim-Labor unter Windows/Hyper-V: zeigt den Zustand aller VMs und Dienste, startet und stoppt das Lab, öffnet Remotedesktop-Profile, Web-Oberflächen und SSH-Terminals, verwaltet die Docker-Container der Docker-VM und überwacht die Backups.

Status: in Entwicklung. Die Funktionen kommen schrittweise über Pull Requests; jeder Schritt liefert eine testbare `LabManager.exe`.

## Funktionen

- **Übersicht** (wie die Prozessliste im Task-Manager): eine Liste in den Gruppen *Virtuelle Maschinen* (Hyper-V und Proxmox), *Hosts* und *Dienste* mit Typ, Zustand, CPU, RAM belegt, RAM zugewiesen, RAM %, Kernen, Laufzeit, Adresse und Info; aktualisiert alle 5 Sekunden und mit „Aktualisieren“.
  - Quellen: Hyper-V (`Get-VM`), Ports der Hosts und Dienste, Proxmox per SSH (`pvesh get /cluster/resources`; CPU, Kerne und Laufzeit des Proxmox-Knotens erscheinen in der Zeile seines Hosts), RAM jedes erreichbaren Hosts per SSH aus `/proc/meminfo`. Ist der Proxmox-Host nicht erreichbar, wird Proxmox nicht abgefragt.
  - RAM: „belegt“ und „RAM %“ misst jeder Host selbst (gesamt minus `MemAvailable`, wie `free`). VM-Zeilen zeigen nur, wie viel RAM zugewiesen ist: Proxmox zählt von außen den Datei-Cache als belegt, und der „Bedarf“ von Hyper-V folgt bei Linux dem reservierten, meist ungenutzten Speicher. Beides würde falsch warnen.
  - Grün = läuft/erreichbar, Rot = aus/nicht erreichbar, Gelb = Zwischenzustand oder Host mit weniger als 10 % verfügbarem RAM.
  - Statuszeile mit Zusammenfassung und Warnungen: Host mit weniger als 10 % verfügbarem RAM, Proxmox weist seinen VMs mehr RAM zu als es hat; dazu Hosts, deren RAM nicht lesbar war.
  - Suchfeld und Sortieren wie im Container-Reiter; beides wirkt innerhalb der Gruppen, leere Gruppen werden bei der Suche ausgeblendet.
  - **Lab starten** startet alle Hyper-V-VMs, die nicht laufen, in der Reihenfolge aus `lab.json`; **Lab herunterfahren** fährt laufende nach Rückfrage sauber herunter, in umgekehrter Reihenfolge (Proxmox fährt seine eigenen VMs dabei mit herunter). **Starten / Herunterfahren** für die markierte Hyper-V- oder Proxmox-VM.
  - Rechtsklick: Hyper-V-VM starten/herunterfahren, Konsole öffnen, Einstellungen öffnen (CPU, RAM …; Hyper-V-Manager-Dialog); Proxmox-VM starten/herunterfahren/neu starten, Hardware in Proxmox öffnen (CPU, RAM …; Weboberfläche direkt auf der Hardware-Seite der VM); Host: SSH-Terminal, Remotedesktop-Profile, Web-Oberflächen; Dienst: öffnen.
  - Zuweisungen (Kerne, RAM) ändert LabManager bewusst nicht selbst: Hyper-V-Manager und Proxmox kennen alle Sonderfälle (VM aus/an, Generation, Grenzen) und werden direkt an der richtigen Stelle geöffnet.

- **Verbinden**: je ein Knopf für jedes Remotedesktop-Profil (die `.rdp`-Datei wird bei jedem Klick frisch aus `lab.json` erzeugt), jede Web-Oberfläche und ein SSH-Terminal (neuer Tab im Windows Terminal, Anmeldung per `ssh_key`) zu jedem Host.
  Browser und Remotedesktop starten ohne Adminrechte, obwohl LabManager selbst mit Adminrechten läuft.
- **Container** (wie die Details-Ansicht im Task-Manager): alle Docker-Container des Docker-Hosts mit Stack, Zustand (inkl. healthy/unhealthy), CPU, RAM, RAM %, Netzwerk- und Datenträger-E/A, Prozessen, Image, Ports und Docker-Status.
  - Suchfeld, Sortieren per Klick auf die Spaltenüberschrift (Zahlenspalten zuerst absteigend).
  - Knöpfe Starten / Stoppen / Neu starten für den markierten Container und „Aktualisieren“ (Liste neu laden, Live-Verbindung neu aufbauen), Rechtsklick-Menü zusätzlich mit Pausieren/Fortsetzen, Logs anzeigen und Shell öffnen (neuer Tab im Windows Terminal) sowie der passenden Web-Oberfläche aus `services`. Ändernde Aktionen außer Starten fragen nach.
  - Live: Solange der Reiter offen ist, laufen `docker stats` und `docker events` als Streams über SSH; Docker meldet Werte und Zustandswechsel selbst. Beim Verlassen des Reiters und beim Schließen werden die Streams beendet. Bricht die Verbindung ab, steht das in der Statuszeile („Aktualisieren“ startet sie neu).
  - Befehle laufen als `sudo -n docker …` (der SSH-Benutzer braucht sudo ohne Passwort).
- **Backups**: Gruppen *Auf Proxmox* (Backups im `remote_dir`), *Kopien auf Windows* (`target_dir`) und *Backup-Jobs* (Zeitplan, Aufbewahrung, nächster Lauf aus Proxmox) mit Größe, Datum und Alter.
  - Das neueste Backup ist rot, wenn es älter als `max_age_days` ist; die neueste Kopie, wenn sie älter als `copy_max_age_days` ist. Warnungen in der Statuszeile, dazu freier Platz im Ziel und der letzte Eintrag aus `copy.log`.
  - **Jetzt kopieren** holt das neueste Backup per `scp` (erst als `.partial`, Größe wird geprüft, dann umbenannt, mit Fortschritt), behält `keep` Kopien und schreibt jeden Lauf nach `copy.log` im Zielordner. Dazu Zielordner öffnen, Protokoll öffnen, Aktualisieren; Rechtsklick: Im Explorer zeigen, Backup-Jobs in Proxmox öffnen.
  - Geladen wird beim Öffnen des Reiters und mit „Aktualisieren“.
- **Einstellungen**: Unterreiter *VMs*, *Hosts* und *Dienste* mit Hinzufügen / Bearbeiten (auch Doppelklick) / Entfernen, dazu der SSH-Schlüssel für alle Hosts; außerdem *Remotedesktop*, *Backups* und *Docker & Proxmox*.
  - Remotedesktop, Backups, Docker und Proxmox haben je ein Formular mit *Übernehmen* (einrichten oder ändern) und *Abschalten* (Abschnitt entfernen, der Reiter oder die Knöpfe verschwinden). Nicht eingerichtete Bereiche zeigen Vorschläge (z. B. `/var/lib/vz/dump`, 4 Kopien), die erst mit *Übernehmen* gelten.
  - Remotedesktop: Ziel-Host, Benutzer, gemeinsame `.rdp`-Zeilen und Profile. Ein Profil ist ein *Fenster* (Breite × Höhe), eine Auswahl von *Bildschirmen* (Häkchen mit Lage, Auflösung und Hauptbildschirm, nummeriert wie `mstsc /l`; der Hauptbildschirm wird zum Hauptbildschirm der Sitzung) oder *Eigene Zeilen*. Beim Einrichten entsteht ein erstes Profil „Fenster“ 1920×1080.
  - Jede Änderung wird sofort mit denselben Regeln geprüft wie beim Laden; der Dialog nennt, was nicht passt, und bleibt offen.
  - Hilfen: *Aus Hyper-V auswählen* (VM-Namen, braucht Adminrechte), *Verbindung testen* (SSH-Port und Anmeldung mit dem Schlüssel), leere Dienst-URL wird zu `http://<Host-Adresse>:<Port>`. *Aus Docker vorschlagen* (Unterreiter Dienste) fragt die Container des Docker-Hosts ab und bietet jeden veröffentlichten Port, der noch kein Dienst ist, zum Ankreuzen an (443, 8006 und 8443 mit `https`, sonst `http`).
  - Schutz: Ein Host, den ein Dienst, Remotedesktop, Backups, Docker oder Proxmox nutzt, und der Dienst der Proxmox-Weboberfläche lassen sich nicht entfernen; LabManager nennt, wer sie nutzt. Die ID eines Hosts ist nach dem Anlegen fest. Wird der Proxmox-Dienst umbenannt, zieht der Verweis mit.
  - Änderungen sammeln sich, bis *Speichern und neu laden* sie schreibt (erst in eine temporäre Datei, dann ersetzen) und LabManager mit der neuen Konfiguration neu startet; *Änderungen verwerfen* nimmt sie zurück. Schließen mit ungespeicherten Änderungen fragt nach.

Hyper-V braucht Adminrechte. Ohne sie (z. B. über `start.bat`) zeigen die Hyper-V-Zeilen „keine Adminrechte“, statt VMs als „nicht gefunden“ zu melden; alle anderen Zeilen und Reiter funktionieren.

## Konfiguration: `lab.json`

Das ganze Lab ist in einer Datei beschrieben: `%APPDATA%\LabManager\lab.json` (also im eigenen Benutzerprofil, Updates des Programms berühren sie nie). Am einfachsten wird sie im Reiter „Einstellungen“ gepflegt. Gibt es noch keine, zeigt LabManager eine Willkommensseite; *Neue Konfiguration anlegen* legt eine leere an und öffnet den Reiter „Einstellungen“.

`vms`, `hosts` und `services` sind Pflicht, dürfen aber leer sein (`[]`). `ssh_key` ist Pflicht, sobald es Hosts gibt. `rdp`, `backup`, `docker` und `proxmox` sind optional: Fehlt ein Abschnitt, blendet LabManager aus, was ihn braucht (Remotedesktop-Knöpfe, Reiter „Backups“, Reiter „Container“, Proxmox-VMs und -Links), und die zugehörigen Befehle melden „… ist in lab.json nicht eingerichtet“. Innerhalb eines Eintrags oder Abschnitts ist jeder Wert Pflicht; fehlt etwas, nennt LabManager beim Start die genaue Stelle.

Kleinstes Beispiel (ein Rechner, sonst nichts):

```json
{
  "ssh_key": "%USERPROFILE%\\.ssh\\id_ed25519",
  "vms": [],
  "hosts": [{ "id": "pi", "label": "Raspberry Pi", "address": "192.168.1.20", "ssh_user": "pi" }],
  "services": []
}
```

| Abschnitt | Inhalt |
| --- | --- |
| `ssh_key` | privater SSH-Schlüssel für alle Hosts, Umgebungsvariablen wie `%USERPROFILE%` erlaubt (Pflicht, sobald es Hosts gibt) |
| `vms` | Hyper-V-VMs (`name` wie im Hyper-V-Manager, `label` für die Anzeige) |
| `hosts` | Rechner im Lab-Netz mit `id`, `label`, `address`, `ssh_user` |
| `services` | Web-Dienste mit `label`, `host` (eine `id` aus `hosts`), `port`, `url` |
| `rdp` (optional) | Remotedesktop zu `host` mit `username`, gemeinsamen Einstellungen `base` und benannten `profiles` |
| `backup` (optional) | Proxmox-Backups: `host`, `remote_dir`, `pattern`, Windows-Ziel `target_dir`, Anzahl Kopien `keep`, Altersgrenzen in Tagen `max_age_days` (Proxmox-Backup) und `copy_max_age_days` (Kopie auf Windows) |
| `docker` (optional) | `host` (eine `id` aus `hosts`), auf dem Docker läuft |
| `proxmox` (optional) | `host` (eine `id` aus `hosts`, SSH-Zugang für `pvesh`) und `web` (das `label` des Proxmox-Dienstes aus `services`, für Direkt-Links) |

## Starten und bauen

- Mit Python: `start.bat` (Python 3.13+, keine Fremdpakete).
- Als Programm: `build.bat` erzeugt `dist\LabManager.exe`. Die `.exe` fragt beim Start nach Adminrechten, weil Hyper-V sie zum Starten und Stoppen von VMs braucht.
- Tests: `python -m unittest discover -s tests` (mit der Beispiel-Konfiguration `tests/fixtures/lab.json`; Adressen aus dem Dokumentationsbereich 192.0.2.x)

## Befehle (ToolHub, Claude, geplante Aufgaben)

| Befehl | Wirkung |
| --- | --- |
| `status` | Hyper-V-VMs, Hosts (erreichbar, RAM im Host gemessen), Proxmox-Knoten und -VMs, Dienste, Warnungen |
| `containers` | Docker-Container mit einer Messung von CPU, RAM, Netzwerk- und Datenträger-E/A |
| `backups` | Proxmox-Backups, Kopien auf Windows, Backup-Jobs, freier Platz, letzter `copy.log`-Eintrag, Warnungen |
| `backup-copy [--dry-run]` | neuestes Backup kopieren wie „Jetzt kopieren“ (protokolliert in `copy.log`); `--dry-run` zeigt nur, was kopiert und gelöscht würde |

- Aufruf mit Python, z. B. `python LabManager.pyw status`; Ausgabe JSON auf stdout, Speichergrößen in Bytes. Ohne Adminrechte meldet `status` die Hyper-V-VMs als nicht abfragbar, alles andere funktioniert.
- `LabManager.exe <befehl>` hat kein Konsolenfenster und gibt daher nichts aus; dort zählt der Exit-Code (in der Aufgabenplanung „Letztes Ergebnis“): 0 = erledigt, 1 = fehlgeschlagen (bei `backup-copy` Grund in `copy.log`), 2 = unbekannter Befehl oder Option, 3 = `lab.json` fehlt oder ist fehlerhaft. Die geplante Aufgabe für `backup-copy` muss „mit höchsten Privilegien“ laufen, weil die `.exe` Adminrechte anfordert.
- **ToolHub:** `toolhub.json` macht LabManager in ToolHub sichtbar und die Befehle für Claude aufrufbar; `backup-copy` läuft dort ohne Bestätigung nur als Vorschau. Gestartet wird `dist\LabManager.exe`; ToolHub fragt dafür per UAC nach Adminrechten (ab ToolHub mit automatischer Rechteanfrage).
