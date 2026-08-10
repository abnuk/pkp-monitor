# pkp-monitor

Monitor wolnych miejsc **siedzących** w pociągach PKP Intercity z powiadomieniami
na komputer i telefon. W przeciwieństwie do zwykłych wyszukiwarek nie sprawdza,
czy bilet da się kupić (bo „kupowalny" bywa też bilet bez gwarancji miejsca,
czyli stojący), tylko czyta **mapy miejsc poszczególnych wagonów** — te same,
które widać przy graficznym wyborze miejsca na intercity.pl.

## Co potrafi

- odróżnia zwykłe wolne miejsca od miejsc specjalnych (dla osób z
  niepełnosprawnością, przewóz roweru itp.) i od biletów „bez gwarancji miejsca",
- filtruje po klasie (`--klasa 1|2`), typie wagonu
  (`--wagon bezprzedzialowy|przedzialowy`) i liczbie pociągów (`--train 5122,5110`),
- rozpoznaje z geometrii mapy **miejsca pojedyncze** — bez sąsiada obok —
  i rozdziela je na „bez stolika" (nikt nie siedzi naprzeciwko) oraz
  „ze stolikiem" (vis-à-vis): `--pojedyncze`,
- znajduje **miejsca dla dwóch osób razem** (`--para`) z rozróżnieniem układu
  (`--styl`): `obok-bez-stolika`, `pojedyncze-stolik` (dwa pojedyncze naprzeciw
  siebie przez stolik), `preferowany` (oba naraz) albo `dowolny`,
- **zagęszcza sprawdzanie przed odjazdem** (`--adaptacyjnie`): >48 h co 15 min,
  <48 h co 10 min, <24 h co 5 min, <3 h co 2 min — bo zwolnione miejsce
  w popularnym pociągu potrafi zniknąć w kilka minut,
- umie pilnować **konkretnych numerów miejsc** per wagon:
  `--miejsca "1:16,26,31;2:16,46"`,
