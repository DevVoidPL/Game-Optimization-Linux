# GameForge Linux

GameForge Linux to prototyp aplikacji desktopowej do zarządzania biblioteką gier
na Linuksie. W zwykłym trybie bezpiecznie wykrywa lokalne biblioteki Steam,
zainstalowane gry, lokalne okładki, system plików każdej instalacji oraz dane
systemu. Uruchamianie gry przez natywny Steam lub Steam Flatpak, analiza
kompresji Btrfs, wykrywanie lokalnych aktualizacji Steam oraz kontrolowana
rekompresja Btrfs są rzeczywiste. Pierwsza kompresja zawsze wymaga przejrzenia
planu i jawnego potwierdzenia. Automatyczna kompresja jest domyślnie wyłączona
i działa tylko podczas uruchomienia GameForge.

Silnik nie usuwa plików, nie zmienia właściciela ani uprawnień, nie modyfikuje
manifestów Steam i nie używa `sudo`, `pkexec`, Polkit ani `shell=True`.
Optymalizacja systemowa, tekstury i kopie zapasowe pozostają niedostępne w
zwykłym trybie i są symulowane wyłącznie w Demo.

Nazwa wyświetlana, wersja oraz identyfikator aplikacji są zdefiniowane centralnie
w `src/gameforge/config.py`, dzięki czemu roboczą nazwę można później zmienić w
jednym miejscu.

## Wymagania

- Linux (projektowany najpierw dla Arch Linux, architektura niezależna od
  dystrybucji),
- Python 3.12 lub nowszy,
- PySide6 / Qt 6.7 lub nowszy,
- `btrfs-progs` z obsługą właściwości kompresji, rekompresji i poziomów ZSTD
  do wykonywania kompresji,
- opcjonalnie `compsize` do dokładniejszego pomiaru istniejącej kompresji,
- opcjonalnie moduł Python `zstandard` albo program `zstd` do estymacji próbek,
- pytest 8 lub nowszy do uruchamiania testów.

Nie jest wymagane `sudo` podczas uruchamiania. Aplikacja celowo odmawia pracy
jako użytkownik `root`.

## Instalacja na Arch Linux

Zależności Qt można zainstalować z repozytoriów systemowych:

```bash
sudo pacman -S --needed python pyside6 python-pip python-pytest python-pytest-cov
```

Następnie, w katalogu projektu, utwórz środowisko wirtualne. Opcja
`--system-site-packages` pozwala wykorzystać systemowe pakiety `pyside6`,
`python-pytest` i `python-pytest-cov`:

```bash
python -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --no-deps -e .
```

Jeśli PySide6 nie został zainstalowany przez menedżer pakietów, samo
`python -m pip install -e '.[dev]'` zainstaluje zależności zadeklarowane w
`pyproject.toml`.

## Uruchamianie

Po instalacji edytowalnej:

```bash
gameforge-linux
```

Bez instalowania pakietu:

```bash
PYTHONPATH=src python -m gameforge.main
```

### Wpis w menu aplikacji

Qt używa identyfikatora `io.github.gameforge_linux.GameForge`, zgodnego z
plikiem `data/io.github.gameforge_linux.GameForge.desktop`. Portal pulpitu może
zgłosić `App info not found`, dopóki ten plik nie zostanie zainstalowany w
lokalnym katalogu użytkownika. Projekt nie robi tego automatycznie. Po
zainstalowaniu pakietu Pythona uruchom świadomie:

```bash
./scripts/install-desktop-entry.sh
```

Skrypt najpierw sprawdza, czy `gameforge-linux` jest dostępny w `PATH`, i nie
instaluje niedziałającego wpisu. Przy pracy bez instalacji globalnej można użyć
launchera z wirtualnego środowiska projektu:

```bash
./scripts/install-desktop-entry.sh --dev
```

Tryb `--dev` preferuje `.venv/bin/gameforge-linux`, a następnie sprawdza `PATH`.
Wpis trafia do `${XDG_DATA_HOME:-~/.local/share}/applications`, a ikona do
odpowiedniego katalogu `icons/hicolor`; żaden plik nie jest instalowany
automatycznie podczas startu aplikacji. Nazwa pliku desktop i identyfikator Qt
to dokładnie `io.github.gameforge_linux.GameForge`.

