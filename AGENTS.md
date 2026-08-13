# Instrukcja dla agenta AI

Ten plik jest po to, żeby agent (Claude Code lub inny) mógł **odtworzyć całe
wdrożenie od zera na dowolnej maszynie** i rozwijać kod bez ponownego
reverse-engineeringu API. Zawiera wiedzę, której nie widać w samym kodzie:
skąd biorą się identyfikatory, jak czytać mapy miejsc, gdzie są pułapki.

Repozytorium: <https://github.com/abnuk/pkp-monitor>

---

## 1. Co to jest i po co powstało

Monitor wolnych miejsc **siedzących** w pociągach PKP Intercity. Sprawdza mapy
miejsc konkretnych wagonów i powiadamia (macOS + push na telefon przez ntfy),
gdy zwolni się miejsce spełniające zadane kryteria.

Kluczowe rozróżnienie, od którego zaczął się projekt: zwykłe wyszukiwarki
(w tym API KOLEO) mówią tylko, czy **da się kupić bilet**. W pociągach IC/TLK
bilet bywa kupowalny bez gwarancji miejsca (miejsce stojące) albo gdy zostały
wyłącznie miejsca specjalne (dla osób z niepełnosprawnością). Dlatego jedynym
wiarygodnym źródłem jest **mapa miejsc wagonu** (SVG) z podsystemu GRM.

## 2. Zawartość repozytorium

| Plik | Rola |
|---|---|
| `pkp_monitor.py` | całość logiki — jeden plik, bez zależności poza `curl_cffi` |
| `build-linux.sh` | budowanie samodzielnej binarki na Linux x86-64 (Docker + PyInstaller) |
| `README.md` | instrukcja dla człowieka |
| `AGENTS.md` | ten plik |

Celowo **nie** trzymamy w repo: `.venv/`, zbudowanej binarki, `state*.json`,
`monitor.log` (patrz `.gitignore`).

## 3. Szybki start (lokalnie)

```bash
python3 -m venv .venv && .venv/bin/pip install curl_cffi
./pkp_monitor.py --from "Warszawa Wschodnia" --to Ciechanow --date 2026-08-13 \
    --train 3528 --klasa 1 --wagon bezprzedzialowy --para --styl preferowany --once
```

Shebang wskazuje na `.venv` **obok skryptu** — po sklonowaniu gdzie indziej albo
popraw shebang, albo uruchamiaj przez `.venv/bin/python pkp_monitor.py`.

## 4. Wiedza o API (reverse-engineered)

Baza: `https://api-gateway.intercity.pl`. Wszystko przez `curl_cffi`
z `impersonate="chrome"` — API stoi za **Akamai Bot Manager** i odrzuca ruch po
odcisku TLS (zwykły `curl`/`requests` dostaje 403/418 albo timeout).

### 4.1 Identyfikatory stacji

`GET https://www.intercity.pl/station/get/?q=<nazwa>` → lista obiektów:

- `n` — nazwa z diakrytykami (`Działdowo`), `p` — bez nich (`Dzialdowo`),
- **`h`** — id do **wyszukiwarki połączeń**,
- **`e`** — id do **map miejsc** (GRM).

To dwa różne systemy numeracji, nie da się ich mieszać. Kod dopasowuje zapytanie
do `n` albo `p`, dzięki czemu w cronie można pisać bez polskich znaków.

### 4.2 Wyszukiwanie połączeń

`POST /server/public/endpoint/Pociagi`, JSON z `"metoda": "wyszukajPolaczenia"`,
`stacjaWyjazdu`/`stacjaPrzyjazdu` = id **`h`**, `urzadzenieNr: "956"`.
Odpowiedź: `polaczenia[]`, w każdym `pociagi[0]` z `nrPociagu`,
`kategoriaPociagu` (IC/TLK/EIP…), `nazwaPociagu`, oraz `dataWyjazdu` /
`dataPrzyjazdu` w formacie `YYYY-MM-DD HH:MM:SS`.

### 4.3 Skład pociągu

`GET /grm/sklad/wbnet/{kat}/{nr}/{odjazd}/{stacjaE}/{przyjazd}/{stacjaE}`
gdzie daty w formacie `YYYYMMDDHHMM`. Zwraca m.in.:

- `wagonySchemat` — `{"1": "1313,WITHOUT_COMPARTMENTS", ...}` (typ + układ),
- `klasa1`, `klasa2` — numery wagonów w danej klasie,
- `wagonyNiedostepne` — wagony do pominięcia.

Tokeny układu: `WITHOUT_COMPARTMENTS`, **`WITH_COMPARTMENTS`** (uwaga: *nie*
`COMPARTMENTS`), `MIXED`. Wagony `MIXED` mają część bezprzedziałową, więc
filtr `--wagon bezprzedzialowy` je **wlicza**.

### 4.4 Mapa miejsc wagonu (najważniejsze)

