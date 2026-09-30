# Mapa pracy dla agentów

Czytaj tylko sekcję potrzebną do zadania. Stałe zasady są w [AGENTS.md](../AGENTS.md).

## Wnioski z analizy projektu — 29.09.2026

- Projekt ma wydzielone HTTP, planowanie synchronizacji, wykonawcę i trwały Store.
  Największe ryzyko regresji dotyczy utraty danych, duplikowania zapisu i wyścigów,
  dlatego te kontrakty są w krótkiej instrukcji startowej.
- Testy są podzielone według zachowania. Encje `todo` i koordynator sprawdza
  `test_lifecycle.py`; opcje oraz sensor/diagnostykę sprawdza również
  `test_shopping_list_bridge.py`. Nie szukaj nieistniejących osobnych modułów testów.
- `docs/integration-plan.md` miesza plan i wykonane etapy, zawiera m.in. planowaną
  nazwę `tests/test_store.py`. Aktualny plik to `tests/test_bridge_store.py`.
  E2E działa przez Python Playwright; opis projektu nie wymaga stosu Node.
- CI definiuje Ruff, backend z pokryciem >=90% oraz osobny Chromium E2E.
  Raporty zapisane w README nie dowodzą przejścia bieżącego CI.
- Z dokumentacji wynikają otwarte obszary: pełny restart procesu HA, test
  długotrwały, reauth przez UI oraz bramki wydania Hassfest/HACS. Sprawdź aktualny
  stan przed podjęciem takiego zadania; ta analiza nie jest nowym testem tych funkcji.

## Mapa: zadanie → kod → testy

Ścieżki kodu poniżej są względem `custom_components/lists_assistant/`, testów względem `tests/`.
To zestawy startowe; przy zmianie kontraktu dodaj testy jego odbiorców.

| Obszar | Kod do sprawdzenia | Testy startowe |
| --- | --- | --- |
| HTTP, login, refresh, retry, format danych | `api.py`, `const.py` | `test_api.py` |
| Konfiguracja, reauth, wybór list | `config_flow.py` | `test_config_flow.py`, `test_translations.py` |
| Opcje, przepięcie/odłączenie Shopping List | `config_flow.py`, `bridge.py`, `bridge_store.py` | `test_shopping_list_bridge.py` |
| Polling, blokady, encje todo, setup/unload | `coordinator.py`, `todo.py`, `__init__.py` | `test_lifecycle.py` |
| Scalanie, wspólna baza, konflikt/usuwanie | `reconcile.py` | `test_reconcile.py`, potem `test_shopping_list_bridge.py` |
| Kolejka, offline, niepewne zapisy, Assist | `bridge.py`, `bridge_store.py`, `coordinator.py` | `test_shopping_list_bridge.py`, `test_bridge_store.py` |
| Format i uszkodzenie Store | `bridge_store.py` | `test_bridge_store.py`, `test_shopping_list_bridge.py` |
| Akcje CRUD list i produktów | `services.py`, `api.py`, `services.yaml` | `test_account_services.py`, `test_api.py`, `test_translations.py` |
| Rozwiązywanie konfliktu/zapisu, sensor, diagnostyka | `bridge_services.py`, `sensor.py`, `diagnostics.py` | `test_shopping_list_bridge.py` |
| Teksty i placeholdery | `strings.json`, `translations/en.json`, `translations/pl.json` | `test_translations.py` |
| Wydanie, domena i HACS | `manifest.json`, główne `hacs.json`, `pyproject.toml`, `.github/workflows/ci.yml` | `test_release.py`, `test_config_flow.py`, `test_lifecycle.py`, E2E |
| Pełny przepływ UI | odpowiedni obszar powyżej | `e2e/ha_ui.py`, instrukcja w `docs/e2e.md` |

Przepływ: `config_flow` → `__init__` → klient + koordynator → encje `todo`/sensor.
Bridge pobiera stan HA i Listonic, wywołuje czysty `plan_changes`, utrwala plan,
a następnie sprawdza aktualność obu stron i wykonuje operację pod blokadą.
Akcje konta korzystają ze świeżego snapshotu także dla list niewybranych jako encje.

## Dokumentacja na żądanie