Skrypt nie korzysta z `sudo`: kopiuje wpis do
`~/.local/share/applications/`, metadane AppStream do `~/.local/share/metainfo/`
i ikony 16-256 px do odpowiednich katalogów `~/.local/share/icons/hicolor/`
(lub do odpowiedniego `$XDG_DATA_HOME`). Następnie wyloguj się i zaloguj ponownie
albo odśwież menu aplikacji, jeżeli środowisko nie zauważy wpisu od razu.

Zwykłe uruchomienie używa rzeczywistych danych Steam. Aby wymusić dane fikcyjne,
na przykład podczas pracy nad GUI na komputerze bez Steam:

```bash
GAMEFORGE_DEMO=1 PYTHONPATH=src python -m gameforge.main
```

Bibliotekę można ponownie przeskanować przyciskiem **Refresh** na stronie Games.
Dodatkowe, niestandardowe katalogi instalacji Steam można dodać w Settings.

### Kontrolery i SDL3

Obsługa kontrolerów korzysta bezpośrednio z systemowej biblioteki SDL3. Nie
instaluje własnej kopii SDL ani pakietu z PyPI. Jeśli środowisko nie udostępnia
`libSDL3`, aplikacja uruchamia się normalnie, pokazuje stan „SDL3 missing” i
wyłącza ustawienia kontrolera. Po zainstalowaniu pakietu runtime/development
SDL3 z repozytorium używanej dystrybucji wykrywanie działa przy kolejnym
uruchomieniu.

Na Arch Linux SDL3 znajduje się w oficjalnym repozytorium `extra`:

```bash
sudo pacman -S sdl3
```

To świadoma operacja administratora wykonywana przez użytkownika; GameForge
nie uruchamia `sudo` ani menedżera pakietów.

Opcjonalną bazę dodatkowych mapowań można umieścić jako
`$XDG_CONFIG_HOME/gameforge-linux/gamecontrollerdb.txt` (domyślnie
`~/.config/gameforge-linux/gamecontrollerdb.txt`). Plik jest tylko odczytywany
przy inicjalizacji SDL3. Ustawienia kontrolera i trybu Couch są zapisywane w
tym samym lokalnym pliku XDG co pozostałe preferencje.

Tryb Couch można uruchomić ręcznie klawiszem `F11`, gdy ustawienie
`Controller mode` ma wartość `Automatic`. Wartość `Couch only` uruchamia ten
interfejs od razu, a `Desktop only` blokuje automatyczne przełączanie po
wejściu z kontrolera. Ponowne naciśnięcie `F11` w trybie automatycznym wraca
do Desktop Mode.
Narzędzia takie jak Proton i Steam Linux Runtime są domyślnie ukryte; przełącznik
**Show Steam tools and runtimes** w Settings włącza ich wyświetlanie.
Ustawienia i cache metadanych są zapisywane w katalogach użytkownika zgodnych z
XDG. Cache przyspiesza start, ale nigdy nie zastępuje odświeżenia w tle. Język
interfejsu można zmienić bez restartu w **Settings → General → Language**;
dostępne są English, Polski i Español, a wybór jest przywracany przy następnym
uruchomieniu.

## Testy

```bash
pytest
```

Opcjonalny raport pokrycia:

```bash
pytest --cov=gameforge --cov-report=term-missing
```

Testy korzystają wyłącznie z tymczasowych struktur Steam i nie odczytują
prawdziwej biblioteki użytkownika. Dodatkowe kontrole statyczne:

```bash
python -m compileall -q src tests
find src/gameforge/qml -name '*.qml' -exec qmllint {} +
```

Rzeczywisty test Btrfs jest celowo opt-in. Wskazana ścieżka musi być zwykłym
katalogiem użytkownika na Btrfs; test tworzy pod nią własny katalog tymczasowy,
syntetyczne `library/steamapps/common`, pliki testowe i reflinki, a następnie
wszystko usuwa. Nie należy wskazywać biblioteki Steam:

```bash
findmnt --target "$PWD" --output TARGET,SOURCE,FSTYPE,OPTIONS
GAMEFORGE_BTRFS_TEST_ROOT="$PWD" \
  pytest -q tests/test_btrfs_compression_integration.py \
           tests/test_btrfs_shared_extents.py
```

Bez zmiennej środowiskowej testy wymagające prawdziwego zapisu są oznaczane
jako pominięte z dokładnym powodem.

## Architektura

Projekt rozdziela prezentację od logiki aplikacji:

```text
QML pages/components
        |
        v
AppController (sygnały, właściwości i sloty dla QML)
        |
        v
Services (katalog, zadania, ustawienia, analiza Btrfs)
        |
        v
Provider interfaces (gry, system plików, kompresja, optymalizacja,
                     tekstury, kopie)
        |
        v
SteamGameProvider / LinuxFilesystemProvider / LinuxSystemProvider lub providery Demo
```

QML odpowiada tylko za prezentację i interakcje. `AppController` mapuje dane na
typy przyjazne QML. Skanowanie bibliotek, dokładnych rozmiarów, fingerprinty,
analiza i kompresja działają poza głównym wątkiem, a wynik wraca do interfejsu
sygnałami Qt. Analiza oraz kompresja korzystają z jednej kolejki Tasks; QML nie
uruchamia poleceń systemowych. Osobny
parser KeyValues obsługuje pliki VDF/ACF, `LibraryCache` zapisuje atomowo wyłącznie
wersjonowane metadane, a błędny cache lub pojedynczy manifest nie zatrzymuje
skanowania. `TranslationManager` ładuje standardowe katalogi Qt TS/QM przez
`QTranslator` i przeładowuje aktywny interfejs.

Najważniejsze katalogi:

```text
src/gameforge/
├── main.py                # punkt wejścia
├── app.py                 # składanie aplikacji i silnika QML
├── config.py              # nazwa, wersja i ścieżki XDG
├── logging_config.py      # standardowe logowanie
├── controllers/           # bezpieczny most Python <-> QML
├── models/                # dataclasses i enumy domenowe
├── providers/             # Steam, KeyValues, Linux filesystem i Demo
├── services/              # rozmiary, cache, zadania, ustawienia i analiza Btrfs
├── translations/          # katalogi Qt dla English, Polski i Español
└── qml/
    ├── Main.qml
    ├── components/        # współdzielone kontrolki i karty
    ├── dialogs/           # potwierdzenia
    ├── pages/             # Games, Tasks, System, Settings i szczegóły
    │   └── details/       # pięć kart funkcjonalnych wybranej gry
    └── Theme.qml          # centralne tokeny jasne/ciemne/systemowe
tests/                     # testy parsera, providerów, cache, usług i kontrolera
```

## Wykrywanie Steam

Przy starcie sprawdzane są typowe instalacje:

- `~/.local/share/Steam`,
- `~/.steam/steam`,
- `~/.steam/root`,
- `~/.var/app/com.valvesoftware.Steam/data/Steam` (Steam Flatpak),
- dodatkowe ścieżki zapisane przez użytkownika w Settings.

Z `steamapps/libraryfolders.vdf` wykrywane są również biblioteki na innych
dyskach. Następnie aplikacja czyta `steamapps/appmanifest_*.acf`, usuwa duplikaty
i pokazuje AppID, nazwę, katalog instalacji, bibliotekę, `SizeOnDisk`,
`StateFlags`, język oraz czas aktualizacji, jeżeli manifest zawiera te dane.
Brak katalogu `steamapps/common/<installdir>` daje widoczny stan **Missing files**,
a uszkodzony manifest jest pomijany bez przerywania pozostałego skanowania.
Jeżeli znana z ostatniego poprawnego cache biblioteka znajduje się na chwilowo
odłączonym dysku, jej gry pozostają widoczne jako **Drive disconnected** /
**Library unavailable**. Nie można ich uruchomić ani analizować; przycisk
**Refresh** przywraca zwykły stan po ponownym zamontowaniu dysku. Brakująca
biblioteka nie powoduje usunięcia tych wpisów z cache, ale aplikacja nie tworzy
rekordów dla gier, których wcześniej nie wykryła.

Wartość `SizeOnDisk` pozwala szybko zapełnić widok. Dokładny rozmiar logiczny i
fizycznie zajęte miejsce są później liczone w tle; podczas pracy GUI pokazuje
**Calculating...**. Skaner nie podąża za dowiązaniami symbolicznymi i toleruje
błędy uprawnień oraz znikające pliki.

Grafiki są odczytywane wyłącznie z lokalnego `appcache/librarycache`; aplikacja
preferuje pionowe kapsuły biblioteki, używa nagłówka jako fallbacku i nigdy nie
pobiera okładek z sieci. Osobny klasyfikator oznacza oczywiste runtime, Proton,
redystrybucje, SDK i serwery dedykowane bez usuwania ich z modelu danych.

