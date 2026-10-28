# Medleser

**Medleser** er en Windows-app som gjør PDF-, Word-, EPUB- og tekstfiler om til naturlig tale og lydbøker. Med *synkronisert medlesing* ser du teksten på skjermen mens den leses opp, og hvert ord markeres i takt med lyden.

Medleser er laget for studenter med dysleksi, lese- og skrivevansker og andre funksjonsnedsettelser, men alle kan bruke den. Alt virker gratis med lokale stemmer, uten at dokumentene forlater datamaskinen. Vil du ha enda mer naturlige KI-stemmer, kan du frivillig koble til ElevenLabs med din egen konto.

## Hovedfunksjoner

- **Dokumenter:** PDF (også to spalter og skannede sider med OCR), Word (.docx), EPUB og tekst (.txt). Dra og slipp, eller velg fil.
- **Tekstklargjøring:** Medleser hopper over sidetall, topp- og bunntekst, URL-er, DOI-er og fotnotetall. Referanseliste, fotnoter og tabeller kan leses eller hoppes over, og tabeller leses rad for rad med kolonneoverskrift. Forkortelser leses fullt ut. Originalteksten beholdes alltid, og du ser hva som hoppes over.
- **Gratis lokale stemmer:**
  - Piper (norsk, engelsk, svensk, dansk) med ordmarkering. Stemmene lastes ned i appen.
  - Windows' egne stemmer, også med ordmarkering.
- **ElevenLabs (frivillig, betalt):**
  - Enkel tilkoblingsveiviser. Nøkkelen lagres sikkert i Windows.
  - Overslag og godkjenning før noe sendes, og en forbruksgrense per dokument.
- **Medlesing:**
  - Ordmarkering og markering av avsnittet som leses.
  - Klikk på et avsnitt for å starte der.
  - Avspilling 0,75–2× med bevart tonehøyde, 10 s tilbake og 30 s frem, kapittelvalg.
  - Automatisk rulling som kan slås av, og distraksjonsfri modus.
  - Lesevisningen kan tilpasses: skrift (blant annet Atkinson Hyperlegible, Lexend og OpenDyslexic), størrelse, linje-, bokstav- og ordavstand, tekstbredde, fargetema, egne farger med kontrollsjekk og markeringsfarge.
- **MP3:** hele dokumentet som én fil, eller én fil per kapittel, med metadata (ID3).
- **Mine dokumenter:** prosjektene lagres lokalt, kan åpnes igjen uten å lage lyden på nytt, og husker hvor du slapp.
- **Universell utforming:** WCAG 2.2 AA er målet. Full tastaturstyring, synlig fokus, skjermleservennlig (markeringen gir ingen skjermleserstøy), redusert bevegelse og zoom med Ctrl +/–.

## Kom i gang

Installasjon og systemkrav: se [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md).

### Kort brukerveiledning

1. **Nytt dokument:** Dra filen inn i vinduet, eller trykk «Velg fil fra datamaskinen». Er dokumentet skannet, kryss av for tekstgjenkjenning.
2. **Arbeidsflaten:**
   1. Sjekk språket.
   2. Velg stemmetjeneste og stemme, og trykk «Hør stemmeprøve».
   3. Under «Hva skal leses opp?» velger du hva som skal hoppes over.
   4. I «Tekst» ser du hele teksten. Overstreket tekst leses ikke. Trykk «Rediger» for å rette feil, eller «Ikke les dette» for å hoppe over et avsnitt.
3. **Lag lyd:** Trykk «Lag lyd». Du kan bruke appen mens lyden lages. Avbryter du, beholdes det som er ferdig. Med ElevenLabs får du først et overslag du må godkjenne.
4. **Medlesing:** Trykk «Åpne medlesing» og «Spill av». I teksten flytter pil opp og pil ned mellom avsnittene, og Enter starter opplesingen der. Hurtigtaster (kan slås av): Mellomrom eller K spiller og pauser, J går 10 s tilbake og L går 30 s frem. Escape avslutter distraksjonsfri modus (virker alltid).
5. **Lagre som MP3:** Velg «Lagre som MP3» eller «Lagre ett kapittel per fil», og velg mappe.

## Kjente begrensninger (versjon 1.0)

- **Kvalitet:**
  - Kvaliteten på stemmene er ikke vurdert systematisk ved lytting. Hør en prøve.
  - Tekstuttrekk fra kompliserte PDF-er (flere spalter med figurer, matematiske formler) og OCR kan gi feil. Teksten kan rettes på arbeidsflaten.
  - Tabeller oppdages i PDF bare når de har tydelig struktur. Usikre tabeller merkes.
- **Opplesning:** Tall og spesialtegn leses slik stemmemotoren tolker dem. Medleser skriver bare vanlige forkortelser fullt ut.
- **Språk:** Bokmål og nynorsk gjenkjennes som skriftlige varianter, men det finnes ingen egen nynorsk uttalemodell. Norske stemmer brukes for begge.
- **ElevenLabs:**
  - Er bare testet mot et simulert API under utviklingen (ingen betalte kall). En separat test mot det ekte API-et kan slås på, se [TEST_REPORT.md](TEST_REPORT.md).
  - For modellene v3 og v4 er det ikke bekreftet at ordtider leveres. Uten ordtider brukes avsnittsmarkering.
- **Plattform:** Ingen mobilapp, ingen deling, ingen synkronisering mellom maskiner (se [ROADMAP.md](ROADMAP.md)).
- **Lisens:** Lisensvalget for Medleser er ikke tatt. Den ferdige appen inneholder AGPL-/GPL-komponenter, se [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

## Dokumentasjon

| Fil | Innhold |
|---|---|
| [INSTALL_WINDOWS.md](INSTALL_WINDOWS.md) | Installasjon, systemkrav, utvikleroppsett, bygging, feilsøking |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Arkitektur, moduler, teknologivalg, synkronisering, sikkerhet |
| [TTS_PROVIDERS.md](TTS_PROVIDERS.md) | Stemmetjenester: lisenser, kostnader, presisjon, begrensninger |
| [PRIVACY.md](PRIVACY.md) | Personvern, hva som sendes hvor, sikkerhet, sletting |
| [TEST_REPORT.md](TEST_REPORT.md) | Testene som er kjørt, med resultater |
| [ROADMAP.md](ROADMAP.md) | Forslag til senere versjoner |
| [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) | Tredjepartskomponenter og lisenser |
| [STATUS.md](STATUS.md) | Løpende utviklingsstatus |

## Prosjektstruktur

```
medleser/            Python-kode (baksystem)
  documents/         dokumentimport, layout, OCR, språkgjenkjenning
  speech/            tekstklargjøring, stemmeleverandører, lyd, MP3, synkronisering
  storage/           SQLite, prosjektmapper, sikker nøkkellagring
web/                 brukergrensesnitt (HTML, CSS, JavaScript, skrifter)
tests/               automatiske tester (pytest)
scripts/             bygging, ikon, utviklerserver
packaging/           PyInstaller-oppsett
installer/           Inno Setup-skript
```
