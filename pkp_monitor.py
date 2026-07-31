#!/Users/abnuk/pkp-monitor/.venv/bin/python
"""
Monitor wolnych miejsc SIEDZĄCYCH w pociągach PKP Intercity.

Czyta mapy miejsc (SVG per wagon) z wewnętrznego API intercity.pl
(api-gateway.intercity.pl), więc odróżnia zwykłe wolne miejsca od miejsc
specjalnych (dla osób z niepełnosprawnością/starszych itp.) i od biletów
"bez gwarancji miejsca" (stojących). Umie też filtrować po typie wagonu
(bezprzedziałowy/przedziałowy).

Podejście podpatrzone w projektach:
  https://github.com/krzsmal/TrickyTrain
  https://github.com/pi0trdotsys/intercity-sniffer
Wymaga curl_cffi (venv w .venv obok skryptu) — API tnie ruch po odcisku TLS.

Przykłady:
  # kilka pociągów, tylko wagony bezprzedziałowe kl.2, push na iPhone'a przez ntfy
  ./pkp_monitor.py --from Działdowo --to "Warszawa Wschodnia" --date 2026-08-02 \
      --train 5122,5110,5146 --wagon bezprzedzialowy --klasa 2 --ntfy moj-tajny-temat-x7k2

  # wszystkie pociągi po 16:00, jednorazowo
  ./pkp_monitor.py --from "Wrocław Główny" --to "Kraków Główny" --date 2026-08-10 \
      --after 16:00 --once
"""

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, date

from curl_cffi import requests

API = "https://api-gateway.intercity.pl"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "user-agent": "Mozilla/5.0",
}
SVG_NS = {"svg": "http://www.w3.org/2000/svg", "eic": "http://www.intercity.pl/eic"}
# znaczenie eic:special ref (niepełne, ustalone empirycznie);
# te trzy traktujemy jak zwykłe miejsca, resztę jako "specjalne"
SPECIAL_OK = {None: "zwykłe", "1": "przy rowerach", "7": "strefa ciszy"}
LAYOUT_PL = {"WITHOUT_COMPARTMENTS": "bezprzedziałowy", "WITH_COMPARTMENTS": "przedziałowy",
             "MIXED": "mieszany"}


def api_request(method, url, payload=None, retries=3):
    last = None
    for attempt in range(retries):
        try:
            if method == "GET":
                r = requests.get(url, headers=HEADERS, impersonate="chrome", timeout=30)
            else:
                r = requests.post(url, headers=HEADERS, data=json.dumps(payload),
                                  impersonate="chrome", timeout=30)
            if r.status_code == 200 and "ACCESS DENIED" not in r.text[:200].upper():
                return r
            last = ConnectionError(f"HTTP {r.status_code} dla {url.split('/wbnet/')[0]}")
        except Exception as e:
            last = e
        time.sleep(0.5 * 2 ** attempt)
    raise last


def resolve_station(name):
    """intercity.pl/station/get zwraca: h = id do wyszukiwarki, e = id do map miejsc."""
    r = api_request("GET", f"https://www.intercity.pl/station/get/?q={name}")
    stations = r.json()
    for s in stations:
        # "n" = nazwa z diakrytykami, "p" = wersja bez nich (przydatne pod cronem)
        if name.lower() in (s["n"].lower(), (s.get("p") or "").lower()):
            return s["h"], s["e"], s["n"]
    hints = ", ".join(s["n"] for s in stations[:5] if "dowolna" not in s["n"].lower())
    sys.exit(f"Nie znaleziono stacji {name!r}. Podpowiedzi: {hints or 'brak'}")


def search_trains(date_iso, from_h, to_h):
    payload = {
        "urzadzenieNr": "956", "metoda": "wyszukajPolaczenia",
        "dataWyjazdu": f"{date_iso} 00:00:00", "dataPrzyjazdu": f"{date_iso} 23:59:59",
        "stacjaWyjazdu": from_h, "stacjaPrzyjazdu": to_h, "stacjePrzez": [],
        "polaczeniaNajszybsze": 0, "liczbaPolaczen": 0, "czasNaPrzesiadkeMax": 1440,
        "liczbaPrzesiadekMax": 2, "polaczeniaBezposrednie": 1, "kategoriePociagow": [],
        "kodyPrzewoznikow": [], "rodzajeMiejsc": [], "typyMiejsc": [], "braille": 0,
        "czasNaPrzesiadkeMin": 3,
    }
    r = api_request("POST", f"{API}/server/public/endpoint/Pociagi", payload)
    return r.json().get("polaczenia") or []


