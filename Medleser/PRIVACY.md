# Personvern og datasikkerhet

Medleser er laget for å behandle dokumentene dine lokalt på datamaskinen din.

## Hva som skjer lokalt (standard)

- **Dokumenter:** Filen du laster opp, kopieres til `%LOCALAPPDATA%\Medleser\projects\<id>\source\`. Teksten hentes ut der og lagres i samme mappe (`document.json`).
- **Tekstgjenkjenning (OCR):** bruker Windows' innebygde OCR. Bildene sendes ikke noe sted.
- **Gratis stemmer (Piper og Windows):** lyden lages på datamaskinen. Ingen tekst sendes over internett.
- **Lyd og synkroniseringsdata:** lagres i prosjektmappen (`segments\`, `audio\`).
- **Innstillinger og dokumentoversikt:** lagres i `%LOCALAPPDATA%\Medleser\medleser.db` (SQLite).

Medleser har ingen brukerkontoer, sentral server, sporing, analyseverktøy eller reklame.

## Når internett brukes

| Situasjon | Hva sendes | Til hvem |
|---|---|---|
| Du laster ned en Piper-stemme | En vanlig nedlastingsforespørsel (ingen dokumentdata) | Hugging Face (rhasspy/piper-voices) |
| Du kobler til og tester ElevenLabs | Tilgangsnøkkelen din som header. Ingen dokumenttekst. | ElevenLabs |
| Du hører en ferdig stemmeprøve fra ElevenLabs | En vanlig nedlasting av lydfilen, uten nøkkel | ElevenLabs' lagringstjeneste |
| Du lager lyd med ElevenLabs, **etter godkjenning** | Bare tekstbitene som skal leses opp (ikke filen), med litt nabotekst for jevnere tonefall | ElevenLabs |
| Du lager en stemmeprøve med din egen tekst hos ElevenLabs, **etter godkjenning** | De første ca. 160–300 tegnene av opplesningsteksten | ElevenLabs |
| Du klikker på lenker til ElevenLabs eller Windows-innstillinger | Ingenting fra Medleser. Siden åpnes i nettleseren eller Windows. | – |

Før tekst sendes til en betalt tjeneste, viser Medleser en egen godkjenningsdialog. Den forklarer at teksten sendes til tjenesten, og at kreditter kan bli brukt. Hvordan ElevenLabs behandler data, står i deres personvernerklæring. Ikke send tekst du ikke har lov til å dele med en ekstern tjeneste.

## Tilgangsnøkler (API-nøkler)

- Lagres i **Windows Legitimasjonsbehandling** (kryptert av Windows) via biblioteket `keyring`, under navnet «Medleser».
- Lagres **aldri** i brukergrensesnittet, SQLite, prosjektfiler, logger eller eksporterte filer.
- Sendes bare til ElevenLabs' API (`api.elevenlabs.io`).
- En nøkkel som ElevenLabs avviser, slettes med en gang.
- «Koble fra» sletter nøkkelen fra Legitimasjonsbehandling.

## Logger

`%LOCALAPPDATA%\Medleser\logs\medleser.log` inneholder bare tekniske hendelser (oppstart, feil). Den skrives aldri med dokumenttekst. Et filter maskerer i tillegg alt som ligner nøkler og sesjonsnøkkelen. Filen roteres ved 1 MB (maks. 4 filer). Ved programkrasj skrives en teknisk stakksporing til `crash.log`, uten dokumentinnhold.

## Lokal sikkerhet

- Den lokale webserveren lytter bare på `127.0.0.1` (ikke nettverket), på en tilfeldig port.
- Andre programmer og nettsider kan ikke bruke den uten sesjonsnøkkelen. Host- og Origin-sjekk stopper DNS-rebinding og CSRF.
- Opplastede filer kontrolleres ut fra innholdet, ikke bare filendelsen. For store filer og «zip-bomber» avvises.
- Makroer i Word-filer kjøres aldri. Skript og aktivt innhold i EPUB-filer kjøres aldri.
- Dokumenttekst vises alltid som ren tekst, aldri som HTML.
- Avslutter du programmet, stoppes alle bakgrunnsjobber og serveren.

## Opphavsrett

Medleser er laget for din egen lesing av dokumenter du har lovlig tilgang til, for eksempel pensum. Du er selv ansvarlig for at du har lov til å behandle dokumentet på denne måten. Medleser deler ingenting med andre. Kopibeskyttede e-bøker (DRM) og PDF-er som er låst med passord eller mot tekstuttrekk, åpnes ikke, og programmet prøver ikke å omgå beskyttelsen. Medleser påstår ikke at all privat konvertering alltid er lovlig.

## Slette data

- **Ett dokument:** «Mine dokumenter» › «Slett». Kopien av originalfilen, teksten, rettelsene og lyden slettes. MP3-filer du har lagret andre steder, slettes ikke.
- **Midlertidige filer:** Innstillinger › Lagring › «Tøm midlertidige filer».
- **Alt:** Avinstaller Medleser, slett mappen `%LOCALAPPDATA%\Medleser`, og fjern eventuelt oppføringen «Medleser» i Windows Legitimasjonsbehandling (Generelle legitimasjoner).