- powiadamia natywnie na macOS (dymek + dźwięk) oraz **push na telefon** przez
  [ntfy.sh](https://ntfy.sh) (`--ntfy nazwa-tematu`),
- działa w pętli (`--interval`, domyślnie 300 s) albo jednorazowo pod crona
  (`--once` + `--state plik.json`, żeby nie powiadamiał w kółko o tym samym),
- po minięciu daty przejazdu kończy pracę sam.

## Jak to działa

Skrypt korzysta z wewnętrznego API `api-gateway.intercity.pl`:

1. `POST /server/public/endpoint/Pociagi` (metoda `wyszukajPolaczenia`) — lista
   pociągów na trasie w danym dniu; ID stacji z `intercity.pl/station/get/?q=`,
2. `GET /grm/sklad/wbnet/…` — skład pociągu: wagony, klasy, typy
   (bezprzedziałowy / przedziałowy / mieszany),
3. `GET /grm/wagon/svg/wbnet/…` — mapa SVG wagonu; każdy fotel ma status
   (wolny/zajęty), współrzędne i orientację, z których liczona jest geometria
   miejsc pojedynczych i par vis-à-vis.

API jest chronione przez Akamai Bot Manager i odrzuca ruch po odcisku TLS —
dlatego zapytania idą przez [curl_cffi](https://github.com/lexiforest/curl_cffi)
z podszywaniem się pod przeglądarkę (`impersonate="chrome"`).

## Instalacja

Wymagany Python 3.11+.

```bash
git clone https://github.com/abnuk/pkp-monitor && cd pkp-monitor
python3 -m venv .venv
.venv/bin/pip install curl_cffi
```

Skrypt ma shebang wskazujący na `.venv` obok siebie, więc po instalacji wystarczy
`./pkp_monitor.py …`.

## Użycie

```bash
# konkretny pociąg, sprawdzanie co 5 minut aż ktoś odda bilet
./pkp_monitor.py --from "Warszawa Centralna" --to "Kraków Główny" \
    --date 2026-08-10 --train 5322

# kilka pociągów, tylko wagony bezprzedziałowe kl.2, push na telefon
./pkp_monitor.py --from Dzialdowo --to "Warszawa Wschodnia" --date 2026-08-02 \
    --train 5112,5122,5138 --wagon bezprzedzialowy --klasa 2 --ntfy moj-tajny-temat

# tylko miejsca pojedyncze (bez sąsiada obok), z podziałem stolik/bez stolika
./pkp_monitor.py --from ... --to ... --date ... --train 5122 --klasa 1 --pojedyncze

# polowanie na konkretne miejsca
./pkp_monitor.py --from ... --to ... --date ... --train 5122 --klasa 1 \
    --miejsca "1:16,26,31,42,52,62,72;2:16,26,36,46"

# jednorazowo, np. pod crona (patrz niżej)
./pkp_monitor.py --from ... --to ... --date ... --once --state state.json
```

Nazwy stacji podawaj tak, jak na intercity.pl (bez polskich znaków też zadziała:
`Dzialdowo`); przy pomyłce skrypt podpowie pasujące stacje.

### Push na telefon (ntfy)

1. Zainstaluj aplikację **ntfy** (iOS/Android) i zasubskrybuj wymyślony,
   trudny do odgadnięcia temat, np. `pkp-x7k2m9q`. Temat działa jak hasło —
   kto go zna, ten widzi (i może wysyłać) powiadomienia.
2. Uruchom skrypt z `--ntfy pkp-x7k2m9q`.

### Cron / serwer bez Pythona

Pod crona używaj `--once` z `--state`, żeby stan przeżywał między uruchomieniami:

```cron
*/15 * * * * /home/user/pkp-monitor/pkp-monitor --from Dzialdowo --to "Warszawa Wschodnia" --date 2026-08-02 --train 5122 --ntfy TEMAT --state /home/user/pkp-monitor/state.json --once >> /home/user/pkp-monitor/monitor.log 2>&1
```

Z `--adaptacyjnie` wpis ustawiasz na **co minutę** — skrypt sam decyduje, czy
cykl jest już należny, na podstawie godziny odjazdu zapamiętanej w pliku stanu.
Pominięty cykl kończy się w ułamku sekundy, nie odpytuje API i nie zapisuje nic
do logu:

```cron
* * * * * /home/user/pkp-monitor/pkp-monitor --from ... --adaptacyjnie --state /home/user/pkp-monitor/state.json --once >> /home/user/pkp-monitor/monitor.log 2>&1
```

Samodzielną binarkę na Linuksa (bez instalowania Pythona i zależności na
serwerze) zbudujesz Dockerem:

```bash
docker run --rm --platform linux/amd64 -v "$PWD":/src -w /build python:3.12-slim bash -c '
  apt-get -qq update && apt-get -qq install -y binutils &&
  pip -q install pyinstaller curl_cffi &&
  cp /src/pkp_monitor.py . &&
  pyinstaller --onefile --collect-all curl_cffi --name pkp-monitor pkp_monitor.py &&
  cp dist/pkp-monitor /src/pkp-monitor-linux-amd64'
```

## Zastrzeżenia

- Projekt **nieoficjalny**, niezwiązany z PKP Intercity S.A. Korzysta z
  nieudokumentowanego API, które może się zmienić lub zniknąć bez ostrzeżenia.
- Wyłącznie do użytku osobistego, tylko odczyt. Zachowaj rozsądne interwały
  (nie schodź poniżej 2–3 minut) — jedno sprawdzenie to ~10 zapytań, a ochrona
  antybotowa potrafi przyciąć zbyt aktywne IP.
- Endpoint map miejsc bywa kapryśny (500/503) — skrypt ma retry z backoffem,
  a pominięte wagony raportuje w wyniku.

## Podziękowania

Wiedza o wewnętrznym API intercity.pl pochodzi z lektury projektów
[TrickyTrain](https://github.com/krzsmal/TrickyTrain) (endpointy, podejście
curl_cffi) i [intercity-sniffer](https://github.com/pi0trdotsys/intercity-sniffer)
(dokumentacja zachowania Akamai). Kod tego repozytorium został napisany od zera —
nie zawiera kodu z powyższych projektów.

## Licencja

[MIT](LICENSE)
