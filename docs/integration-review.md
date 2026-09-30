# Przegląd istniejących integracji Listonic

Data: 2026-09-27. Przegląd statyczny źródeł, testów, historii commitów,
wszystkich dostępnych otwartych/zamkniętych issues, ich komentarzy oraz PR-ów.
Nie uruchamiano integracji w HA ani nie testowano konta Listonic.

Aktualizacja decyzji projektowej: użytkownik wybrał synchronizację jednej listy
Listonic z wbudowaną Shopping List HA, wybieraną w config flow. Poniższy przegląd
zachowuje ocenę wariantu bezpośredniego todo; obowiązujący zakres bridge i testy
znajdują się w [planie integracji](integration-plan.md).

Rewizje:

- omelhus/ha-listonic: `aaf9794c1b109b6d27d769300bde9828964f9ad1` (0.3.1).
- Sanji78/listonic: `600338e9bf37995ded198cdd2152344f1a012322` (1.1.0).
- Dla zgodności sprawdzono również źródła Home Assistant Core `2026.9.4`.

## Rekomendacja

Zbudować własną integrację z niewielkim, oddzielonym klientem API.
Za wzorzec organizacji przyjąć omelhus; zakres zarządzania całymi listami
i trwałe przechowywanie refresh tokenu zaczerpnąć koncepcyjnie z Sanji78.
Nie kopiować żadnej z tych integracji w całości.

Obie mają licencję MIT. Przy przenoszeniu fragmentów kodu zachować wymagane
informacje o autorach i licencji. W tym przeglądzie nie przeniesiono kodu.

## omelhus — dobre rozwiązania

- Rozdzielone API, modele danych, koordynator, encje i config flow.
- Współdzielona sesja HTTP Home Assistant, runtime_data, poprawny kierunek
  obsługi reauth i rozróżniania awarii komunikacji od błędów logowania.
- Odczyt wszystkich list z produktami jednym żądaniem.
- Diagnostyka pomijająca nazwy produktów i maskująca email/hasło.
- Testy klienta, encji, konfiguracji, koordynatora i diagnostyki.
- Dynamiczne dodawanie encji; `list_id` jako atrybut; regulowany polling.

