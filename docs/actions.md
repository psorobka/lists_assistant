# Akcje Listonic w Home Assistant

Akcje są dostępne w Narzędziach deweloperskich → Akcje oraz w skryptach
i automatyzacjach. Formularze mają opisy PL/EN. Każde wywołanie wymaga
`config_entry_id`, czyli wybranego konta integracji Listonic. Selektor konta
w formularzu pomaga wybrać właściwy wpis przy wielu kontach.

`list_id` i `item_id` to numeryczne identyfikatory Listonic, zapisane jako tekst
lub liczby. Nie są nazwami ani identyfikatorami encji HA. Nazwy mogą się powtarzać;
integracja nie dopasowuje ich automatycznie. `list_id` jest też atrybutem encji
`todo` Listonic; oba ID i metadane można odczytać przez `lists_assistant.get_lists`.

## Dostępne akcje

| Akcja | Pola poza kontem | Odpowiedź |
| --- | --- | --- |
| `lists_assistant.get_lists` | brak | `lists`: dostępne aktywne listy, produkty i ich metadane |
| `lists_assistant.create_list` | `name` | opcjonalnie `list_id` nowej listy |
| `lists_assistant.rename_list` | `list_id`, `name` | brak |
| `lists_assistant.delete_list` | `list_id` | brak; dezaktywacja przez `Active:0` |
| `lists_assistant.add_item` | `list_id`, `name`, opcjonalne metadane | opcjonalnie `item_id` nowego produktu |
| `lists_assistant.update_item` | `list_id`, `item_id`, przynajmniej jedno zmieniane pole | brak |
| `lists_assistant.delete_item` | `list_id`, `item_id` | brak |

Metadane produktu: `amount`, `unit`, `description`, `price`. Edycja dodatkowo
obsługuje `name` i `checked` (`true`/`false`). Ilość przyjmuje kropkę lub przecinek
dziesiętny; jest przesyłana jako tekst bez obliczeń na liczbach zmiennoprzecinkowych.
Cena musi być skończoną liczbą nieujemną. Puste `amount`, `unit` lub `description`
czyści dane pole. `price: 0` ustawia cenę na zero. Pominięte pola są zachowywane.

Akcje działają również na dostępnych listach niewybranych jako encje `todo`.
Nową listę trzeba wybrać w opcjach integracji, aby otrzymała encję; tworzenie
nie przełącza powiązania Shopping List. Rename zachowuje ID i tożsamość encji.
Dezaktywacja powoduje niedostępność istniejącej encji i zatrzymuje powiązanie
Shopping List. Nie jest potwierdzonym trwałym usunięciem ani opróżnieniem listy.

## Odczyt i przykład skryptu

Odczyt w Narzędziach deweloperskich:

```yaml
action: lists_assistant.get_lists
data:
  config_entry_id: TWOJ_WPIS_KONFIGURACJI
```

Poniższa sekwencja skryptu tworzy listę i dodaje produkt. `response_variable`
jest używane wewnątrz skryptu/automatyzacji, a nie w formularzu pojedynczej akcji.

```yaml
sequence:
  - action: lists_assistant.create_list
    data:
      config_entry_id: TWOJ_WPIS_KONFIGURACJI
      name: Weekend
    response_variable: created_list
  - action: lists_assistant.add_item
    data:
      config_entry_id: TWOJ_WPIS_KONFIGURACJI
      list_id: "{{ created_list.list_id }}"
      name: Mleko
      amount: "2,5"
      unit: l
      price: 3.49
      description: Bez laktozy
    response_variable: created_product
  - action: lists_assistant.update_item
    data:
      config_entry_id: TWOJ_WPIS_KONFIGURACJI
      list_id: "{{ created_list.list_id }}"
      item_id: "{{ created_product.item_id }}"
      checked: true
```

## Błędy i potwierdzenie zapisu

Przed edycją/usunięciem integracja pobiera aktualny snapshot wskazanego konta
i sprawdza istnienie celu. Polling, bridge i akcje korzystają ze wspólnej blokady
zapisu. Nie zastępuje to operacji warunkowej na serwerze: zmiana z telefonu może
wystąpić między odczytem a zapisem.

Potwierdzony zapis pozostaje sukcesem, nawet jeżeli kolejny odczyt nie powiedzie
się. Niepewny zapis, np. utrata odpowiedzi POST, zgłasza błąd z instrukcją
sprawdzenia `get_lists` przed ponowieniem. Akcje nie kolejkowują ani nie ponawiają
zapisów automatycznie. Kolejka i akcje rozstrzygania z
[instrukcji Shopping List](shopping-list.md) dotyczą wyłącznie bridge.

Nie dodawaj automatycznego retry tworzenia listy/produktu po niepewnym wyniku.
Odpowiedź `get_lists` zawiera nazwy i metadane zakupów — uwzględnij to przy
udostępnianiu śladów automatyzacji. Diagnostyka integracji nadal pomija te dane.
