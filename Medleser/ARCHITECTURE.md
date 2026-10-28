# Arkitektur

Medleser er en Windows-app med webbasert brukergrensesnitt. Et lokalt Python-baksystem gjør alt arbeidet: dokumentlesing, talesyntese, lyd og lagring. Brukergrensesnittet vises i et vanlig programvindu (WebView2).

```
┌──────────────── Medleser.exe (én prosess) ─────────────────────────────┐
│  pywebview-vindu (Edge WebView2)          Lokal webserver (FastAPI)     │
│  ┌──────────────────────────┐   HTTP     ┌───────────────────────────┐  │
│  │ web/  HTML, CSS, JS      │ ─────────► │ routes.py  (API)          │  │
│  │ (norsk, WCAG 2.2 AA)     │ 127.0.0.1  │ server.py  (sikkerhet)    │  │
│  └──────────────────────────┘ tilfeldig  └──────┬────────────────────┘  │
│                                port             │                       │
│   documents/  PDF, DOCX, EPUB, TXT, OCR ◄───────┤                       │
│   speech/     tekstklargjøring, stemmer, ◄──────┤   jobs.py (bakgrunns- │
│               lyd, MP3, synkronisering          │   jobber med fremdrift │
│   storage/    SQLite, prosjektmapper, ◄─────────┘   og avbrudd)         │
│               nøkler i Windows Legitimasjonsbehandling                  │
└─────────────────────────────────────────────────────────────────────────┘
         │ bare hvis brukeren velger og godkjenner en betalt tjeneste
         ▼
   ElevenLabs API (HTTPS, brukerens egen nøkkel)
```

## Teknologivalg (kort begrunnet)

| Behov | Valg | Begrunnelse |
|---|---|---|
| Språk | Python 3.13 | Best økosystem for dokumenter og tale. Har ferdige hjul (wheels) for Windows. |
| Programvindu | pywebview 6 + WebView2 | Ekte programvindu med moderne nettlesermotor. WebView2 følger med Windows 11. |
| Baksystem | FastAPI + uvicorn | Enkelt, testbart og raskt. Støtter Range-forespørsler, som avspilleren trenger for å spole. |
| Brukergrensesnitt | Ren HTML/CSS/JavaScript (ES-moduler) | Krever ikke byggeverktøy eller Node. Lett å lese og pakke. Full kontroll over tilgjengelighet. |
| PDF | PyMuPDF | Rask og presis tekst med posisjoner, tabellgjenkjenning og bilder for OCR. Lisens: AGPL, se THIRD_PARTY_LICENSES.md. |
| Word | python-docx | Overskriftsstiler, lister og tabeller. Kjører aldri makroer. |
| EPUB | Egen parser (zipfile + defusedxml + lxml) | Trygg, ingen kjøring av skript. Oppdager kopisperre (DRM) og avviser den uten forsøk på omgåelse. |
| OCR | Windows' innebygde tekstgjenkjenning (WinRT) | Gratis, uten installasjon og uten nett. Støtter norsk når norsk språkpakke finnes. |
| Gratis tale | Piper (lokalt nevralt) + Windows-stemmer | Begge er gratis og virker uten internett. Begge gir tidspunkter per ord (se under). |
| Betalt tale | ElevenLabs REST API | Første prioriterte tjeneste. `with-timestamps` gir tidspunkt per tegn. |
| MP3 | LAME via `lameenc` | Strømmer lyd del for del, så hele boken trenger ikke ligge i minnet. Ingen FFmpeg-avhengighet. |
| Lagring | SQLite + JSON-filer per prosjekt | SQLite holder oversikt og innstillinger. JSON-filene er lette å lese og sikkerhetskopiere. |
| Hemmeligheter | `keyring` → Windows Legitimasjonsbehandling | Nøklene krypteres av Windows og lagres aldri i Medlesers filer. |
| Pakking | PyInstaller (onedir) + Inno Setup | Rask oppstart, få falske alarmer i antivirus, installasjon per bruker uten administratorrettigheter. |

## Moduler

| Mappe/fil | Ansvar |
|---|---|
| `medleser/app_desktop.py` | Oppstart: én instans, server på 127.0.0.1, programvindu, ryddig avslutning, selvtest. |
| `medleser/server.py` | FastAPI-app, sikkerhetsmellomvare (nøkkel, Host, Origin, CSP), feilhåndtering. |
| `medleser/routes.py` | API for dokumenter, stemmer, lyd, eksport, innstillinger og system. |
| `medleser/jobs.py` | Bakgrunnsjobber (innlesing, nedlasting, lyd, eksport) med fremdrift og avbrudd. |
| `medleser/documents/` | Dokumentimport: `importer.py`, `pdf_reader.py`, `layout.py` (kolonner), `docx_reader.py`, `epub_reader.py`, `txt_reader.py`, `ocr.py`, `cleanup.py` (URL/DOI, orddeling, referanser), `language.py` (språkgjenkjenning), `model.py` (datamodell). |
| `medleser/speech/text_prep.py` | Lager opplesningsteksten fra blokkene etter brukerens valg, med sporbar kobling til synlig tekst. |
| `medleser/speech/providers/` | Stemmeleverandører bak ett felles grensesnitt (`base.py`): `piper_provider.py`, `windows_provider.py`, `elevenlabs_provider.py`. |
| `medleser/speech/generator.py` | Segmentering, cache, gjenopptak, kostnadsvern, sammensetting til MP3 og synkroniseringsdata. |
| `medleser/speech/mp3.py`, `audio.py` | MP3-koding med ID3-merker, kontroll av lydfil, PCM-verktøy. |
| `medleser/storage/` | `db.py` (SQLite), `projects.py` (prosjektmapper), `secrets.py` (nøkler). |
| `medleser/runtime_fix.py`, `winrt_support.py` | Windows-stabilitet: riktig C++-kjøretid lastes først, og COM-apartmentet (MTA) holdes i live. |
| `web/` | Brukergrensesnittet: visninger i `js/views/`, felles komponenter i `js/ui.js`. |

