# Listy i produkty

Operacje oznaczone „API na żywo” sprawdzono 28.09.2026 na nowej liście
testowej. Wszystkie zmiany zweryfikowano kolejnym odczytem. Żądania korzystają
z nagłówków opisanych w [dokumencie sesji](authentication.md).

## Identyfikatory i wielkość liter

Integracja przechowuje identyfikatory jako stringi i dopuszcza tylko ID
złożone z cyfr. Nie używa nazwy listy jako adresu docelowego.
Serwer zwraca istotne pola w PascalCase, np. `Name`, `Amount`, `Checked`.
Żądania produktów używają camelCase, np. `name`, `amount`, `checked`.
Nie należy mechanicznie zmieniać wielkości liter między żądaniem i odpowiedzią.

## Endpointy list

| Operacja | Żądanie | Potwierdzenie |
| --- | --- | --- |
| Pełny odczyt | `GET /api/lists?includeShares=true&archive=false&includeItems=true` | API na żywo |
| Szczegóły listy | `GET /api/lists/{listId}?includeShares=true` | Kod WWW |
| Utworzenie | `POST /api/lists` | API na żywo |
| Zmiana nazwy | `PATCH /api/lists/{listId}` | API na żywo |
| Usunięcie z aktywnych | `PATCH /api/lists/{listId}` z `Active:0` | API na żywo |
| Osobna operacja DELETE | `DELETE /api/lists/{listId}` | Kod WWW; skutki niepotwierdzone |
| Kopiowanie | `POST /api/lists?asCopyOf={listId}` | Kod WWW; nieprzetestowane |

### Odczyt

Ilustracyjna część odpowiedzi:

```json
[
  {
    "Id": "1001",
    "Name": "Zakupy",
    "Active": 1,
    "Items": [
      {
        "Id": "2001",
        "Name": "Mleko",
        "Checked": 0,
        "Amount": "2.5",
        "Unit": "l",
        "Price": 3.49,
        "Description": "Przykładowa notatka"
      }
    ]
  }
]
```

Przykład pokazuje pola używane przez integrację, nie pełny schemat odpowiedzi.
`Amount` normalizujemy jako tekst liczby dziesiętnej; test na żywo sprawdzał
wartość liczbową, nie wymuszał konkretnego typu JSON odpowiedzi.

Klient wymaga tablicy list i tablicy `Items`. Gdy `Items` nie ma w odpowiedzi
listy, odczytuje `GET /api/lists/{listId}/items`. Ten fallback ma testy
automatyczne; nie był wymagany w teście na żywo. Rekordy z `Active:0`
lub `Deleted` są wykluczane. Brak poprawnego snapshotu oznacza błąd odczytu.

### Utworzenie listy

```http
POST /api/lists
Content-Type: application/json

{"Name":"Zakupy testowe","SortMode":0}
```

Odpowiedź na żywo zawierała obiekt z `Id`, użyty do kolejnych operacji.
Nie utożsamiamy nazwy z identyfikatorem. Niejednoznacznego wyniku POST nie
powtarzamy automatycznie, ponieważ mógł już utworzyć listę.

### Zmiana nazwy

```http
PATCH /api/lists/1001
Content-Type: application/json

{"Id":"1001","Name":"Zakupy — nowa nazwa"}
```

Test używał zgodnego ID w URL i body oraz sprawdził nową nazwę w pełnym odczycie.

### Usunięcie z aktywnych list

```http
PATCH /api/lists/1001
Content-Type: application/json

{"Id":"1001","Active":0}
```

Po tej operacji testowa lista zniknęła z odczytu aktywnych list. Ten wynik
potwierdza dezaktywację. Nie dowodzi trwałego usunięcia, zachowania kosza
ani możliwości przywrócenia. Osobnego `DELETE` listy nie testowano.

## Endpointy produktów

| Operacja | Żądanie | Potwierdzenie |
| --- | --- | --- |
| Osobny odczyt produktów | `GET /api/lists/{listId}/items` | Kod WWW i test automatyczny fallbacku |
| Dodanie | `POST /api/lists/{listId}/items` | API na żywo |
| Edycja / odhaczanie | `PATCH /api/lists/{listId}/items/{itemId}` | API na żywo |
| Usunięcie | `DELETE /api/lists/{listId}/items/{itemId}` | API na żywo |
| Dodanie wielu | `POST /api/lists/{listId}/multipleitems` | Kod WWW |
| Usunięcie wielu | `DELETE /api/lists/{listId}/multipleitems` | Kod WWW; body do ustalenia |
| Odznaczenie wszystkich | `POST /api/lists/{listId}/items/checkall?direction=false` | Kod WWW |
| Zmiana kolejności | `POST /api/lists/{listId}/items/customsort` | Kod WWW; body do ustalenia |

### Dodanie z metadanymi

```http
POST /api/lists/1001/items
Content-Type: application/json

{
  "name": "Mleko",
  "amount": "2.5",
  "unit": "l",
  "description": "Przykładowa notatka",
  "price": 3.49
}
```

W teście odpowiedź zawierała obiekt z `Id`. Kolejny pełny odczyt potwierdził
nazwę i wszystkie cztery metadane. Ilość wysyłamy jako tekst, z kropką
dziesiętną i bez zapisu naukowego. Klient odrzuca wartości ujemne, NaN i infinity.
Nie sprawdzono jeszcze maksymalnych długości pól ani wszystkich jednostek.

### Jednoczesna zmiana nazwy i odhaczenie

```http
PATCH /api/lists/1001/items/2001
Content-Type: application/json

{"name":"Mleko bez laktozy","checked":1}
```

Test potwierdził zmianę obu pól w jednym żądaniu. `Amount`, `Unit`, `Price`
i `Description` pozostały niezmienione. Nie trzeba odsyłać pełnego produktu.
Testowany endpoint działał bez dodatkowego `itemId` w body.

### Odznaczenie i wyczyszczenie notatki

```http
PATCH /api/lists/1001/items/2001
Content-Type: application/json

{"checked":0,"description":""}
```

`checked:1` oznacza odhaczenie, `checked:0` odznaczenie. Pominięte pole oznacza
brak zmiany. Pusty string `description` usuwa notatkę — potwierdzono na żywo.
Semantyki `null` i pustych wartości pozostałych metadanych nie testowano.

### Usunięcie produktu

```http
DELETE /api/lists/1001/items/2001
```

Po operacji produkt był nieobecny w kolejnym snapshotcie listy.
Nie testowano ponownego DELETE tego samego ID ani zachowania usunięć w delcie.

## Odwzorowanie w Home Assistant

| Listonic | Home Assistant |
| --- | --- |
| ID listy | Tożsamość encji `todo`, wraz z tożsamością konta. |
| `Id` produktu | `TodoItem.uid` |
| `Name` | `TodoItem.summary` |
| `Checked:1` | `TodoItemStatus.COMPLETED` |
| `Checked:0` | `TodoItemStatus.NEEDS_ACTION` |
| `Description` | Opis pozycji, jeśli encja deklaruje tę funkcję. |
| `Amount`, `Unit`, `Price` | Dodatkowe dane produktu; nie dopisujemy ich do nazwy. |

Standardowe odhaczanie powinno wysyłać tylko zmienione pola. Dzięki temu HA
zachowuje metadane, których podstawowy widok listy nie reprezentuje.
