# Lokalne uruchamianie Home Assistant

Uruchomienie wykonuj w WSL z katalogu repozytorium. Skrypt używa Docker Desktop,
montuje bieżący kod integracji i zapisuje konfigurację w ignorowanym
`.test-state/local-ha-docker`.

```sh
bash scripts/run_local_ha.sh
```

Instancja jest dostępna wyłącznie lokalnie pod `http://localhost:8125`.
Kontener startuje z `--rm`,
więc po `Ctrl+C` skrypt zatrzymuje kontener, a Docker usuwa go automatycznie.
Można go też zatrzymać z drugiego terminala poleceniem
`docker stop --timeout 60 lists-assistant-local-ha`. Konfiguracja w `.test-state`
pozostaje. Zmiany w kodzie integracji wymagają restartu kontenera.

Przy pierwszym uruchomieniu Home Assistant wymaga jednorazowego utworzenia
właściciela w onboardingu. Potem skonfigurowane `trusted_networks` pomija ekran
logowania dla localhost i bramy lokalnego bridge Dockera, gdy HA widzi dokładnie
jednego użytkownika. Zwykłe logowanie HA pozostaje dostępnym mechanizmem zapasowym.
Ta konfiguracja dotyczy wyłącznie lokalnej instancji testowej.
