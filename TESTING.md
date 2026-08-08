# GameForge Linux Flatpak - test skrócony

## Instalacja

```bash
cd dist
sha256sum -c SHA256SUMS
flatpak install --user ./GameForge-Linux-0.1.2-alpha-x86_64.flatpak
```

## Podstawowe sprawdzenie

Uruchom interfejs desktopowy:

```bash
flatpak run io.github.gameforge_linux.GameForge --desktop
```

Sprawdź wykrycie lokalnych bibliotek Steam na stronie Gry. Podłączony pad lub
przełącznik interfejsu pozwala wejść do Couch Mode. Sprawdź powrót przez F11.

W sekcji Optymalizacja wybierz lokalne archiwum OptiScalera `.7z`. Okno wyboru
powinno domyślnie pokazywać `*.7z` i `*.zip`. Przed instalacją sprawdź plan,
katalog docelowy, konflikty i listę plików. Nie testuj na ważnej instalacji gry.

## GameForge Runner na hoście

```bash
flatpak run --command=gameforge-install-runner io.github.gameforge_linux.GameForge
~/.local/share/gameforge-linux/bin/gameforge-run --appid 480 --plan-only -- /usr/bin/true
```

Pierwsze polecenie instaluje tylko mały wrapper użytkownika. Nie wymaga `sudo`.

## Odinstalowanie

```bash
flatpak uninstall --user io.github.gameforge_linux.GameForge
```

W zgłoszeniu podaj wersję systemu, wynik weryfikacji sumy, tryb Desktop/Couch
oraz krótki opis problemu. Nie dołączaj prywatnych ścieżek ani listy gier.
