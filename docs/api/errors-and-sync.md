# Błędy i synchronizacja

Poniższe reguły opisują zachowanie naszego klienta. Obsługa błędów jest
sprawdzana automatycznie na atrapach HTTP; w teście na żywo nie wywoływano
celowo limitów API, awarii serwera ani utraty dostępu.

## Klasy błędów

| Sytuacja | Wynik klienta | Reakcja |
| --- | --- | --- |
| Brak refresh tokena / odrzucona sesja | `AuthError` | Ponowne logowanie. |
| 401 operacji API | Jedno odnowienie i ponowienie | Kolejne 401 kończy żądanie przez `AuthError`. |
| 403 zasobu | `ForbiddenError` | Zatrzymać zapis do celu; sprawdzić dostęp. |
| 404 zasobu | `NotFoundError` | Sprawdzić istnienie zasobu i mapowanie ID. |
| 429 GET | Ograniczone ponowienia | Respektować `Retry-After`. |
| 429 zapisu | `ApiError` | Brak automatycznego powtórzenia zapisu. |
| 5xx GET | Ograniczone ponowienia | Zachować ostatni poprawny snapshot. |
| 5xx zapisu / timeout zapisu | `AmbiguousWrite` | Odczytać stan przed kolejną próbą. |
| Błędny JSON odpowiedzi zapisu | `AmbiguousWrite` | Nie zakładać, że serwer nie wykonał operacji. |
| Awaria sieci / błędna odpowiedź odczytu | `ApiError` | Oznaczyć odczyt jako nieudany; nie zwracać pustej listy. |

HTTP 400/401/403 podczas logowania i odnowienia jest traktowane przez klienta
jako błąd autoryzacji. To lokalna polityka klienta, nie kompletna specyfikacja
wszystkich kodów odpowiedzi serwera.

## Limity i ponawianie

Timeout pojedynczego żądania wynosi 20 sekund. Klient dopuszcza maksymalnie
trzy próby żądania w swojej pętli; ponowienie po 401 również wykorzystuje próbę.
Retry 429/5xx dotyczy GET. Bez `Retry-After` odstępy wynoszą 1 i 2 sekundy.

Aktualna implementacja rozumie `Retry-After` jako liczbę sekund. Błędna wartość
uruchamia domyślny odstęp. Wartość niefinitywna lub większa niż 30 sekund
kończy żądanie błędem odroczenia. Format daty HTTP nie jest jeszcze obsługiwany.
Nie są znane limity Listonic dla liczby wywołań na konto.

Nie należy bezwarunkowo powtarzać POST po timeoutie. Serwer mógł zapisać
produkt lub listę, zanim połączenie zostało przerwane. Brak potwierdzonego
klucza idempotencji nie pozwala obiecywać dokładnie jednego zapisu.

## Pełny odczyt i delta

Integracja zaczyna od pełnych snapshotów bez `X-Last-Version`. Odczytuje listy
z produktami i odświeża je po operacji zapisu. Domyślny odstęp koordynatora
wynosi 30 sekund. Jest decyzją integracji, nie oficjalnym limitem Listonic.

Aplikacja WWW używa `X-Last-Version` i scala zmienione rekordy ze swoim stanem.
Nie potwierdzono zakresu numeru wersji, tombstone usunięć ani reguł wszystkich
operacji delty. Pusta delta nie oznacza pustej listy. Nie włączamy odczytów
przyrostowych bez potwierdzenia tych reguł.

Awaria pełnego odczytu zachowuje poprzednie dane i powinna oznaczać encję jako
niedostępną. Nie może kasować mapowań, produktów ani aliasów użytkownika.
HTTP 403 nie jest dowodem skasowania listy. Przejściowego braku listy nie należy
wykorzystywać do trwałego usuwania encji.

## Powiązanie z Shopping List HA

API nie jest backendem wbudowanej Shopping List. Moduł synchronizacji
łączy lokalne ID HA z ID Listonic, przechowuje wspólny snapshot i kolejkę zmian.
Ta sama nazwa produktu nie dowodzi, że to ten sam rekord.

Potwierdzenie standardowej komendy Assist oznacza lokalny zapis w HA. Osobny
sensor synchronizacji wskazuje, czy zmiana dotarła do Listonic. Bezpośrednie
akcje na encji Listonic mogą potwierdzać zapis po odpowiedzi API.

Konflikt edycji i usunięcia oraz zmiana listy docelowej wymagają jawnej obsługi.
Oczekujące operacje starego powiązania nie mogą zostać wysłane do nowej listy.
Pierwszy import zachowuje wszystkie pozycje, także powtarzające się nazwy.
Niepewne dodanie blokuje automatyczne zapisy do jawnego rozstrzygnięcia.
Instrukcję użytkownika i ograniczenia opisuje [Shopping List i Assist](../shopping-list.md).
Docelowy zakres wydania opisuje [plan integracji](../integration-plan.md).