`GET /grm/wagon/svg/wbnet/{kat}/{nr}/{wagon}/{typ}/{odjazd}/{przyjazd}/{stacjaE}/{stacjaE}`
→ SVG. Każde miejsce to `<g data-class="first class|second class">` zawierające:

- `<text>` — numer miejsca,
- `<image status="…">` — **`1` = wolne**, inne = zajęte/niedostępne,
- `xlink:href` — grafika fotela; końcówka **`3R.png` / `3L.png` koduje kierunek**,
  w którą stronę fotel jest zwrócony,
- atrybuty `x`, `y` — pozycja w wagonie (viewBox zwykle `0 0 880 160`, fotel 40×40),
- opcjonalne `<eic:special ref="…">` — miejsce specjalne. `ref` `1` (przy
  rowerach) i `7` (strefa ciszy) traktujemy jak zwykłe; pozostałe (np. `5`,
  dla osób z niepełnosprawnością) liczone są osobno jako „spec." i **nie**
  wyzwalają powiadomień.

### 4.5 Geometria — jak z SVG wynika układ miejsc

To sedno projektu. Reguły wyprowadzone empirycznie i potwierdzone na mapach:

- `y < 60` = jedna strona przejścia (górna), `y > 60` = druga (dolna).
  W typowym wagonie rzędy to `y = 0` i `40` (góra) oraz `80` i `120` (dół);
  `y = 0`/`120` to miejsca przy oknie, `40`/`80` przy korytarzu.
- **Dwa miejsca obok siebie** = to samo `x`, ta sama strona, różne `y`.
- **Miejsce pojedyncze** = brak drugiego fotela o tym samym `x` po tej samej stronie.
- **Stolik / vis-à-vis** = fotel `R`, przed którym w tym samym rzędzie (`y`)
  stoi fotel `L` w odległości ≤ 120 px (i symetrycznie). Pole `faces` trzyma
  numer miejsca naprzeciwko, `facing` to skrót logiczny.

Przykład kontrolny — wagon typu **`1313`** (typowa kl. 1 bezprzedziałowa IC,
37 miejsc). Przy pustym wagonie powinno wyjść dokładnie:

- pojedyncze przez stolik: `15+16`, `25+26`, `41+42`, `51+52`, `61+62`, `71+72`,
- obok siebie bez stolika: `35+33`,
- razem `--styl preferowany` = **7 układów**, `--styl dowolny` = 11 par.

Jeśli po zmianach w parserze te liczby się nie zgadzają — regresja.

## 5. Tryby wyszukiwania (co potrafi CLI)

- `--pojedyncze` — miejsca bez sąsiada obok, z podziałem na te ze stolikiem i bez,
- `--para` + `--styl`:
  - `dowolny` — dowolne dwa obok siebie,
  - `obok-bez-stolika` — obok siebie, nikt naprzeciwko,
  - `pojedyncze-stolik` — dwa **pojedyncze** zwrócone do siebie przez stolik,
  - `preferowany` — dwa powyższe łącznie,
- `--miejsca "1:16,26,31;2:16,46"` — konkretne numery per wagon (ma pierwszeństwo),
- filtry: `--train`, `--klasa`, `--wagon`, `--after`, `--before`.

## 6. Wdrożenie na serwerze bez Pythona

Cel: jeden plik wykonywalny, zero instalacji na maszynie docelowej.

```bash
./build-linux.sh                                    # → pkp-monitor-linux-amd64
scp pkp-monitor-linux-amd64 user@host:pkp-monitor/pkp-monitor
ssh user@host 'chmod +x ~/pkp-monitor/pkp-monitor'
```

Binarka to PyInstaller `--onefile` z `--collect-all curl_cffi` (bez tego brakuje
bibliotek TLS w runtime) i wymaga `binutils` w obrazie budującym. Buduj pod
architekturę **docelowej** maszyny (`--platform linux/amd64` na Macu ARM).

### Cron

Bez `--adaptacyjnie` — stały interwał, np. co 15 minut:

```cron
*/15 * * * * ~/pkp-monitor/pkp-monitor --from ... --once --state ~/pkp-monitor/state.json >> ~/pkp-monitor/monitor.log 2>&1
```

Z `--adaptacyjnie` — wpis ustawiamy na **co minutę**, skrypt sam decyduje:

```cron
* * * * * ~/pkp-monitor/pkp-monitor --from ... --adaptacyjnie --once --state ~/pkp-monitor/state.json >> ~/pkp-monitor/monitor.log 2>&1
```

Progi: >48 h co 15 min, <48 h co 10 min, <24 h co 5 min, <3 h co 2 min.
Godziny odjazdu zapamiętywane są w `state.json` pod `__meta__`, dzięki czemu
pominięty cykl kończy się w ~0,9 s **bez ani jednego zapytania do API**
(bramka działa przed rozwiązaniem stacji i przed wypisaniem nagłówka).

