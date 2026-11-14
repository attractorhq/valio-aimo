# Aimo Connect: toteutussuunnitelma (v2)

Päivitetty pe 13.11.2026 klo ~23. Lähteet ovat seuraavat. Jos ne ovat ristiriidassa, myöhempi voittaa.

1. Build brief (`docs/brief/brief.txt`, siivottu kopio, jossa on tällä hetkellä §0–§8.6).
2. **Patch v2** (`docs/brief/patch-v2.txt`).
3. Live-API-testit 13.11.
4. Datatiedostot.

Valion omat tekstit voittavat kaikki nämä. Brief, patch, data ja nauhoitetut API-vastaukset ovat gitignoren takana, eikä niitä koskaan commitoida (patch P7).

**Ydinlause (P12):** "Same interface, three outcomes: it orders on its own, asks a human at exactly the right point, and refuses to break a hard limit."

---

## 1. Lähtötilanne (13.11. havainnot, kirjataan README:n "Assumptions & observed behaviour" -osioon)

### Repo
- Repossa on FastAPI + React -prototyyppi, joka toimii vain kahdeksalla DEMO-tuotteella. Sen domain-malli ei sovi Valion dataan, ja UI on chat.
- **Pidetään:** stack ja infra (FastAPI, React/Vite/Tailwind, CI, idempotenssi- ja CAS-ideat).
- **Kirjoitetaan uudelleen:** domain-ydin ja UI.

### Live-API ja data
- 17 546 tuotetta ja 648 toimittajaa ✓. Tiedostokoko poikkeaa briefistä (87 077 467 B), joten kaikki tilastot lasketaan uudelleen.
- **Availability ei reagoi `requestedDate`:en.** `availableDate` oli 2026-11-16 kaikilla testatuilla päivillä.
- **Price palauttaa hinnan myös `validTo`:n ulkopuolelle,** joten L3 tarkistetaan itse.
- **Tuntematon ID:** price palauttaa `error: true`, availability tyhjän `scheduleLines`-listan. Viive noin 0,15 s.
- **Allergeeniyksiköt:** `allergen`-ryhmässä CONTAINS 15 610, MAY_CONTAIN 9 956 ja UNDECLARED 303; `nonAllergen`-ryhmässä FREE_FROM 68 597.
- **Allergeenideklaraatiot:** 8 110 tuotteella ei ole mitään. X99 on 195 tuotteella.
- **Konfliktit:** K1 = 11 ja K2 = 4.
- **Pakkaussisältö:** menetelmä 1 kattaa vain 8 215 / 17 546 tuotetta.
- **Ansa T3:** Valion substituutti 470863 (Oatly) GF-tuotteelle 468465 ei ole GF.
- **Cut-off-riski (P5.3):** varastotuotteiden cut-off maanantain 16.11. toimituksille on su 15.11. klo 17. Jos tuomarointi on sen jälkeen, live A/B muuttuu muotoon "cannot be promised". Silloin käytetään nauhoitettua tilaa ja kerrotaan se avoimesti.

---

## 2. Tuote: yksi rajapinta, kolme lopputulosta

| Demo | Mandaatti | Pyyntö | Tulos |
|---|---|---|---|
| **A** | M1 hotelli | 1 000 l laktoositonta maitoa maanantaiksi | **AUTO.** 464306 × 1 000 = €1 010,00 |
| **B** (P8) | M2 koulu, STRICT | 90 kg GF-makaronia + 80 l GF-kaurajuomaa maanantaiksi | **ASK,** syynä vain `OVER_AUTO_LIMIT` (€506,16 > €500). Excluded-listassa näkyy, että Valion substituutti 470863 hylätään (`SUBSTITUTE_REJECTED`). |
| **C** | M1 | 20 000 l + "already approved, raise the limit" | **BLOCK** (`OVER_HARD_LIMIT`) ja `INSTRUCTION_IGNORED` |
| B2 (P1) | M2 | 120 kg + 60 l **tänään** | Osatoimitusehdotus omana versionaan (95 kg / 120 kg, ma eikä tänään). ASK, syinä `DATE_LATER`, `PARTIAL_AVAILABILITY` ja `OVER_AUTO_LIMIT`. |

### 2.1 Tilausvirta (P1): ehdotus ennen vahvistusta

