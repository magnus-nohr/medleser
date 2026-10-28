# Testrapport – Medleser 1.0

**Testmiljø:**
- Windows 11 Enterprise 10.0.26200 (norsk, nb-NO), PowerShell i ConstrainedLanguage-modus
- Python 3.13.15, WebView2 154
- Windows-stemmer: Jon (nb-NO), David, Mark og Zira (en-US)
- Windows OCR: en-US og nb
- Testet 8.–9. oktober 2026

## Sammendrag

| Kjøring | Resultat |
|---|---|
| Automatiske tester (`.venv\Scripts\python.exe -m pytest -q`) | **214 bestått**, 0 feilet, 0 hoppet over. 2 integrasjonstester er avslått som standard (ekte ElevenLabs). |
| Uavhengig QA-gjennomgang (sikkerhet, korrekthet, WCAG 2.2 AA) | Ingen kritiske sikkerhetshull. Alle 3 høye og 7 middels funn er rettet, i tillegg til de fleste lave (se under). |
| Selvtest av ferdigbygd `dist\Medleser\Medleser.exe --selftest` | Bestått |
| Installasjon av `Medleser-Setup-1.0.0.exe` (stille, per bruker) | Bestått. Programmet ble installert i `%LOCALAPPDATA%\Programs\Medleser`, med Startmeny-snarvei og oppføring under «Installerte apper». |
| Selvtest av installert program | Bestått |
| Oppstart av installert program | Vinduet «Medleser» åpnes og svarer. Grensesnittet kobler seg til den lokale serveren («UI session started» i loggen). |
| Lukking av vinduet | Prosessen og serveren avsluttes etter ca. 2 s |
| Ende-til-ende i grensesnittet (utviklermodus i innebygd nettleser) | Bestått, se under |
| Ekte betalte kall mot ElevenLabs | **Ikke kjørt** (krever din godkjenning, se under) |

## Automatiske tester per område

