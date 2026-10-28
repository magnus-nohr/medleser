# Stemmetjenester (talesyntese)

Medleser har tre fungerende stemmetjenester bak ett felles grensesnitt. Bare tjenester med fungerende integrasjon vises som valg i appen.

| | Piper (lokal KI) | Windows-stemmer | ElevenLabs |
|---|---|---|---|
| Pris | Gratis | Gratis | Betalt (kreditter hos ElevenLabs) |
| Internett | Bare for å laste ned stemmen | Nei | Ja |
| Tekst forlater PC-en | Nei | Nei | Ja, etter godkjenning |
| Innlogging | Ingen | Ingen | Brukerens egen tilgangsnøkkel |
| Ordmarkering | Ja (modellens fonemvarigheter) | Ja (ordgrenser fra Windows) | Ja (tidspunkt per tegn) |
| Norsk | Ja | Ja, hvis norsk stemme er installert i Windows | Ja (Flash v2.5, v3, v4) |
| Engelsk | Ja | Ja | Ja |

## 1. Piper – gratis lokale KI-stemmer

- **Motor:** `piper-tts` 1.8.0 (OHF-Voice/piper1-gpl, GPL-3.0). Kjører på prosessoren med onnxruntime. espeak-ng følger med.
- **Fart på utviklingsmaskinen:** syntesen gikk 7–30 ganger raskere enn sanntid etter at stemmen var lastet. Stemmen lastes på ca. 1–2 sekunder.
- **Nedlasting:** fra den offisielle samlingen <https://huggingface.co/rhasspy/piper-voices>. Størrelse og MD5 er låst i Medleser, så en endret eller ufullstendig fil avvises.
- **Ordtider:** ved installasjon får stemmefilen et ekstra utdata som viser hvor mange lydsamples hver lyd (fonem) får. Dette gjøres med Pipers eget `patch_voice_with_alignment`. Tidspunktene kommer dermed fra modellen selv, med ca. 12 ms oppløsning. Kobling til ord:
  1. espeak skiller ord med mellomrom. Er antallet fonemord likt antallet tekstord, kobles de direkte.
  2. Ellers (tall, forkortelser) sammenlignes fonemstrømmen med fonemene for hvert enkelt ord. Ord uten treff får tiden mellom naboene.
  3. Lykkes ikke koblingen, brukes avsnittsmarkering.

### Stemmekatalog i Medleser (kun stemmer med åpne datalisenser)

| Stemme | Språk | Lisens (datasett) | Merknad |
|---|---|---|---|
| `no_NO-nvcc-medium` (anbefalt) | Norsk | CC0 (Språkbanken, NB) | 10 talere, trent fra bunnen av, 77 MB |
| `no_NO-talesyntese-medium` | Norsk | CC0 (Språkbanken) | Finjustert fra «lessac», som har egne vilkår |
| `en_GB-cori-medium` (anbefalt) | Engelsk (britisk) | Public domain (LibriVox) | Trent fra bunnen av |
| `en_GB-cori-high` | Engelsk (britisk) | Public domain (LibriVox) | Større (114 MB) |
| `en_US-kristin-medium` | Engelsk (amerikansk) | Public domain (LibriVox) | Trent fra bunnen av |
| `en_US-norman-medium` | Engelsk (amerikansk) | Public domain (LibriVox) | Trent fra bunnen av |
| `sv_SE-nst-medium` | Svensk | CC0 (Språkbanken) | Trent av KBLab |
| `da_DK-talesyntese-medium` | Dansk | CC0 (Språkbanken) | Finjustert fra «lessac» |

Stemmer med bare ikke-kommersiell lisens (f.eks. `en_US-hfc_female`, `en_US-ryan`) er utelatt.

**Kvalitet:** Medleser har *ikke* gjort en systematisk, lyttebasert kvalitetsvurdering av stemmene. Appen kaller dem derfor ikke «naturlige» eller «høykvalitets», og ber brukeren høre en stemmeprøve. Talerkodene i NVCC (KNN, KSV, MMN …) vises slik de står i modellen. Medleser har ikke verifisert hva kodene betyr.

## 2. Windows-stemmer (OneCore)

- Følger med Windows. På en norsk Windows-installasjon finnes vanligvis «Microsoft Jon» (nb-NO).
- Bruker `Windows.Media.SpeechSynthesis.SpeechSynthesizer` via pywinrt. `IncludeWordBoundaryMetadata` gir starttid og tegnposisjon for hvert ord.
- Flere stemmer: Windows-innstillinger › Tid og språk › Tale › Legg til stemmer.
- Lyden er 16 kHz mono.

## 3. ElevenLabs – betalte KI-stemmer

**Tilkobling:** veiviseren i «Stemmer og KI-tjenester». Brukeren lager selv en tilgangsnøkkel på <https://elevenlabs.io/app/settings/api-keys> og limer den inn. Nøkkelen lagres i Windows Legitimasjonsbehandling og sendes som headeren `xi-api-key`. Medleser ber aldri om passord og henter aldri innloggingsdata fra nettleseren. ElevenLabs har ingen dokumentert innloggingsflyt (OAuth) for tredjepartsapper som Medleser kan bruke.

