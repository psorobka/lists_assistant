# Shopping List i Assist

Integracja synchronizuje jedną wybraną listę Listonic z wbudowaną Shopping List
Home Assistant. Pozostałe wybrane listy są dostępne jako encje `todo`.
Powiązanie korzysta z publicznych akcji `todo` i zdarzeń HA oraz własnego pliku
Store; nie modyfikuje core ani plików danych Shopping List.

## Włączenie

1. Skonfiguruj w HA integrację **Shopping List** i sprawdź, że jej lista działa.
2. Dodaj integrację **Lists Assistant**, zaloguj się i wybierz listy.
3. Wskaż listę Listonic, którą chcesz powiązać z listą zakupów HA.
4. Przeczytaj podsumowanie liczby pozycji i potwierdź pierwsze scalenie.
5. Sprawdź sensor **Synchronizacja listy zakupów**. Stan „Zsynchronizowano”
   oznacza, że aktualne snapshoty wybranej listy i Shopping List są zgodne.
6. Powiedz do Assist **„Dodaj mleko do listy zakupów”**.

Istniejące wpisy z obu stron zostają zachowane. Pierwsze scalenie traktuje je
jako osobne produkty: jedna pozycja „mleko” w HA i jedna „mleko” w Listonic dadzą
dwie pozycje w każdej liście. Nie zgadujemy tożsamości produktów po nazwie.
Po pierwszym scaleniu mapowania ID zapobiegają ponownemu importowi przy reload.

Wcześniejsza konfiguracja, utworzona zanim działał bridge, wymaga wejścia w
**Opcje** i potwierdzenia scalenia. Sam wcześniej zapisany wybór listy nie
uruchomi nowych zapisów w chmurze.

## Jakie zmiany działają

- Dodawanie z Shopping List i Listonic.
- Zmiana nazwy, odhaczanie i odznaczanie w obie strony.
- Usuwanie pozycji, także czyszczenie wykonanych i zbiorcze odhaczanie w HA.
- Zachowanie ilości, jednostki, ceny i opisu Listonic podczas zmian nazwy/statusu
  z Shopping List. Lokalna Shopping List nie reprezentuje tych metadanych.
- Import zmian wykonanych bezpośrednio przez encję `todo` Listonic.

Odczyt chmury następuje domyślnie co 30 sekund i po zapisie. Zmiana w telefonie
może więc pojawić się w HA po następnym odczycie.

## Sukces lokalny i zapis w chmurze

Assist korzysta z wbudowanej Shopping List. Jego potwierdzenie oznacza zapis
lokalny. Sensor synchronizacji pokazuje osobno stan wysyłki do Listonic.
Podczas awarii HA może przyjąć zakup, a sensor będzie pokazywał oczekiwanie.
Po powrocie sieci bridge odczytuje obie strony i uzgadnia zmiany.

Mapowania, wspólny snapshot i kolejka są zapisywane w
`.storage/lists_assistant_bridge.<config_entry_id>`. To plik należący do tej integracji.
Operacja jest utrwalana przed zapisem do celu. Przerwanie procesu z operacją
w locie oznacza niepewny wynik po odtworzeniu; dodawanie nie zostanie ślepo
powtórzone. Nowe lokalne zakupy podczas niedostępności setup integracji nadal
pozostają w natywnej Shopping List i zostaną porównane po odzyskaniu połączenia.

## Sensor i naprawy

| Stan | Znaczenie |
| --- | --- |
| `synchronized` | Brak oczekujących zmian i zgodny wspólny stan. |
| `pending` | Oczekiwanie na połączenie, retry lub odczyt potwierdzający zapis. |
| `conflict` | Sprzeczna edycja/usunięcie albo niepewny wynik zapisu; potrzebna decyzja. |
| `shelved` | Zmiany starego powiązania są zachowane i nie trafiają na nową listę. |
| `shopping_list_missing` | Natywna lista zakupów nie jest gotowa. |
| `list_unavailable` | Wybrana lista Listonic jest nieobecna lub niedostępna. |
| `confirmation_required` | Potwierdź scalenie w opcjach integracji. |
| `target_changed` | Zmieniła się tożsamość natywnej Shopping List; stare mapowania nie są używane do zapisów. |
| `storage_error` | Dane synchronizacji są uszkodzone albo zapis się nie udał; zapisy zostały zatrzymane. |

Sensor udostępnia liczbę oczekujących operacji, konfliktów, niepewnych zapisów
i pozycji pozostających przy starym powiązaniu. Nie zawiera nazw zakupów ani
tokenów. Diagnostyka integracji zawiera wyłącznie status i liczniki.

