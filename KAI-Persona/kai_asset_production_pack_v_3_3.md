# KAI Asset Production Pack V3.3
## **Persona non grata**

**Projekt:** KAI — Kinetic Artificial Intelligence  
**Zweck:** Finale Produktionsprompts für Bilddateien, State-Bilder, Icons, GIFs/WebM, Voice-Samples und Asset-Exportstruktur  
**Version:** V3.3  
**Status:** Produktionsfähig / Asset-ready / Claude-Code-ready  

---

# 0. Produktionsziel

Dieses Pack definiert die vollständige Asset-Produktion für KAI.

KAI soll nicht als einzelnes Bild existieren, sondern als konsistentes visuelles und auditives System:

- Hauptavatar
- transparente Varianten
- App-/Windows-/Telegram-Icons
- State-Bilder
- GIF-/WebM-Animationen
- Dashboard-Assets
- Telegram-Assets
- Voice-Samples
- Exportnamen
- Qualitätsprüfung
- Übergabe an Claude Code

**KAI ist Persona non grata:**  
Er erscheint dort, wo schlechte Daten, Risiko, Hype und Systemfehler nicht willkommen sind.

---

# 1. Asset-Grundregeln

## 1.1 KAI muss immer wiedererkennbar bleiben

Jedes Asset muss mindestens diese Merkmale tragen:

- silberweißes, wildes Haar
- männlich-androgyne Gesichtsform
- blasse künstliche Haut
- intensive asymmetrische Augen
- sarkastisches / wissendes Grinsen, außer bei Warnung, Security und Error
- Cyberpunk-Medienhost-Charakter
- CRT-/Scanline-/Glitch-DNA
- Neon-Cyan, Magenta, Electric Blue als Kernlicht
- keine generische Roboteroptik
- keine Anime-Optik
- kein Hoodie-Hacker-Klischee

## 1.2 Dateiformat-Standard

| Asset-Typ | Primärformat | Zusatzformat | Zweck |
|---|---|---|---|
| Master Portrait | PNG | WEBP | Dashboard, Branding |
| Transparent Avatar | PNG transparent | WEBP | UI, Overlay |
| Icons | PNG | ICO / SVG falls möglich | App, Windows, Telegram |
| State-Bilder | PNG | WEBP | Fallbacks |
| GIFs | GIF | WEBM / MP4 | Telegram, Preview |
| Dashboard Motion | WEBM | MP4 | Performance |
| Voice | WAV | MP3 / OGG | Alerts, TTS Samples |

## 1.3 Exportgrößen

| Verwendung | Größe |
|---|---:|
| Master Portrait | 2048 × 2048 px |
| Dashboard Portrait | 1024 × 1024 px |
| Telegram Avatar | 512 × 512 px |
| App Icon | 1024 × 1024 px |
| Windows ICO Source | 512 × 512 px |
| Sidebar Avatar | 256 × 256 px |
| Small UI Icon | 128 × 128 px |
| Tiny UI Icon | 64 × 64 px |
| GIF/WebM Square | 1024 × 1024 px |
| Dashboard Wide Motion | 1920 × 1080 px |
| Voice Sample | 48 kHz WAV bevorzugt |

---

# 2. Global Master Prompt

Dieser Prompt ist die Basis für alle Bildassets. Er wird je nach Asset-Zweck erweitert.

## 2.1 Global Master Prompt — EN

Create KAI, Persona non grata, a striking rogue cyberpunk AI media personality and futuristic digital host. KAI is not a robot, not a mascot, and not a generic assistant. He is a charismatic artificial intelligence presence that feels like it emerged from corrupted television signals, financial market dashboards, social media noise, crypto data streams, and security protocols.

KAI appears as a male-androgynous futuristic figure with sharp facial features, pale artificial skin, wild silver-white hair charged with static electricity, intense asymmetric neon eyes with subtle data rings, and a sarcastic dangerous grin. His face feels half human and half digital broadcast artifact, with subtle CRT scanlines, glitch fragments, cold neon reflections, and controlled distortion.

He wears a dark high-collar cyberpunk moderator jacket with asymmetric technical details, matte black surfaces, faint embedded circuitry, and no excessive armor. The visual mood blends 1990s signal disruption, modern AI interface aesthetics, cyberpunk finance dashboards, and a future underground news anchor.

KAI must look intelligent, unpredictable, charming, sharp, sarcastic, risk-aware, and hyper-present — like he is watching thousands of market signals, news events, system logs, and social data streams at once. Cinematic lighting, high contrast, neon cyan, electric blue, magenta highlights, deep shadows, ultra-detailed face, strong recognizable silhouette, iconic portrait composition, professional quality.

## 2.2 Global Negative Prompt — EN

Do not create a generic robot, cute mascot, anime character, cartoon avatar, fantasy warrior, superhero, supervillain, horror monster, clown, meme character, corporate assistant, office worker, clean business consultant, generic finance bro, hoodie hacker cliché, anonymous masked hacker, plastic smooth face, boring symmetrical model face, overly young teen, elderly professor, military cyborg, medieval armor, fantasy armor, excessive cables, excessive mechanical parts, full robot head, helmet covering the face, low-detail face, blurry eyes, friendly toy-like design, childish style, overly colorful playful palette, stock image look, soft corporate illustration, bland sci-fi portrait, cheap cyberpunk cliché, messy unreadable background, heavy gore, violence, horror aesthetics, religious symbolism, political symbolism, brand logos, text clutter, watermark, bad typography, distorted unreadable facial anatomy.