**Anbefalte tillatelser for nøkkelen:**
- «Text to Speech» og «Voices» (lese) er nødvendig.
- «Models» og «User» (lese) er valgfritt, for å vise modeller og gjenstående kreditter.

ElevenLabs lar deg også sette en **kredittkvote på nøkkelen**. Det er den sikreste grensen, og veiviseren anbefaler den.

**Endepunkter:**

| Formål | Endepunkt | Koster kreditter? |
|---|---|---|
| Test tilkobling, list stemmer | `GET /v1/voices` | Nei |
| Gjenstående kreditter | `GET /v1/user/subscription` | Nei |
| Modeller, språk, tegngrense, kostnadsfaktor | `GET /v1/models` | Nei |
| Ferdige stemmeprøver | Stemmens `preview_url` (hentes uten nøkkel, bare fra tillatte domener) | Nei |
| Lag lyd med tidspunkter | `POST /v1/text-to-speech/{voice_id}/with-timestamps?output_format=pcm_24000` | **Ja** |

Lyden hentes som 24 kHz PCM, slik at bitene kan settes nøyaktig sammen. Tillater ikke kontoen PCM, brukes `mp3_44100_128`, som dekodes lokalt. `previous_text` og `next_text` sendes for jevnere tonefall mellom bitene.

**Modeller (ifølge ElevenLabs' dokumentasjon, oktober 2026).** Medleser henter den gjeldende listen fra `/v1/models` og viser bare modeller som støtter dokumentets språk.

| Modell | Norsk | Tegn per forespørsel | Kostnad | Ordtider bekreftet |
|---|---|---|---|---|
| `eleven_flash_v2_5` (standard) | Ja | 40 000 | Ca. 50 % lavere per tegn | Ja |
| `eleven_v4` | Ja (bokmål) | 10 000 | Se ElevenLabs | Ikke bekreftet av Medleser |
| `eleven_v3` | Ja | 5 000 | Se ElevenLabs | Ikke bekreftet av Medleser |
| `eleven_multilingual_v2` | **Nei** | 10 000 | Ca. 1 kreditt per tegn | Ja |

Medleser deler uansett teksten i biter på høyst 4 800 tegn, så et nytt forsøk aldri blir dyrt.

**Kostnadskontroll (absolutt krav):**
1. Generering starter aldri automatisk.
2. Før noe sendes, viser appen: tjeneste, stemme, modell, antall tegn og deler som skal sendes, deler som allerede er laget, estimert forbruk i kreditter og gjenstående kreditter når nøkkelen tillater det. Estimatet merkes alltid som *omtrentlig*, og en kostnad som ikke kan beregnes, vises som «Kan ikke beregnes sikkert».
3. Brukeren må krysse av for at teksten sendes og at kreditter kan brukes.
4. Godkjenningen er knyttet til nøyaktig de delene som vises. Endres teksten eller stemmen, må brukeren godkjenne på nytt (kontrolleres på serveren).
5. Forbruksgrense per dokument (standard 50 000 tegn, kan endres) stopper jobben før grensen overskrides. Grensen gjelder tegnene Medleser sender. Hvor mange kreditter ElevenLabs trekker, bestemmer ElevenLabs.
6. Ferdige deler lagres og sendes aldri på nytt. En avbrutt jobb fortsetter der den slapp.
7. En stemmeprøve med dokumentets egen tekst krever egen godkjenning, med antall tegn.

**Feil som håndteres** (vises på norsk, med tekniske detaljer under «Vis detaljer»):
- Ugyldig eller utløpt nøkkel (401): nøkkelen beholdes ikke.
- Manglende tillatelse (403 `missing_permissions`).
- Tom kvote (`quota_exceeded`).
- For mange forespørsler eller opptatt (429): nytt forsøk etter 2, 4 og 8 sekunder.
- Serverfeil (5xx): nytt forsøk.
- Manglende internett: nytt forsøk, deretter melding.
- Stemme finnes ikke (404).

**Kjente begrensninger:**
- Flash v2.5 normaliserer ikke tall og datoer som standard (ifølge ElevenLabs), så tall kan leses mindre naturlig. Medleser skriver vanlige forkortelser fullt ut selv.
- Medleser har ikke gjort ekte (betalte) kall mot ElevenLabs under utviklingen. Integrasjonen er testet mot et simulert API som følger den dokumenterte formen. En separat test mot det ekte API-et finnes, se TEST_REPORT.md.

## Planlagte tjenester

Google Cloud Text-to-Speech og Microsoft Azure AI Speech er forberedt i arkitekturen (felles `TTSProvider`-grensesnitt), men ikke implementert. De vises derfor ikke som valg, bare som «planlagt».
