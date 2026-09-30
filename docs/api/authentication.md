# Logowanie i sesja

Logowanie email/hasło oraz odnowienie sesji samym refresh tokenem zostały
sprawdzone na prawdziwym API 28.09.2026. [Zakres testu](live-validation.md).

## Wspólne nagłówki

```http
DeviceId: <uuid urządzenia>
Version: web:4.0.0
Culture: pl
Accept: application/json
```

`DeviceId` tworzymy raz dla sesji integracji i zachowujemy przy odnowieniu.
Test potwierdził działanie zestawu powyżej, bez nagłówka `LCode` obecnego
w aplikacji WWW. Nie sprawdzał, które z tych nagłówków można pominąć.

## Logowanie email i hasłem

```http
POST /api/loginextended?provider=password&autoMerge=1&autoDestruct=1
Content-Type: application/x-www-form-urlencoded
clientauthorization: Bearer <base64(client_id:client_secret)>
```

W formularzu należy wysłać:

| Pole | Wartość |
| --- | --- |
| `username` | Email konta, bez spacji na początku i końcu. |
| `password` | Hasło konta. |
| `client_id` | `listonicv2` — identyfikator publicznego klienta WWW. |
| `client_secret` | Publiczna konfiguracja aplikacji WWW; używana wartość znajduje się w `custom_components/lists_assistant/const.py`. |
| `redirect_uri` | `https://listonicv2api.jestemkucharzem.pl` |

Każde pole kodujemy jako wartość formularza. Znak `+` w emailu musi zostać
zakodowany jako `%2B`, a znaki `&`, `#` i `@` w haśle jako część wartości.
Nie sklejamy body z niezakodowanych danych. `aiohttp` wykonuje kodowanie przy
przekazaniu słownika do `data=`. Konto użyte w teście miało `+` w emailu
i znaki specjalne w haśle; logowanie przeszło.

Konfiguracja klienta jest dostarczana przez Listonic w publicznym pakiecie WWW.
Nie zastępuje tokenów konta i nie gwarantuje dostępu do danych użytkownika.

Minimalny przykład odpowiedzi, z zastępczymi wartościami:

```json
{
  "access_token": "<access token>",
  "refresh_token": "<refresh token>"
}
```

Klient wymaga niepustego `access_token`. Nie zakłada konkretnego TTL ani
obecności innych pól. `autoMerge=1` i `autoDestruct=1` odwzorowują wywołanie
aplikacji WWW; ich znaczenie dla scalania innych sesji nie zostało przetestowane.
Login działał bez wcześniejszego tworzenia anonimowego konta.

## Zwykłe żądania

Po logowaniu używamy wspólnych nagłówków i:

```http
Authorization: Bearer <access token>
```

Przy zapisie JSON: `Content-Type: application/json`. Nagłówka
`clientauthorization` używamy do wymiany tokenów, a `Authorization` do
operacji na profilu, listach i produktach.

## Odświeżenie sesji

```http
POST /api/loginextended?provider=refresh_token&autoMerge=1&autoDestruct=1
Content-Type: application/x-www-form-urlencoded
clientauthorization: Bearer <base64(client_id:client_secret)>

refresh_token=<zakodowany refresh token>
```

Aktualny klient wysyła tylko `refresh_token` w body odnowienia. Ten wariant
z zachowanym `DeviceId` przeszedł test API. Nie wymagał hasła ani access tokena.

Po udanej wymianie:

- zapisujemy nowy access token;
- zastępujemy refresh token, jeżeli odpowiedź go zawiera;
- zachowujemy poprzedni refresh token, jeżeli nowy nie został zwrócony;
- utrwalamy zmianę przed użyciem jej po kolejnym restarcie integracji.

Jednoczesne żądania, które otrzymały 401 dla tego samego access tokena,
korzystają z jednej odnowionej sesji. Nasz klient serializuje refresh blokadą
asynchroniczną i sprawdza, czy inna operacja już zmieniła odrzucony token.
To zachowanie potwierdzają testy automatyczne.

## Tożsamość konta

```http
GET /api/account/userinfo
Authorization: Bearer <access token>
```

Odpowiedź zawiera obiekt z polem `Username`. Klient wymaga jego obecności.
Przykład części odpowiedzi:

```json
{"Username": "demo@example.test"}
```

Test porównał `Username` przed i po odnowieniu sesji. Pozostałe pola profilu
nie są potrzebne do tego sprawdzenia i nie są zapisywane w raporcie.

## Obsługa błędów i ograniczenia

HTTP 400/401/403 z wymiany tokenów nasz klient traktuje jako odrzucenie
uwierzytelnienia. Timeout i 5xx oznaczają awarię usługi; nie kasują zachowanych
tokenów. Po 401 zwykłego żądania klient wykonuje jedno odnowienie i ponowienie.
Kolejne 401 wymaga ponownego logowania.

Nie potwierdzono jeszcze logowania Google/Facebook/magic, rzeczywistego TTL,
długotrwałej rotacji ani cofnięcia zgody. Natychmiastowy refresh nie zastępuje
testu wygaśnięcia tokena po wielu godzinach.