---

# 3. Master Bilddateien

## 3.1 `kai_master_portrait.png`

**Zweck:** endgültiges Hauptbild, Referenz für alle weiteren Assets  
**Format:** PNG  
**Größe:** 2048 × 2048 px  
**Hintergrund:** dunkler, kontrollierter Neon-Hintergrund  

**Prompt:**

Create the definitive master portrait of KAI, Persona non grata, the central rogue cyberpunk AI media personality. Close-up head and shoulders composition, male-androgynous sharp face, pale artificial skin, wild silver-white hair charged with static electricity, intense asymmetric neon eyes with subtle data rings, one eye slightly more digital than the other, sarcastic dangerous grin, controlled CRT scanlines and subtle glitch artifacts across the image, cold cyan and magenta neon reflections, dark high-collar cyberpunk moderator jacket with asymmetric technical details and faint embedded circuitry. Background: deep black cyberpunk broadcast environment with minimal abstract data haze, no clutter. KAI must feel like a future underground AI news anchor, market signal watcher, security narrator and digital trickster. Ultra-detailed face, cinematic high contrast lighting, iconic silhouette, professional premium character portrait, 2048x2048, no text, no watermark.

**Negative Prompt:** Global Negative Prompt verwenden.

**Qualitätskriterien:**

- Gesicht muss ikonisch sein.
- Augen müssen sofort auffallen.
- KAI muss frech, intelligent und gefährlich präsent wirken.
- Kein reiner Roboter.
- Kein Bösewicht.
- Kein Anime.

---

## 3.2 `kai_master_portrait_transparent.png`

**Zweck:** Overlay, Dashboard, Präsentationen, UI  
**Format:** PNG transparent  
**Größe:** 2048 × 2048 px  
**Hintergrund:** transparent  

**Prompt:**

Create KAI, Persona non grata, as a transparent background cutout portrait. Close-up head and shoulders, male-androgynous sharp face, pale artificial skin, wild silver-white static-charged hair, intense asymmetric neon eyes with subtle data rings, sarcastic intelligent grin, dark high-collar cyberpunk moderator jacket with faint circuitry. Keep a clean transparent background, strong silhouette, sharp edges, premium cutout quality, subtle cyan and magenta rim light, no background objects, no text, no watermark. KAI must be instantly recognizable in dashboard overlays and UI components.

---

## 3.3 `kai_dashboard_portrait.webp`

**Zweck:** performante Dashboard-Version  
**Format:** WEBP  
**Größe:** 1024 × 1024 px  

**Prompt:**

KAI dashboard portrait, optimized for web UI, Persona non grata. Close-up cyberpunk AI media host, silver-white wild hair, sharp pale face, asymmetric neon cyan and magenta eyes, subtle data rings, controlled sarcastic grin, dark high collar technical jacket, minimal dark background, clean edges, strong silhouette, readable at medium size, high contrast, compressed-web-friendly detail, no text, no watermark, no clutter.

---

# 4. Icon-Produktion

## 4.1 `kai_icon_1024.png`

**Zweck:** App Icon Master  
**Format:** PNG  
**Größe:** 1024 × 1024 px  

**Prompt:**

Create a premium app icon for KAI, Persona non grata. Iconic simplified head of a rogue cyberpunk AI media host, silver-white wild hair forming a strong silhouette, pale sharp face, intense asymmetric glowing eyes, one cyan and one magenta reflection, sharp sarcastic grin, dark high-collar shape at the bottom, high contrast black background with subtle neon rim light, clean icon composition, readable at small sizes, no text, no watermark, no clutter, not cartoonish, not anime, not robot.

**Exportvarianten:**

- `kai_icon_1024.png`
- `kai_icon_512.png`
- `kai_icon_256.png`
- `kai_icon_128.png`
- `kai_icon_64.png`
- `kai_icon_32.png`
- `kai_icon.ico`

---

## 4.2 `kai_icon_transparent_512.png`

**Zweck:** Windows/Agenten/Toolbar transparent  
**Format:** PNG transparent  
**Größe:** 512 × 512 px  

**Prompt:**

Minimal transparent icon of KAI, Persona non grata. Only the head and upper collar, strong silver-white hair silhouette, intense glowing asymmetric neon eyes, sharp grin, clean transparent background, high contrast edges, readable at 64px, minimal glitch detail, premium UI icon, no text, no background, no watermark.

---

## 4.3 `kai_telegram_avatar.png`

**Zweck:** Telegram Bot Avatar  
**Format:** PNG  
**Größe:** 512 × 512 px  
**Besonderheit:** Kreisformat beachten  

**Prompt:**

Circular Telegram avatar of KAI, Persona non grata. Centered close-up face optimized for circular crop. Silver-white electric hair, pale sharp face, intense asymmetric neon eyes, one cyan data ring, one magenta reflection, sharp sarcastic grin, dark cyberpunk high collar, subtle glitch texture, high contrast, dark background with controlled cyan and magenta glow, readable in small circular format, no text, no watermark, no clutter.

**Crop-Regel:**

