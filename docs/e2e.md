# Testy interfejsu Home Assistant

Wynik lokalny w WSL z 29.09.2026: **2/2 przypadki zaliczone**, 38,31 s.
Ruff przeszedł. Job GitHub Actions został dodany, ale nie był jeszcze uruchamiany.

Harness `tests/e2e/ha_ui.py` uruchamia prawdziwy frontend Home Assistant
2026.2.3 i Chromium poprzez Python Playwright. HTTP i WebSocket obsługuje
testowy serwer HA, a osobny serwer HTTP udaje Listonic. Klient integracji
wykonuje rzeczywiste żądania do tego serwera; nie przechwytujemy ich w przeglądarce.
Nie potrzeba Node ani Dockera, a domowa instancja HA pozostaje niezależna.

## Uruchomienie w Linux/WSL

Wymagany Python 3.13, izolowany venv i biblioteka systemowa libturbojpeg:

```sh
sudo apt-get update
sudo apt-get install -y libturbojpeg
python -m pip install -r requirements_e2e.txt
python -m playwright install --with-deps chromium
python -m pytest tests/e2e/ha_ui.py -q --timeout=180 --tb=short
```

Moduł uruchamiamy jawnie; zwykły `pytest` wykonuje regresje backendu bez
wymagania przeglądarki. Osobny job `e2e` w CI instaluje te zależności.

## Zakres

Dwa przypadki: angielski desktop 1440×1000 w jasnym motywie oraz polski widok
mobilny 390×844 w ciemnym motywie. Każdy przechodzi:

1. Dodanie integracji przez UI, odrzucone hasło i poprawne logowanie.
2. Wybór listy, powiązanie Shopping List i potwierdzenie scalenia.
3. Dodanie i odhaczenie produktu w natywnym panelu zakupów.
4. Powrót zmiany nazwy i statusu z serwera Listonic do panelu HA.
5. Polską komendę `conversation.process`: „Dodaj mleko do listy zakupów”.
6. Awarię odczytu API, lokalne dodanie produktu i stan `pending`.
7. Unload/setup integracji, odzyskanie zapisanej kolejki i dokładnie jeden POST.

Sprawdzamy wynik w UI, stan integracji i żądania odebrane przez fake API.
Oczekiwania zależą od zmian stanu, bez sztywnych opóźnień interfejsu.
HA Store jest przechwycony przez fixture testową i przeżywa reload; Recorder
korzysta z tymczasowej bazy SQLite. To nie zastępuje restartu całego procesu HA
z dyskowym storage ani testu rzeczywistego konta.

## Artefakty i granice

Screenshoty etapów i `trace.zip` zapisujemy w `.test-state/e2e/<wariant>/`.
Po błędzie dochodzą screenshot, snapshot dostępności strony i diagnostyka
połączenia. Katalog jest wyłączony z Git; CI przechowuje artefakty siedem dni.
Trace zawiera sesję fikcyjnego HA i dane testowe, nie dane konta Listonic.

Pozostają do osobnej weryfikacji: reauth przez UI, pełny restart HA, pozostałe
kombinacje języka/rozmiaru/motywu, rzeczywisty telefon oraz mikrofon/STT.
Wyniki prawdziwego API opisuje [raport live](api/live-validation.md).
