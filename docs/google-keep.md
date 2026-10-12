# Google Keep i Listonic

Wybrane listy Google Keep są dostępne w HA jako encje `todo`. Opcjonalnie jedną
listę Keep można dodatkowo połączyć z wbudowaną Shopping List HA. Gdy w tym samym
wpisie skonfigurujesz też bridge Listonic, Shopping List HA będzie wspólną listą
pośrednią dla obu usług.

## Konfiguracja

1. Skonfiguruj w HA integrację **Shopping List**, jeśli chcesz utworzyć most.
2. Dodaj **Lists Assistant** i na pierwszym ekranie wybierz Listonic, Google Keep
   albo oba źródła. Wybór tylko Google Keep nie wymaga konta Listonic.
3. Przy wyborze Google Keep podaj e-mail konta, Android ID telefonu/emulatora
   i cookie `oauth_token`.
   Formularz wymieni cookie na master token; surowe cookie nie zostanie zapisane.
   Następnie wybierz listy Keep do utworzenia encji `todo` oraz osobno, opcjonalnie,
   listę do synchronizacji z Shopping List HA.
4. Jeśli wybrano most, sprawdź liczbę pozycji po obu stronach i potwierdź scalenie.
5. Obserwuj sensor **Synchronizacja Google Keep**.

Keep nie udostępnia tu wspieranego OAuth dla kont osobistych. Integracja korzysta
z nieoficjalnego klienta [gkeepapi](https://github.com/kiwiz/gkeepapi), który
komunikuje się z prywatnym protokołem Keep. Formularz używa nieoficjalnego
[przepływu gpsoauth](https://github.com/simon-weber/gpsoauth#alternative-flow),
by wymienić cookie `oauth_token` na master token. Cookie jest używane tylko przy
konfiguracji; HA zapisuje master token potrzebny do kolejnych synchronizacji.
Master token nie ma zakresu ograniczonego do jednej listy i może dawać dostęp do
wszystkich notatek konta. Zalecamy osobne konto Google, któremu udostępnisz tylko
listę zakupów. Nie wklejaj tokenów do zgłoszeń błędów ani logów.

## Synchronizacja

- Dodawanie, zmiana nazwy, odhaczanie, odznaczanie i usuwanie są synchronizowane
  między Keep i Shopping List HA. Zmiany HA trafiają dalej do Listonic.
- Google Keep jest odpytywany co 60 sekund; zmiany głosowe mogą dotrzeć z takim
  opóźnieniem. Zmiana na lokalnej Shopping List uruchamia synchronizację od razu.
- Pierwsze scalenie zachowuje każdą pozycję. Identyczne nazwy nie są traktowane
  jako dowód, że chodzi o ten sam produkt. Przy trzech listach istniejące produkty
  mogą więc pozostać osobnymi pozycjami, dopóki nie zostaną jawnie zmapowane.
- Mapowania Keep↔HA są przechowywane osobno dla konta i wybranej listy Keep.
  Przełączenie listy zachowuje stare mapowania, a nowa lista przechodzi własne
  potwierdzenie scalenia.
- Jeżeli wynik zapisu jest niepewny, integracja zatrzymuje automatyczne ponowienie.
  Sprawdź obie strony przez `lists_assistant.get_sync_issues`, a potem użyj
  `lists_assistant.resolve_keep_write` lub `lists_assistant.resolve_keep_conflict`.

Usunięcie wpisu z dowolnej strony propaguje usunięcie na pozostałe strony.
Jeśli wolisz zachować kupione produkty jako odhaczone, odhaczaj je zamiast usuwać.

## Ograniczenia

Prywatny protokół Keep i uwierzytelnianie tokenem mogą przestać działać po zmianie
po stronie Google. Biblioteka nie jest wspierana przez Google. Pełna dostępność
tej synchronizacji nie ma gwarancji równoważnej oficjalnemu API.

## Test na prawdziwym koncie

Do próby live użyj osobnego konta Google bez prywatnych notatek i osobnego konta
Listonic do testów. Master token daje dostęp do wszystkich notatek konta Keep;
wpisuj go wyłącznie w ukrytym promptcie skryptu, nigdy w czacie, terminalu jako
argument, pliku ani logu.

Na telefonie zaloguj Asystenta do testowego konta Google i ustaw Google Keep jako
dostawcę **Notes & Lists** w ustawieniach Asystenta. [Instrukcja Google](https://support.google.com/assistant/answer/14171370?hl=pl)
opisuje tę ścieżkę i wymagane uprawnienie Keep.

W WSL, z katalogu repozytorium, uruchom:

```sh
uv run --python 3.13 --with gkeepapi==0.17.1 \
  --with-requirements requirements_test.txt -- \
  python -m scripts.live_keep_bridge_probe
```

Skrypt poprosi lokalnie o dane Listonic, e-mail Google, Android ID oraz cookie
`oauth_token`. Cookie wpisz w ukrytym promptcie. Skrypt wymieni je na master token
w pamięci i nie zapisze ani nie wyświetli tokenów. Google opisuje ten nieoficjalny
proces w [dokumentacji gpsoauth](https://github.com/simon-weber/gpsoauth#alternative-flow).
Cookie uzyskaj wyłącznie dla testowego konta na stronie EmbeddedSetup, a Android ID
odczytaj z telefonu testowego. Oba identyfikatory podaj tylko w lokalnym terminalu.

Skrypt utworzy tymczasowe listy Keep i Listonic, poda komendę do wypowiedzenia
Asystentowi i poczeka do trzech minut na wpis w Keep. Potem sprawdzi przepływ Keep
→ Shopping List HA → Listonic, zmianę i odhaczenie z HA oraz usunięcie testowej
pozycji. Po próbie usuwa listę Keep i dezaktywuje listę Listonic. Test zmienia
wyłącznie te dwie listy; uruchamiaj go tylko na dedykowanych kontach.