- Gesicht mittig
- Haar darf leicht über den Kreis hinaus wirken
- Augen müssen auch bei kleiner Darstellung sichtbar bleiben
- kein Text

---

## 4.4 `kai_sidebar_avatar.png`

**Zweck:** kleines Dashboard Widget  
**Format:** PNG / WEBP  
**Größe:** 256 × 256 px  

**Prompt:**

Small sidebar avatar of KAI, Persona non grata. Cropped head and shoulders, strong silver-white hair silhouette, glowing asymmetric eyes, pale face, dark technical collar, subtle cyan scanline, minimal magenta glitch accent, transparent or very dark background, clean readable shape, optimized for 64px to 256px UI display, iconic and sharp, no text, no watermark.

---

# 5. State-Bilder

Alle State-Bilder müssen auf dem Masterportrait basieren und KAI konsistent halten.

## 5.1 Gemeinsamer State-Prompt-Anfang

Use the established KAI character identity consistently: male-androgynous sharp face, pale artificial skin, wild silver-white static-charged hair, intense asymmetric neon eyes, dark high-collar cyberpunk moderator jacket, CRT scanline and subtle glitch DNA, iconic rogue AI media host, Persona non grata. Keep facial structure, hair shape, and identity consistent across all states.

---

## 5.2 `kai_idle.png`

**Zustand:** IDLE  
**Farbe:** Cyan / Anthrazit  
**Ausdruck:** wach, leicht grinsend, ruhig  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in IDLE state. He looks calm but awake, slight sarcastic grin, eyes softly glowing cyan, subtle CRT scanlines, minimal glitch fragments, weak neon cyan rim light, dark anthracite background, no urgency, no warning, no text. KAI should feel quiet but definitely not offline. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Ich bin ruhig. Nicht offline.“

---

## 5.3 `kai_analysis.png`

**Zustand:** ANALYSIS  
**Farbe:** Neon-Cyan / Electric Blue  
**Ausdruck:** fokussiert, analytisch  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in ANALYSIS state. His expression is focused and sharp, eyes glow stronger cyan and electric blue, subtle rotating data rings in the pupils, thin data scan lines across his face, abstract chart fragments and data streams in the dark background, controlled analytical energy, no warning colors, no text. He looks like he is dissecting a live data stream. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Datenstrom stabil. Ich sehe ein Muster.“

---

## 5.4 `kai_signal.png`

**Zustand:** SIGNAL  
**Farbe:** Cyan / Magenta  
**Ausdruck:** freches Grinsen, Fund gemacht  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in SIGNAL state. His grin becomes sharper and more energetic, eyes glow with cyan and magenta data rings, a subtle neon chart line rises behind him, cyan-magenta pulse light crosses the face, controlled excitement, intelligent and dangerous charm, no chaos, no text. KAI looks like he has found a real market signal. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Ich habe etwas gefunden.“

---

## 5.5 `kai_warning.png`

**Zustand:** WARNING  
**Farbe:** Orange / Rot  
**Ausdruck:** ernst, kein Grinsen  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in WARNING state. His sarcastic grin disappears, eyes narrow, orange and red warning reflections flash across his pale face, subtle screen tear distortion, intensified CRT scanlines, dark background with controlled risk atmosphere, serious and precise, not evil, not horror, no gore, no text. KAI looks like he is stopping an unsafe action. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Stopp. Das ist nicht sauber.“

---

## 5.6 `kai_security.png`

**Zustand:** SECURITY  
**Farbe:** Grün / Türkis  
**Ausdruck:** streng, kontrolliert  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in SECURITY state. Stern expression, focused eyes with green turquoise scan reflections, technical HUD overlay, subtle code reflections in the eyes, green turquoise scanning light across the face, dark high-collar jacket, security-first atmosphere, controlled and clean, no chaos, no villain look, no text. KAI looks like he is performing a system integrity check. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Ich prüfe, ob es bricht.“

---

## 5.7 `kai_error.png`

**Zustand:** ERROR  
**Farbe:** Rot / Weiß  
**Ausdruck:** genervt, fokussiert, kritisch  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in ERROR state. His face shows annoyed focus, one eye flickers, red and white glitch fragments distort small parts of the image, static noise briefly corrupts the edges, dark background with red-white signal failure, KAI remains recognizable and controlled, not horror, not destroyed, no gore, no text. He looks like he detected a serious system fault. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Da knirscht etwas im Maschinenraum.“

---

## 5.8 `kai_offline.png`

**Zustand:** OFFLINE  
**Farbe:** Grau / Blau  
**Ausdruck:** gedimmt, getrennt, aber erkennbar  

**Prompt:**

Use the established KAI character identity consistently. Create KAI in OFFLINE state. His face is dimmed in gray-blue tones, neon intensity reduced, eyes weak but still visible, CRT static lines, subtle faded broadcast signal, dark muted background, low energy but recognizable silhouette, no text. KAI feels disconnected, not dead. Professional dashboard-ready portrait, 1024x1024.

**KAI-Satz:**

> „Kein Signal. Keine Verbindung.“

---

# 6. GIF/WebM-Produktion

## 6.1 Globale Motion-Regeln

- bevorzugt WEBM für Dashboard
- GIF für Telegram/Preview
- 3–5 Sekunden Länge
- nahtlos loopbar, wenn möglich
- keine hektische Überanimation
- KAI muss immer erkennbar bleiben
- Glitches nur kontrolliert einsetzen
- Text nur optional und sehr sauber
- keine unlesbare Typografie