`LinuxSystemProvider` czyta dystrybucję, kernel, sesję, CPU i RAM z `/etc`,
`/proc` oraz środowiska sesji. GPU wykrywa kolejno przez `vulkaninfo --summary`,
`glxinfo -B` i `lspci -nnk`, a dostępność narzędzi przez `shutil.which`.

`LinuxFilesystemProvider` odczytuje punkt montowania, typ systemu plików,
urządzenie źródłowe, opcje montowania, rozmiar, zajęte i wolne miejsce oraz
dostęp do zapisu. Preferuje `findmnt --json` uruchamiany z listą argumentów,
a gdy narzędzie nie jest dostępne, korzysta z `/proc/self/mountinfo` i
`statvfs`. Widok System pokazuje `/`, osobne `/home`, dyski bibliotek gier oraz
użytkowe partycje fizyczne; techniczne pseudo-systemy są domyślnie ukryte.

## Analiza i kompresja Btrfs

W szczegółach gry zakładka **Storage** udostępnia przycisk **Analyze
compression**. Analiza działa jako anulowalne zadanie widoczne na stronie
Tasks. Sprawdza system plików, punkt montowania, wolne miejsce, dostęp do zapisu,
rozmiar logiczny i fizyczny, liczbę plików, katalogów i dowiązań oraz -
best-effort - uruchomione procesy gry. Nie podąża przez dowiązania symboliczne
i otwiera dane gry wyłącznie do odczytu.

Jeżeli zainstalowano `compsize`, aplikacja mierzy istniejącą kompresję i jej
typy. Bez niego nadal raportuje bezpiecznie policzone rozmiary, lecz wyświetla
**compsize not installed** zamiast wymyślonych danych. Opcjonalne próbkowanie
ZSTD czyta ograniczone fragmenty reprezentatywnych plików i kompresuje ich
kopie wyłącznie w pamięci lub potoku. Po pełnej analizie gry na Btrfs odblokowują
się profile planowania Fast (`zstd:1`), Balanced (`zstd:3`), Maximum (`zstd:9`)
i Auto (deterministyczny wybór spośród 1, 3, 6 i 9 według mierzonego zysku i
czasu).

Wyniki są zapisywane atomowo w wersjonowanym cache XDG i unieważniane po
zmianie ścieżki, metadanych gry, rozmiaru, sygnatury katalogu lub wersji
analizatora. Backend osobno modeluje trwały algorytm `zstd` oraz poziom
jednorazowej rekompresji:

- `btrfs property set -t inode … compression zstd` ustawia algorytm dla
  przyszłych zapisów w katalogu;
- `btrfs filesystem defragment -f -czstd --level N …` rekompresuje po jednym
  zweryfikowanym zwykłym pliku z planu;
- nie jest używane rekurencyjne `-r`, a każdy plik jest ponownie sprawdzany
  przez deskryptor `O_NOFOLLOW` bezpośrednio przed operacją;
- po operacji wykonywany jest nowy, wyłącznie odczytowy pomiar. Kod wyjścia 0
  nie wystarcza do oznaczenia wyniku jako udany.

Przed pierwszym zapisem Storage pokazuje nazwę, pełną ścieżkę, rozmiary,
profil, zakres przewidywanej oszczędności, liczbę plików oraz informację, czy
plan jest pełny, czy obejmuje tylko zmiany. Użytkownik musi zaakceptować ten
plan. Kompresja jest blokowana dla niedostępnej biblioteki, nie-Btrfs,
uruchomionej gry, aktywnej aktualizacji Steam, niepełnej analizy, braku
uprawnień lub narzędzi, zbyt małej ilości wolnego miejsca i równoległego zadania
zapisu.

### Reflinki i współdzielone extenty

Analizator uruchamia wyłącznie odczytowe:

```text
btrfs filesystem du --raw --summarize <katalog-gry>
```

Z raportu zapisuje `Total`, `Exclusive` i `Set shared`. Nie korzysta z
`btrfs inspect-internal dump-tree`. Jeżeli wykryto współdzielone extenty albo
wyniku nie można wiarygodnie ustalić, rekompresja pozostaje zablokowana
(fail-closed). Interfejs pokazuje konserwatywny górny limit możliwego wzrostu
alokacji. Aplikacja nie oferuje obecnie ryzykownego „wymuś mimo reflinków”,
ponieważ defragmentacja może zerwać współdzielenie ze snapshotem, reflinkiem
lub wynikiem deduplikacji.