def parse_wagon_seats(svg_text):
    """Parsuje mapę SVG wagonu na listę miejsc z geometrią.

    Geometria (viewBox 880x160, fotel 40x40): y<60 = strona górna, y>60 = dolna.
    Fotel bez drugiego fotela obok (to samo x, ta sama strona) = pojedynczy.
    Grafika 3R/3L koduje kierunek siedzenia; fotel "R" z najbliższym fotelem "L"
    do 120 px przed sobą (ten sam rząd y) = para vis-à-vis (stolik).
    """
    root = ET.fromstring(svg_text)
    seats = []
    for g in root.findall(".//svg:g", SVG_NS):
        seat_class = g.get("data-class")
        if not seat_class:
            continue
        txt = g.find(".//svg:text", SVG_NS)
        img = g.find(".//svg:image", SVG_NS)
        if txt is None or img is None:
            continue
        href = img.get("{http://www.w3.org/1999/xlink}href") or ""
        sp = g.find(".//eic:special", SVG_NS)
        seats.append({
            "nr": (txt.text or "").strip(),
            "cls": "1" if "first" in seat_class.lower() else "2",
            "free": img.get("status") == "1",
            "ref": sp.get("ref") if sp is not None else None,
            "x": int(float(img.get("x") or 0)),
            "y": int(float(img.get("y") or 0)),
            "ori": "R" if "R.png" in href else ("L" if "L.png" in href else "?"),
        })
    for s in seats:
        s["side"] = "t" if s["y"] < 60 else "b"
    for s in seats:
        s["single"] = not any(o["side"] == s["side"] and o["x"] == s["x"] and o["y"] != s["y"]
                              for o in seats if o is not s)
        row = sorted((o for o in seats if o["side"] == s["side"] and o["y"] == s["y"]),
                     key=lambda o: o["x"])
        i = row.index(s)
        s["facing"] = False
        if s["ori"] == "R" and i + 1 < len(row):
            nxt = row[i + 1]
            s["facing"] = nxt["ori"] == "L" and nxt["x"] - s["x"] <= 120
        elif s["ori"] == "L" and i > 0:
            prv = row[i - 1]
            s["facing"] = prv["ori"] == "R" and s["x"] - prv["x"] <= 120
    return seats


def count_wagon_seats(svg_text, entry):
    """Zlicza wolne miejsca z mapy SVG jednego wagonu do słownika entry."""
    for s in parse_wagon_seats(svg_text):
        if not s["free"]:
            continue
        if s["ref"] in SPECIAL_OK:
            entry[f"kl{s['cls']}"].append(s)
        else:
            entry[f"kl{s['cls']}_spec"] += 1