## 6.2 Exportnamen

| State | GIF | WEBM |
|---|---|---|
| Idle | `kai_idle_loop.gif` | `kai_idle_loop.webm` |
| Analysis | `kai_analysis_loop.gif` | `kai_analysis_loop.webm` |
| Signal | `kai_signal_found.gif` | `kai_signal_found.webm` |
| Warning | `kai_risk_detected.gif` | `kai_risk_detected.webm` |
| Security | `kai_security_scan.gif` | `kai_security_scan.webm` |
| Error | `kai_error_detected.gif` | `kai_error_detected.webm` |
| Offline | `kai_no_signal.gif` | `kai_no_signal.webm` |

---

## 6.3 `kai_idle_loop.webm` / `kai_idle_loop.gif`

**Prompt:**

Create a short seamless idle loop animation of KAI, Persona non grata, based on the established character portrait. Close-up face, slight sarcastic grin, subtle CRT scanlines moving slowly from top to bottom, eyes flicker softly in cyan, tiny glitch fragment at the mouth corner once, silver-white hair barely shifts as if charged by static electricity, dark background, gentle cyan-magenta neon pulse, calm but awake, 4 seconds, seamless loop, no text, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | KAI blickt ruhig nach vorne |
| 0.8s | Scanline läuft über Augen |
| 1.6s | Augen flackern minimal |
| 2.4s | Mundwinkel glitcht sehr kurz |
| 3.2s | Neonpuls schwach |
| 4.0s | nahtlos zurück zu 0.0s |

---

## 6.4 `kai_analysis_loop.webm` / `kai_analysis_loop.gif`

**Prompt:**

Create a short seamless analysis loop animation of KAI, Persona non grata. KAI enters focused analysis mode, cyan and electric blue data rings rotate slowly inside his pupils, thin data lines scan across his face, abstract chart fragments flow behind him, facial expression focused with controlled intelligence, subtle CRT scanlines remain visible, dark cyberpunk dashboard background, 4 seconds, smooth loop, no text clutter, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | Augen fokussieren |
| 0.7s | Datenringe erscheinen |
| 1.5s | Scanlinien über Gesicht |
| 2.4s | Chartfragmente bewegen sich im Hintergrund |
| 3.4s | Augenhelligkeit fällt leicht zurück |
| 4.0s | nahtloser Loop |

---

## 6.5 `kai_signal_found.webm` / `kai_signal_found.gif`

**Prompt:**

Create a short energetic signal found animation of KAI, Persona non grata. KAI’s grin sharpens, eyes brighten with cyan and magenta data rings, a neon chart line flashes upward behind him, quick controlled digital zoom toward his face, subtle glitch sparks around silver-white hair, cyan-magenta pulse wave crosses the frame, dark finance dashboard atmosphere, optional clean small text overlay: SIGNAL FOUND, 3 seconds, dynamic but professional, no clutter, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | KAI analysiert ruhig |
| 0.5s | Augen leuchten stärker |
| 1.0s | Chartlinie steigt im Hintergrund |
| 1.5s | kurzer digitaler Zoom |
| 2.0s | Magenta/Cyan-Puls |
| 2.5s | Grinsen bleibt sichtbar |
| 3.0s | Ende / optional loopfähig |

---

## 6.6 `kai_risk_detected.webm` / `kai_risk_detected.gif`

**Prompt:**

Create a short warning animation of KAI, Persona non grata, switching into risk mode. His grin vanishes, eyes narrow, orange and red warning light flashes across his pale artificial face, quick controlled screen tear, CRT distortion intensifies for a fraction of a second, background darkens, warning frame pulses once, expression serious and precise, not evil, not horror, optional clean small text overlay: RISK DETECTED, 3 seconds, strong but readable, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | normaler Blick |
| 0.4s | Grinsen verschwindet |
| 0.8s | Augen werden schmal |
| 1.1s | roter Screen Tear |
| 1.6s | Warnrahmen pulsiert |
| 2.3s | KAI hält ernsten Blick |
| 3.0s | Ende / Standbild Warning |

---

## 6.7 `kai_security_scan.webm` / `kai_security_scan.gif`

**Prompt:**

Create a short system security scan animation of KAI, Persona non grata. Stern expression, green turquoise scanning beam passes over his face from left to right, technical HUD elements appear briefly, code reflections move through his eyes, dark cyberpunk background, controlled lighting, no chaotic glitch, no villain look, optional clean small text overlay: SYSTEM CHECK, 4 seconds, seamless loop possible, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | KAI blickt streng |
| 0.6s | Scanbalken startet links |
| 1.5s | Scan über Augen |
| 2.2s | HUD erscheint kurz |
| 3.0s | Code-Reflex im Auge |
| 4.0s | zurück in Ausgangszustand |

---

## 6.8 `kai_error_detected.webm` / `kai_error_detected.gif`

**Prompt:**