## Dataflyt: fra dokument til lyd og medlesing

1. **Opplasting:** Filen kontrolleres (type ut fra innhold, størrelse, zip-bombe) og kopieres til prosjektmappen.
2. **Innlesing (jobb):** Leseren lager en liste med *blokker* (overskrift, avsnitt, fotnote, tabell, topptekst …). Ingenting slettes, alt klassifiseres. URL-er, DOI-er og fotnotetall merkes som tegnområder (spans).
3. **Klargjøring:** `text_prep.build_plan` bygger opplesningsteksten etter valgene. Den hopper over det som er valgt bort og skriver forkortelser fullt ut. Hver bit tekst får en *Piece* som peker tilbake til blokk, del og tegnposisjon i den synlige teksten.
4. **Segmentering:** Teksten deles ved setningsgrenser til biter som passer leverandøren (Piper ca. 700 tegn, ElevenLabs ca. 1500). Hver bit får en cache-nøkkel ut fra leverandør, stemme, modell, språk, tempo og tekst.
5. **Generering (jobb):** Bare biter som mangler i cachen, sendes til stemmen. Lyd og ordtider lagres per bit, så en avbrutt jobb fortsetter der den slapp. Betalt lyd lages aldri på nytt.
6. **Sammensetting:** Bitene strømmes gjennom én MP3-koder med korte pauser og myke overganger (6 ms inn- og uttoning). Ordtidene forskyves med nøyaktig antall samples, og resultatet lagres i `sync.json`.
7. **Medlesing:** Nettleseren spiller MP3-filen. En tidtaker på 40 ms slår opp gjeldende ord ved binærsøk og markerer det med CSS Custom Highlight API. Det gjør ingen endringer i DOM-en og gir ingen skjermleserstøy.

## Synkronisering og presisjon

| Leverandør | Kilde til tidspunkter | Presisjon |
|---|---|---|
| Piper | Modellens egne varigheter per fonem. Stemmefilen tilpasses ved installasjon så den eksponerer dem. Fonemordene kobles til tekstordene: 1:1 når antallet stemmer, ellers ved sekvensjustering mot fonemene for hvert enkelt ord. | Ord (ca. 12 ms oppløsning) |
| Windows-stemmer | `SpeechSynthesizer` med `IncludeWordBoundaryMetadata`. Gir starttid og tegnposisjon for hvert ord. | Ord |
| ElevenLabs | `with-timestamps` gir start- og sluttid per tegn, som Medleser kobler til ord. | Ord (for modeller som sender tidspunkter) |

Medleser gjetter aldri ordvarighet ut fra ordlengde. Kan ordene ikke kobles sikkert, brukes avsnittsmarkering, og brukeren får vite det («Markering: …» i medlesingsvinduet).

## Sikkerhet

- Serveren lytter bare på `127.0.0.1` med tilfeldig port og `SO_EXCLUSIVEADDRUSE`.
- En tilfeldig sesjonsnøkkel overleveres én gang til vinduet og blir en HttpOnly-informasjonskapsel med `SameSite=Strict`. Uten den svarer serveren 401.
- Host-headeren må være `127.0.0.1:<port>`, som stopper DNS-rebinding.
- Endringer krever samme opprinnelse (Origin) og headeren `X-Medleser: 1`, som stopper CSRF.
- Streng Content-Security-Policy, ingen CORS-headere, `nosniff` og `no-referrer`.
- Dokumenttekst settes alltid inn som tekst, aldri som HTML.
- Eksterne lenker og mapper åpnes bare fra en fast tillatt liste.
- Nøkler ligger bare i Windows Legitimasjonsbehandling. Loggen filtrerer bort alt som ligner nøkler.
- Ved avslutning stoppes jobber, server og tråder, og prosessen avsluttes.

## Utvide med nye stemmetjenester

Lag en klasse som arver `TTSProvider` (`speech/providers/base.py`) og registrer den i `ProviderRegistry`. Grensesnittet består av:
- `get_provider_capabilities()`
- `status()` og `check_connection()` (må være gratis)
- `list_voices()` og `list_models()`
- `synthesize()` (returnerer PCM og ordtider)
- `estimate_usage()`

Resten av appen, med kostnadsvern, cache, MP3 og medlesing, virker da uten endringer.