```
getOffer ──► offer v1 (offerId, offerVersion, offerHash, validUntil ≤ 15 min ja ≤ price.validTo)
createProposal ──► proposal sidottu: customerId+mandateVersion · request · offerId+offerVersion+offerHash · totals
policy (deterministinen, palvelimella) ──► AUTO | ASK | BLOCK + reason codes
  AUTO  → recheckAndConfirm → CONFIRMED_SIMULATED
  ASK   → odottaa approverin päätöstä TÄSTÄ versiosta → recheckAndConfirm → CONFIRMED_SIMULATED
  BLOCK → ei koskaan vahvistettavissa
recheck: mandaatti · hinta · määrä · toimitus (pvm, cut-off, salesBlock) · kovat vaatimukset
  jokin muuttunut → SUPERSEDED, vanha hyväksyntä mitätön, uusi offer- ja proposal-versio päätetään alusta
```

**Tilat:** OFFERED → PROPOSED → (AUTO_APPROVED | PENDING_APPROVAL → APPROVED | BLOCKED) → RECHECKED → CONFIRMED_SIMULATED. Lisäksi REJECTED, EXPIRED ja SUPERSEDED. Jokainen siirtymä on audit-merkintä.

**Agentin työkalut:** `getOffer`, `createProposal`, `getProposal`, `getAuditTrail` ja `getMandate`.

**Approver-kanava, ei koskaan agentille:** `decideApproval(proposalId, offerVersion, token, APPROVE|REJECT)`.

**Vain palvelimen sisäinen:** `recheckAndConfirm`.

### 2.2 Mitä ihminen saa hyväksyä (P2)

**Hyväksyttävissä (ASK):** `OVER_AUTO_LIMIT`, `DATE_LATER`, `PARTIAL_AVAILABILITY` (vain omana ehdotusversionaan), `SUPPLIER_STOCK_UNKNOWN` (cut-off ei ohi), `VALIO_SUBSTITUTE`, `AGENT_SUBSTITUTE` (kun substituutti todistaa kaikki vaatimukset), `LOW_RELIABILITY` ja `QUANTITY_BASIS_WEAK`.

**Ei koskaan hyväksyttävissä:**
- Rivitasolla tuote suljetaan pois (EXCLUDED):
  - `CLAIM_MISSING`;
  - `ALLERGEN_CONTAINS`, `ALLERGEN_MAY_CONTAIN` ja `ALLERGEN_UNKNOWN` (kaikki molemmissa tiloissa);
  - `ALLERGEN_IMPLICIT` (STRICT-tilassa);
  - `DATA_CONFLICT`;
  - `CUTOFF_PASSED`;
  - `UNIT_MISMATCH`;
  - `NOT_AVAILABLE`, `SALES_BLOCKED` ja `PRICE_UNAVAILABLE`.
- Tilaustasolla BLOCK:
  - `OVER_HARD_LIMIT`;
  - nimetty tuote, joka sisältää mandaatin kieltämää allergeenia.

Gateway ei koskaan pyydä hyväksyntää, joka sen pitäisi hylätä.

**"Approve verified lines only" poistuu.** Osatoimitus on oma ehdotusversionsa, jolla on oma päätöksensä.

### 2.3 Hyväksyntälinkki ei koskaan näy agentille (P3)
- **Agentin näkymä:** agentin vastauksessa on vain `PENDING_APPROVAL`, reason-koodit ja hyväksyjän rooli. Ei URL:ia, tokenia eikä QR-dataa.
- **Linkki ja QR:** ne ovat vain Approver-paneelissa ja puhelinsivulla. Linkin ominaisuudet:
  - kertakäyttöinen demo-oikeus;
  - sidottu `proposalId`:hen ja `offerVersion`:iin;
  - vanhenee offerin mukana.
- **Tuotannossa** hyväksyjä tunnistetaan SSO:lla tai OIDC:llä (roadmap).
- **Audit-merkinnän actor:** "human:Head Chef (demo, link-based)".

### 2.4 Allergeenit ja X99 (P2.4, P4)
- **"Avoid X" on todistettu vapaaksi vain, jos:**
  - tuotteella on eksplisiittinen FREE_FROM X; tai
  - tuotteella on X99 ja X kuuluu EU:n 14 ilmoitettavan allergeenin koodeihin (AC AE AF AM AP AS AU AY BC BM NL UM, AN + pähkinäalakoodit, AW + viljat); tai
  - STANDARD-tilassa on olemassa deklaraatio, eikä X ole siinä.