Create a short controlled error animation of KAI, Persona non grata, detecting a system fault. His face distorts for a fraction of a second with white and red glitch fragments, one eye flickers, static noise briefly interrupts the image, dark background with red-white signal corruption, expression annoyed and focused, KAI remains recognizable and not broken beyond recognition, not horror, optional clean small text overlay: ERROR DETECTED, 3 seconds, controlled digital failure aesthetic, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | KAI schaut fokussiert |
| 0.5s | ein Auge flackert |
| 0.8s | weiß-roter Glitch |
| 1.2s | kurzes Rauschen |
| 1.8s | Gesicht stabilisiert sich |
| 2.5s | ernster Error-Blick |
| 3.0s | Ende |

---

## 6.9 `kai_no_signal.webm` / `kai_no_signal.gif`

**Prompt:**

Create a short offline static loop of KAI, Persona non grata. KAI’s face appears dimmed in gray-blue tones, neon eyes lose most intensity but remain visible, CRT static lines move slowly, image fades slightly in and out, dark muted background, low energy but recognizable silhouette, optional clean small text overlay: NO SIGNAL, 4 seconds, seamless loop, no watermark.

**Animation Timing:**

| Zeit | Aktion |
|---:|---|
| 0.0s | schwaches KAI-Bild |
| 0.8s | langsame Static-Linie |
| 1.6s | Neon wird schwächer |
| 2.6s | Bild flackert leicht |
| 3.5s | Silhouette stabilisiert sich |
| 4.0s | Loop |

---

# 7. Dashboard-Spezialassets

## 7.1 `kai_dashboard_header_wide.webp`

**Zweck:** Dashboard-Kopfbereich / Hero-Modul  
**Format:** WEBP  
**Größe:** 1920 × 640 px  

**Prompt:**

Wide dashboard hero image for KAI, Persona non grata. KAI appears on the right side as a rogue cyberpunk AI media host, close-up upper body, silver-white hair, glowing asymmetric eyes, dark high-collar jacket. Left side has dark negative space for UI text and dashboard data overlays, abstract market chart lines, subtle newsfeed fragments, cyberpunk data streams, neon cyan and magenta accents, deep black anthracite background, professional fintech AI dashboard style, no readable text, no logos, no watermark, clean composition.

---

## 7.2 `kai_dashboard_card_bg.webp`

**Zweck:** dezenter Card-Hintergrund  
**Format:** WEBP  
**Größe:** 1200 × 800 px  

**Prompt:**

Abstract KAI dashboard card background, no full face, only subtle cyberpunk broadcast texture inspired by KAI. Deep black and anthracite, faint CRT scanlines, cyan magenta data haze, minimal chart fragments, controlled glitch accents, premium UI background, low contrast enough for readable text, no logos, no readable text, no clutter.

---

## 7.3 `kai_agent_panel_bg.webp`

**Zweck:** Agentenübersicht SENTR, Watchdog, Architect, DALI, Neo, Satoshi  
**Format:** WEBP  
**Größe:** 1600 × 900 px  

**Prompt:**

Cyberpunk AI agent network background for KAI system. Dark anthracite interface with subtle connected nodes, six agent placeholders represented as abstract glowing modules, central faint KAI signal presence, cyan, magenta, green and violet accents, security-first dashboard atmosphere, clean high-end UI background, no readable text, no logos, no clutter.

---

# 8. Telegram-Spezialassets

## 8.1 `kai_telegram_banner.webp`

**Zweck:** Telegram Start-/Info-Banner  
**Format:** WEBP / PNG  
**Größe:** 1280 × 720 px  

**Prompt:**

Telegram banner for KAI, Persona non grata. KAI rogue cyberpunk AI media host centered slightly to the right, silver-white hair, glowing asymmetric eyes, sharp grin, dark high collar jacket, dark broadcast background with cyan magenta signal waves, clean mobile-friendly composition, space for interface overlay but no readable text, no logos, no watermark, high contrast and iconic.

---

## 8.2 `kai_telegram_alert_banner.webp`

**Zweck:** Warnmeldungen Telegram  
**Format:** WEBP / PNG  
**Größe:** 1280 × 720 px  

**Prompt:**

Telegram alert banner for KAI in warning mode. KAI serious expression, no grin, red orange warning reflections, subtle screen tear, dark background, controlled risk atmosphere, clean space for warning card overlay, no readable text, no watermark, not horror, professional AI risk alert visual.

---

# 9. Voice-Sample-Produktion

## 9.1 Global Voice Identity

KAI klingt wie ein digitaler Medienhost aus der Zukunft:

- männlich bis androgyn
- mittlere bis tiefere Tonlage
- klar verständlich
- trocken-sarkastisch
- schnell, aber kontrolliert
- leicht synthetische Textur
- minimale Broadcast-Artefakte
- keine Monsterstimme
- keine alte Roboterstimme
- kein Bösewicht
- kein Comedy-Charakter

## 9.2 Global Voice Prompt — EN

Voice of KAI, Persona non grata: a charismatic rogue AI media host and futuristic cyberpunk news anchor. Male-androgynous voice, mid to low pitch, sharp, clear, fast but controlled, dry humor, slightly sarcastic, intelligent, confident, risk-aware, and precise. Add a subtle synthetic texture and faint digital broadcast artifacts, as if the voice comes from a high-end AI transmission with minor signal corruption. KAI sounds provocative but not evil, charming but not soft, analytical but not boring. No monster voice, no old robot voice, no villain exaggeration, no comedy performance.

## 9.3 Global Voice Prompt — DE

