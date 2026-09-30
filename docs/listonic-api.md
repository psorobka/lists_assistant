# Rozpoznanie API Listonic

Data: 2026-09-27. Cel: integracja Home Assistant obsługująca listy i produkty.

Aktualizacja 2026-09-28: wykonano udany test zalogowanego API, obejmujący login,
refresh i CRUD na dedykowanej liście. Aktualny opis kontraktu znajduje się
w [dokumentacji API](api/README.md), a dokładny zakres w
[raporcie weryfikacji](api/live-validation.md). Poniższa analiza zachowuje
historyczny poziom potwierdzenia ustaleń z kodu WWW.

## Wniosek

Nie znaleziono oficjalnej publicznej dokumentacji pełnego API do zarządzania
listami użytkownika. Oficjalny [Listonic Button](https://buttons.listonic.com/docs)
opisuje widget dodawania produktów, a nie API do synchronizacji konta.

Istnieje [nieoficjalny opis API](https://github.com/ArturLys/listonic-mcp/blob/main/API.md)
oraz integracje [omelhus/ha-listonic](https://github.com/omelhus/ha-listonic)
i [Sanji78/listonic](https://github.com/Sanji78/listonic).
Nie trzeba więc zaczynać reverse engineeringu od zera. Nie przeprowadzono audytu
tych integracji ani testu ich działania; deklaracje README nie są gwarancją.

Dokument społecznościowy zawiera rozbieżności, m.in. `isChecked`/`Checked`,
`quantity`/`Amount`, oraz twierdzenie o PascalCase dla wszystkich żądań.
Aktualna aplikacja wysyła także pola camelCase. Poniżej zapisano własne
ustalenia z analizy publicznych plików JavaScript Listonic.

## Źródła i poziom potwierdzenia

Pobrano HTML https://app.listonic.com/en oraz publiczne moduły wskazane przez
HTML i manifest Next.js. Nie logowano się, nie zakładano konta i nie zmieniano
danych użytkownika.

Build: `Lyy2EzgqcL6pxXfOm0Knd`.

Główne źródła:

- https://app.listonic.com/_next/static/chunks/pages/_app-59e2fd53eb6017bb.js
  — moduł `34614`: logowanie; `48732`: konfiguracja klienta;
  `31090`: klient HTTP; `96426`: produkty; `55571`: listy.
- https://app.listonic.com/_next/static/chunks/pages/lists/%5BlistId%5D-641072a5d1e0c26b.js
  — formularze produktów, odhaczanie, odczyt pól odpowiedzi.
- https://app.listonic.com/_next/static/chunks/988-b41f8370d04d6fe9.js
  — pobieranie list, zmiana nazwy i usuwanie listy z interfejsu.

SHA256 głównego pliku `_app`:
`85FF551738246E81C4CE9FF4A79387E1CEB343206E327FBBDEFDB78494AC23EB`.

„Potwierdzone w kodzie” oznacza zaobserwowanie wywołania w kliencie WWW.
Nie oznacza sprawdzenia odpowiedzi serwera dla zalogowanego użytkownika.

## Uwierzytelnianie

Baza: `https://api.listonic.com`.

Logowanie email/hasło:

```http
POST /api/loginextended?provider=password&autoMerge=1&autoDestruct=1
Content-Type: application/x-www-form-urlencoded
clientauthorization: Bearer <base64(client_id:client_secret)>
DeviceId: <stały identyfikator urządzenia>
Version: web:4.0.0
Culture: pl
```

Body formularza: `username`, `password`, `client_id`, `redirect_uri`,
`client_secret`; wartości trzeba poprawnie zakodować jako formularz.
Moduł `48732` zawiera publiczną konfigurację klienta WWW: `client_id=listonicv2`,
sekret aplikacji i `redirect_uri=https://listonicv2api.jestemkucharzem.pl`.
Konfiguracja ta jest dostarczana każdemu użytkownikowi WWW, nie jest osobistym
tokenem konta. Jej stabilność nie jest gwarantowana.

Kod logowania może dołączać `Authorization` istniejącej sesji do operacji
łączenia kont. Semantyka `autoMerge` i `autoDestruct` oraz konieczność
posiadania anonimowej sesji przed logowaniem wymagają testu. Nie należy
zakładać, że integracja musi tworzyć konto anonimowe.

Klient odczytuje z odpowiedzi `access_token` i `refresh_token`. Zwykłe żądania
wysyłają `Authorization: Bearer <access_token>`, `DeviceId`, `Version`,
`Culture`, `Content-Type: application/json` i `LCode` (czas startu klienta
w milisekundach). Konieczność każdego nagłówka po stronie serwera nie została
sprawdzona. `DeviceId` jest zachowywany przez klienta między uruchomieniami.

Odświeżanie:

```http
POST /api/loginextended?provider=refresh_token&autoMerge=1&autoDestruct=1
Content-Type: application/x-www-form-urlencoded
clientauthorization: Bearer <base64(client_id:client_secret)>

refresh_token=<token zakodowany jako wartość formularza>
```

Kod WWW po HTTP 401 odświeża sesję i ponawia żądanie. Zapisuje oba tokeny
z nowej odpowiedzi. Integracja powinna serializować odświeżanie, zachowywać
nowy refresh token i ograniczać ponowienia. Nie hardkodować czasu ważności:
wartość zapisywana przez frontend w cookie nie jest dowodem TTL serwera.

W kodzie istnieją także providery `google`, `facebook` i `magic`. Samo ich
istnienie nie potwierdza, że dowolny własny klient OAuth Google będzie
akceptowany przez Listonic. To wymaga osobnego sprawdzenia.

## Listy — wywołania potwierdzone w kodzie WWW

| Operacja | Metoda i ścieżka | Parametry / body |
| --- | --- | --- |
| Pobranie list | `GET /api/lists` | `includeShares=true&archive=false&includeItems=true` |
| Szczegóły listy | `GET /api/lists/{listId}` | `includeShares=true` |
| Utworzenie | `POST /api/lists` | `{"Name":"Zakupy","SortMode":0}` |
| Zmiana nazwy | `PATCH /api/lists/{listId}` | `{"Id":"<listId>","Name":"Nowa nazwa"}` |
| Usunięcie z interfejsu | `PATCH /api/lists/{listId}` | `{"Id":"<listId>","Active":0}` |
| Osobny endpoint DELETE | `DELETE /api/lists/{listId}` | Znaleziony w warstwie API; skutki wymagają testu |
| Kopiowanie listy | `POST /api/lists` | `asCopyOf=<listId>` |

Nie utożsamiać `DELETE` ze zwykłym przeniesieniem do kosza. Interfejs zwykłego
usunięcia korzysta z `Active:0`. Znaczenie DELETE, przywracania i archiwizacji
należy potwierdzić na liście testowej.

Klient czyta m.in. pola `Id`, `Name`, `Items`, `Shares`, `SortOrder`.
Identyfikatory należy zachowywać jako stringi po stronie HA.

## Produkty — wywołania potwierdzone w kodzie WWW

| Operacja | Metoda i ścieżka |
| --- | --- |
| Pobranie produktów | `GET /api/lists/{listId}/items` |
| Dodanie | `POST /api/lists/{listId}/items` |
| Edycja / odhaczanie | `PATCH /api/lists/{listId}/items/{itemId}` |
| Usunięcie | `DELETE /api/lists/{listId}/items/{itemId}` |
| Dodanie wielu | `POST /api/lists/{listId}/multipleitems`, body `{"Items":[...]}` |
| Usunięcie wielu | `DELETE /api/lists/{listId}/multipleitems`, body wymaga dalszej weryfikacji |
| Odznaczenie wszystkich | `POST /api/lists/{listId}/items/checkall?direction=false` |
| Własna kolejność | `POST /api/lists/{listId}/items/customsort`, body do weryfikacji |

Przykładowe body dodawania odpowiadające formularzowi klienta:

```json
{"name":"Mleko","amount":"2","unit":"l"}
```

Edycja szczegółów wysyła m.in. `name`, `amount`, `unit`, `price`, `description`.
Odhaczanie w jednej ze ścieżek klienta:

```json
{"itemId":"<itemId>","checked":1}
```

`checked:0` oznacza odznaczenie. Klient czyta odpowiedzi z polami `Id`,
`Name`, `Checked`, `Amount`, `Unit`, `Price`, `Description`, `CategoryId`,
`PictureUrl`, `SortOrder`. `Checked` jest porównywane z liczbą 1.
Nie używać bez weryfikacji `isChecked` ani `quantity` z początkowych przykładów
dokumentu społecznościowego. Nie zakładać też identycznej wielkości liter
w żądaniach i odpowiedziach.

## Synchronizacja

Klient używa nagłówka `X-Last-Version` do odczytu list i produktów i odczytuje
`x-last-version` z odpowiedzi. Kod scala zwrócone zmiany z bieżącym stanem,
więc odpowiedź przyrostowa nie może zastępować całej kolekcji. Pusta tablica
przy odczycie przyrostowym nie oznacza pustej listy zakupów.

W analizowanych modułach widoczny jest polling przez `setInterval`.
Nie potwierdzono mechanizmu push/WebSocket odpowiedniego dla integracji.
Na początek proponowany jest pełny odczyt bez `X-Last-Version`, co około
30–60 s oraz po zapisie; to decyzja projektowa, nie limit zalecany przez Listonic.
Limity API, stronicowanie i gwarancje pełnego odczytu wymagają testu.

## Proponowane odwzorowanie w Home Assistant

Według [dokumentacji TodoListEntity](https://developers.home-assistant.io/docs/core/entity/todo/)
platforma `todo` obsługuje odczyt, dodawanie, aktualizację i usuwanie pozycji.

- Jedna `TodoListEntity` na listę Listonic; stabilne ID oparte na koncie i ID listy.
- `Id` produktu → `TodoItem.uid`, `Name` → `summary`,
  `Checked` → `COMPLETED` / `NEEDS_ACTION`, `Description` → `description`.
- Standardowe akcje `todo` do produktów; własne akcje integracji do tworzenia,
  zmiany nazwy i usuwania całych list oraz pól ilość/jednostka/cena.
- Asynchroniczny klient HTTP, wspólny koordynator odczytu, ponowna autoryzacja
  po utracie sesji i zachowanie tokenów poza repozytorium/logami.
- Odkrywanie nowych list i poprawna obsługa list usuniętych oraz współdzielonych.

## Pozostała walidacja przed uznaniem klienta za działający

Potrzebne jest konto testowe lub autoryzowana sesja użytkownika. Nie trzeba
przesyłać hasła ani tokenów w rozmowie.

1. Sprawdzić login bez anonimowego konta i odświeżanie tokenu po ponownym uruchomieniu.
2. Na nowej liście testowej sprawdzić tworzenie i zmianę nazwy.
3. Dodać produkt, zmienić opis/ilość/cenę, odhaczyć i odznaczyć, następnie usunąć.
4. Zweryfikować synchronizację zmian z telefonu i dostęp do list współdzielonych.
5. Potwierdzić różnicę między `Active:0`, archiwizacją i `DELETE`.
6. Ustalić rzeczywiste formaty odpowiedzi, puste odpowiedzi PATCH/DELETE,
   błędy 401/403/429, paginację i semantykę odczytów przyrostowych.

Powyższa lista przedstawia zakres proponowany 27.09.2026. Aktualny wynik
weryfikacji z 28.09.2026 opisuje [raport testu na żywo](api/live-validation.md).