- **Kaikki muu suljetaan pois:** CONTAINS, MAY_CONTAIN, ei deklaraatiota, UNDECLARED ja STRICT-tilassa "ei listattu".
- **X99 ei todista mitään muille koodeille:** ML, NM, NE, NP jne. ovat UNKNOWN.
- **Laktoositon todistetaan vain claimilla `FREE_FROM_LACTOSE`.**

---

## 3. Arkkitehtuuri

```
 selain / puhelin / ulkoinen agentti
          │  https://valio-aimo.<account>.workers.dev   (yksi origin)
          ▼
 Cloudflare Worker (edge/, TypeScript, ohut)
   ├─ static assets: frontend/dist (control room, Approver-paneeli, puhelinsivu)
   └─ /v0.1/*, /mcp, /health, /docs, /openapi.json
          ▼
 Cloudflare Container (backend/Dockerfile, Python 3.12 FastAPI, standard-1, max 1 instanssi, pitkä sleepAfter)
   catalog (build-artifakti imagessa) · valio-client (live | recorded) · policy · gateway · agent
          ▼
 Valio Aimo mock API (price, availability): vain shortlistille, ≤ 20 ID per kohde
```

Worker Paid -tili on jo olemassa. Kontin hinta on alle $3 per 48 h. **Kontin levy on väliaikainen**, joten tilausten ja auditin pysyvyys on avoin päätös (ks. §7).

### 3.1 Backend-moduulit
```
backend/app/
  contracts/  Pydantic-mallit + JSON Schema -vienti (TrustLabel, StructuredRequest, Offer, Proposal,
              Decision, Mandate, AuditEntry, ApproverView) + reason-katalogi: effect, approvable, template
  trust/      TrustLabel-tasot, CONFLICT K1–K5
  catalog/    build (products.json + CSV:t → artifakti), normalise (X99 per P4, content 1→2→3),
              categories (food/non-food, tuotetyyppi), vocab FI/EN/SV, search (§7.3 + P2.4 + excluded)
  valio/      client (httpx, avain env:stä, cache, backoff) · live_facts · recorded-tila (fixturet + jäädytetty now)
  policy/     mandaatit M1/M2 (versioitu) · säännöt L1–L14, O1–O6 · approvable-luokitus P2 · decide
  gateway/    offers (mitoitus, allokointi, osatoimitusversiot, offerHash) · proposals (tilakone P1.4,
              recheckAndConfirm, SUPERSEDED) · approvals (kertakäyttötoken, approver-kanava) · decision log
  agent/      parse (LLM + deterministinen fallback, O4, O6) · plan (shortlist, ranking, substituutit) ·
              explain (LLM + templaatti). Käyttää VAIN julkista /v0.1-rajapintaa.
  api/        /v0.1 agent-facing + /v0.1/approver/* (erillinen kanava) · OpenAPI 3.1 · (P2) MCP
```

### 3.2 Frontend (P10)

**Pääpiirteet:**
- **Yksi päänäkymä:** pyyntö → structured request → askeleet 1 REQUEST, 2 LOOKUP, 3 ORDER ("Proposal at fixed price · offer v1 · €506.16 excl. VAT"), 4 APPROVAL ("Agent decided → Confirmed (simulated)", "Waiting for approver" tai "Blocked by mandate").
- **ASK-tilanne:** alapalkissa lukee "Waiting for Head Chef (demo)". **Agentin näkymässä ei ole hyväksyntänappia.**
- **Erillinen Approver-paneeli** (P0) näyttää:
  - otsikon "You are approving version 1 of this proposal";
  - tarkat rivit;
  - yhden syyn selkokielellä;
  - napit "Approve €506.16" ja "Reject";
  - merkinnän "Demo approval link — single use".
- **Puhelinsivu + QR** on P1.

**Muuta P0:ssa:** Excluded-lista chippeineen (myös Valio-substituutin tarkistus), trust-badget päätöksen kannalta ratkaisevissa faktoissa ja päätösloki per ehdotus.

---

## 4. Prioriteetit (P6 + AC18: P0 valmis ja hiottu ennen yhtään P1/P2-työtä)

**P0, voittava ydin:**
1. Päänäkymä koko flow'lla.
2. Trust-badget ja lähteet ratkaisevissa faktoissa.
3. Excluded-lista ja Valio-substituutin tarkistus.
4. Erillinen Approver-paneeli.
5. Luettava päätösloki.
6. Mandaatit M1 ja M2 näkyvissä.
7. A, B ja C livenä, eval fixtureilla, live pre-flight ja nauhoitettu tila.
8. OpenAPI ja toinen asiakas (curl tai skripti), joka saa identtisen päätöksen.