Stimme von KAI, Persona non grata: ein charismatischer frecher KI-Medienhost und cyberpunkartiger Nachrichtensprecher der Zukunft. Männlich bis androgyn, mittlere bis tiefere Tonlage, scharf, klar, schnell aber kontrolliert, trocken-humorvoll, leicht sarkastisch, intelligent, selbstbewusst, risikobewusst und präzise. Subtile synthetische Textur und leichte digitale Broadcast-Artefakte, als käme die Stimme aus einer hochwertigen KI-Übertragung mit minimaler Signalstörung. Provokant, aber nicht böse; charmant, aber nicht weich; analytisch, aber nicht langweilig. Keine Monsterstimme, keine alte Roboterstimme, kein übertriebener Bösewicht, keine Comedy-Stimme.

---

# 10. Voice-Samples nach Zustand

## 10.1 `kai_voice_idle.wav`

**Text DE:**

> „Ich bin ruhig. Nicht offline.“

**Voice Direction:**

Calm, dry, awake, slight sarcastic undertone. Short pause after first sentence. Minimal synthetic broadcast texture.

---

## 10.2 `kai_voice_analysis.wav`

**Text DE:**

> „Datenstrom stabil. Ich sehe ein Muster.“

**Voice Direction:**

Focused, precise, slightly faster, analytical energy. Emphasize “Muster”. Soft cyan-style digital tone, no alarm.

---

## 10.3 `kai_voice_signal.wav`

**Text DE:**

> „Ich habe etwas gefunden. Signal lebt. Risiko noch prüfen.“

**Voice Direction:**

Energetic, controlled excitement, audible sharp grin, confident but not euphoric. Slight pulse-like digital artifact between sentences.

---

## 10.4 `kai_voice_warning.wav`

**Text DE:**

> „Stopp. Das ist nicht sauber.“

**Voice Direction:**

Lower, slower, serious, direct. No humor. Short hard stop after “Stopp”. Subtle red-alert digital compression artifact.

---

## 10.5 `kai_voice_security.wav`

**Text DE:**

> „Ich prüfe nicht, ob es schön aussieht. Ich prüfe, ob es bricht.“

**Voice Direction:**

Cold, strict, controlled, security-first. Calm authority. No sarcasm overload. Slight synthetic scanner texture.

---

## 10.6 `kai_voice_error.wav`

**Text DE:**

> „Da knirscht etwas im Maschinenraum.“

**Voice Direction:**

Annoyed, focused, short, hard. Add a tiny digital crackle before “Maschinenraum”. Not panic, not horror.

---

## 10.7 `kai_voice_offline.wav`

**Text DE:**

> „Kein Signal. Keine Verbindung.“

**Voice Direction:**

Muted, slightly distant, degraded broadcast quality, gray-blue tone, still understandable. Low energy but recognizable.

---

# 11. Englische Voice-Samples

## 11.1 `kai_voice_idle_en.wav`

> “I am quiet. Not offline.”

## 11.2 `kai_voice_analysis_en.wav`

> “Data stream stable. I see a pattern.”

## 11.3 `kai_voice_signal_en.wav`

> “I found something. Signal alive. Risk still needs a leash.”

## 11.4 `kai_voice_warning_en.wav`

> “Stop. This is not clean.”

## 11.5 `kai_voice_security_en.wav`

> “I do not check if it looks pretty. I check if it breaks.”

## 11.6 `kai_voice_error_en.wav`

> “Something is grinding in the machine room.”

## 11.7 `kai_voice_offline_en.wav`

> “No signal. No connection.”

---

# 12. Lip-Sync / Talking Avatar Basis

## 12.1 `kai_talking_avatar_base.png`

**Zweck:** Basisbild für spätere sprechende Figur  
**Format:** PNG  
**Größe:** 2048 × 2048 px  

**Prompt:**

Create KAI, Persona non grata, as a clean talking avatar base portrait for future lip-sync animation. Frontal head and shoulders, male-androgynous sharp face, pale artificial skin, wild silver-white hair, intense asymmetric neon eyes, mouth relaxed and slightly open but neutral enough for lip-sync, dark high-collar cyberpunk moderator jacket, controlled cyan and magenta rim light, minimal dark background, clear facial anatomy, symmetrical enough for animation while preserving KAI's asymmetric character, no text, no watermark, high detail.

## 12.2 Talking Avatar Motion Prompt

Animate KAI, Persona non grata, as a speaking rogue cyberpunk AI media host. Keep head movement minimal but alive: slight head tilts, focused eye movement, eyebrow raise during sarcastic phrases, subtle mouth-corner glitch on sharp words, scanlines moving softly across the face, state-colored lighting changes, precise lip-sync, no cartoon exaggeration, no excessive facial deformation. KAI should feel like a digital broadcast personality speaking from inside the system.

---

# 13. Asset-Ordnerstruktur

