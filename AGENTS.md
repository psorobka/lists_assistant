# Instrukcje projektu — Codex i Claude

Integracja Home Assistant `lists_assistant`, Python 3.13. Kod: `custom_components/lists_assistant/`.
Rozmawiaj po polsku; zachowuj angielskie nazwy, docstringi i styl istniejącego kodu.

## Praca z małym kontekstem

- Na początku sprawdź `git status --short`; zachowaj zastane zmiany i pliki untracked.
- Dobierz obszar przez mapę w `docs/agent-workflow.md`; przeczytaj tylko potrzebną
  sekcję, moduł i jego testy. Nie analizuj od nowa całego projektu.
- Szukaj najpierw ścieżek (`rg --files`), potem symboli (`rg -n` w wybranych
  plikach). Odczytuj funkcję wraz z potrzebnym kontekstem; poszerzaj zakres,
  gdy wymagają tego wywołania lub zależności. Nie ponawiaj niezmienionych odczytów.
- Pomijaj `.venv/`, cache, `.test-state/`, logi i `uv.lock`, chyba że zadanie
  dotyczy środowiska, zależności lub konkretnego artefaktu diagnostycznego.
- Bieżący kod, testy i CI określają stan implementacji. Plany w `docs/` zawierają
  również propozycje; historyczne wyniki testów nie są wynikiem bieżącej sesji.
- Korzystaj z lokalnego kontraktu `docs/api/` przy pracy nad Listonic.
  Sięgaj do źródeł zewnętrznych, gdy trzeba potwierdzić zmianę lub brakującą informację.
- Wykonuj najmniejszą kompletną zmianę. Nie dodawaj pobocznych refaktorów,
  zależności ani nowych warstw bez potrzeby wynikającej z zadania.
- Proste zadanie realizuj bez rozbudowanego planu. Przy złożonym określ krótko
  zakres, ryzyka i warunki odbioru. Nie deleguj rutynowo pracy wielu agentom.

## Kontrakty, które trzeba zachować

- `api.py` obsługuje HTTP/sesję bez zależności od HA; `reconcile.py` planuje zmiany
  bez I/O. Integrację z HA i wykonywanie operacji pozostaw odpowiednim modułom.
- Zapis i polling serializuj przez `coordinator.async_mutate` / blokadę
  koordynatora. Nie zagnieżdżaj pobrania tej samej blokady. Nie traktuj błędu
  odczytu po potwierdzonym zapisie jako nieudanego zapisu.
- `AmbiguousWrite` oznacza nieznany wynik: bez ślepego ponowienia, zwłaszcza POST.
  Zachowaj trwałe stany `queued`, `in_flight`, `uncertain` i zapis przed skutkiem
  ubocznym. Uszkodzony Store blokuje operacje; nie resetuj go do pustego stanu.
- Tożsamość produktu wynika z ID, nie z nazwy. Scalanie zachowuje duplikaty,
  wspólną bazę i konflikty. Niepełny/błędny snapshot nie oznacza pustej listy.
- Bridge synchronizuje nazwę i odhaczenie; zachowuj pozostałe metadane produktu.
  Używaj publicznych usług `todo` do zmian Shopping List HA.
- Zachowaj potwierdzenie pierwszego scalenia, jednego właściciela Shopping List,
  mapowania/kolejki osobne dla celów i brak automatycznego wyboru listy zastępczej.
- Zachowuj rotację tokenów i `device_id`; nie utrwalaj hasła ani nie ujawniaj
  danych logowania w logach, diagnostyce, testowych artefaktach lub odpowiedzi.
- Zmiany etykiet/akcji uwzględnij w `strings.json`, `translations/en.json`,
  `translations/pl.json` i, gdy dotyczy, `services.yaml` oraz dokumentacji akcji.

## Weryfikacja i zakończenie

- Operacje na GitHubie, w tym tworzenie i obsługa PR, sprawdzanie CI, tagowanie
  i publikowanie wydań, wykonuj przez `gh`.
- Merge do `main` traktuj jako publikację wydania: przed merge'em przygotuj numer
  wersji i `CHANGELOG.md`; po zielonym CI utwórz tag i GitHub Release. Nie kończ
  zadania na samym merge'u.
- Pod Windows wszystkie testy uruchamiaj w WSL, nie natywnie w PowerShell/CMD.
  Używaj linuksowego Pythona 3.13 i venv w WSL, nie windowsowego `.venv`.
  Na Linuxie uruchamiaj je bezpośrednio; wykorzystuj istniejące środowisko.
- Najpierw uruchom testy zmienianego zachowania z mapy w `docs/agent-workflow.md`.
  Dodaj regresję dla błędu/nowego zachowania; unikaj testów kopiujących implementację.
- Po zmianie kodu uruchom Ruff lint/format check; przed oddaniem zmian backendu
  pełny pytest z progiem pokrycia 90%. E2E dobieraj do wpływu na UI/Assist/bridge.
  Same zmiany dokumentacji wymagają kontroli treści, ścieżek i poleceń.
- Testy automatyczne używają fikcyjnego Listonic. `scripts/live_*` zmieniają
  rzeczywiste konto: uruchamiaj je tylko w zakresie autoryzowanego testu live.
- Po zielonych kontrolach kończ; powtarzaj je po zmianie kodu lub nowej przesłance.
  Jeśli środowisko blokuje test, podaj przyczynę i niewykonane polecenie.
- Odpowiedź końcowa: co zmieniono, wynik kontroli, istotne ograniczenie lub następny
  krok. Bez pełnych logów. Mapę aktualizuj przy zmianie ścieżek/komend; nie dopisuj
  historii każdej sesji do instrukcji.
