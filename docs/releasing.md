# Wydanie na GitHub

Repozytorium: `https://github.com/psorobka/lists_assistant`.
Nazwa w UI: **Lists Assistant**; domena: `lists_assistant`.

## Kontrole przed wydaniem

1. Utrzymuj tę samą wersję w `custom_components/lists_assistant/manifest.json`,
   `pyproject.toml` i `uv.lock`. Uzupełnij `CHANGELOG.md`.
2. Uruchom kontrole z [mapy pracy](agent-workflow.md), w tym pełny backend
   z pokryciem >=90% i E2E po zmianach domeny/UI.
3. W repozytorium GitHub włącz Issues i ustaw opis, np.
   `Listonic shopping lists, Home Assistant Shopping List sync and Assist support`.
   Dodaj tematy `home-assistant`, `hacs`, `custom-integration`, `listonic`,
   `shopping-list`. Repozytorium musi pozostać publiczne dla HACS.
4. Wyślij przygotowany commit do GitHub i poczekaj na zielone CI, w tym
   Hassfest i HACS. HACS sprawdza również metadane zdalnego repozytorium;
   lokalna kontrola plików ich nie zastępuje.
5. Odczytaj wersję z `custom_components/lists_assistant/manifest.json`.
   Utwórz odpowiadający jej tag (`vX.Y.Z`) i szkic GitHub Release o tytule
   `Lists Assistant X.Y.Z`. Użyj treści pasującej sekcji z `CHANGELOG.md`.
   Sprawdź CI uruchomione dla taga, a po jego powodzeniu opublikuj szkic release.

HACS pobiera `custom_components/lists_assistant` z repozytorium wskazanego przez
tag wydania. Nie potrzeba osobnego ZIP-a ani `zip_release` w `hacs.json`.
Sam tag bez GitHub Release nie udostępnia numerowanego wydania w HACS.

Na GitHub istnieje już commit z licencją MIT. Przy pierwszym wysłaniu lokalnych
plików zachowaj tę historię; nie używaj force push do jej zastąpienia.

## Zgodność i grafika

`hacs.json` deklaruje minimum HA 2026.2.3, używane przez testy backendu i UI.
Lokalne ikony w `custom_components/lists_assistant/brand/` są przeznaczone dla
wersji HA obsługujących katalog `brand`; starsze HA mogą wyświetlać ikonę domyślną.
Zgłoszenie do domyślnego katalogu HACS to odrębny etap od instalacji jako
niestandardowe repozytorium.

Źródła wymagań: [HACS](https://www.hacs.xyz/docs/publish/integration/),
[metadane repozytorium](https://www.hacs.xyz/docs/publish/start/),
[walidacja HACS](https://www.hacs.xyz/docs/publish/action/),
[manifest HA](https://developers.home-assistant.io/docs/creating_integration_manifest/).
