# Veikart – forslag til senere versjoner

## Kort sikt (1.x)

- **Flere stemmetjenester:** Microsoft Azure AI Speech (ordgrenser via SDK, gode norske nevrale stemmer) og Google Cloud Text-to-Speech (tidspunkter via SSML-merker). Grensesnittet `TTSProvider` er klart.
- **Bedre tall og datoer:** Medleser skriver selv tall, datoer, prosent og enheter fullt ut på norsk, med sporbar kobling, før teksten går til stemmer uten egen normalisering (Piper, ElevenLabs Flash).
- **Uttaleordbok:** Brukeren kan rette uttalen av fagord og navn. For ElevenLabs kan dette kobles til «pronunciation dictionaries».
- **Bedre PDF-forståelse:** Tabeller uten linjer, matematiske formler (alternativ tekst), fotnoter som leses ved fotnotemerket, og bedre håndtering av figurtekst.
- **Tesseract som valgfri OCR-motor:** For språk Windows ikke har.
- **Lyttebasert kvalitetsvurdering av stemmer:** Strukturert lyttetest med studenter, og resultatene vist i appen.
- **Bekrefte ordtider for ElevenLabs v3 og v4:** Med en liten betalt test, godkjent av brukeren.
- **Egen sletteknapp for alle data:** Og eksport og import av prosjekter (uten nøkler).
- **Automatiske oppdateringer:** Signert installasjonsfil.
- **Kodesignering:** Av `Medleser.exe` og installasjonsfilen, for færre SmartScreen-advarsler.

## Mellomlang sikt

- **Lisensfleksibilitet:** PyMuPDF (AGPL) byttes med pypdfium2, og Piper kjøres som egen prosess, hvis prosjektet ønsker en mer tillatende lisens.
- **Repetisjon og læring:** Bokmerker, notater knyttet til tekststeder, «les setningen igjen» og ordforklaringer (utenfor v1).
- **Bedre støtte for skjermlesere:** Valgfri modus der skjermleseren leser setningen som spilles av, på forespørsel.
- **Flere språk i grensesnittet:** Nynorsk og engelsk (tekstene ligger samlet i visningene og er lette å trekke ut).

## Utenfor versjon 1 (bevisst utelatt)

Mobilapper, brukerkontoer, skylagring, deling, LMS-integrasjon (Canvas), KI-oppsummeringer, automatisk oversettelse, stemmekloning, abonnement gjennom Medleser, sosiale funksjoner og samarbeid, offentlig pensumbibliotek. Arkitekturen hindrer ikke dette senere.