Nie usuwaj pliku mapowań jako sposobu naprawy konfliktu: utrata tożsamości
produktów spowoduje ponowne pierwsze scalenie. Przy błędzie danych zachowaj plik
i sprawdź kopię zapasową. Przy `target_changed` zatrzymaj bridge i sprawdź,
dlaczego została odtworzona/usunięta konfiguracja natywnej Shopping List;
automatyczny reset mapowań nie jest wykonywany.

## Rozstrzyganie konfliktów

W **Narzędziach deweloperskich → Akcje** wywołaj:

```yaml
action: lists_assistant.get_sync_issues
data:
  config_entry_id: "<ID wpisu integracji Listonic>"
```

Odpowiedź zawiera `pending`, `conflicts` oraz `shelved`. Ta jawna akcja może
zawierać nazwy produktów i obie wersje konfliktu; nie jest zanonimizowaną
diagnostyką przeznaczoną do publicznego zgłoszenia błędu.

Po porównaniu obu wersji wybierz stronę, którą chcesz zachować:

```yaml
action: lists_assistant.resolve_sync_conflict
data:
  config_entry_id: "<ID wpisu>"
  conflict_id: "<klucz z conflicts>"
  choose: home_assistant  # albo listonic
```

Usunięcie kontra edycja też wymaga wyboru. Zachowanie istniejącej wersji może
odtworzyć produkt po stronie, gdzie został usunięty; wybór usunięcia usuwa drugą
wersję. Decyzja jest jawna i nie jest ustalana przez zegary urządzeń.

Niepewne dodanie wymaga sprawdzenia listy docelowej. Jeśli produkt powstał,
wskaż jego nowy ID z celu:

```yaml
action: lists_assistant.resolve_sync_write
data:
  config_entry_id: "<ID wpisu>"
  operation_id: "<klucz niepewnej operacji z pending>"
  outcome: applied
  target_item_id: "<ID nowego produktu w celu>"
```

Dla kierunku `remote` celem jest Listonic, dla `local` — Shopping List HA.
Istniejące przed operacją lub już zmapowane ID zostaną odrzucone. Jeśli po
sprawdzeniu potwierdzisz, że zapis nie nastąpił, użyj `outcome: not_applied`;
wtedy bridge może ponownie wykonać dodanie. Niepewny PATCH/DELETE z konkretnym
ID może zostać uzgodniony automatycznie, jeśli świeży odczyt potwierdzi jego wynik.

## Zmiana listy i odłączenie

Opcje integracji pozwalają zmienić wybór list i powiązanie. Przed nowym
powiązaniem pokażą liczbę pozycji oraz liczbę wpisów pozostających przy starym
celu. Oczekujące operacje i konflikty starego celu pozostają zapisane pod jego
ID. Powiązane z nimi lokalne pozycje nie są importowane do nowej listy.
Niepewne zapisy trzeba rozstrzygnąć przed przełączeniem celu.

Aby wznowić zachowane operacje, ponownie wybierz poprzednią listę w opcjach.
Odłączenie przez „Nie łącz” („No bridge” po angielsku) pozostawia zawartość obu list i zapisane mapowania.
Na jednej instancji HA może być tylko jedno powiązanie, także przy wielu kontach.

## Walidacja i ograniczenia

Testy w WSL używają rzeczywistych komponentów HA Shopping List, todo,
conversation i sensor oraz fikcyjnego API Listonic. Polska komenda
`conversation.process` została sprawdzona online i offline. Sprawdzono też
scalenie z duplikatami, reload, kolejkę offline, przerwanie POST, konflikty
edycji/usunięcia i zmianę celu. Nie zastępuje to testu mikrofonu/STT, testu
interfejsu przeglądarkowego ani długotrwałego testu rzeczywistej sesji.

Osobny test z prawdziwym API w izolowanej instancji HA potwierdził polski Assist,
zmiany w obie strony, zachowanie metadanych, reload bez duplikatów oraz usuwanie.
Lista jednorazowa została dezaktywowana. Szczegóły i polecenie powtórzenia:
[raport live](api/live-validation.md).

Konflikty są rozpoznawane na podstawie odczytanych snapshotów. API Listonic nie
ma potwierdzonej w tej integracji operacji warunkowej/CAS: zmiana w chmurze
między ostatnim GET a PATCH może zostać nadpisana. Live test nie sprawdzał tego
wyścigu; ostatni odczyt przed zapisem ogranicza jego okno, ale go nie eliminuje.
