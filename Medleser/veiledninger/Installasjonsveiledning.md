# Slik installerer du Medleser

Medleser er et gratis program for Windows. Det leser opp PDF-, Word-, EPUB- og tekstfiler, og markerer ordene på skjermen mens de leses. Du kan også lagre lyden som MP3.

Installasjonen tar et par minutter. Du trenger ikke administratorrettigheter.

## Dette trenger du

| | |
|---|---|
| Datamaskin | Windows 11 eller Windows 10 (64-bit) |
| Diskplass | Ca. 500 MB til programmet og ca. 65 MB til en norsk stemme |
| Internett | Bare for å laste ned programmet og stemmen. Etterpå virker Medleser uten internett. |
| Rettigheter | Vanlig bruker holder. Medleser installeres bare for deg. |

## 1. Last ned

1. Gå til **https://github.com/magnus-nohr/medleser/releases/latest**.
2. Under **Assets** trykker du på **Medleser-Setup-1.0.0.exe**.
3. Filen havner i mappen **Nedlastinger**.

Nettleseren kan si at filen «ikke lastes ned ofte». Velg da **Behold** (i Edge: trykk på de tre prikkene ved filen og velg **Behold**, deretter **Behold likevel**).

## 2. Installer

1. Åpne mappen **Nedlastinger** og dobbeltklikk på **Medleser-Setup-1.0.0.exe**.
2. Windows viser kanskje en blå boks: **«Windows beskyttet PC-en»**. Trykk da på **Flere opplysninger**, og deretter på **Kjør likevel**. Advarselen kommer fordi Medleser er et nytt program som ikke er kodesignert ennå. Den betyr ikke at det er funnet noe farlig.
3. Les informasjonen om personvern, og trykk **Neste**.
4. Behold mappen som er foreslått, og trykk **Neste**.
5. Kryss av for **Lag ikon på skrivebordet** hvis du vil ha et ikon der. Trykk **Neste**.
6. Trykk **Installer**.
7. La **Kjør Medleser** være krysset av, og trykk **Ferdig**. Medleser starter.

Du finner Medleser i Start-menyen, og på skrivebordet hvis du valgte det.

## 3. Last ned en norsk stemme (første gang)

Medleser trenger en stemme for å lese opp. Den lastes ned én gang.

1. Trykk **Stemmer og KI-tjenester** i menyen til venstre.
2. Under **Norsk bokmål** finner du **Norsk (Talesyntese)**, merket **Anbefalt**.
3. Trykk **Last ned (63 MB)**.
4. Trykk **Hør prøve** for å høre stemmen.

Nå er Medleser klar. Se **Brukerveiledning** for resten.

Har Windows en norsk stemme fra før (for eksempel «Jon»), kan du også velge **Windows-stemmer**.

## Oppdatere til en ny versjon

1. Last ned den nye installasjonsfilen på samme måte.
2. Lukk Medleser.
3. Installer den nye versjonen. Den legger seg over den gamle.

Dokumentene, lyden og stemmene dine blir liggende.

## Avinstallere

1. Åpne **Innstillinger** i Windows.
2. Velg **Apper** og så **Installerte apper**.
3. Finn **Medleser**, trykk på de tre prikkene og velg **Avinstaller**.

Dokumentene, lyden og stemmene dine blir liggende i mappen `%LOCALAPPDATA%\Medleser`. Vil du fjerne alt, sletter du den mappen selv. Lim inn `%LOCALAPPDATA%` i adressefeltet i Filutforsker for å finne den.

## Hvis noe går galt

| Problem | Hva du kan gjøre |
|---|---|
| Jeg får ikke installert på en jobb- eller skole-PC | Noen maskiner tillater ikke nye programmer. Kontakt IT-avdelingen og vis dem denne veiledningen. |
| «Medleser trenger Microsoft Edge WebView2 Runtime» | Installer **WebView2 Runtime** fra Microsofts nettsider, og start Medleser på nytt. Den følger normalt med Windows 11. |
| Ingenting skjer når jeg starter Medleser | Medleser er kanskje allerede åpen. Se etter vinduet på oppgavelinjen. |
| Nedlastingen av stemmen stopper | Sjekk internettforbindelsen og prøv igjen. Halve filer blir forkastet automatisk. |

Finner du ikke svaret, kan du melde fra på **https://github.com/magnus-nohr/medleser/issues**. Legg gjerne ved filen `medleser.log` fra mappen `%LOCALAPPDATA%\Medleser\logs`.

---

Medleser er laget av Magnus Nohr og er fri programvare under lisensen GNU AGPL-3.0.
