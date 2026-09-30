# API Listonic — dokumentacja integracji

Stan weryfikacji: 28.09.2026.

Dokumentacja opisuje API używane przez aplikację WWW Listonic i przez klienta
tej integracji. Nie jest oficjalną specyfikacją Listonic. Kontrakt może zmienić
się wraz z aplikacją. Baza adresów: `https://api.listonic.com`.

## Dokumenty

| Dokument | Zawartość |
| --- | --- |
| [Logowanie i sesja](authentication.md) | Formularz logowania, nagłówki, refresh token, odtworzenie sesji i obsługa 401. |
| [Listy i produkty](resources.md) | Endpointy, pola, przykłady JSON, odhaczanie i usuwanie. |
| [Błędy i synchronizacja](errors-and-sync.md) | Retry, niejednoznaczny zapis, pełne snapshoty i ograniczenia API. |
| [Raport testu na żywo](live-validation.md) | Dokładnie sprawdzony zakres, wynik, środowisko i scenariusze jeszcze niepotwierdzone. |
| [Analiza aplikacji WWW](../listonic-api.md) | Źródła wcześniejszych ustaleń i endpointy znalezione w JavaScript. |

## Poziomy potwierdzenia

- **API na żywo**: wywołanie wykonane na autoryzowanym koncie, a zmiana
  sprawdzona kolejnym odczytem. Dotyczy wyłącznie zakresu raportu testu.
- **Kod WWW**: wywołanie znalezione w publicznym JavaScript aplikacji Listonic.
  Nie dowodzi, że endpoint działa w każdej sytuacji.
- **Test automatyczny**: zachowanie naszego klienta sprawdzone na kontrolowanej
  odpowiedzi HTTP. Taki test nie potwierdza zachowania serwera Listonic.

Przykłady odpowiedzi pokazują tylko pola potrzebne integracji. Są ilustracyjne:
identyfikatory, nazwy, email i tokeny są zastępcze. Nie są surowymi odpowiedziami
z konta użytkownika. Nie zapisujemy haseł ani tokenów w dokumentacji.

## Krótki przebieg użycia

1. Wygeneruj `DeviceId` i zachowuj go razem z sesją.
2. Wywołaj logowanie formularzem email/hasło.
3. Zapamiętaj `access_token` i `refresh_token`; hasło nie jest potrzebne do
   kolejnych odnowień sprawdzonych w krótkim teście.
4. Odczytaj profil konta, a następnie listy z produktami.
5. Używaj ID listy i produktu w operacjach zapisu.
6. Po zmianie wykonaj pełny odczyt i sprawdź rezultat.
7. Przy 401 odśwież sesję raz. Przy niepewnym wyniku zapisu uzgodnij stan,
   zanim ponowisz operację.

Przy odtworzeniu sesji klient odczytuje zachowane `refresh_token` i `DeviceId`,
pobiera nowy access token i sprawdza tożsamość konta. Długotrwała ważność
refresh tokena nadal wymaga osobnego testu.