def check_seats(train, from_e, to_e):
    """Pobiera skład i mapy wszystkich wagonów; zwraca (lista wagonów, pominięte)."""
    t = train["pociagi"][0]
    cat, nr = t["kategoriaPociagu"], t["nrPociagu"]
    dep = datetime.strptime(train["dataWyjazdu"], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")
    arr = datetime.strptime(train["dataPrzyjazdu"], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")
    sklad = api_request("GET", f"{API}/grm/sklad/wbnet/{cat}/{nr}/{dep}/{from_e}/{arr}/{to_e}").json()
    unavailable = {str(w) for w in sklad.get("wagonyNiedostepne") or []}
    wagons, skipped = [], []
    for wagon, wtype in (sklad.get("wagonySchemat") or {}).items():
        if str(wagon) in unavailable:
            continue
        entry = {"nr": str(wagon), "layout": wtype.split(",")[-1],
                 "kl1": [], "kl2": [], "kl1_spec": 0, "kl2_spec": 0}
        try:
            r = api_request(
                "GET",
                f"{API}/grm/wagon/svg/wbnet/{cat}/{nr}/{wagon}/{wtype}/{dep}/{arr}/{from_e}/{to_e}")
            count_wagon_seats(r.text, entry)
            wagons.append(entry)
        except Exception:
            skipped.append(str(wagon))
        time.sleep(0.6)  # nie zalewajmy API
    return wagons, skipped


def wagon_matches(layout, want):
    if want == "any" or layout == "MIXED":  # mieszany ma część bezprzedziałową i przedziały
        return True
    if want == "bezprzedzialowy":
        return layout == "WITHOUT_COMPARTMENTS"
    if want == "przedzialowy":
        return layout == "WITH_COMPARTMENTS"
    return True


def _by_wagon(pairs):
    """[(wagon, miejsce), ...] -> 'wag.1: nr 31; wag.2: nr 5'."""
    grouped = {}
    for w, s in pairs:
        grouped.setdefault(w["nr"], []).append(s["nr"])
    return "; ".join(f"wag.{wn}: nr {','.join(nrs[:6])}" + ("…" if len(nrs) > 6 else "")
                     for wn, nrs in grouped.items())


def parse_miejsca(spec):
    """'1:16,26,31;2:16,46' -> {'1': {'16','26','31'}, '2': {'16','46'}}"""
    out = {}
    for part in spec.split(";"):
        wagon, _, nrs = part.partition(":")
        out[wagon.strip()] = {n.strip() for n in nrs.split(",") if n.strip()}
    return out


def summarize(wagons, skipped, klasa, wagon_want, single_only=False, wishlist=None):
    """Zwraca (liczba miejsc wyzwalających powiadomienie, tekst podsumowania)."""
    total, parts = 0, []
    for cls in ("1", "2") if klasa == "any" else (klasa,):
        pool, spec = [], 0
        for w in wagons:
            if not wagon_matches(w["layout"], wagon_want):
                continue
            spec += w[f"kl{cls}_spec"]
            pool += [(w, s) for s in w[f"kl{cls}"]]
        if wishlist:
            wanted = [(w, s) for w, s in pool if s["nr"] in wishlist.get(w["nr"], set())]
            total += len(wanted)
            s_txt = f"kl.{cls} z listy: {len(wanted)}"
            if wanted:
                s_txt += f" ({_by_wagon(wanted)})"
            others = len(pool) - len(wanted)
            if others:
                s_txt += f"; innych zwykłych: {others}"
            if spec:
                s_txt += f" (+{spec} spec.)"
            parts.append(s_txt)
            continue
        if single_only:
            bez = [(w, s) for w, s in pool if s["single"] and not s["facing"]]
            ze = [(w, s) for w, s in pool if s["single"] and s["facing"]]
            inne = len(pool) - len(bez) - len(ze)
            total += len(bez) + len(ze)
            s_txt = f"kl.{cls} pojedyncze: bez stolika {len(bez)}"
            if bez:
                s_txt += f" ({_by_wagon(bez)})"
            s_txt += f", ze stolikiem {len(ze)}"
            if ze:
                s_txt += f" ({_by_wagon(ze)})"
            if inne:
                s_txt += f"; innych zwykłych: {inne}"
        else:
            total += len(pool)
            s_txt = f"kl.{cls}: {len(pool)} wolnych"
            if pool:
                s_txt += " — " + _by_wagon(pool)
        if spec:
            s_txt += f" (+{spec} spec.)"
        parts.append(s_txt)
    if skipped:
        parts.append(f"(bez wag. {','.join(skipped)} — błąd mapy)")
    return total, "; ".join(parts)


def notify(title, message, ntfy_topic=None):
    """Powiadomienie systemowe macOS + dźwięk + opcjonalnie push przez ntfy.sh."""
    try:
        msg = message.replace('"', "'")
        subprocess.run(["osascript", "-e",
                        f'display notification "{msg}" with title "{title}" sound name "Glass"'],
                       check=False)
        subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
    except FileNotFoundError:
        pass
    if ntfy_topic:
        try:
            requests.post("https://ntfy.sh/",
                          json={"topic": ntfy_topic, "title": title, "message": message,
                                "priority": 5, "tags": ["bullettrain_side"]},
                          timeout=15)
        except Exception as e:
            print(f"  (ntfy nie wyszło: {e})")


def main():
    p = argparse.ArgumentParser(description="Monitor wolnych miejsc siedzących PKP Intercity (mapa miejsc)")
    p.add_argument("--from", dest="src", required=True, help="stacja początkowa (pełna nazwa jak na intercity.pl)")
    p.add_argument("--to", dest="dst", required=True, help="stacja końcowa")
    p.add_argument("--date", required=True, help="data podróży YYYY-MM-DD")
    p.add_argument("--train", default=None, help="numer(y) pociągu po przecinku, np. 5122,5110")
    p.add_argument("--after", default="00:00", help="odjazdy od godziny HH:MM")
    p.add_argument("--before", default="23:59", help="odjazdy do godziny HH:MM")
    p.add_argument("--klasa", choices=["1", "2", "any"], default="any",
                   help="która klasa wyzwala powiadomienie (domyślnie dowolna)")
    p.add_argument("--wagon", choices=["bezprzedzialowy", "przedzialowy", "any"], default="any",
                   help="licz tylko miejsca w wagonach tego typu (mieszane zawsze wliczane)")
    p.add_argument("--pojedyncze", action="store_true",
                   help="powiadamiaj tylko o miejscach pojedynczych (bez sąsiada obok); "
                        "w raporcie rozdzielone na bez stolika / ze stolikiem (vis-à-vis)")
    p.add_argument("--miejsca", default=None, metavar="LISTA",
                   help='powiadamiaj tylko o konkretnych miejscach, per wagon: "1:16,26,31;2:16,46" '
                        "(ma pierwszeństwo przed --pojedyncze)")
    p.add_argument("--ntfy", default=None, metavar="TEMAT",
                   help="wyślij też push przez ntfy.sh na ten temat (appka ntfy na iOS)")
    p.add_argument("--interval", type=int, default=300, help="co ile sekund sprawdzać (domyślnie 300)")
    p.add_argument("--once", action="store_true", help="sprawdź raz i zakończ")
    p.add_argument("--dump-wagon", default=None, metavar="NR",
                   help="(diagnostyka) wypisz surowe SVG mapy wagonu NR pierwszego pasującego pociągu i zakończ")
    p.add_argument("--state", default=None, metavar="PLIK",
                   help="plik stanu między uruchomieniami (ważne pod crona z --once, "
                        "żeby nie powiadamiał w kółko o tych samych miejscach)")
    args = p.parse_args()
    trains_filter = {t.strip() for t in args.train.split(",")} if args.train else None

    if date.fromisoformat(args.date) < date.today():
        print(f"Data {args.date} już minęła — nic do monitorowania, kończę. "
              "(Usuń wpis z crona, jeśli to on mnie uruchamia.)")
        return

    from_h, from_e, from_name = resolve_station(args.src)
    to_h, to_e, to_name = resolve_station(args.dst)
    print(f"Monitoruję (mapa miejsc): {from_name} -> {to_name}, {args.date}"
          + (f", pociągi: {', '.join(sorted(trains_filter))}" if trains_filter else "")
          + f", odjazd {args.after}-{args.before}, klasa: {args.klasa}, wagony: {args.wagon}"
          + (f", ntfy: {args.ntfy}" if args.ntfy else ""))

    if args.dump_wagon:
        for train in search_trains(args.date, from_h, to_h):
            nr = str(train["pociagi"][0]["nrPociagu"])
            if trains_filter and nr not in trains_filter:
                continue
            t = train["pociagi"][0]
            dep = datetime.strptime(train["dataWyjazdu"], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")
            arr = datetime.strptime(train["dataPrzyjazdu"], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")
            sklad = api_request("GET", f"{API}/grm/sklad/wbnet/{t['kategoriaPociagu']}/{t['nrPociagu']}"
                                       f"/{dep}/{from_e}/{arr}/{to_e}").json()
            wtype = (sklad.get("wagonySchemat") or {}).get(args.dump_wagon)
            if not wtype:
                sys.exit(f"brak wagonu {args.dump_wagon}; schemat: {sklad.get('wagonySchemat')}")
            r = api_request("GET", f"{API}/grm/wagon/svg/wbnet/{t['kategoriaPociagu']}/{t['nrPociagu']}"
                                   f"/{args.dump_wagon}/{wtype}/{dep}/{arr}/{from_e}/{to_e}")
            sys.stdout.write(r.text)
            return
        sys.exit("nie znaleziono pociągu do zrzutu")

    # nr pociągu -> ostatnia liczba zwykłych wolnych miejsc (po filtrach)
    wishlist = parse_miejsca(args.miejsca) if args.miejsca else None
    state_key = (f"{from_name}|{to_name}|{args.date}|{args.klasa}|{args.wagon}"
                 + ("|pojedyncze" if args.pojedyncze else "")
                 + (f"|miejsca={args.miejsca}" if args.miejsca else ""))
    saved_state, last_counts = {}, {}
    if args.state and os.path.exists(args.state):
        try:
            with open(args.state) as f:
                saved_state = json.load(f)
            last_counts = saved_state.get(state_key, {})
        except Exception as e:
            print(f"nie odczytałem stanu z {args.state}: {e} — zaczynam od zera")
    while True:
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            found = search_trains(args.date, from_h, to_h)
            matched = []
            for train in found:
                dep_time = train["dataWyjazdu"][11:16]
                nr = str(train["pociagi"][0]["nrPociagu"])
                if not (args.after <= dep_time <= args.before):
                    continue
                if trains_filter and nr not in trains_filter:
                    continue
                matched.append(train)
            if not matched:
                print(f"[{stamp}] nie znaleziono pasujących pociągów — sprawdź numer/datę/godziny")
            newly = []
            for train in matched:
                t = train["pociagi"][0]
                nr = str(t["nrPociagu"])
                desc = (f"{train['dataWyjazdu'][11:16]} {t['kategoriaPociagu']} {nr}"
                        f" {(t.get('nazwaPociagu') or '').strip()}".rstrip())
                wagons, skipped = check_seats(train, from_e, to_e)
                free, summary = summarize(wagons, skipped, args.klasa, args.wagon,
                                          single_only=args.pojedyncze, wishlist=wishlist)
                print(f"[{stamp}] {desc}: {summary}")
                if free > 0 and not last_counts.get(nr):
                    newly.append(f"{desc}: {summary}")
                last_counts[nr] = free
            if newly:
                notify("PKP Intercity — są miejsca siedzące!",
                       f"{args.date}: " + " | ".join(newly), args.ntfy)
            if args.state:
                saved_state[state_key] = last_counts
                with open(args.state, "w") as f:
                    json.dump(saved_state, f)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{stamp}] błąd: {e} (kolejna próba za {args.interval}s)")
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
