# Medleser – løpende status

Sist oppdatert: 2026-10-09.

## Tilstand: versjon 1.0 er funksjonelt ferdig

Alle obligatoriske krav i oppdraget er implementert og testet: 214 automatiske tester og en uavhengig QA-gjennomgang med funn rettet (se TEST_REPORT.md). Unntaket er ekte (betalte) ElevenLabs-kall, som venter på brukerens godkjenning og nøkkel.

Den ferdige installasjonsfilen ligger i `dist\Medleser-Setup-1.0.0.exe` (79 MB, bygget 2026-10-09).

| Obligatorisk krav | Status |
|---|---|
| Starter som Windows-program | ✅ `Medleser.exe` (PyInstaller) og installasjonsfil (Inno Setup, per bruker, Startmeny-snarvei). Testinstallert. |
| Laste opp PDF, hente ut og forhåndsvise tekst | ✅ Med to spalter, OCR, tabeller, fotnoter og referanser. Teksten kan redigeres. |
| Gratis lokal talesyntese | ✅ Piper (nedlastbare stemmer) og Windows-stemmer, begge med ordtider |
| Generere og lagre MP3 | ✅ Én fil eller én per kapittel, med ID3 |
| Koble til ElevenLabs sikkert, velge stemme | ✅ Veiviser og Windows Legitimasjonsbehandling. Testet mot simulert API. |
| Godkjenning av betalt generering | ✅ Overslag, avkrysning, token knyttet til delene og forbruksgrense |
| Høre teksten i appen, medlesing med dokumentert presisjon | ✅ Ordmarkering (CSS Highlight API) og avsnitt som reserve. Presisjonen vises i appen. |
| Uten betalt abonnement og uten kommandolinje | ✅ |
| Norsk grensesnitt, lokal lagring | ✅ |

## Versjonskontroll

Git-repo med rot i `C:\Lydbok app` (opprettet 2026-10-28, hovedgren `main`). Det ligger som privat repo på GitHub: https://github.com/magnus-nohr/medleser (`origin`). Forfatter er Magnus Nohr <m@hiof.no>, satt lokalt i repoet. Oppsett på en ny maskin står i INSTALL_WINDOWS.md («Jobbe på flere maskiner») og gjøres av `scripts\setup_dev.ps1`.

## Miljø (utviklingsmaskinen)

- Python 3.13.15 (per bruker) og Inno Setup 6.7.3 er installert med winget. Det virtuelle miljøet ligger i `.venv` (`requirements.txt`, `requirements-dev.txt`).
- Piper-stemmer i `%LOCALAPPDATA%\Medleser\voices\piper`: `no_NO-talesyntese-medium` og `en_GB-cori-medium`.
- Utviklingsdata ligger i `.devdata` (koblingspunktet `voices` peker til den ekte stemmemappen). Utviklerserveren startes med `scripts\dev_server.py`.
- Medleser 1.0.0 er testinstallert per bruker i `%LOCALAPPDATA%\Programs\Medleser`. Avinstaller via Innstillinger › Apper.
- PowerShell kjører i ConstrainedLanguage-modus. Unngå .NET-metodekall i skript, og lagre `.ps1`/`.iss`/`.txt` med UTF-8-BOM (`scripts\add_bom.py`).

## Viktige tekniske funn (ikke åpenbare fra koden)

- pywinrt leverer en gammel `msvcp140.dll` (14.29) som får onnxruntime til å krasje hvis WinRT lastes først. Derfor laster `runtime_fix.preload_cpp_runtime()` den nyeste kjøretiden først. PyInstaller-bygget gir pywinrt-kopien et annet navn mens det kjører, og pakken utelater den.
- WinRT-objekter krasjer når siste tråd i COM-MTA avsluttes. Derfor holder `winrt_support.ensure_mta()` (`CoIncrementMTAUsage`) MTA i live.
- LAME-forsinkelsen på 1105 samples spilles av i WebView2 og kompenseres med `audio_offset` i `sync.json`.
- ElevenLabs `eleven_multilingual_v2` støtter ikke norsk. Standard for norsk er `eleven_flash_v2_5`. Modellisten hentes fra `/v1/models`.
- PyMuPDF er ikke trådsikkert. Bruk `documents.pdf_reader.PDF_LOCK`.

## Gjenstår / neste steg

1. Kjøre ElevenLabs-integrasjonstestene mot ekte API når brukeren godkjenner det (se TEST_REPORT.md).
2. Teste med skjermleser (NVDA/Forteller), ekte pensumdokumenter og brukere med dysleksi.
3. Lytte gjennom og vurdere stemmene. Norsk er gjort (2026-10-28): «Norsk (Talesyntese)» er anbefalt, og NVCC er fjernet. Engelsk, svensk og dansk gjenstår.
4. Velge lisens for Medleser (AGPL-3.0 anbefales, se THIRD_PARTY_LICENSES.md).
5. Kodesignering av exe-filen og installasjonsfilen.
6. Se ROADMAP.md for videre funksjoner.
