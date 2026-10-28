# Tredjepartskomponenter og lisenser

Medleser bygger på åpen kildekode. Tabellen viser komponentene som følger med den ferdige Windows-versjonen, med lisensen slik pakkene selv oppgir den (hentet fra pakkenes metadata 8. oktober 2026).

## Viktig: konsekvenser for distribusjon

Flere komponenter har *copyleft*-lisens:

| Komponent | Lisens | Betydning |
|---|---|---|
| PyMuPDF (MuPDF) | GNU AGPL-3.0 (eller kommersiell lisens fra Artifex) | Den samlede appen må distribueres på AGPL-3.0-vilkår, med kildekode tilgjengelig for mottakerne. |
| piper-tts (inkl. espeak-ng) | GNU GPL-3.0-or-later | Forenlig med AGPL-3.0. Kildekoden må gjøres tilgjengelig. |
| lameenc (LAME MP3-koder) | GNU LGPL-3.0-or-later | Tillatt å bruke. Endringer i selve biblioteket må deles. |

**Anbefaling:** Lisensier Medleser under GNU AGPL-3.0-or-later, og legg kildekoden ved eller lenk til den når programmet deles. Selve lisensvalget for Medleser er ikke tatt. Det er prosjekteierens beslutning, og Medleser har derfor ingen LICENSE-fil ennå.

Ønsker man en mer tillatende lisens for Medleser, kan PyMuPDF byttes ut med `pypdfium2` (Apache-2.0/BSD) og Piper kjøres som et eget program. Se ROADMAP.md.

## Python-pakker i den ferdige appen

| Pakke | Versjon | Lisens |
|---|---|---|
| pymupdf | 1.28.2 | AGPL-3.0 / kommersiell |
| piper-tts | 1.8.0 | GPL-3.0-or-later |
| lameenc | 1.8.4 | LGPL-3.0-or-later |
| onnxruntime | 1.30.0 | MIT |
| onnx | 1.23.2 | Apache-2.0 |
| numpy | 2.5.3 | BSD-3-Clause m.fl. |
| miniaudio | 1.71 | MIT |
| python-docx | 1.2.0 | MIT |
| lxml | 6.1.3 | BSD-3-Clause |
| defusedxml | 0.7.1 | PSF |
| fastapi | 0.142.4 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| python-multipart | 0.0.32 | Apache-2.0 |
| pydantic / pydantic-core | 2.13.5 / 2.46.5 | MIT |
| anyio | 4.15.1 | MIT |
| h11 | 0.16.0 | MIT |
| httpx / httpcore | 0.28.1 / 1.0.9 | BSD-3-Clause |
| certifi | 2026.7.22 | MPL-2.0 |
| idna | 3.20 | BSD-3-Clause |
| pywebview | 6.2.1 | BSD |
| bottle (brukes av pywebview) | 0.13.4 | MIT |
| proxy_tools | 0.1.0 | MIT |
| pythonnet / clr_loader | 3.2.0 / 0.3.1 | MIT |
| keyring | 25.7.0 | MIT |
| jaraco.classes / .context / .functools | – | MIT |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| winrt-runtime og winrt-Windows.* (pywinrt) | 3.2.1 | MIT |
| protobuf | 7.36.2 | BSD-3-Clause |
| flatbuffers | 25.12.19 | Apache-2.0 |
| ml_dtypes | 0.6.0 | Apache-2.0 |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| pathvalidate | 3.3.1 | MIT |
| annotated-types, annotated-doc, typing-inspection, more-itertools, click, cffi, pycparser, typing_extensions | – | MIT / BSD / PSF |

Python selv (3.13) har PSF-lisensen.

## Skrifter (i `web/fonts/`)

| Skrift | Lisens |
|---|---|
| Atkinson Hyperlegible Next (Braille Institute) | SIL Open Font License 1.1 (`OFL-AtkinsonHyperlegible.txt`) |
| Lexend | SIL Open Font License 1.1 (`OFL-Lexend.txt`) |
| OpenDyslexic | SIL Open Font License 1.1 (`OFL-OpenDyslexic.txt`) |

## Microsoft C++-kjøretid

Den ferdige appen inneholder `msvcp140.dll` fra Microsoft Visual C++ Redistributable, som lokal kopi i programmappen. Den trengs fordi onnxruntime krever en nyere versjon enn den pywinrt følger med (se `medleser/runtime_fix.py`). Filen er underlagt Microsofts vilkår for videredistribusjon av Visual C++-kjøretiden. Alternativt kan brukeren installere «Microsoft Visual C++ Redistributable» fra Microsoft.

## Stemmemodeller (lastes ned av brukeren, følger ikke med)

Piper-stemmene lastes ned fra <https://huggingface.co/rhasspy/piper-voices> når brukeren ber om det. Lisensen for hver stemme vises i appen og er beskrevet i [TTS_PROVIDERS.md](TTS_PROVIDERS.md).