Źródło: [katalog integracji](https://github.com/omelhus/ha-listonic/tree/aaf9794c1b109b6d27d769300bde9828964f9ad1/custom_components/listonic).

## omelhus — problemy do uniknięcia

| Problem i skutek | Źródło w analizowanej rewizji |
| --- | --- |
| Edycja pozycji ignoruje zmianę nazwy. `elif` pomija opis przy równoczesnej zmianie statusu. Dodawanie ignoruje opis mimo zadeklarowanej obsługi opisów. | `todo.py:205–240` |
| OptionsFlow przypisuje `self.config_entry`, które w HA 2026.9.4 jest property bez settera: otwarcie opcji prowadzi do błędu. | `config_flow.py:155–157`; HA `config_entries.py`, klasa `OptionsFlow` |
| Refresh używa `grant_type` w body bez `provider=refresh_token`; różni się od aktualnego WWW. Działanie wymaga testu; fallback do hasła może maskować problem. Tokeny pozostają tylko w pamięci, hasło jest zachowywane. | `api.py:356–410`, `config_flow.py:81–84` |
| Powtórzenia po 5xx dotyczą każdej metody, również POST. Ponowienie dodawania po niejednoznacznej odpowiedzi może stworzyć duplikat. | `api.py:225–287` |
| Usunięcie ostatniej listy nie przechodzi przez cleanup, ponieważ pusty stan jest zawsze ignorowany. | `todo.py:75–106` |
| `summary` zawiera ilość, np. `Mleko (2 l)`. Standardowy intent HA szuka produktu po całej nazwie, więc polecenie usunięcia samego „mleko” może go nie znaleźć. | `todo.py:180–186`; HA `todo/intent.py` |
| Encje mają ID tylko od listy, co koliduje przy dwóch kontach mających tę samą współdzieloną listę. | `todo.py:132` |
| Klient zawiera tworzenie/usuwanie list, ale warstwa akcji HA rejestruje tylko zmianę nazwy. | `__init__.py:80–102` |

Testy są zaletą, lecz nie dowodem pełnej zgodności: część fixture używa
`isChecked`/`quantity`, a test refresh sprawdza sukces odpowiedzi pod
dopasowanym URL, nie pełny kontrakt żądania. Potrzebne są testy oparte na
zanonimizowanych rzeczywistych odpowiedziach.

## Sanji78 — dobre rozwiązania

- Całe listy można tworzyć, przemianowywać i usuwać z HA.
- Edycja produktu przekazuje zarówno nazwę, jak i status.
- Usunięcie listy używa `Active:0`, zgodnie ze zwykłym usunięciem w WWW.
- Rotowany refresh token Listonic jest zapisywany w config entry.
- Usuwanie wielu pozycji korzysta z endpointu zbiorczego.
- Rozważono logowanie Google i dynamiczne odkrywanie list.

Źródło: [katalog integracji](https://github.com/Sanji78/listonic/tree/600338e9bf37995ded198cdd2152344f1a012322/custom_components/listonic).

## Sanji78 — problemy do uniknięcia

| Problem i skutek | Źródło w analizowanej rewizji |
| --- | --- |
| Polling co 2 s oraz osobne pobieranie produktów każdej listy. Przed prawie każdym żądaniem dodatkowy GET list w celu sprawdzenia tokenu. Dla N list około 2×(N+1) żądań na cykl przy ważnym tokenie. | `todo.py:37–61`, `listonic_api.py:48–74` |
| Nowa `ClientSession` niemal dla każdego żądania, brak wspólnej puli połączeń i jednolitej polityki timeoutów. | `listonic_api.py` |
| Błąd pobrania produktów jest zamieniany na `[]`, więc awaria wygląda jak wyczyszczona lista. | `todo.py:44–49` |
| OAuth odbudowuje klienta z `entry.data.client_id/client_secret`, choć standardowy flow HA zapisuje `auth_implementation` i `token`. Oczekiwane jest pobranie istniejącej implementacji HA. To luka szczególnie istotna dla odświeżenia Google. | `oauth2.py:4–14`; HA `config_entry_oauth2_flow.py` |
| Reauth wraca do tworzenia nowego wpisu zamiast aktualizować istniejący; brak sprawdzenia tożsamości konta. | `config_flow.py:64–75` |
| Formularz opcji używa niezdefiniowanego `vol`; nie ma standardowego OptionsFlow ani podłączenia wyboru list do działania. | `config_flow.py:48–62` |
| Globalne akcje są rejestrowane dla każdego wpisu i zamykają w sobie klienta ostatniego konta. Unload nie usuwa tych akcji. | `__init__.py:36–126,135–144` |
| Listener koordynatora nie jest rejestrowany do zwolnienia przy unload. | `todo.py:119–124` |
| Brak testów funkcjonalnych i plików tłumaczeń w drzewie repozytorium. | Pełne drzewo analizowanej rewizji |

Nie ma podstaw, aby bez testu przypisać codzienne wygasanie tokenów wyłącznie
Google. Kod ma własne problemy z cyklem życia uwierzytelnienia.

## Issues i PR-y — sprawdzone także zamknięte

GitHub API `issues?state=all&per_page=100` zwróciło dla omelhus tylko otwarty
PR Renovate #1, bez zgłoszeń użytkowników. Dla Sanji78: cztery issues
(dwa otwarte, dwa zamknięte) oraz jeden otwarty PR. Wszystkie mieściły się
na pierwszej stronie. Sprawdzono komentarze obu zamkniętych issues.

| Źródło | Faktyczny stan | Wniosek do naszej implementacji |
| --- | --- | --- |
| [Sanji78 #1 — ilości produktów](https://github.com/Sanji78/listonic/issues/1) | Zamknięte; komentarz nie przedstawia poprawki, a kod `todo` nadal pomija ilość. | Ilość i jednostka muszą przechodzić przez API oraz być dostępne w HA; osobny test wyświetlania i zapisu. |
| [Sanji78 #2 — utrata połączenia](https://github.com/Sanji78/listonic/issues/2) | Zamknięte z komentarzem „Fixed”; logi wskazują DNS timeout i HTTP 504. Zdarzenie zamknięcia nie zawiera commit_id. | Test automatycznego powrotu po DNS/504, bez reload. Nigdy nie zamieniać awarii w pustą listę. |
| [Sanji78 PR #3 — amount/unit/description](https://github.com/Sanji78/listonic/pull/3) | Otwarty, niescalony. Opis deklaruje konwersję ilości do string, ale przejrzany diff przypisuje `amount` bez konwersji, a selector zwraca liczbę. | Faktyczna normalizacja ilości przed HTTP oraz test JSON typu string, w tym `0.1`. |
| [Sanji78 #4 — odpowiedź create_list i list_id](https://github.com/Sanji78/listonic/issues/4) | Otwarte. Tworzenie nie zwraca danych akcji, encje nie udostępniają list_id. | `create_list` zwraca list_id i name; encje mają list_id; pozostałe akcje przyjmują wygodny cel encji. |
| [Sanji78 #5 — codzienna reautoryzacja](https://github.com/Sanji78/listonic/issues/5) | Otwarte; brak logów i komentarzy ustalających przyczynę. | Trwała sesja, blokada równoległego refresh, test restartu i dłuższej pracy. |
| [omelhus PR #1](https://github.com/omelhus/ha-listonic/pull/1) | Otwarty onboarding Renovate, nie poprawka funkcjonalna. | Automatyczne sprawdzanie zależności można wykorzystać; nie stanowi walidacji działania. |

## Co pokazuje historia poprawek

- [Sanji78 3d43604](https://github.com/Sanji78/listonic/commit/3d43604b88eb062550c817085b5bdf87485a1fc2)
  poprawia globalny błąd odczytu: wcześniej zwracał zero list, potem rzuca
  wyjątek. Jednocześnie błąd pojedynczej listy nadal zwraca zero produktów.
  To poprawa częściowa; nie powielać tego drugiego zachowania.
- [Sanji78 7ca7cec](https://github.com/Sanji78/listonic/commit/7ca7cec2cecd79c53ddc49ac2318297f0c0b4ecd)
  dodaje zapis/odczyt refresh tokenu i sprawdzanie tokenu przez GET przed
  żądaniem. Zachować trwałość tokenu, zastąpić nadmiarowy GET obsługą 401/TTL.
- [omelhus 15ffdef](https://github.com/omelhus/ha-listonic/commit/15ffdef80e383ba7e2d4f8f8695223b35f19d77c)
  naprawia odkrywanie nowych list i nieograniczone ponawianie 401.
  Zachować oba scenariusze w testach regresji.
- [omelhus 30a5a75](https://github.com/omelhus/ha-listonic/commit/30a5a75dbf0434e457dac155280d5f9bfbf7c00a)
  usuwa encje znikających list, a kolejna
  [poprawka 4b1ca48](https://github.com/omelhus/ha-listonic/commit/4b1ca48683be920a9a5406ca1161081bce2dbb0e)
  blokuje masowe usunięcie przy pustej odpowiedzi. Powstaje luka dla prawdziwego
  usunięcia ostatniej listy. Trzeba rozróżnić awarię, deltę i pełny pusty snapshot.
- [omelhus 33e9753](https://github.com/omelhus/ha-listonic/commit/33e97535f4f3cd33b3b812c4922b920e694461f7)
  dodaje timeouty i obsługę przejściowych błędów startu. Testować także sam etap
  logowania, który w aktualnym setup jest przed blokiem obsługi odczytu.

Nie utożsamiono chronologicznie zbliżonego commita z oficjalnie przypisaną
poprawką issue #2: w zdarzeniu zamknięcia nie ma takiego powiązania.

## Assist i platforma todo

Bezpośrednia platforma `todo` jest właściwym fundamentem. Lista Listonic
pojawia się w panelu list HA i obsługuje standardowe akcje. Nie wymaga to
drugiej, lokalnej kopii w `shopping_list`.

Sprawdzony [kod HA 2026.9.4](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/todo/intent.py)
ma intenty dodawania, odhaczania i usuwania pozycji z nazwanej listy.
[Aktualne polskie zdania](https://ohf-voice.github.io/intents/language_pl.html)
obejmują „dodaj jabłka do listy zakupy”, „odhacz jabłka z listy zakupy”
i „usuń jabłka z listy zakupy”. Wersja pakietu zdań na docelowym HA wymaga
testu; bieżąca gałąź słownika nie musi odpowiadać starszej instalacji.

Encję trzeba [udostępnić Assist](https://www.home-assistant.io/voice_control/voice_remote_expose_devices/).
Nazwa i alias mają być krótkie i jednoznaczne, np. „Listonic”.

Istotne rozróżnienie: fraza „dodaj mleko do listy zakupów” może uruchomić
`HassShoppingListAddItem`, którego
[handler](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/shopping_list/intent.py)
zapisuje do lokalnej integracji shopping_list. Sam wybór „listy domyślnej”
w naszej konfiguracji nie zmieni tego routingu.
Potrzebna jest jawna nazwa listy albo dodatkowa, przetestowana komenda/automatyzacja.

Prosty Assist przekazuje tekst pozycji. Rozbicie „mleko, chleb i jajka” na trzy
produkty oraz odczyt ilości z „dwa litry mleka” to dodatkowa funkcja, której
nie daje samo udostępnienie encji. Dla pierwszej wersji: jedna pozycja na
komendę. Dla rozszerzenia: osobna akcja batch i parser lub agent LLM z narzędziem.

Plan realizacji: [integration-plan.md](integration-plan.md).