Historia w `$XDG_STATE_HOME/gameforge-linux/compression-history-v2.json`
przechowuje stan przed i po, profil, Build ID, liczbę plików, rzeczywistą
oszczędność, błędy oraz wynik weryfikacji. Marker rozpoczętej operacji jest
zapisywany atomowo przed pierwszym zapisem. Po niekontrolowanym przerwaniu
aplikacja nie wznawia operacji w ciemno, tylko pokazuje
**Compression state requires verification**.

## Lokalne aktualizacje Steam i automatyzacja

Strona **Updates** w Desktop Mode i Couch Mode porównuje wyłącznie lokalne
manifesty ACF oraz bezpieczny fingerprint metadanych plików: ścieżkę względną,
rozmiar, mtime i ctime. Nie pobiera danych z Internetu i nie liczy pełnych
hashy wielkich plików podczas każdego uruchomienia. Zmiana Build ID, manifestu
lub stabilnej zawartości instalacji trafia przez okres stabilizacji do stanów
**Update detected**, **Analysis required** albo **Compression pending**.

Po zweryfikowanej kompresji zapisany fingerprint staje się punktem odniesienia.
Następny plan może objąć wyłącznie nowe i zmienione pliki; usunięte pliki są
raportowane, ale nie trafiają do rekompresji. Gdy punkt odniesienia jest
niepełny lub niewiarygodny, GameForge wymaga nowej pełnej analizy.

W Settings opcja **Automatic compression** ma tryby Off, po nowej instalacji,
po aktualizacji albo w obu przypadkach. Domyślna wartość to **Off**. Można
wybrać profil, opóźnienie stabilizacyjne, minimalne wolne miejsce,
powiadomienia, pomijane AppID i biblioteki. Automatyzacja nie jest demonem:
działa tylko wtedy, gdy interfejs jest uruchomiony, gra jest zamknięta, Steam
zakończył zapis, biblioteka jest dostępna, a wszystkie zwykłe zabezpieczenia
planu przechodzą.

## Tryb demonstracyjny i funkcje symulowane

`GAMEFORGE_DEMO=1` zachowuje dotychczasową bibliotekę fikcyjnych gier. W tym
trybie symulowane są:

- alternatywna demonstracyjna analiza gry i raport końcowy,
- alternatywny demonstracyjny przebieg kompresji i jej oszczędności (bez
  uruchamiania prawdziwego providera Btrfs),
- kolejka, prędkość, postęp, pauza, wznowienie i anulowanie zadań,
- demonstracyjne informacje o dystrybucji, sprzęcie i narzędziach systemowych,
- profile optymalizacji i generowany wyłącznie jako tekst podgląd komendy,
- ulepszanie tekstur i porównanie przed/po,
- tworzenie, przywracanie oraz usuwanie kopii zapasowych (tylko lista w pamięci),
- ręczne dodawanie i uruchamianie gry (wyłącznie komunikat w interfejsie),
- status aktualizacji.

Do plików lokalnych zapisywane są tylko ustawienia aplikacji i cache metadanych
biblioteki. Cache nie zawiera treści manifestów ani plików gier.

## Funkcje jeszcze niezaimplementowane

- wykrywanie bibliotek Heroic i innych launcherów niż Steam,
- bezpieczne wymuszanie rekompresji plików ze współdzielonymi extentami
  (obecnie zawsze blokowane),
- osobny demon/usługa automatyzacji działająca po zamknięciu GameForge,
- Deep Optimize, kwarantanna i bezpieczne usuwanie zbędnych danych,
- przetwarzanie lub podmiana tekstur,
- stosowanie GameMode, Gamescope, MangoHud, profili CPU/GPU i OptiScaler,
- wykonywanie i przywracanie prawdziwych kopii zapasowych,
- zgodność OptiScalera i silnika osobno dla każdej gry,
- pobieranie aktualizacji samego GameForge, instalator i pakiety dla
  dystrybucji.

Do czasu wdrożenia osobnej, audytowalnej warstwy uprawnień projekt nie uruchamia
`sudo`, nie prosi o hasło administratora, nie używa `shell=True`, nie pobiera
danych z internetu i nie uruchamia gry bez wyraźnej akcji użytkownika. Logi
opisują przebieg skanowania, lecz nie zapisują pełnej zawartości manifestów.
