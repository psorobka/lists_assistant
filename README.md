# Lists Assistant

![Lists Assistant](brand/icon.png)

Integracja Home Assistant z listami zakupów Listonic. Wersja **1.0.0**,
domena **`lists_assistant`**. Wymagany Home Assistant **2026.2.3 lub nowszy**.

## Funkcje

- Osobna encja `todo` dla każdej wybranej listy Listonic.
- Dwukierunkowa synchronizacja jednej listy z wbudowaną Shopping List HA
  i standardowymi komendami zakupów Assist.
- Tworzenie, zmiana nazwy i dezaktywacja list oraz zarządzanie produktami,
  w tym ilością, jednostką, opisem i ceną.
- Trwała kolejka offline, rozpoznawanie konfliktów i obsługa niepewnych zapisów.
- Konfiguracja i ponowne logowanie przez UI, sensor synchronizacji,
  diagnostyka oraz polskie i angielskie tłumaczenia.

To niezależny projekt społecznościowy korzystający z API aplikacji Listonic.
Nie jest oficjalną integracją producenta; zmiany jego API mogą wymagać aktualizacji.

## Instalacja przez HACS

1. W HACS otwórz menu **Custom repositories / Niestandardowe repozytoria**.
2. Dodaj `https://github.com/psorobka/lists_assistant` z kategorią **Integration**.
3. Wyszukaj **Lists Assistant**, pobierz wydanie i uruchom ponownie Home Assistant.
4. W **Ustawienia → Urządzenia i usługi → Dodaj integrację** wybierz
   **Lists Assistant** i zaloguj się do Listonic.
5. Wybierz listy, które mają być widoczne jako encje `todo`.

Repozytorium można dodać ręcznie do HACS; obecność w domyślnym katalogu HACS
wymaga osobnego zgłoszenia i akceptacji.

## Instalacja ręczna

Z [wydania GitHub](https://github.com/psorobka/lists_assistant/releases)
pobierz archiwum źródeł. Skopiuj katalog `custom_components/lists_assistant`
do `/config/custom_components/lists_assistant` w Home Assistant, a następnie
uruchom HA ponownie i dodaj integrację przez UI.

## Shopping List i Assist

Włącz wbudowaną integrację **Shopping List**. Podczas konfiguracji Lists Assistant
wybierz listę do synchronizacji i potwierdź pierwsze scalenie. Każdy istniejący
produkt z obu stron zostanie zachowany, także produkty o takich samych nazwach.
Jedna integracja może być właścicielem Shopping List. Powiązanie można zmienić
w opcjach; zatrzymane operacje pozostają przypisane do poprzedniego celu.

Po połączeniu list można użyć komendy Assist „Dodaj mleko do listy zakupów”.
Szczegóły: [konfiguracja, konflikty i offline](docs/shopping-list.md).
Akcje mają nazwy `lists_assistant.get_lists`, `lists_assistant.add_item` itd.
[Dokumentacja akcji i przykłady YAML](docs/actions.md).

## Przejście z testowej integracji listonic

Wydanie 1.0.0 zmienia domenę z `listonic` na `lists_assistant`. Nie ma automatycznej
migracji wpisów konfiguracji, encji ani kolejki zapisów z wersji rozwojowych.

1. Zrób pełną kopię zapasową HA, w tym `.storage`. W starej integracji rozwiąż
   konflikty i niepewne zapisy oraz poczekaj na opróżnienie kolejki. Jeżeli nie
   jest to możliwe, zachowaj starą instalację do czasu rozstrzygnięcia operacji.
2. Wyłącz starą integrację i zrestartuj HA, aby zatrzymać synchronizację.
   Nie uruchamiaj obu integracji jednocześnie z tym samym Shopping List.
3. Zainstaluj Lists Assistant i skonfiguruj konto ponownie. Początkowo wybierz
   same encje `todo`, bez powiązania Shopping List.
4. Przed włączeniem bridge przygotuj stan obu list. Nowa integracja nie zna
   dawnych mapowań: scalenie dwóch identycznych list może zdublować produkty.
   Po wykonaniu kopii można pozostawić pełną listę po jednej stronie i pustą
   po drugiej; nie opróżniaj list przy działającej starej synchronizacji.
5. W opcjach włącz bridge, sprawdź liczby produktów i potwierdź scalenie.
   Zaktualizuj automatyzacje z `listonic.*` na `lists_assistant.*` i sprawdź ID encji.

Starych plików `.storage/listonic_bridge.*` nie usuwaj ani nie przemianowuj
ręcznie. Wartość `choose: listonic` w rozstrzyganiu konfliktów nadal oznacza
wersję produktu po stronie Listonic i pozostaje bez zmian.

## Rozwój i weryfikacja

Testy uruchamiaj w Linux/WSL na Pythonie 3.13, w linuksowym venv:

```sh
python -m pip install -r requirements_test.txt
ruff check --no-cache .
ruff format --no-cache --check .
python -m pytest -q --timeout=30 --cov=custom_components/lists_assistant --cov-report=term-missing --cov-fail-under=90
```

CI zawiera Ruff, pytest z progiem 90%, Chromium E2E, Hassfest i HACS.
Automatyczne testy używają fikcyjnego serwera Listonic.
[Uruchomienie i zakres E2E](docs/e2e.md), [przygotowanie wydania](docs/releasing.md),
[historia zmian](CHANGELOG.md), [mapa pracy](docs/agent-workflow.md).

Pełny restart procesu HA, test długotrwały i reauth przez UI pozostają osobnymi
obszarami do weryfikacji. Historyczne testy rzeczywistego API opisano w
[raporcie live](docs/api/live-validation.md); skrypty `scripts/live_*` zmieniają
prawdziwe konto i nie są częścią automatycznych kontroli wydania.

## Pomoc i licencja

[Zgłoszenia błędów](https://github.com/psorobka/lists_assistant/issues).
Do zgłoszenia dołącz wersje HA i integracji oraz zanonimizowany opis problemu.
Nie publikuj haseł, tokenów ani plików `.storage`.

[Licencja MIT](LICENSE). Dokumentacja kontraktu API: [docs/api](docs/api/README.md).