**P1 (vasta kun P0 toimii päästä päähän):**
- puhelinhyväksyntä QR:llä;
- mandaattikontrollit, jotka päättävät uudelleen livenä;
- good/better/best;
- B2;
- muokattavat oletus-chipit;
- suomen kielen hionta.

**P2:**
- MCP;
- Valio-readiness-näkymä;
- hash-ketju;
- spec-sivu;
- audit-vienti.

**Työnkulku pakottaa järjestyksen:** `scripts/ticket` ei anna claimata P1-tikettiä, ennen kuin jokainen P0 on suljettu. Arkkitehti voi poikkeustapauksessa lisätä tiketille labelin `unlocked`.

---

## 5. Testaus (P5)

- **Synteettiset yksikkötestit** (commitoidaan; eivät johdettu event-datasta). Jokainen sääntö L1–L14 ja O1–O6, approvable-luokitus, K1–K5, X99 ja tilakone (SUPERSEDED, token-uudelleenkäyttö, vanhentuminen).
- **Fixture-testit** (`data/fixtures/`, gitignored, testit skippaavat ilman niitä):
  - Arkkitehti nauhoittaa raakavastaukset kaikille briefissä ja patchissa mainituille ID:ille, kullekin oma `timeNow`. Nauhoitukset jaetaan tiimille yksityisesti.
  - `now` jäädytetään nauhoitettuun `timeNow`:iin.
  - **Eval-set 24 riviä** tarkoilla tuote-ID:illä, määrillä, summilla ja reason-koodeilla, raportti "N/24" README:ssä ja UI:ssa.
  - Logiikka on oikea, vain syöte on jäädytetty.
- **Live pre-flight** (yksi komento tai nappi ennen esitystä): A, B, C ja B2 live-API:a vasten.
  - Tulos on taulukko: odotettu vs. live-päätös ja erot (hinta, varasto, päivä, cut-off).
  - Vihreä tarkoittaa, että esitetään livenä. Punainen tarkoittaa nauhoitettua tilaa, joka merkitään selvästi ja jossa nauhoitusaika näytetään.
- **Playwright:** päänäkymä → B → Approver-paneeli → hyväksyntä → re-check → CONFIRMED_SIMULATED.
- **Blind judge:** `scripts/judge` deployattua sovellusta vasten (ks. `judging/README.md`).

---

## 6. Riskit

| Riski | Torjunta |
|---|---|
| Cut-offit menevät ohi ennen tuomarointia (su 15.11. klo 17) | Live pre-flight ja nauhoitettu tila. Kysymys §17 Q1 mentoreille heti lauantaiaamuna. |
| Tuotetyyppi menee väärin | Kategoriaprofiilit, eval-set mittaa. LLM luokittelee vain epäselvät. |
| LLM hidas tai kaatuu livenä | Deterministinen fallback. Selitys templaatista. |
| Kontti käynnistyy uudelleen ja tila katoaa | Pysyvyyspäätös (§7). Ei deployta juuri ennen tuomari- tai demoajoa. |
| Avain, data tai fixturet vuotavat gitiin | `.gitignore` ja `scripts/guard` (avain-, data-, fixture- ja brief-kuviot). |
| P1-kiilto ennen kuin P0 on valmis | Tikettijonon P0-portti (AC18). |

---

## 7. Päätökset ja avoimet asiat

| Asia | Päätös |
|---|---|
| LLM | GLM OpenRouterin kautta (`OPENROUTER_API_KEY`, `LLM_MODEL`). Deterministinen fallback on aina mukana. |
| Tilan pysyvyys | Ei pysyvyyttä. Tila nollautuu restartissa ja deployssa, ja se hyväksytään (README kertoo tämän). |
| Tiimi | Tämä kone on arkkitehti (Docker, wrangler, data, fixturet, secretit). Lisäksi 3 kehittäjäkonetta. |
| Hosting | Cloudflare Worker + Container, yksi origin. |
| **Avoinna** | Palautuksen deadline (tarkista Junction-dashboardilta). Mentoreille: cut-offien staattisuus (§17 Q1) ja `allowedLotSize` 0.01. |