> **Każdy monitor musi mieć własny plik `--state`.** Przy cronie co minutę dwa
> zadania potrafią wystartować w tej samej sekundzie i nadpisać sobie stan
> (read-modify-write), co kończy się zgubionym albo zdublowanym powiadomieniem.

### Powiadomienia push (ntfy)

1. aplikacja **ntfy** (iOS/Android) → subskrypcja wymyślonego, trudnego do
   odgadnięcia tematu (temat = hasło, każdy kto go zna, widzi powiadomienia),
2. `--ntfy <temat>`.

Treść jest **czystym tekstem z nowymi liniami** — Markdown renderuje tylko
webowa aplikacja ntfy, na mobilkach zobaczyłbyś dosłowne `**gwiazdki**`.
Push jest przycinany do 1200 znaków na granicy linii.

## 7. Pułapki (sprawdzone w boju)

| Objaw | Przyczyna / obejście |
|---|---|
| `403`/`418`/timeout na każdym zapytaniu | brak `curl_cffi` + `impersonate="chrome"` |
| `500`/`503` na mapie wagonu | endpoint bywa kapryśny — jest retry z backoffem, pominięte wagony raportowane w wyniku |
| nagle „brak pociągów", choć są | API potrafi oddać **200 z pustą listą** `polaczenia` po serii zapytań; kod pomija wtedy cykl **bez nadpisywania stanu** (inaczej po powrocie danych poszłoby fałszywe powiadomienie) |
| działa na serwerze, nie działa lokalnie | pojedyncze IP bywa przycinane po intensywnych testach — uruchamiaj przez maszynę zdalną, użyj `--dump-wagon` do diagnostyki |
| monitor „umarł" po pierwszym z kilku pociągów | historyczny błąd: zapisywany był tylko najwcześniejszy odjazd. Teraz `__meta__.departures` to lista; tempo wyznacza najbliższy jeszcze nieodjechały, koniec dopiero po ostatnim |
| monitor cicho przestał sprawdzać | zegar maszyny poszedł do przodu → `Data … już minęła`. Sprawdź `timedatectl` (na jednej z VM RTC chodził kilka dni w przód mimo poprawnego czasu systemowego) |

## 8. Jak testować zmiany (bez czekania na zwrot biletu)

1. **Zrzuć prawdziwą mapę**: `--dump-wagon <nr>` wypisuje surowe SVG na stdout
   (pierwsza linia to nagłówek „Monitoruję…", obetnij ją `tail -n +2`).
   Podanie nieistniejącego numeru wagonu wypisze cały `wagonySchemat` — wygodny
   sposób na poznanie składu.
2. **Testuj geometrię offline**, ładując moduł i wołając `parse_wagon_seats()` /
   `find_pairs()` na zapisanym SVG. Symuluj „pusty wagon" (wszystkie miejsca
   w puli), żeby policzyć **pojemność strukturalną** danego układu — tak sprawdza
   się, czy filtr nie jest tak wąski, że nigdy się nie odezwie.
3. **Nie testuj na produkcyjnym stanie**: uruchamiaj bez `--state` i bez `--ntfy`
   albo z tymczasowym plikiem stanu, inaczej wyślesz push i zepsujesz historię.
4. Sprawdź progi `interval_for()` dla kilku odległości od odjazdu.

## 9. Typowe zadania

**Dodać monitor**: dopisz wpis do crona z własnym `--state`; przetestuj tę samą
komendę „verbatim" (`crontab -l | grep … | sed 's|^\* \* \* \* \* ||' | bash`).

**Zmienić kryteria**: edytuj argumenty w cronie. Klucz stanu zawiera wszystkie
filtry, więc zmiana kryteriów startuje z czystym stanem (pierwszy przebieg może
od razu powiadomić, jeśli coś pasuje — to zamierzone).

**Wyłączyć wszystko**:

```bash
crontab -l | grep -v pkp-monitor | crontab -
rm -rf ~/pkp-monitor          # binarka, state*.json, monitor.log
```

**Zakończone przejazdy**: monitor sam kończy pracę po odjeździe ostatniego
pilnowanego pociągu (albo po minięciu daty), ale wpis w cronie zostaje i będzie
co minutę dopisywał linię do logu — po podróży wyczyść crontab.

## 10. Alternatywa, której świadomie nie użyto

`api.koleo.pl` (nagłówki `x-koleo-version: 2`, `x-koleo-client: Nuxt-1`) działa
bez logowania i jest znacznie prostszy, ale zwraca tylko `purchasable` — bez
rozróżnienia miejsc siedzących, stojących i specjalnych. Nadaje się do
sprawdzania „czy w ogóle są bilety", nie do polowania na konkretne miejsce.

## 11. Zastrzeżenia

Projekt nieoficjalny, niezwiązany z PKP Intercity S.A., wyłącznie do użytku
osobistego i tylko do odczytu. API może się zmienić bez ostrzeżenia. Nie schodź
poniżej ~2 minut między sprawdzeniami — jedno sprawdzenie pociągu to ~10 zapytań.