| Testfil | Antall | Dekker |
|---|---|---|
| `tests/documents/test_doc_pdf.py` | 21 | Norsk PDF (æøå, bokmål), to spalter, typesatt artikkel med avsnitt over spalter og sider, topptekst/bunntekst/sidetall, fotnote med hevet markør, tabell med overskrift, tabell med bare vannrette linjer, usikker tabell (sammenslåtte celler), figurtekst, referanseliste med DOI/URL, nummerert referanseliste, orddeling, liggende side, kryptert PDF avvises, kopisperre (med og uten tilgjengelighetsrett), metadata-tittel, PDF-innholdsfortegnelse gir overskriftsnivåer, 300 sider raskt, skadet PDF |
| `tests/documents/test_doc_ocr.py` | 8 | Skannet PDF gjenkjennes (ekte Windows OCR) i arbeidertråd og i løkke med asyncio, tvunget OCR, blank skanning, hjelpsom feil uten OCR |
| `tests/documents/test_doc_docx.py` | 6 | Overskrifter (også norske stilnavn), lister, tabeller, fotnoter, felt, sporede endringer, tekstbokser leses |
| `tests/documents/test_doc_epub.py` | 6 | To kapitler uten skript, DRM avvises (kryptering og rights.xml), skriftobfuskering tillates, stier kan ikke gå ut av arkivet, entitetsangrep (XXE / «billion laughs») |
| `tests/documents/test_doc_txt.py` | 7 | UTF-8, UTF-16, cp1252, overskrifter, tittel, URL-er |
| `tests/documents/test_doc_importer.py` | 17 | Filtype ut fra innhold (exe omdøpt til .pdf avvises), .doc og zip-bombe avvises, størrelsesgrenser, avbrudd, fremdrift på norsk |
| `tests/documents/test_doc_cleanup.py`, `test_doc_layout.py`, `test_doc_language.py` | 88 | URL/DOI/e-post, orddeling, sidetall, bildetekster, referanseoverskrifter, kolonnerekkefølge, språk (nb, nn, en, sv, da, de, fr, es, ukjent) |
| `tests/test_text_prep.py` | 10 | Sporbar kobling fra opplesningstekst til synlig tekst, URL/DOI/fotnotetall hoppes over, forkortelser, standardvalg (topptekst, referanser, fotnoter), brukervalg, tabeller rad for rad, lange avsnitt, setningsdeling, kapitler |
| `tests/test_generator.py` | 14 | MP3 og ordsynkronisering, gjenopptak etter feil, avbrudd beholder ferdige deler, nye forsøk ved midlertidige feil, betalt tjeneste krever godkjenning og respekterer forbruksgrense, endret tekst ugyldiggjør godkjenning, avsnittsmarkering som reserve, kapittel-MP3, ID3, langt dokument (53 591 tegn, 400+ deler), gjenåpning etter omstart uten ny generering, sletting |
| `tests/test_elevenlabs.py` | 16 | Simulert ElevenLabs-API: ikke tilkoblet, gratis tilkoblingstest, valgfri kontoinformasjon, ugyldig nøkkel, ordtider fra tegntidspunkter, MP3 i stedet for PCM, tom kvote, travelt (429), manglende internett, manglende tillatelse (403), stemmeprøver bare fra tillatte domener uten nøkkel, modeller filtrert på språk med faktisk kostnadsfaktor, ingen automatisk nytt forsøk etter tidsavbrudd (kan være belastet), stemme-ID kodes i URL-en |
| `tests/test_server.py` | 14 | Sesjonsnøkkel/informasjonskapsel, Host- og Origin-sjekk, CSRF-header, CSP, nøkkel lagres i Legitimasjonsbehandling og lekker ikke (svar, filer, logg), ugyldig nøkkel beholdes ikke, betalt generering krever godkjenning (forfalsket token avvises), Range-forespørsler (spoling), redigering og gjenoppretting av tekst, lenker og mapper bare fra tillatt liste, avsnittsvalg slås sammen, valideringsfeil gjentar ikke innsendte verdier, bufret betalt stemmeprøve telles én gang, slettet prosjektmappe gjenskapes ikke, tilbakesporinger i logg sladdes |
| `tests/test_local_voices.py` | 5 | Ukjente Piper-stemme-ID-er avvises (ingen stitriks). **Ekte** Piper: ordtider for alle ord (også tall), dokument til MP3 med riktig markering av forkortelser og URL. **Ekte** Windows-stemme: ordtider, også fra arbeidertråd |
| `tests/test_winrt_threads.py` | 2 | Regresjon: WinRT fra kortlivede tråder, og onnxruntime etter WinRT (begge krasjet før rettelsene) |

## Oppdragets testliste

| Test | Status | Hvor |
|---|---|---|
| Norsk tekstbasert PDF | ✅ automatisk | `test_doc_pdf.py` |
| Engelsk akademisk PDF med to spalter | ✅ automatisk | `test_two_column_reading_order`, `test_typeset_article…` |
| Skannet PDF med OCR | ✅ automatisk (ekte Windows OCR, norsk) | `test_doc_ocr.py` |
| DOCX med overskrifter | ✅ automatisk | `test_doc_docx.py` |
| EPUB med kapitler | ✅ automatisk | `test_doc_epub.py` |
| Norsk tekst med URL-er | ✅ automatisk | `test_text_prep.py`, `test_local_voices.py` |
| Dokument med tabeller | ✅ automatisk | `test_doc_pdf.py`, `test_text_prep.py` |
| Dokument med referanseliste | ✅ automatisk | `test_doc_pdf.py`, `test_text_prep.py` |
| Generering av kort MP3 | ✅ automatisk | `test_generator.py`, `test_local_voices.py` |
| Generering av langt dokument | ✅ automatisk og målt | `test_long_document…`. Ekte Piper: 8 340 tegn ble 8,5 min lyd på 22 s (ca. 23× sanntid), med alle 1 290 ord tidfestet. |
| Avbrutt lydgenerering | ✅ automatisk | `test_cancel_keeps_finished_segments` |
| Gjenopptakelse av generering | ✅ automatisk | `test_resume_only_generates_missing_segments` |
| Sikker API-nøkkellagring | ✅ automatisk | `test_api_key_is_stored_securely_and_never_leaks` (med simulert nøkkellager) |
| Ugyldig API-nøkkel | ✅ automatisk (simulert API) | `test_invalid_key…` |
| Manglende internett | ✅ automatisk (simulert) | `test_missing_internet` |
| Manglende kreditter hos leverandør | ✅ automatisk (simulert) | `test_quota_and_rate_limit_errors` |
| Riktig markering av ord | ✅ automatisk og manuelt | Ordtider testes mot den synlige teksten. Manuelt i nettleseren: markeringen fulgte ordene under avspilling. |
| Pause og hopping under avspilling | ✅ manuelt (innebygd nettleser, styrt med JavaScript) | Pause stopper markeringen. Klikk på avsnitt hopper til riktig tid. Gjenopptak fra lagret posisjon. |
| Lagring og gjenåpning av prosjekt | ✅ automatisk | `test_project_reopens_after_restart…` |
| Sletting av lokale data | ✅ automatisk | `test_project_store…deletes`, `test_free_sample_and_project_delete` |