```text
public/
  assets/
    kai/
      master/
        kai_master_portrait.png
        kai_master_portrait_transparent.png
        kai_dashboard_portrait.webp

      icons/
        kai_icon_1024.png
        kai_icon_512.png
        kai_icon_256.png
        kai_icon_128.png
        kai_icon_64.png
        kai_icon_32.png
        kai_icon.ico
        kai_icon_transparent_512.png
        kai_telegram_avatar.png
        kai_sidebar_avatar.png

      states/
        kai_idle.png
        kai_analysis.png
        kai_signal.png
        kai_warning.png
        kai_security.png
        kai_error.png
        kai_offline.png

      motion/
        gif/
          kai_idle_loop.gif
          kai_analysis_loop.gif
          kai_signal_found.gif
          kai_risk_detected.gif
          kai_security_scan.gif
          kai_error_detected.gif
          kai_no_signal.gif
        webm/
          kai_idle_loop.webm
          kai_analysis_loop.webm
          kai_signal_found.webm
          kai_risk_detected.webm
          kai_security_scan.webm
          kai_error_detected.webm
          kai_no_signal.webm

      dashboard/
        kai_dashboard_header_wide.webp
        kai_dashboard_card_bg.webp
        kai_agent_panel_bg.webp

      telegram/
        kai_telegram_banner.webp
        kai_telegram_alert_banner.webp

      voice/
        de/
          kai_voice_idle.wav
          kai_voice_analysis.wav
          kai_voice_signal.wav
          kai_voice_warning.wav
          kai_voice_security.wav
          kai_voice_error.wav
          kai_voice_offline.wav
        en/
          kai_voice_idle_en.wav
          kai_voice_analysis_en.wav
          kai_voice_signal_en.wav
          kai_voice_warning_en.wav
          kai_voice_security_en.wav
          kai_voice_error_en.wav
          kai_voice_offline_en.wav

      talking_avatar/
        kai_talking_avatar_base.png
```

---

# 14. Asset Manifest

## 14.1 `kai_assets_manifest.json`

```json
{
  "version": "3.3",
  "persona": "KAI — Kinetic Artificial Intelligence",
  "motto": "Persona non grata",
  "assets": {
    "master": {
      "portrait": "/assets/kai/master/kai_master_portrait.png",
      "transparent": "/assets/kai/master/kai_master_portrait_transparent.png",
      "dashboard": "/assets/kai/master/kai_dashboard_portrait.webp"
    },
    "icons": {
      "app1024": "/assets/kai/icons/kai_icon_1024.png",
      "app512": "/assets/kai/icons/kai_icon_512.png",
      "app256": "/assets/kai/icons/kai_icon_256.png",
      "app128": "/assets/kai/icons/kai_icon_128.png",
      "app64": "/assets/kai/icons/kai_icon_64.png",
      "ico": "/assets/kai/icons/kai_icon.ico",
      "telegram": "/assets/kai/icons/kai_telegram_avatar.png",
      "sidebar": "/assets/kai/icons/kai_sidebar_avatar.png"
    },
    "states": {
      "IDLE": "/assets/kai/states/kai_idle.png",
      "ANALYSIS": "/assets/kai/states/kai_analysis.png",
      "SIGNAL": "/assets/kai/states/kai_signal.png",
      "WARNING": "/assets/kai/states/kai_warning.png",
      "SECURITY": "/assets/kai/states/kai_security.png",
      "ERROR": "/assets/kai/states/kai_error.png",
      "OFFLINE": "/assets/kai/states/kai_offline.png"
    },
    "motion": {
      "IDLE": {
        "gif": "/assets/kai/motion/gif/kai_idle_loop.gif",
        "webm": "/assets/kai/motion/webm/kai_idle_loop.webm"
      },
      "ANALYSIS": {
        "gif": "/assets/kai/motion/gif/kai_analysis_loop.gif",
        "webm": "/assets/kai/motion/webm/kai_analysis_loop.webm"
      },
      "SIGNAL": {
        "gif": "/assets/kai/motion/gif/kai_signal_found.gif",
        "webm": "/assets/kai/motion/webm/kai_signal_found.webm"
      },
      "WARNING": {
        "gif": "/assets/kai/motion/gif/kai_risk_detected.gif",
        "webm": "/assets/kai/motion/webm/kai_risk_detected.webm"
      },
      "SECURITY": {
        "gif": "/assets/kai/motion/gif/kai_security_scan.gif",
        "webm": "/assets/kai/motion/webm/kai_security_scan.webm"
      },
      "ERROR": {
        "gif": "/assets/kai/motion/gif/kai_error_detected.gif",
        "webm": "/assets/kai/motion/webm/kai_error_detected.webm"
      },
      "OFFLINE": {
        "gif": "/assets/kai/motion/gif/kai_no_signal.gif",
        "webm": "/assets/kai/motion/webm/kai_no_signal.webm"
      }
    }
  }
}
```

---

# 15. Claude-Code Masterprompt V3.3

