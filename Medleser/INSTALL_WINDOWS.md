# Installere Medleser på Windows 11

## Systemkrav

| | Krav |
|---|---|
| Operativsystem | Windows 11, 64-bit (Windows 10 versjon 1809 eller nyere skal også virke, men er ikke testet) |
| Nettlesermotor | Microsoft Edge WebView2 Runtime (følger med Windows 11) |
| Prosessor | Vanlig moderne prosessor. Lokale stemmer bruker bare CPU, ikke skjermkort. |
| Minne | Minst 4 GB RAM (8 GB anbefales for lange dokumenter) |
| Diskplass | ca. 500 MB for programmet, 60–115 MB per lokal stemme, og plass til lydfiler (ca. 30 MB per time lyd) |
| Internett | Bare for å laste ned stemmer og for betalte KI-tjenester |
| Administratorrettigheter | Ikke nødvendig. Medleser installeres bare for din bruker. |

Valgfritt:
- Norsk tale i Windows (stemmen «Microsoft Jon») for Windows-stemmer.
- Norsk tekstgjenkjenning (følger med norsk språkpakke) for OCR.

## Installere (for brukere)

1. Dobbeltklikk på `Medleser-Setup-1.0.0.exe`.
2. Følg veiviseren. Velg eventuelt «Lag snarvei på skrivebordet».
3. Start Medleser fra Start-menyen (eller skrivebordet).

### Første gang du bruker Medleser

1. Gå til **Stemmer og KI-tjenester** og trykk **Last ned** ved «Norsk (Talesyntese)» (63 MB). Stemmen virker deretter uten internett. Har Windows allerede stemmen «Jon», kan du også bruke den med en gang.
2. Gå til **Nytt dokument**, og dra en PDF inn i vinduet (eller trykk «Velg fil fra datamaskinen»).
3. Kontroller teksten, velg stemme og trykk **Lag lyd**.
4. Trykk **Åpne medlesing**, og trykk **Spill av**.
5. Trykk **Lagre som MP3** for å lagre lydboken.

Se også brukerveiledningen i [README.md](README.md).

### Avinstallere

Innstillinger › Apper › Installerte apper › Medleser › Avinstaller.

Dokumentene, lyden og stemmene dine i `%LOCALAPPDATA%\Medleser` beholdes. Vil du slette alt, sletter du den mappen selv. Se også [PRIVACY.md](PRIVACY.md).

## Kjøre fra kildekoden (for utviklere)

```powershell
winget install -e --id Python.Python.3.13 --scope user
cd "<mappen du klonet til>\Medleser"
py -3.13 -m venv .venv        # eller: & "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m medleser
```

- **Utviklermodus i nettleser:** `.venv\Scripts\python.exe scripts\dev_server.py`, og åpne deretter `http://127.0.0.1:8765/start?token=medleser-dev-token-0001`. Data lagres da i `.devdata`.
- **Tester:** `.venv\Scripts\python.exe -m pytest -q`. Betalte API-kall er avslått; se [TEST_REPORT.md](TEST_REPORT.md).
- **Selvtest:** `.venv\Scripts\python.exe -m medleser --selftest resultat.json`.

### Jobbe på flere maskiner (Git og GitHub)

Prosjektet ligger i et Git-repo der roten også inneholder `LICENSE`, `README.md` og `CLAUDE.md`. `.venv`, `build`, `dist`, `.devdata` og logger holdes utenfor.

På en ny maskin:

```powershell
winget install -e --id Git.Git --scope user
winget install -e --id Python.Python.3.13 --scope user
git clone https://github.com/magnus-nohr/medleser.git
cd "<mappen du klonet til>\Medleser"
powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1
```

I Claude kan du i stedet åpne den klonede mappen og be om: «Les Medleser/STATUS.md og sett opp utviklingsmiljøet.»

Hver maskin må hente sine egne Piper-stemmer (i appen) og ElevenLabs-nøkkel (veiviseren). Nøkler ligger i Windows Legitimasjonsbehandling og havner aldri i Git.

Daglig bruk: `git pull` når du starter, og `git add -A`, `git commit -m "…"` og `git push` når du er ferdig.

### Bygge programmet og installasjonsfilen

Krever Inno Setup 6 (`winget install -e --id JRSoftware.InnoSetup --scope user`).

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Skriptet kjører testene, lager ikonet, bygger `dist\Medleser\` med PyInstaller, kjører selvtest av den ferdige `Medleser.exe` og lager `dist\Medleser-Setup-1.0.0.exe`. Bruk `-SkipTests` eller `-SkipInstaller` ved behov.

Merk: På maskiner der PowerShell kjører i begrenset modus (*ConstrainedLanguage*, vanlig på bedriftsmaskiner), virker skriptet fordi det bare bruker tillatte kommandoer.

## Feilsøking

| Problem | Løsning |
|---|---|
| «Medleser trenger Microsoft Edge WebView2 Runtime» | Installer «WebView2 Runtime» fra Microsoft (følger normalt med Windows 11) og start på nytt. |
| Ingenting skjer når jeg starter Medleser | Medleser kjører kanskje allerede. Det åpne vinduet hentes da frem. Se ellers i `%LOCALAPPDATA%\Medleser\logs\medleser.log` og `crash.log`. |
| Ingen norske Windows-stemmer | Windows-innstillinger › Tid og språk › Tale › Legg til stemmer › Norsk (bokmål). Start Medleser på nytt. Eller bruk de lokale Piper-stemmene. |
| «Vi klarte ikke å hente ut tekst …» | PDF-en er sannsynligvis skannet. Prøv «Prøv med tekstgjenkjenning (OCR)». Norsk OCR krever norsk språk i Windows. |
| Teksten leses i feil rekkefølge (to spalter) | Rett eller skjul avsnitt i forhåndsvisningen på arbeidsflaten («Rediger» / «Ikke les dette»). |
| Nedlasting av stemme feiler | Sjekk internettforbindelsen og prøv igjen. Ufullstendige filer forkastes automatisk. |
| ElevenLabs: «godtok ikke tilgangsnøkkelen» | Lag en ny nøkkel på elevenlabs.io › Settings › API Keys, og lim inn hele nøkkelen. |
| ElevenLabs: «mangler tillatelse» | Gi nøkkelen tilgang til «Text to Speech» og «Voices» (lese). |
| ElevenLabs: «ikke nok kreditter» | Vent til kvoten fornyes, oppgrader hos ElevenLabs, eller bruk en gratis lokal stemme. Ferdige deler er lagret. |
| Lyden ble avbrutt | Trykk «Fortsett å lage lyd». Ferdige deler gjenbrukes. |
| Markeringen henger etter eller ligger foran lyden | Skal ikke skje. Prøv «Lag lyden på nytt», og meld fra med `medleser.log` hvis det fortsetter. |
| Hurtigtastene forstyrrer skjermleseren | Slå dem av under Lesevisning › «Hurtigtaster i medlesingen». |
