# Raport weryfikacji API na żywo

Data: 28.09.2026. Test autoryzowany przez użytkownika, wykonany na wskazanym
koncie z logowaniem email/hasło. Dane logowania i odpowiedzi uwierzytelnienia
nie są zapisane w tym dokumencie ani w kodzie.

## Wynik

**PASS** — cały scenariusz w `scripts/live_api_probe.py` zakończył się kodem 0.
Zmiany dotyczyły nowej listy o unikalnej nazwie `HA API test ...`.
Po zakończeniu została dezaktywowana przez `Active:0`; kolejny pełny odczyt
potwierdził jej brak w aktywnych listach. Nie modyfikowano istniejących zakupów.

| Sprawdzenie | Wynik |
| --- | --- |
| Login email/hasło, bez wcześniejszej sesji anonimowej | PASS |
| Odczyt `Username` z profilu | PASS |
| Nowy obiekt klienta, tylko refresh token i zachowany DeviceId, bez hasła/access tokena | PASS |
| To samo konto po odnowieniu sesji | PASS |
| Pełny snapshot list z produktami | PASS |
| Utworzenie dedykowanej listy i odczyt jej ID | PASS |
| Zmiana nazwy listy i weryfikacja odczytem | PASS |
| Produkt z ilością 2.5, jednostką l, opisem i ceną 3.49 | PASS |
| Zmiana nazwy i odhaczenie produktu jednym PATCH | PASS |
| Zachowanie ilości, jednostki, ceny i opisu po odhaczeniu | PASS |
| Odznaczenie produktu i wyczyszczenie opisu | PASS |
| Usunięcie produktu i potwierdzenie jego nieobecności | PASS |
| Dezaktywacja listy testowej i potwierdzenie jej nieobecności | PASS |

## Środowisko

- WSL2, dystrybucja Ubuntu 24.04.
- Python 3.13.15, aiohttp 3.13.3.
- Klient z tego repozytorium, z bazą `https://api.listonic.com`.
- Odpowiedzi i sekrety pozostawały w pamięci procesu; raport zawiera wyłącznie
  wyniki sprawdzeń. Hasło wprowadzono przez ukryty prompt.

Osobno uruchomiono istniejące testy automatyczne w rzeczywistym środowisku
Home Assistant 2026.2.3 z `pytest-homeassistant-custom-component` 0.13.316:
**149 testów przeszło**, pokrycie kodu integracji **95,03%**. Obejmują klienta API,
config flow i reauth, prawdziwe akcje `todo`, setup/unload/reload, dwukierunkowy
bridge Shopping List, kolejkę offline, przerwany POST, konflikty, zmianę celu,
polskie `conversation.process`, zachowanie metadanych i tłumaczenia PL/EN.
Nowe akcje konta obejmują również walidację, wiele kont, niepewny zapis,
potwierdzony zapis przy nieudanym odczycie i brakujące cele.
Odpowiedzi Listonic w tych testach są fikcyjne;
uruchomienie ich nie wykonuje zapisów na żywym koncie.

## Bridge Shopping List i Assist na prawdziwym API

Osobny `scripts/live_bridge_probe.py` zakończył się kodem 0: **1 test przeszedł**.
Używał prawdziwego API i izolowanej instancji testowej Home Assistant 2026.2.3,
z natywną Shopping List oraz polskim `conversation.process`.

| Sprawdzenie | Wynik |
| --- | --- |
| „Dodaj mleko do listy zakupów” → Shopping List HA → Listonic | PASS |
| Zmiana nazwy i odhaczenie w HA → Listonic | PASS |
| Zachowanie ilości, jednostki, opisu i ceny | PASS |
| Zmiana nazwy i odznaczenie przez API → Shopping List HA | PASS |
| Unload/reload integracji bez duplikatów, z zachowanym lokalnym UID | PASS |
| Usunięcie w HA → brak produktu w Listonic | PASS |
| Dezaktywacja jednorazowej listy i potwierdzenie jej nieobecności | PASS |
| Akcje HA: utworzenie listy ze zwróconym ID i zmiana nazwy | PASS |
| Akcje HA: produkt z ilością, jednostką, opisem i ceną | PASS |
| Akcje HA: edycja nazwy/statusu zachowuje metadane | PASS |
| Akcje HA: wyczyszczenie ilości, jednostki i opisu oraz cena 0 | PASS |
| Akcje HA: usunięcie produktu i dezaktywacja listy | PASS |

Zapis dotyczył wyłącznie nowych list `HA bridge test ...`; istniejące zakupy nie
były zmieniane. Nie jest to restart procesu HA ani test interfejsu przeglądarkowego,
mikrofonu lub telefonu. Wszystkie listy z wcześniejszych nieudanych prób
uruchomienia środowiska testowego również zostały dezaktywowane.

## Powtórzenie testu

W aktywnym środowisku Python z aiohttp, z katalogu głównego projektu:

```sh
python -m scripts.live_api_probe
# W środowisku z requirements_test.txt:
python -m scripts.live_bridge_probe
```

Skrypt pyta o email i ukryte hasło. Każdy przebieg tworzy własną listę.
W bloku `finally` próbuje dezaktywować tę listę również po nieudanej asercji.
Awaria sieci podczas sprzątania może uniemożliwić cleanup; wtedy proces kończy
się błędem i listę `HA API test ...` trzeba sprawdzić ponownie.

Nie uruchamiamy tego skryptu w zwykłym pytest ani CI. Jest testem zapisującym
dane do rzeczywistego konta i wymaga autoryzacji dla tego konta.

## Co pozostaje niepotwierdzone

- Odtworzenie sesji po rzeczywistym restarcie HA lub systemu. Wykonany test
  tworzył nowego klienta w tym samym procesie i używał tylko refresh tokena.
- Trwałość refresh tokena w czasie, realne wygaśnięcie access tokena, rotacja
  po wielu odnowieniach i test długotrwały co najmniej 72 godziny.
- Logowanie Google, Facebook i magic oraz cofnięcie zgody.
- Listy współdzielone, utrata uprawnień i synchronizacja z telefonem.
- Różnica między koszem, archiwizacją, dezaktywacją i DELETE listy.
- Limity, paginacja, błędy 429/5xx na żywym serwerze i formaty delty.
- Operacje zbiorcze i zmiana kolejności produktów.
- E2E interfejsu HA, rzeczywisty mikrofon/STT i działająca instalacja użytkownika.

Skrypt sprawdza skutki zapisu przez kolejne GET, ale nie zapisuje dokładnych
statusów HTTP i surowych payloadów. Dokumentacja nie przypisuje mu potwierdzenia
konkretnego statusu 200/201/204 ani pełnego schematu wszystkich pól odpowiedzi.