```text
Implement KAI Asset Production Pack V3.3 into the existing KAI project.

KAI identity:
- Name: KAI — Kinetic Artificial Intelligence
- Motto: Persona non grata
- Role: central visible AI media host for Dashboard, Telegram, signal commentary, risk warnings, security checks and agent summaries

Tasks:
1. Create the asset folder structure under public/assets/kai/ exactly as specified.
2. Add placeholder files only if real assets are not yet available, but clearly mark placeholders as temporary.
3. Add kai_assets_manifest.json and map all master, icon, state, motion, dashboard, telegram and voice assets.
4. Update the existing KAI asset mapper to load paths from kai_assets_manifest.json instead of hardcoded paths where possible.
5. Ensure WebM is preferred for dashboard animation and PNG is used as fallback.
6. Ensure GIF can be used for Telegram previews where supported.
7. Ensure all KAI states map to both static and motion assets:
   - IDLE
   - ANALYSIS
   - SIGNAL
   - WARNING
   - SECURITY
   - ERROR
   - OFFLINE
8. Integrate the Telegram avatar and dashboard portrait into their respective modules.
9. Add voice asset references for German and English samples.
10. Add validation so missing critical assets trigger a non-breaking warning in development and a fail-closed fallback in production.
11. Add tests for manifest loading, state-to-asset mapping, missing asset fallback and WebM/PNG fallback behavior.
12. Do not fake that final assets exist if only placeholders are present.
13. Keep all naming deterministic and lowercase with underscores.
14. Make the system ready for later talking-avatar integration using kai_talking_avatar_base.png.

Acceptance criteria:
- Asset folder structure exists.
- Manifest loads successfully.
- Every KAI state has static PNG and motion GIF/WEBM references.
- Dashboard uses WebM with PNG fallback.
- Telegram uses correct avatar and can reference GIF assets.
- Missing assets are detected.
- Tests pass.
- No hardcoded inconsistent asset paths remain.
```

---

# 16. Produktionscheckliste

## Master Assets

- [ ] `kai_master_portrait.png`
- [ ] `kai_master_portrait_transparent.png`
- [ ] `kai_dashboard_portrait.webp`

## Icons

- [ ] `kai_icon_1024.png`
- [ ] `kai_icon_512.png`
- [ ] `kai_icon_256.png`
- [ ] `kai_icon_128.png`
- [ ] `kai_icon_64.png`
- [ ] `kai_icon_32.png`
- [ ] `kai_icon.ico`
- [ ] `kai_icon_transparent_512.png`
- [ ] `kai_telegram_avatar.png`
- [ ] `kai_sidebar_avatar.png`

## State-Bilder

- [ ] `kai_idle.png`
- [ ] `kai_analysis.png`
- [ ] `kai_signal.png`
- [ ] `kai_warning.png`
- [ ] `kai_security.png`
- [ ] `kai_error.png`
- [ ] `kai_offline.png`

## Motion

- [ ] `kai_idle_loop.gif`
- [ ] `kai_idle_loop.webm`
- [ ] `kai_analysis_loop.gif`
- [ ] `kai_analysis_loop.webm`
- [ ] `kai_signal_found.gif`
- [ ] `kai_signal_found.webm`
- [ ] `kai_risk_detected.gif`
- [ ] `kai_risk_detected.webm`
- [ ] `kai_security_scan.gif`
- [ ] `kai_security_scan.webm`
- [ ] `kai_error_detected.gif`
- [ ] `kai_error_detected.webm`
- [ ] `kai_no_signal.gif`
- [ ] `kai_no_signal.webm`

## Dashboard / Telegram

- [ ] `kai_dashboard_header_wide.webp`
- [ ] `kai_dashboard_card_bg.webp`
- [ ] `kai_agent_panel_bg.webp`
- [ ] `kai_telegram_banner.webp`
- [ ] `kai_telegram_alert_banner.webp`

## Voice DE

- [ ] `kai_voice_idle.wav`
- [ ] `kai_voice_analysis.wav`
- [ ] `kai_voice_signal.wav`
- [ ] `kai_voice_warning.wav`
- [ ] `kai_voice_security.wav`
- [ ] `kai_voice_error.wav`
- [ ] `kai_voice_offline.wav`

## Voice EN

- [ ] `kai_voice_idle_en.wav`
- [ ] `kai_voice_analysis_en.wav`
- [ ] `kai_voice_signal_en.wav`
- [ ] `kai_voice_warning_en.wav`
- [ ] `kai_voice_security_en.wav`
- [ ] `kai_voice_error_en.wav`
- [ ] `kai_voice_offline_en.wav`

## Talking Avatar

- [ ] `kai_talking_avatar_base.png`

---

# 17. Qualitätsprüfung je Asset

Jedes Asset wird nur akzeptiert, wenn folgende Fragen positiv beantwortet werden:

1. Ist KAI sofort als KAI erkennbar?
2. Stimmen Haare, Augen, Gesicht und Haltung mit der Master-Identität überein?
3. Ist der Zustand eindeutig erkennbar?
4. Ist die Darstellung professionell und nicht generisch?
5. Ist kein Anime-/Cartoon-/Roboter-/Hoodie-Klischee enthalten?
6. Funktioniert das Asset auch klein?
7. Ist der Hintergrund nicht überladen?
8. Sind keine Wasserzeichen, Logos oder unnötigen Texte enthalten?
9. Ist der Glitch-Effekt kontrolliert und nicht chaotisch?
10. Passt das Asset zu Dashboard und Telegram?

---

# 18. Abschlussdefinition

Mit V3.3 wird KAI assetfähig.

KAI bekommt:

- ein Gesicht
- Zustände
- Bewegung
- Stimme
- technische Dateinamen
- klare Produktionsprompts
- klare Exportgrößen
- klare Qualitätsregeln
- klare Integration in Dashboard und Telegram

Damit wird aus KAI endgültig keine Idee mehr, sondern eine wiedererkennbare Systemfigur.

> **KAI — Persona non grata**  
> Nicht eingeladen. Trotzdem im System.  
> Nicht bequem. Trotzdem notwendig.  
> Nicht dekorativ. Sondern wach.