| Potrzeba | Otwórz |
| --- | --- |
| Kontrakt logowania/tokenów | [api/authentication.md](api/authentication.md) |
| Endpointy i pola | [api/resources.md](api/resources.md) |
| Błędy i niepewny zapis | [api/errors-and-sync.md](api/errors-and-sync.md) |
| Zakres faktycznie sprawdzonego API | [api/live-validation.md](api/live-validation.md) |
| Usługi i przykłady użytkownika | [actions.md](actions.md) |
| Shopping List, Assist, pierwsze scalenie | [shopping-list.md](shopping-list.md) |
| Przeglądarka i artefakty E2E | [e2e.md](e2e.md) |
| Publikacja wersji i wymagania HACS | [releasing.md](releasing.md) |
| Uzasadnienie architektury/plan rozszerzeń | [integration-plan.md](integration-plan.md) |
| Historyczne źródła i zgłoszenia innych integracji | [integration-review.md](integration-review.md), [listonic-api.md](listonic-api.md) |

## Polecenia weryfikacji

Uruchamiaj z katalogu głównego repozytorium w Linux/WSL, w aktywnym środowisku
Python 3.13. Sprawdź interpreter i dostępność zależności raz; nie instaluj ich
ponownie przed każdym testem. Windows PowerShell i WSL mają oddzielne środowiska.
**Pod Windows wszystkie testy uruchamiaj w WSL**, z linuksowym interpreterem
i venv. Z PowerShell możesz wejść do WSL poleceniem `wsl`; przed testami sprawdź
`pwd` i `python --version` oraz aktywuj właściwy linuksowy venv.

```sh
# Tylko przy przygotowaniu/braku zależności środowiska:
python -m pip install -r requirements_test.txt

# Przykład testu obszaru; zastąp pliki według mapy:
python -m pytest tests/test_reconcile.py tests/test_bridge_store.py -q --timeout=30 --tb=short

# Końcowe kontrole po zmianie kodu:
ruff check --no-cache .
ruff format --no-cache --check .
python -m pytest -q --timeout=30 --cov=custom_components/lists_assistant --cov-report=term-missing --cov-fail-under=90

# Po zmianach przepływu UI, Assist lub bridge; setup opisuje docs/e2e.md:
python -m pytest tests/e2e/ha_ui.py -q --timeout=180 --tb=short
```

Zwykły pytest nie zbiera `ha_ui.py`; E2E trzeba wskazać jawnie.
Nawet testy czystych funkcji ładowane spod `tests/` korzystają z globalnego
`conftest.py` i środowiska testowego HA. Nie obchodź go atrapami importów.
Nie stosuj globalnego progu pokrycia do pojedynczego pliku testów.
Po błędzie zawęź do przypadku przez `plik::nazwa_testu`, przeczytaj przyczynę,
napraw ją i powtórz odpowiedni zestaw. Nie ukrywaj błędów przez filtrowanie logów.

## Jak zlecać kolejne implementacje

Wystarczy cel, obserwowalne kryteria i ograniczenia; mapa zastępuje opis repozytorium.

```text
Cel: [jedna konkretna zmiana zachowania].
Akceptacja: [wejście/scenariusz → oczekiwany wynik, także istotny przypadek błędu].
Zakres/ograniczenia: [np. bez zmiany publicznego schematu akcji].
Wskazówka: [opcjonalnie plik, test albo krótki komunikat błędu].
Wdróż zgodnie z instrukcjami projektu i zweryfikuj odpowiednie testy.
```

Przy przekazaniu pracy do nowej sesji zapisz krótko: cel, zmienione pliki,
decyzje niewynikające z kodu, wykonane kontrole z wynikiem i następny krok.
Nie kopiuj całej rozmowy ani pełnych logów. Nie zapisuj takiej historii w AGENTS.md.

## Utrzymanie instrukcji i ocena oszczędności

`AGENTS.md` jest wspólnym źródłem zasad. `CLAUDE.md` importuje je przez
`@AGENTS.md`; mapy nie importujemy automatycznie. Nie powielaj zasad w obu plikach.
Po dodaniu lub zmianie instrukcji rozpocznij nową sesję w repozytorium i sprawdź
załadowane źródła instrukcji; w Claude Code służy do tego także `/context`.

Mechanizm opisują [dokumentacja Codexa](https://developers.openai.com/codex/guides/agents-md)
i [dokumentacja Claude Code](https://code.claude.com/docs/en/memory).

Oczekiwany zysk wynika z mniejszej liczby ponownych odczytów, ograniczenia logów
i szybszego wyboru testów. Nie zmierzono jeszcze oszczędności tokenów.
Porównaj kilka podobnych zadań przed/po: tokeny wejścia/wyjścia (jeśli dostępne),
czas, liczbę odczytanych plików i powtórzeń testów oraz regresje/poprawki.
Oszczędność nie powinna wynikać z pominięcia wymaganej weryfikacji.