## Ende-til-ende i grensesnittet (manuell kontroll)

Utført i den innebygde nettleseren mot utviklerserveren (`scripts\dev_server.py`), med en generert, typesatt norsk artikkel med to spalter (`story_article_pdf`):

1. **Opplasting via API-et som grensesnittet bruker.** Tittelen, 2 sider og bokmål (sikkerhet 1,0) ble funnet. Topptekst og sidetall ble klassifisert, avsnittene sto i riktig rekkefølge, 6 referanser og kapitlene Innledning, Metode og Diskusjon ble funnet.
2. **Arbeidsflaten.** Stemme, filtervalg med antall funn, overstreket tekst som ikke leses, tabellvalg og «Lag lyd» vises riktig.
3. **«Lag lyd» med Piper.** Fremdriften ble vist («del 3 av 18 – under ett minutt igjen»), og resultatet var 6:15 lyd med ord-for-ord-markering.
4. **«Lagre som MP3» og «Lagre ett kapittel per fil».** Filene ble skrevet. Navngivingen av delen før første kapittel ble rettet etter denne testen.
5. **Medlesing.** Markeringen fulgte ordene («faglitteratur → og → tekster → komme → gjennom …»), avsnittet ble markert, og pause, klikk og spoling virket.

Feil funnet og rettet under testingen:
- Krasj (tilgangsfeil) når WinRT ble brukt fra nye servertråder. Rettet ved å holde COM-apartmentet (MTA) i live.
- Krasj i onnxruntime når WinRT var lastet først (DLL-konflikt med `msvcp140.dll`). Rettet ved å laste den nyeste C++-kjøretiden først. Det samme gjaldt bygget med PyInstaller.
- Markeringen stoppet når vinduet ikke ble tegnet (`requestAnimationFrame`). Erstattet med en tidsstyrt oppdatering.
- Forsinkelsen i MP3-koderen (1105 samples, målt i WebView2) gjorde at markeringen kom ca. 50 ms for tidlig. Den kompenseres nå.
- Diverse visningsfeil: overskriftsstørrelser, dobbel merking, «1 minutter», årsak for bunntekst og fokus i veiviseren.

## Uavhengig QA-gjennomgang og rettelser

En egen QA-agent gikk gjennom koden uten å endre den, med fokus på sikkerhet, korrekthet, WCAG 2.2 AA og norsk språk. Rettet etter gjennomgangen:

| Alvor | Funn | Rettelse og kontroll |
|---|---|---|
| Høy | «Ikke les dette» på et nytt avsnitt angret valget for et tidligere avsnitt, som da ble lest opp (og kunne koste kreditter). | Valgene leses ved klikk og slås sammen også på serveren. Testet i nettleseren og i `test_paragraph_choices_are_merged…`. |
| Høy | Knapper og dempet tekst i forhåndsvisningen hadde 1,1–1,9:1 kontrast når programmet var mørkt og lesesiden lys. | Fargene følger nå lesetemaet: 16,5:1 og 6,6:1, målt i nettleseren. |
| Høy | Skiplenken sendte brukeren til «Nytt dokument» (stoppet avspillingen). | Lenken flytter fokus uten å endre adressen, og ruteren ignorerer ankre. Testet. |
| Middels | Rød sletteknapp i mørk modus hadde 1,7:1 kontrast. | 10,7:1. |
| Middels | Rask navigasjon kunne blande visninger og etterlate skjult lyd og hurtigtaster. | Navigasjonen nummereres, og utdaterte visninger forkastes og ryddes opp. Testet. |
| Middels | En ferdig jobb kunne flytte brukeren til en annen side midt i medlesingen. | Pollingen avbrytes når brukeren forlater siden. |
| Middels | Endringer i innstillinger kunne tilbakestille valgt MP3-mappe. | Bare endrede felt sendes. |
| Middels | Alle avsnitt i medlesingen var egne tabulatorstopp foran avspilleren. | Teksten har én tabulatorstopp med piltaster (roving tabindex), og det finnes en knapp for å gå til avspilleren. Testet. |
| Middels | Avspillerlinjen kunne skjule avsnittet som hadde fokus. | `scroll-padding-bottom` i medlesingen. |
| Middels | Fokus forsvant når innhold ble tegnet på nytt (bibliotek, språk/tjeneste, filtre, stemmer). | Fokus bevares (`keepFocus`, data-fk). Testet for avsnittsvalg. |
| Lav | Flere pollere per jobb, stille feil, tellemåte for forbruksgrense, nye forsøk etter tidsavbrudd, bufret stemmeprøve telt to ganger, stemme-ID uten validering, valideringsfeil som gjentok inndata, tilbakesporinger uten sladding, gjenskapt mappe etter sletting, samtidige endringer i `project.json`, «pratsomme» live-regioner, Escape med hurtigtaster av, entall/flertall og store bokstaver i språknavn | Alle er rettet. De som gjelder baksystemet, har regresjonstester. |

## Ikke testet / begrensninger i testingen

- **Ekte ElevenLabs-kall er ikke gjort.** Oppdraget krever godkjenning før betalte kall, og det fantes ingen nøkkel. Integrasjonen er testet mot et simulert API som følger den offisielle dokumentasjonen (oktober 2026). Slik kjører du de separate testene mot det ekte API-et når du vil:
  ```powershell
  $env:MEDLESER_ELEVENLABS_LIVE = "1"        # bare gratis kall (tilkobling, stemmer, modeller)
  $env:MEDLESER_ALLOW_PAID_TESTS = "1"       # i tillegg én betalt test på 27 tegn
  .venv\Scripts\python.exe -m pytest -m integration tests/integration -q
  ```
- **Visuell kontroll av selve programvinduet (WebView2) med skjermbilder er ikke gjort.** Det finnes ikke verktøy for skjermtilgang i dette miljøet. Vinduet er bekreftet å starte, koble seg til og avslutte. Grensesnittet er kontrollert visuelt og funksjonelt i den innebygde nettleseren, som bruker samme Chromium-motor.
- **Skjermleser (NVDA/JAWS/Forteller) er ikke testet manuelt.** Tilgjengeligheten er bygget etter WCAG 2.2 AA og gjennomgått i kode, men bør testes med ekte brukere og skjermlesere.
- **Lyttebasert vurdering av stemmekvalitet** er ikke gjort, se TTS_PROVIDERS.md.
- **Testing på andre maskiner er ikke gjort.** Det gjelder for eksempel maskiner uten norsk språkpakke, uten Visual C++-kjøretid i System32 eller med Windows 10.
- **Tester med ekte dokumenter fra pensum er ikke gjort.** Alle testdokumenter er syntetiske, som oppdraget anbefaler. Ekte akademiske PDF-er varierer mer.
