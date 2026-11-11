# Politi-header (Web Viewer) — installation

Kort guide för att installera huvudmenyn för *Politiets helikoptertjeneste* flight log.

## Filer

| Fil | Roll |
| --- | --- |
| `Politi-HeaderHTML.html` | Självständig HTML/CSS/JS (förhandsgranska i webbläsare) |
| `Politi-Header-WebViewer.calc.txt` | Web Viewer-beräkning (läser fält, inte hela HTML:en) |

## Steg

1. **Skapa textfält för HTML**  
   Förslag: `Globals::HeaderHTML` (Text).  
   Valfritt containerfält för helikopterbild: `Globals::HeaderHeli` (Container).

2. **Klistra in HTML**  
   Öppna `Politi-HeaderHTML.html`, kopiera hela innehållet och klistra in i `Globals::HeaderHTML`  
   (t.ex. via en post i Globals-tabellen eller Layout → fält).

3. **Lägg till Web Viewer**  
   - Skapa ett Web Viewer-objekt på layouten.  
   - Ge objektet ett namn (t.ex. `wv_header`).  
   - Under webbadress / beräkning: klistra in innehållet från `Politi-Header-WebViewer.calc.txt`.  
   - **Viktigt (annars fungerar inga knappar):** i Web Viewer-inställningarna kryssa i  
     **Allow JavaScript to perform FileMaker scripts** (och gärna *Allow interaction*).  
   - Fält: `Helicop::HeaderHTML` / valfritt `Helicop::HeaderHeli`.

4. **Storlek**  
   Dimensionera Web Viewern så den täcker den gamla knappraden (och gärna hela brand-ytan: titel + lime-meny).  
   Ungefärlig höjd: ~125–140 px beroende på layout.

5. **Testa**  
   - **FileMaker Pro** först — klicka menyval och kontrollera att rätt script körs (scriptnamn = etikett).  
   - Därefter **WebDirect** — samma meny; PerformScript ska fungera via `data:text/html;base64,`-URL:en.

## Anteckningar

- Privilegier döljer inte knappar; scripten hanterar «No access».
- Toppnivå **Statistikk** och undermenyposterna **Statistikk** (Aktivitet / Logg due) anropar samma scriptnamn avsiktligt.
- Förhandsgranska HTML lokalt: öppna `agent/sandbox/Politi-HeaderHTML.html` i webbläsaren — `fm(...)` loggas till konsolen.
