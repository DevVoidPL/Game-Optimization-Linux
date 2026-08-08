# Walidacja backendu kompresji Game Optimization Linux

Stan dokumentu: audyt techniczny i rzeczywista walidacja tylko do odczytu,
2026-07-27  
Zakres: istniejący backend Btrfs, pomiary, bezpieczeństwo, benchmarki
lokalnych instalacji i kierunki dalszych testów.  
Poza zakresem: wdrożenie nowego backendu produkcyjnego oraz rekompresja
oryginalnych plików gier.

## 1. Wnioski wykonawcze

1. Obecna ścieżka wykonawcza nie ogranicza się do ustawienia właściwości
   katalogu. Dla każdego zaplanowanego pliku wywołuje
   `btrfs filesystem defragment -f -czstd --level N`, a więc próbuje wykonać
   jednorazową rekompresję istniejących danych.
2. Właściwość trwała i poziom jednorazowej rekompresji są w aktualnym modelu
   rozdzielone poprawnie:
   `compression=zstd` jest ustawiane na katalogu, natomiast poziom `1`, `3`,
   `6` albo `9` jest argumentem defragmentacji. Do `btrfs property set` nie
   trafia błędna wartość `zstd:3`.
3. Historyczny wynik Metro 2033 Redux „0 B” nie jest dowodem braku kompresji.
   Historia ma puste pola `compsize_*`, ponieważ nieuprzywilejowane `compsize`
   kończyło się w tym środowisku komunikatem `SEARCH_V2: Operation not
   permitted`. Aplikacja odejmowała wtedy dwie jednakowe wartości oparte na
   `st_blocks`, które nie są wiarygodnym miernikiem oszczędności kompresji
   Btrfs. Późniejszy wynik `sudo compsize` użytkownika, około 0,8 GiB
   oszczędności, jest zgodny z hipotezą „zapis wykonano, wynik zmierzono źle”.
4. Sam kod wyjścia `0` z defragmentacji nie dowodzi odzyskania miejsca. Może
   oznaczać poprawne wykonanie bez istotnej zmiany alokacji. Sukces biznesowy
   wymaga osobnych pomiarów: `compsize`, `btrfs filesystem du` oraz wolnego
   miejsca całego systemu plików.
5. Snapper może zachować stare extenty w snapshotach. Wtedy bieżący katalog
   gry może mieć lepszy wynik `compsize`, a wolne miejsce systemu plików nie
   musi wzrosnąć; w skrajnym przypadku może przejściowo spaść, bo nowa
   reprezentacja istnieje obok starej. Nie wolno na tej podstawie ani usuwać
   snapshotów, ani twierdzić, że kompresja nic nie zrobiła.
6. `btrfs filesystem du` jest dobrym publicznym mechanizmem oceny
   współdzielenia extentów, ale nie jest zamiennikiem `compsize`. Kolumny
   `Total`, `Exclusive` i `Set shared` opisują współdzielenie/alokację
   extentów, a nie rozkład algorytmów i stopień kompresji.
7. Do wiarygodnego działania bez uruchamiania całego GUI jako root potrzebny
   jest minimalny, systemowy helper autoryzowany przez Polkit. Helper powinien
   samodzielnie weryfikować instalację Steam i przyjmować AppID/identyfikator
   planu, nie dowolną ścieżkę ani dowolne argumenty polecenia.
8. Benchmark v2 całego miksu Jedi Survivor objął 2 GiB w 16 392 oknach.
   Punktowa redukcja payloadu wyniosła około 0,85 GiB dla ZSTD-3,
   0,88 GiB dla ZSTD-9, 0,93 GiB dla ZSTD-15 i 1,3 GiB dla zewnętrznych
   ZSTD-19/XZ-9. Wynik nie wspiera możliwości zbliżenia natywnego Btrfs na
   tej instalacji do obserwacji 155 GB → 111 GB z LZX. Nie jest to jednak
   pomiar incremental reclaim ani test kodeka LZX.
9. Wszystkie dotychczas wygenerowane raporty gier w schema v1 są odrzuconymi
   pilotażami. Ujawniły problemy narzędzia, ale nie są wynikami benchmarku:
   próbkowanie wewnątrz grup nie było proporcjonalne do ich rozmiaru, a
   wyliczony przedział był pseudo-CI bez podstaw statystycznych. Raportów v1
   nie wolno agregować, porównywać profili ani używać do rekomendacji.
10. `estimated payload reduction` opisuje modelowaną redukcję badanego
    strumienia danych po zastosowaniu kodeka. Nie jest to `incremental
    reclaim`, czyli przyrost wolnego miejsca możliwy do przypisania operacji
    na już istniejącym Btrfs. Druga wartość zależy również od aktualnej
    kompresji, granic extentów, `sectorsize`, reflinków, snapshotów, metadanych
    i równoległych zapisów.
11. Kontrolowany test na kopiach potwierdził trzy ważne fakty jakościowe:
    powstanie encoded extents, niezmienność zawartości oraz zerwanie
    współdzielenia ze snapshotem. Bez uprzywilejowanego `compsize` przed i po
    oraz pełnej macierzy pomiarów każdego profilu test nie dowodzi jednak
    liczby zaoszczędzonych bajtów ani przewagi któregokolwiek profilu.
12. Raport v2 ma jawne limity czasu, pamięci i tymczasowego miejsca, odczyt
    bez aktualizacji atime (`O_NOATIME`), zapisany `sectorsize`, hash źródła
    narzędzia oraz fingerprint inwentarza i manifestu przed/po. Nie zapisuje
    jeszcze kanonicznego hasha całego JSON ani pełnego manifestu offsetów;
    ogranicza to odtwarzalność, lecz nie zmienia wyniku porównania kodeków.
    Hardlinki i sparse data są wykrywane fail-closed, a shared extents są
    mierzone osobno w analizie Btrfs, nie wywnioskowane z próbki kodeka.

## 2. Co zostało sprawdzone

Audyt objął kod od kontrolki QML do procesu potomnego, model planu i wyniku,
analyzer, kolejkę zadań, zapis historii oraz rzeczywistą historię XDG. W
środowisku użytkownika odczytowo potwierdzono także:

- Btrfs dla biblioteki Steam i opcję montowania `compress=zstd:3`;
- obecność częstych snapshotów Snappera dla `home`;
- poprawną składnię aktualnego `btrfs-progs` dla `property`, `filesystem du`
  i `filesystem defragment`;
- wynik `compression=zstd` zwracany przez `btrfs property get` na badanych
  katalogach;
- możliwość uruchomienia `btrfs filesystem du --raw --summarize` bez
  niskopoziomowego `dump-tree`;
- odmowę `compsize` bez podniesionych uprawnień:
  `SEARCH_V2: Operation not permitted`;
- historyczne wpisy Metro zapisane w
  `~/.local/state/game-optimization-linux/compression-history-v2.json`.

W kontrolowanym teście rekompresowano wyłącznie kopie w wydzielonym zakresie
testowym. Potwierdzono obecność encoded extents po operacji, zgodność
integralności treści oraz przejście danych współdzielonych ze snapshotem do
nowych extentów bieżącej kopii. Test potwierdza działanie mechanizmu zapisu i
realność ryzyka opisanego w rozdziale 7; nie daje jeszcze wiarygodnego wyniku
oszczędności ani porównania profili, ponieważ brakowało uprzywilejowanego
`compsize` i kompletnej, identycznej sekwencji pomiarowej dla każdego profilu.

Nie rekompresowano oryginalnych plików gier, nie usunięto snapshotów
użytkownika i nie zmieniono konfiguracji montowania. Raporty schema v1
zachowują wartość wyłącznie diagnostyczną jako odrzucone pilotaże. Wyniki v2
w rozdziale 11 pochodzą z rzeczywistych, tylko odczytowych przebiegów; brak
instalacji Metro oraz brak uprzywilejowanego pomiaru `compsize` pozostają
jawnie oznaczone jako brak danych, nigdy jako zero.

## 3. Pełna ścieżka QML → backend → argv

### 3.1. Ręczne uruchomienie

```text
StorageTab.qml
  prepareCompressionPlan()
    └─ controller.prepareCompression(gameId, selectedMode, false)
       └─ AppController.prepareCompression()
          ├─ odszukanie Game i ukończonego BtrfsAnalysisReport
          ├─ CompressionService.prepare()
          │  └─ BtrfsCompressionProvider.create_plan()
          ├─ zapis planu w pamięci kontrolera/usługi
          └─ jawna mapa QVariant dla QML

StorageTab.qml
  ConfirmDialog.accepted
    └─ controller.startCompression(planId)
       └─ AppController._start_compression_plan(confirmed=True)
          └─ TaskService.enqueue_compression_plan()
             └─ worker: TaskService._run_compression()
                └─ CompressionService.execute()
                   ├─ CompressionHistoryStore.begin_operation()
                   ├─ BtrfsCompressionProvider.execute_plan()
                   │  ├─ ponowna analiza i preflight
                   │  ├─ walidacja deskryptorów i tożsamości plików
                   │  ├─ property set
                   │  ├─ dla każdego pliku: du → defragment
                   │  ├─ filesystem sync
                   │  └─ ponowna analiza/pomiar
                   └─ CompressionHistoryStore.finish_operation()
```

Pliki i punkty wejścia:

- `src/game_optimization_linux/qml/pages/details/StorageTab.qml`:
  `prepareCompressionPlan()`, dialog potwierdzenia i wywołanie
  `startCompression`;
- `src/game_optimization_linux/controllers/app_controller.py`:
  `prepareCompression()`, `startCompression()` i
  `_start_compression_plan()`;
- `src/game_optimization_linux/services/compression.py`:
  `prepare()` i `execute()`;
- `src/game_optimization_linux/services/analysis_tasks.py`:
  `enqueue_compression_plan()` i `_run_compression()`;
- `src/game_optimization_linux/providers/btrfs_compression.py`:
  `create_plan()`, `execute_plan()` i funkcje uruchamiające polecenia;
- `src/game_optimization_linux/services/btrfs_analysis.py`:
  pomiary przed i po;
- `src/game_optimization_linux/services/compression_history.py`:
  trwały marker rozpoczęcia i wynik.

Utworzenie planu jest tylko odczytowe. Pierwsza zamierzona modyfikacja następuje
dopiero po zatwierdzeniu dialogu i zapisaniu trwałego markera operacji.

### 3.2. Tryb automatyczny

Tryb automatyczny przechodzi przez tę samą kolejkę i ten sam provider, ale
`AppController._start_automatic_compression()` wymaga aktualnego raportu.
Jeżeli publiczny pomiar `btrfs filesystem du` nie zwróci pewnego stanu
`not_detected`, automatyczna rekompresja jest pomijana. Wykryte shared extents
mogą być rozpatrzone tylko w ścieżce ręcznej, po pokazaniu ryzyka i jawnym
potwierdzeniu. To właściwe rozróżnienie: współdzielenie nie jest bezwarunkowym
błędem, ale nie powinno być łamane bez wiedzy użytkownika.

### 3.3. Dokładne tablice argumentów

`BTRFS` oznacza ścieżkę zwróconą przez wyszukiwanie programu, zwykle
`/usr/bin/btrfs`. `ROOT_FD` i `FILE_FD` są otwartymi, zweryfikowanymi
deskryptorami przekazanymi procesowi potomnemu.

1. Trwały algorytm dla przyszłych zapisów, raz na operację:

   ```text
   [
     BTRFS, "property", "set", "-t", "inode",
     "/proc/self/fd/ROOT_FD", "compression", "zstd"
   ]
   ```

2. Ostatnia, tylko odczytowa kontrola współdzielenia przed każdym plikiem:

   ```text
   [
     BTRFS, "filesystem", "du", "--raw", "--summarize",
     "/proc/self/fd/FILE_FD"
   ]
   ```

3. Jednorazowa rekompresja każdego zaplanowanego pliku:

   ```text
   [
     BTRFS, "filesystem", "defragment", "-f", "-czstd",
     "--level", LEVEL, "/proc/self/fd/FILE_FD"
   ]
   ```

4. Opróżnienie oczekujących zapisów przed pomiarem końcowym:

   ```text
   [
     BTRFS, "filesystem", "sync", "/proc/self/fd/ROOT_FD"
   ]
   ```

Nie ma `shell=True`, rekursywnego `-r`, `sudo`, `pkexec`, `chattr`,
`btrfs inspect-internal` ani `dump-tree`. Rekurencję realizuje wcześniej
utworzona, jawna lista regularnych plików. Symlinki nie są śledzone.

Analyzer uruchamia ponadto:

```text
[BTRFS, "filesystem", "du", "--raw", "--summarize", GAME_ROOT]
[COMPSIZE, "--bytes", "--one-file-system", GAME_ROOT]
```

Druga komenda jest poprawna pomiarowo, lecz w sprawdzonym systemie potrzebuje
uprawnień do ioctl Btrfs, których zwykły proces GUI nie ma.

## 4. Profile i semantyka ustawień

| Profil | Jednorazowy poziom ZSTD | Trwała właściwość katalogu | Znaczenie |
|---|---:|---|---|
| Fast | 1 | `compression=zstd` | najniższy koszt CPU, priorytet szybkości |
| Balanced | 3 | `compression=zstd` | kompromis; odpowiada także obecnej opcji montowania `zstd:3` |
| Maximum | 9 | `compression=zstd` | większy koszt CPU; wynik musi wykazać benchmark |
| Auto | 1, 3, 6 albo 9 | `compression=zstd` | poziom wybrany przez analyzer na podstawie próbek; awaryjnie 3 |

Model rozdziela dwie różne rzeczy:

- `persistent_compression_algorithm = "zstd"` - algorytm dziedziczony przez
  przyszłe zapisy w katalogu;
- `one_time_recompression_level = N` - poziom używany tylko podczas bieżącej
  defragmentacji/rekompresji już istniejących plików.

To rozdzielenie jest wymagane przez Btrfs. Właściwość inode przyjmuje nazwę
algorytmu, nie poziom. Ustawienie właściwości samo w sobie nie rekompresuje
istniejących danych. Istniejące dane zmienia dopiero defragmentacja z `-c`.

Warto też zachować rozróżnienie w historii: nawet jeśli użytkownik widzi jeden
profil, raport powinien podawać osobno algorytm trwały i poziom jednorazowej
operacji.

## 5. Co oznaczają poszczególne pomiary

Żadna pojedyncza liczba nie odpowiada na wszystkie pytania. Wartości należy
przechowywać oddzielnie i porównywać w parach bezpośrednio przed i po
operacji.

| Źródło | Aktualna wartość/model | Co mierzy | Czego nie wolno z niej wnioskować | Zalecane użycie |
|---|---|---|---|---|
| `sum(st_size)` | `logical_bytes` | logiczną długość unikalnych regularnych plików; symlinki i powtórne hardlinki są pomijane | fizycznej alokacji, kompresji, danych zatrzymanych przez snapshot | kontrola, czy zakres danych nie zmienił się między pomiarami |
| `sum(st_blocks * 512)` | skanowe `physical_bytes` | bloki przypisane przez `stat` do inode według semantyki VFS | wiarygodnej oszczędności Btrfs przy kompresji, reflinkach i snapshotach; może nie odpowiadać realnie zwolnionym bajtom | awaryjna informacja opisowa, nigdy źródło komunikatu „zaoszczędzono” |
| `btrfs filesystem du --raw --summarize` | `Total`, `Exclusive`, `Set shared`, stan shared extents | publiczne, oparte na FIEMAP oszacowanie extentów i ich współdzielenia | typu algorytmu, stopnia kompresji ani realnej zmiany wolnego miejsca całego FS | blokada/ostrzeżenie przed zerwaniem sharingu i pomiar zmiany exclusive/shared |
| `compsize --bytes --one-file-system` | `compsize_disk_bytes`, `compsize_uncompressed_bytes`, `compsize_referenced_bytes`, rozkład kodeków | bieżący disk usage extentów oraz rozmiary uncompressed/referenced i udział `none`, ZSTD itd. | natychmiastowego wzrostu `df`; snapshoty mogą nadal referować stare extenty | podstawowy miernik wyniku kompresji bieżącego drzewa |
| `statvfs`/`disk_usage.free` | `filesystem_available_bytes` | bajty dostępne dla nieuprzywilejowanego użytkownika na całym systemie plików | oszczędności wyłącznie tej gry, jeśli równolegle działa Steam/Snapper lub trwa delayed allocation | dodatkowy, rzeczywisty efekt dla użytkownika po `sync`, w spokojnym oknie pomiarowym |

Istotne zależności:

- `compsize_disk_after < compsize_disk_before` oznacza, że reprezentacja
  bieżącego drzewa gry zajmuje mniej;
- `statvfs_after > statvfs_before` oznacza, że użytkownik faktycznie widzi
  więcej wolnego miejsca na całym FS, ale różnica może być zakłócona przez
  inne procesy;
- spadek `compsize`, któremu nie towarzyszy wzrost `statvfs`, jest możliwy,
  gdy snapshot trzyma stare extenty;
- wzrost `Exclusive` po defragmentacji jest spodziewanym sygnałem zerwania
  reflinków i trzeba go pokazać razem z wynikiem kompresji;
- brak `compsize` oznacza „wynik niemierzalny”, a nie `0 B`.

### 5.1. `estimated payload reduction` a `incremental reclaim`

Te pojęcia nie są zamienne:

| Wielkość | Definicja | Możliwe źródło | Warunki wiarygodności |
|---|---|---|---|
| `estimated_payload_reduction` | modelowana różnica między rozmiarem wejścia próbek a rozmiarem ich skompresowanej reprezentacji, przeskalowana na jawnie zdefiniowaną populację | benchmark kodeka w pamięci/pliku tymczasowym | reprezentatywny schemat próbkowania, znane prawdopodobieństwa włączenia, korekta granic bloków/extentów i `sectorsize`; nadal jest tylko estymacją payloadu |
| `live_tree_compression_delta` | zmiana disk usage extentów widocznych z bieżącego drzewa | para `compsize_disk_bytes` bezpośrednio przed i po | ten sam zakres, sync, kompletne pomiary i brak zmiany inventory |
| `incremental_reclaim` | dodatkowe bajty dostępne na całym FS, które można przypisać konkretnej operacji | kontrolowana różnica `statvfs`/usage wraz z `compsize` i `btrfs du` | spokojne okno pomiarowe, sync, kontrola snapshotów/reflinków, metadanych i równoległych zapisów |

Estymacja payloadu może być dodatnia, gdy incremental reclaim wynosi zero
albo jest ujemny. Dzieje się tak między innymi wtedy, gdy dane są już
skompresowane, wynik zaokrągla się do tych samych sektorów, snapshot trzyma
stare extenty albo zerwanie sharingu alokuje więcej nowych danych niż wynosi
zysk kodeka. UI i JSON nie mogą używać wspólnej nazwy `estimated_savings` dla
tych trzech wartości bez jawnego pola `metric`.

### 5.2. Hardlinki, sparse data i shared extents

Te przypadki wymagają oddzielnych liczników i nie mogą być sprowadzone do
jednego `logical_bytes`:

| Zjawisko | Semantyka inventory | Semantyka payloadu | Semantyka alokacji/reclaim |
|---|---|---|---|
| hardlink | wiele ścieżek wskazuje ten sam `(st_dev, st_ino)`; raport zapisuje `path_count`, `unique_inode_count` i grupy aliasów | treść inode jest próbkowana i ważona raz, chyba że raport jawnie opisuje rozmiar referencjonowany przez ścieżki | inode/extent nie może być wielokrotnie doliczany do fizycznej alokacji |
| sparse file | `st_size` obejmuje dziury; inventory zapisuje długość logiczną, data extents i hole bytes osobno | dziur nie podaje się kodekowi jako rzekomych rzeczywistych bajtów bez jawnego wariantu eksperymentu | `st_blocks`/FIEMAP opisują alokowane zakresy; nie wolno prezentować dziur jako oszczędności kompresji |
| reflink/shared extent | różne inode lub snapshoty referują te same extenty | treść może być identyczna albo częściowo wspólna, ale próbkowanie kodeka nie mierzy kosztu zerwania sharingu | raport przechowuje `Total`, `Exclusive`, `Set shared` i stan snapshot sharing; incremental reclaim uwzględnia nowe i zachowane extenty |

Hardlink nie jest reflinkiem: pierwszy współdzieli inode, drugi extenty między
odrębnymi inode. Sparse nie oznacza skompresowany. `FIEMAP_EXTENT_SHARED` nie
określa stopnia kompresji, a `FIEMAP_EXTENT_ENCODED` potwierdza zakodowaną
reprezentację extentu, lecz samo nie podaje jej rozmiaru ani odzyskanych
bajtów. Parser i schema raportu muszą zachować te rozróżnienia do końca.

## 6. Metro 2033 Redux - analiza fałszywego „0 B”

### 6.1. Dowód z historii aplikacji

Dwa historyczne wpisy dla `steam-286690` mają następujące cechy:

| Pole | Auto | Maximum |
|---|---:|---:|
| `processed_files` | 47 | 47 |
| liczba zapisanych kodów wyjścia | 48 | 48 |
| wszystkie zapisane kody | 0 | 0 |
| `logical_bytes` | 8 381 570 020 | 8 381 570 020 |
| `physical_bytes` przed i po | 8 381 673 472 | 8 381 673 472 |
| `compsize_disk_bytes` przed i po | `null` | `null` |
| `actual_saved_bytes` | 0 | 0 |
| status | `completed_with_warning` | `completed_with_warning` |

48 kodów odpowiada historycznej wersji: jedno `property set` i 47
defragmentacji. Wpisy powstały przed dodaniem końcowego `filesystem sync`.
Nie zawierają wyników odczytowych kontroli `btrfs du` ani tekstu z procesów.

### 6.2. Łańcuch przyczyn

1. `compsize` próbował wykonać ioctl `SEARCH_V2` jako zwykły użytkownik.
2. Kernel odmówił: `Operation not permitted`.
3. Parser poprawnie pozostawił pola `compsize_*` jako `null`.
4. Starsza logika użyła awaryjnego `st_blocks * 512` jako „physical”.
5. Ta wartość była identyczna przed i po, więc zapisano `0`.
6. UI wyświetliło `0 B`, zamiast powiedzieć, że pomiar się nie udał.
7. Późniejszy `sudo compsize` użytkownika pokazał około 7,8 GiB
   nieskompresowanych danych i około 7,0 GiB disk usage, czyli około 0,8 GiB
   różnicy.

Najbardziej prawdopodobne wyjaśnienie brzmi: rekompresja części lub całości
zakresu rzeczywiście zaszła, ale aplikacja nie miała autorytatywnego pomiaru.
Nie można jednak przypisać 0,8 GiB konkretnie pierwszej albo drugiej operacji,
ponieważ dla żadnej nie zachowano uprzywilejowanego wyniku `compsize` przed.

### 6.3. Wymagana prezentacja po naprawie

Jeżeli dowolny z dwóch pomiarów `compsize` jest niedostępny:

```text
Operacja zakończyła się, ale nie udało się zmierzyć oszczędności.
```

W modelu musi pozostać `actual_saved_bytes = null`. Wartość `0` jest
poprawna wyłącznie wtedy, gdy oba wiarygodne pomiary istnieją, dotyczą tego
samego zakresu i ich różnica rzeczywiście wynosi zero.

Historyczne wpisy z pustym `compsize_*` i `actual_saved_bytes=0` powinny być
prezentowane jako „wynik nieznany” albo jednorazowo migrowane do `null`; nie
należy przepisywać ich na podstawie późniejszego pojedynczego pomiaru.

## 7. Snapper, reflinki i shared extents

### 7.1. Dlaczego wolne miejsce może nie wzrosnąć

Snapshot Btrfs początkowo współdzieli extenty z bieżącym podwolumenem.
Defragmentacja/rekompresja zapisuje nowe extenty dla plików w bieżącym
podwolumenie. Stare extenty nie mogą zostać zwolnione, dopóki referuje je
snapshot. Powstają wtedy jednocześnie:

- stara wersja zatrzymana przez Snappera;
- nowa, zwykle skompresowana i exclusive wersja bieżącej gry.

Dlatego po operacji możliwe są trzy różne obserwacje:

1. `compsize` bieżącego katalogu spada - rekompresja działa;
2. `Exclusive` rośnie i `Set shared` spada - sharing został zerwany;
3. wolne miejsce `statvfs` nie rośnie albo chwilowo maleje - snapshot trzyma
   stare extenty.

To nie jest uszkodzenie danych, ale może być niepożądanym wynikiem
przestrzennym. Game Optimization nie powinien usuwać ani zmieniać snapshotów
użytkownika.

### 7.2. Polityka bezpieczeństwa

Stan powinien być trójwartościowy:

- `not_detected` - operacja może być automatyczna po spełnieniu pozostałych
  warunków;
- `detected` - nie jest bezwarunkowym błędem; tryb ręczny może przygotować
  plan, policzyć konserwatywny możliwy wzrost i wymagać dokładnego
  potwierdzenia;
- `unknown` - fail closed, ponieważ ryzyka nie da się wiarygodnie ocenić.

Przed każdym plikiem należy ponownie sprawdzić sharing na otwartym
deskryptorze. Jeśli pojawił się po przygotowaniu planu albo wzrósł ponad
potwierdzony zakres, plan trzeba odrzucić i przygotować ponownie. Tryb
automatyczny nie może łamać współdzielenia.

`Set shared` nie jest gwarantowaną liczbą realnego wzrostu globalnej alokacji.
Należy opisywać ją jako konserwatywny zakres ryzyka, a nie prognozę z
dokładnością do bajta.

## 8. Lista błędów i minimalnych poprawek

Poniższa lista rozróżnia defekty znalezione w danych historycznych od stanu
obecnej ścieżki roboczej.

### P0 - wiarygodność wyniku

| Problem | Skutek | Minimalna poprawka | Stan w audytowanym kodzie |
|---|---|---|---|
| `compsize` bez wymaganych uprawnień | brak autorytatywnego pomiaru | minimalny helper Polkit wykonujący wyłącznie dozwolony odczyt dla zweryfikowanej gry | niezaimplementowany |
| fallback `st_blocks` zapisywany jako `0 B` | fałszywy sukces Metro i innych gier | liczyć oszczędność tylko z dwóch niepustych `compsize_disk_bytes`; w pozostałych przypadkach `null` i ostrzeżenie | logika bieżąca poprawiona; stare wpisy wymagają zgodnej prezentacji/migracji |
| sukces oparty głównie na kodach `0` | brak dowodu rezultatu biznesowego | wynik `completed` tylko po końcowej walidacji i pomiarze; brak pomiaru → `completed_with_warning/measurement_unavailable` | zasadniczo poprawione, lecz helper nadal blokuje pełną walidację |
| brak trwałego, per-procesowego dowodu | po fakcie nie wiadomo, co dokładnie zwrócił konkretny proces | zapisywać ograniczone rekordy `{kind, argv, exit_code, stdout_excerpt, stderr_excerpt, started_at, duration}` | argv/kod/skrócony stderr są w bieżących logach; historia nadal przechowuje tylko listę części kodów |
| nieproporcjonalne próbkowanie schema v1 | nad- lub niedoważenie plików wewnątrz grup i niemiarodajne estymacje gier | zastąpić je schema v2 z jawną populacją, wagami/inclusion probability i manifestem próbek | wszystkie istniejące raporty v1 odrzucone jako pilotaże |
| pseudo-CI schema v1 | zakres wygląda jak statystyczny przedział ufności mimo braku poprawnego modelu losowania | nie nazywać zakresu CI; w v2 użyć estymatora zgodnego ze schematem warstwowym albo raportować wyłącznie zakres scenariuszy | wszystkie liczby CI z v1 odrzucone |

### P1 - spójność pomiaru i bezpieczeństwo

| Problem | Skutek | Minimalna poprawka |
|---|---|---|
| brak uprzywilejowanego `compsize` przed i po w jednej transakcji | brak porównywalnej pary | helper wykonuje `sync → before → write → sync → after`, wiążąc pomiary z jednym identyfikatorem operacji |
| końcowa walidacja nie potwierdza właściwości katalogu | kod `property set=0` jest jedynym dowodem | po operacji wykonać `btrfs property get -t inode FD compression` i zapisać wynik |
| lista kodów nie obejmuje read-only `btrfs du`; stdout/stderr nie trafiają do historii | nie można odtworzyć ryzyka shared ani problemu parsera | ujednolicić wszystkie uruchomienia w strukturze `ProcessEvidence` |
| globalny `statvfs` jest podatny na równoległy Steam/Snapper | fałszywe przypisanie wzrostu/spadku grze | oznaczać wynik jako globalny, mierzyć w krótkim spokojnym oknie i zapisywać znacznik zakłóceń |
| historyczne `0` bez `compsize` | nadal mylą w Overview/history | UI traktuje taką kombinację jak nieznany pomiar; nie pokazuje „0 B” |
| możliwy TOCTOU między GUI i przyszłym helperem | uprzywilejowana operacja na innym obiekcie | plan oparty na deskryptorach/tożsamościach inode, krótki TTL i ponowna walidacja w helperze |
| brak rozdzielenia hardlink/sparse/shared w schema v1 | błędne mianowniki, podwójne liczenie albo przypisanie holes jako zysku kodeka | osobne inventory ścieżek, inode, data extents, holes oraz shared extents |
| brak `sectorsize` w estymacji | wynik payloadu nie odzwierciedla minimalnej alokacji Btrfs | wykryć i zapisać wartość wraz z jej źródłem, modelować zaokrąglenia; brak wartości unieważnia rekomendację |

### P2 - diagnostyka i ergonomia

- Utrwalać wersje `kernel`, `btrfs-progs`, `compsize` i składnię wykrytą z
  `--help`, aby wynik był odtwarzalny.
- Otwierać pliki próbek z `O_NOATIME`. Jeśli nie można zagwarantować braku
  aktualizacji atime (albo udokumentowanego równoważnego trybu montowania),
  raport nie spełnia wymogu read-only i nie może być przyjęty jako v2.
- Egzekwować niezależne, zapisane w manifeście limity: wall time, CPU time,
  całkowite bajty odczytu oraz maksymalny rozmiar danych tymczasowych.
- Zapisywać dwa rezultaty: „zmiana bieżącego drzewa wg compsize” oraz
  „zmiana wolnego miejsca całego FS”. Nie łączyć ich w jedno pole.
- Pokazywać osobno: kompresję bieżących extentów, zmianę sharingu, możliwy
  koszt snapshotów i wynik netto widoczny użytkownikowi.
- Dla anulowania zapisywać, który plik był ostatni, jaki proces zakończono i
  czy potomka rzeczywiście zebrano. Bieżący kod używa osobnej grupy procesów,
  `SIGTERM`, ograniczonego oczekiwania, `SIGKILL` i `communicate`; wymaga to
  jeszcze testu integracyjnego z realnym, długim procesem.
- Nie uruchamiać pełnego analyzera po anulowaniu tylko w celu stworzenia
  wyniku. Bieżąca ścieżka anulowania poprawnie oznacza pomiar końcowy jako
  pominięty i nie powinna wydłużać zamykania GUI.

## 9. Projekt minimalnego helpera Polkit

### 9.1. Cel i granica zaufania

GUI, QML, cache i dane dostarczone przez użytkownika są niezaufane. Helper jest
małą, instalowaną systemowo usługą D-Bus uruchamianą jako root. Nie importuje
QML, nie skanuje dowolnych ścieżek i nie przyjmuje tablic poleceń. Jedyną
granicą uprzywilejowania jest jego wąskie API.

Proponowane akcje Polkit:

- `io.github.DevVoidPL.GameOptimizationLinux.measure-steam-game-compression` - tylko odczyt:
  `compsize`, `btrfs filesystem du`, property get i statvfs;
- `io.github.DevVoidPL.GameOptimizationLinux.recompress-steam-game` - dokładnie jedna
  zatwierdzona operacja: opcjonalny property set, per-file kontrola sharingu,
  per-file rekompresja, sync i pomiar;
- anulowanie nie wymaga nowej autoryzacji, ale jest dostępne wyłącznie dla
  właściciela operacji i tego samego D-Bus subject.

Interakcję z Polkit należy wykonywać asynchronicznie. Flaga pozwalająca na
interakcję użytkownika może być użyta tylko po bezpośredniej akcji
użytkownika, nigdy z timera lub automatycznego skanu. GUI nie powinno blokować
wątku Qt podczas autoryzacji.

### 9.2. Minimalne API

```text
MeasureSteamGame(caller, app_id, library_id) -> MeasurementResult
PrepareRecompression(caller, app_id, library_id, profile, changed_only)
  -> {opaque_plan_token, summary, expires_at}
ExecuteRecompression(caller, opaque_plan_token, confirmation_nonce)
  -> operation_id
Cancel(caller, operation_id)
GetOperation(caller, operation_id) -> progress/result
```

`path`, `argv`, nazwa programu i poziom nie są parametrami API. Profile są
zamkniętym enumem. Helper mapuje je na `1`, `3`, `9`, a dla Auto przyjmuje
wyłącznie podpisany wynik własnej analizy dla `1/3/6/9`.

Token planu:

- jest losowy, jednorazowy i ma krótki TTL;
- jest związany z UID, unikalną nazwą D-Bus, AppID i build ID;
- zawiera po stronie helpera tożsamość katalogu
  `(st_dev, st_ino, st_uid, st_mode)`;
- zawiera tożsamości manifestu i każdego pliku
  `(dev, ino, size, mtime_ns, ctime_ns)`;
- wiąże wynik shared extents, wymagane wolne miejsce, algorytm i poziom;
- staje się nieważny po zmianie manifestu, ścieżki, pliku, montowania,
  sharingu albo dostępnego miejsca poniżej progu.

### 9.3. Weryfikacja Steam i ścieżki

Helper samodzielnie:

1. ustala UID wywołującego z poświadczeń D-Bus;
2. czyta konfigurację bibliotek Steam należącą do tego UID;
3. otwiera wskazaną bibliotekę bez podążania za symlinkami;
4. otwiera dokładnie
   `steamapps/appmanifest_<appid>.acf`;
5. sprawdza w manifeście zgodność `appid`, `installdir`, `buildid` i stan
   instalacji/aktualizacji;
6. wyprowadza katalog wyłącznie jako
   `steamapps/common/<installdir>`;
7. sprawdza, że root gry jest prawdziwym katalogiem na Btrfs, należy do
   wywołującego i leży na tym samym urządzeniu co zweryfikowana biblioteka;
8. odrzuca root `/`, `/home`, sam katalog biblioteki, `steamapps`,
   `steamapps/common`, punkt montowania oraz katalog nadrzędny;
9. odrzuca instalację w trakcie aktualizacji i uruchomioną grę.

Sama obecność pliku o nazwie `appmanifest_*.acf` nie jest wystarczającym
zaufaniem. Wszystkie komponenty ścieżki muszą mieć bezpieczne typy i
właściciela; manifest oraz katalog biblioteki nie mogą przekierować helpera do
pliku root lub innego użytkownika.

### 9.4. Canonicalizacja bez wyścigów

Preferowany mechanizm Linux:

```text
openat2(library_fd, relative_path,
        RESOLVE_BENEATH |
        RESOLVE_NO_SYMLINKS |
        RESOLVE_NO_MAGICLINKS |
        RESOLVE_NO_XDEV)
```

Na systemie bez `openat2` należy przejść każdy komponent przez
`openat(..., O_NOFOLLOW | O_DIRECTORY)` i po każdym otwarciu wykonać `fstat`.
Plik końcowy otwierany jest `O_NOFOLLOW`, musi być regularny, mieć oczekiwany
UID, urządzenie, inode, rozmiar i czasy z planu. Operacje systemowe otrzymują
wewnętrznie `/proc/self/fd/N`, a deskryptory są przekazywane przez `pass_fds`.
Po otwarciu nie należy wracać do canonicalizacji tekstowej ścieżki.

To zapobiega:

- `..` i ścieżkom absolutnym;
- symlink escape i magic-link escape;
- podmianie komponentu pomiędzy `realpath` a wykonaniem;
- przekroczeniu granicy montowania;
- skierowaniu uprzywilejowanej komendy na dowolny plik.

### 9.5. Biała lista operacji

Helper sam tworzy wyłącznie poniższe argv:

```text
[compsize, "--bytes", "--one-file-system", FD_PATH]
[btrfs, "filesystem", "du", "--raw", "--summarize", FD_PATH]
[btrfs, "property", "get", "-t", "inode", FD_PATH, "compression"]
[btrfs, "property", "set", "-t", "inode", FD_PATH, "compression", "zstd"]
[btrfs, "filesystem", "defragment", "-f", "-czstd",
 "--level", {"1"|"3"|"6"|"9"}, FD_PATH]
[btrfs, "filesystem", "sync", ROOT_FD_PATH]
```

Nie ma:

- `shell=True`, interpretera poleceń, `sudo` ani `pkexec` wewnątrz helpera;
- argumentów użytkownika poza zamkniętym enumem i zweryfikowanym tokenem;
- dziedziczenia dowolnego `PATH`, `LD_PRELOAD`, `PYTHONPATH` lub locale;
- dostępu do `inspect-internal`, `dump-tree`, snapshot delete, mount,
  subvolume delete, `chattr`, dowolnego unlink/rename/write.

Programy powinny mieć skompilowane lub instalacyjnie ustalone ścieżki, a
środowisko potomka być minimalne (`LANG=C`, `LC_ALL=C`, stały `PATH`).

### 9.6. Shared extents i wolne miejsce

Przed autoryzacją helper zwraca:

- `Total`, `Exclusive`, `Set shared`;
- konserwatywny możliwy wzrost alokacji;
- `compsize` i `statvfs`;
- minimalny wymagany zapas: stały bufor + największy plik + potwierdzone
  ryzyko zerwania sharingu.

Tryb automatyczny akceptuje tylko pewne `not_detected`. Tryb ręczny może
wykonać plan z `detected` tylko wtedy, gdy:

- użytkownik dostał konkretną liczbę/range ryzyka;
- token wiąże dokładnie ten wynik;
- sharing nie wzrósł podczas końcowego preflight;
- zapas wolnego miejsca pozostaje wystarczający.

Stan `unknown` zawsze blokuje modyfikację.

### 9.7. Anulowanie i procesy potomne

Każdy proces jest uruchamiany w osobnej grupie/sesji. Helper:

1. odczytuje stdout i stderr bez ryzyka zapełnienia pipe;
2. reaguje na anulowanie w krótkim interwale;
3. wysyła `SIGTERM` do grupy;
4. czeka ograniczony czas;
5. w razie potrzeby wysyła `SIGKILL`;
6. zawsze wykonuje `wait/communicate`, aby nie pozostawić zombie;
7. po częściowej operacji zwraca `cancelled`,
   `verification_required` i listę zakończonych plików;
8. nie rozpoczyna po anulowaniu długiego pełnego skanu, który opóźniłby
   zamknięcie aplikacji.

Anulowanie nie cofa już zapisanych extentów i UI nie może sugerować rollbacku.

### 9.8. Logi i wynik strukturalny

Dla każdego procesu helper zapisuje:

```json
{
  "kind": "recompress_file",
  "argv_template": [
    "btrfs", "filesystem", "defragment", "-f", "-czstd",
    "--level", "3", "<verified-fd>"
  ],
  "exit_code": 0,
  "stdout_excerpt": "",
  "stderr_excerpt": "",
  "duration_ms": 0,
  "timed_out": false,
  "cancelled": false
}
```

Powyżej jest schemat, nie wynik benchmarku. Fragmenty stdout/stderr muszą mieć
limit bajtów i redakcję danych wrażliwych. Nie zapisuje się zawartości plików,
hasła, tokenu Polkit ani pełnej prywatnej konfiguracji. Historia operacji jest
zapisywana atomowo, a marker rozpoczęcia powstaje przed pierwszą modyfikacją.

## 10. Kontrolowany test kopii Btrfs

### 10.1. Fakty potwierdzone

Test wykonany na kopiach, nie na oryginalnych plikach gry, potwierdził:

- powstanie extentów oznaczonych jako encoded po ścieżce rekompresji;
- zgodność integralności danych przed i po operacji;
- wykrycie danych współdzielonych ze snapshotem przed operacją;
- zerwanie tego współdzielenia dla zmodyfikowanej bieżącej kopii.

Są to ważne dowody funkcjonalne. Potwierdzają, że backend może utworzyć
zakodowaną reprezentację bez zmiany treści oraz że ostrzeżenie o snapshotach
nie jest teoretyczne. Nie są jednak dowodem liczby odzyskanych bajtów:

- `FIEMAP_EXTENT_ENCODED` nie podaje disk usage;
- nie uzyskano kompletnej pary uprzywilejowanych odczytów `compsize` przed i
  po;
- nie wykonano identycznej, pełnej sekwencji pomiarów dla wszystkich profili;
- zerwanie sharingu mogło zwiększyć exclusive allocation niezależnie od
  redukcji zakodowanego payloadu.

Wyniku tego testu nie wolno przekształcać w procent oszczędności, ranking
Fast/Balanced/Maximum/Auto ani wartość incremental reclaim. Jego status to
„mechanizm i ryzyko potwierdzone; efekt przestrzenny niezmierzony”.

### 10.2. Protokół testu rozstrzygającego

Test produkcyjnej wiarygodności powinien ponownie użyć wyłącznie kopii w
jednorazowym katalogu na tym samym Btrfs:

1. wybrać po benchmarku próbkę dobrze, średnio i słabo kompresowalną;
2. skopiować próbki z `--reflink=never`, aby baseline nie dzielił extentów z
   oryginałem;
3. utworzyć osobny wariant shared przez `cp --reflink=always` pomiędzy
   plikami testowymi;
4. sprawdzić sumę `st_size`, `st_blocks`, `btrfs filesystem du`,
   `compsize --bytes --one-file-system` i `statvfs`;
5. wykonać `filesystem sync`;
6. rekompresować wyłącznie kopię ustalonym profilem;
7. ponownie wykonać sync i wszystkie pomiary;
8. osobno, po zgodzie użytkownika, sprawdzić wariant z testowym podwolumenem i
   testowym snapshotem;
9. zapisać wynik netto, zmianę exclusive/shared i działanie detektora;
10. usunąć wyłącznie jawnie utworzone obiekty testowe i potwierdzić ich brak.

Test integracyjny ochrony reflinków musi zawierać dwa pliki utworzone przez
`cp --reflink=always` i wykazać `detected` przed jakąkolwiek defragmentacją.
Automatyczny plan ma je pominąć/zablokować. Wariant ręczny może zostać
wykonany wyłącznie w katalogu testowym po potwierdzeniu policzonego ryzyka.

Nie należy używać `btrfs inspect-internal dump-tree`. FIEMAP,
`btrfs filesystem du` i publiczne ioctl są wystarczającą bazą aplikacji.

## 11. Wyniki benchmarków całych gier

Sekcja zawiera wyłącznie wyniki rzeczywistych, tylko odczytowych benchmarków
schema v2. Estymacje z jednego pliku, opis użytkownika oraz odrzucone piloty
v1 nie zostały przeniesione do tabeli.

### 11.1. Status raportów schema v1

Wszystkie dotychczasowe raporty poszczególnych gier oznaczone schema v1 są
**odrzuconymi pilotażami**. Można zachować je jako materiał diagnostyczny do
testowania parsera i wykrywania regresji, ale nie wolno:

- przenosić ich wartości do tabeli wyników;
- wyliczać z nich średniego zysku dla gry lub biblioteki;
- porównywać na ich podstawie profili/kodeków;
- używać ich do wyboru Auto;
- nazywać ich zakresów przedziałami ufności;
- przedstawiać ich jako dowodu incremental reclaim.

Przyczyny odrzucenia są metodologiczne:

1. Wewnątrz grup plików/rozszerzeń próbki nie reprezentowały
   proporcjonalnie udziału bajtów poszczególnych plików i zakresów. Mały
   fragment lub plik mógł otrzymać tę samą wagę co dominujący plik.
2. Późniejsze przeskalowanie wyniku grupy nie naprawia błędnego doboru
   wewnątrz grupy, jeśli nie są znane prawdopodobieństwa włączenia i poprawne
   wagi.
3. Raportowany zakres był pseudo-CI: nie wynikał z poprawnego losowego lub
   warstwowego schematu próbkowania ani estymatora wariancji odpowiedniego dla
   tego schematu.
4. Estymacja dotyczyła redukcji badanego payloadu, a nie zmiany aktualnej
   alokacji Btrfs ani wzrostu `f_bavail`.

Odrzucenie v1 nie oznacza, że próbki „dowiodły braku kompresowalności”.
Oznacza tylko, że ich wynik ilościowy nie spełnia kryterium dowodowego.

<!-- BENCHMARK_RESULTS_START -->

**Status: wykonano 13 kanonicznych benchmarków schema v2.** Metro 2033
Redux nie jest obecnie zainstalowane: brak `appmanifest_286690.acf`, a
pozostałe 412 728 B to stub danych, nie gra. Braku nie zastąpiono zerem ani
fikcyjnym benchmarkiem.

Środowisko: kernel `7.1.3-zen2-2-zen`, `btrfs-progs 7.1`, Zstandard CLI
`1.5.7`, XZ Utils `5.8.3`. Wszystkie raporty mają schema `2`, metodologię
`2`, wersję narzędzia `2.0.0` i zgodny SHA-256 źródła:

```text
80eaafdb367934128406d59d183268ae5ca69c453a0ed091dd5be12462941fea
```

Manifest Steam i fingerprint inwentarza pozostały stabilne w 13/13
przebiegów. Każdy raport ma `read_only_source=true`, `noatime=true`,
`nofollow=true`, brak przejścia poza system plików i brak błędów uprawnień,
hardlinków oraz sparse/unknown data. Pole incremental savings jest `null` we
wszystkich algorytmach.

| Gra | AppID | Logicznie | Pliki | Próbka | Pokrycie | Rekomendacja | Raport |
|---|---:|---:|---:|---:|---:|---|---|
| Jedi Survivor | 1774580 | 130,35 GiB | 682 | 2,00 GiB | 1,534% | nie - brak materialnej dolnej granicy | [JSON](../reports/compression_benchmarks/STAR-WARS-Jedi-Survivor-20260727-114738.json) |
| Batman Arkham Origins | 209000 | 27,06 GiB | 9839 | 511,9 MiB | 1,847% | tak | [JSON](../reports/compression_benchmarks/Batman-Arkham-Origins-20260727-112837.json) |
| Batman Arkham Asylum GOTY | 35140 | 7,89 GiB | 3273 | 511,8 MiB | 6,336% | tak | [JSON](../reports/compression_benchmarks/Batman-Arkham-Asylum-GOTY-Edition-20260727-112658.json) |
| Batman Arkham Knight | 208650 | 53,94 GiB | 5658 | 512,0 MiB | 0,927% | tak | [JSON](../reports/compression_benchmarks/Batman-Arkham-Knight-20260727-112943.json) |
| Wiedźmin 3 | 292030 | 61,18 GiB | 2852 | 512,0 MiB | 0,817% | tak | [JSON](../reports/compression_benchmarks/The-Witcher-3-Wild-Hunt-20260727-113232.json) |
| Dying Light | 239140 | 30,52 GiB | 632 | 512,0 MiB | 1,638% | tak | [JSON](../reports/compression_benchmarks/Dying-Light-20260727-113228.json) |
| Detroit Become Human | 1222140 | 58,75 GiB | 50 | 512,0 MiB | 0,851% | tak | [JSON](../reports/compression_benchmarks/Detroit-Become-Human-20260727-113352.json) |
| House Flipper | 613100 | 11,79 GiB | 2319 | 2,00 GiB | 16,964% | tak | [JSON](../reports/compression_benchmarks/House-Flipper-20260727-115349.json) |
| Rayman Origins | 207490 | 2,31 GiB | 180 | 511,9 MiB | 21,634% | tak | [JSON](../reports/compression_benchmarks/Rayman-Origins-20260727-112108.json) |
| Rayman Legends | 242550 | 5,71 GiB | 81 | 511,9 MiB | 8,753% | tak | [JSON](../reports/compression_benchmarks/Rayman-Legends-20260727-112440.json) |
| Spelunky | 239350 | 184,5 MiB | 107 | 184,5 MiB | 100,000% | tak | [JSON](../reports/compression_benchmarks/Spelunky-20260727-111654.json) |
| Bloons TD 6 | 960090 | 2,58 GiB | 293 | 512,0 MiB | 19,364% | tak | [JSON](../reports/compression_benchmarks/Bloons-TD-6-20260727-111900.json) |
| Bloons Adventure Time TD | 979060 | 542,1 MiB | 508 | 511,8 MiB | 94,409% | tak | [JSON](../reports/compression_benchmarks/Bloons-Adventure-Time-TD-20260727-111840.json) |
| Metro 2033 Redux | 286690 | niedostępne | - | - | - | brak instalacji | [JSON](../reports/compression_benchmarks/Metro-2033-Redux-unavailable-20260727.json) |

Poniższe liczby to GiB szacowanej redukcji całego payloadu względem
nieskompresowanej bazy. `Z1…Z15` oznacza Btrfs ZSTD, a `zl1…zl9` Btrfs zlib.
ZSTD-19 i XZ-9 są zewnętrznymi punktami odniesienia. Poziom ZSTD-15 jest
obsługiwany przez badany kernel, ale produkcyjny profil Maximum Game Optimization
pozostaje na poziomie 9.

| Gra | Z1 | Z3 | Z6 | Z9 | Z15 | zl1 | zl3 | zl6 | zl9 | ref Z19 | ref XZ9 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Jedi Survivor | 0,78 | 0,83 | 0,85 | 0,85 | 0,93 | 0,84 | 0,85 | 0,88 | 0,89 | 1,33 | 1,30 |
| Batman Arkham Origins | 1,67 | 1,73 | 1,98 | 1,99 | 2,23 | 2,04 | 2,08 | 2,06 | 2,06 | 2,80 | 3,15 |
| Batman Arkham Asylum | 0,35 | 0,37 | 0,40 | 0,40 | 0,44 | 0,38 | 0,39 | 0,40 | 0,40 | 0,64 | 0,88 |
| Batman Arkham Knight | 1,53 | 1,61 | 1,80 | 1,81 | 2,15 | 1,89 | 1,94 | 1,98 | 1,99 | 2,82 | 3,17 |
| Wiedźmin 3 | 1,72 | 1,83 | 1,99 | 2,00 | 2,38 | 2,15 | 2,18 | 2,21 | 2,22 | 2,76 | 3,13 |
| Dying Light | 8,52 | 8,83 | 9,65 | 9,73 | 10,10 | 8,79 | 8,97 | 9,35 | 9,49 | 10,30 | 11,38 |
| Detroit Become Human | 3,97 | 4,16 | 4,34 | 4,36 | 4,61 | 3,93 | 4,00 | 4,08 | 4,11 | 5,82 | 6,20 |
| House Flipper | 4,34 | 4,47 | 4,71 | 4,73 | 4,93 | 4,43 | 4,52 | 4,65 | 4,68 | 5,00 | 5,35 |
| Rayman Origins | 0,19 | 0,19 | 0,20 | 0,20 | 0,21 | 0,19 | 0,20 | 0,20 | 0,20 | 0,24 | 0,36 |
| Rayman Legends | 0,50 | 0,50 | 0,51 | 0,51 | 0,54 | 0,50 | 0,51 | 0,52 | 0,52 | 0,62 | 0,94 |
| Spelunky | 0,02 | 0,02 | 0,02 | 0,02 | 0,02 | 0,02 | 0,02 | 0,02 | 0,02 | 0,03 | 0,03 |
| Bloons TD 6 | 0,37 | 0,38 | 0,42 | 0,42 | 0,47 | 0,43 | 0,43 | 0,44 | 0,44 | 0,57 | 0,68 |
| Bloons Adventure Time TD | 0,18 | 0,18 | 0,19 | 0,20 | 0,21 | 0,19 | 0,19 | 0,20 | 0,20 | 0,26 | 0,27 |

Najważniejsze pasma czułości dla profili zbliżonych do produkcyjnych:

| Gra | ZSTD-3: punkt [low-high] | ZSTD-9: punkt [low-high] |
|---|---:|---:|
| Jedi Survivor | 0,83 [0,00-4,59] GiB | 0,85 [0,00-4,62] GiB |
| Batman Arkham Origins | 1,73 [0,98-2,48] GiB | 1,99 [1,24-2,74] GiB |
| Batman Arkham Asylum | 0,37 [0,25-0,49] GiB | 0,40 [0,28-0,52] GiB |
| Batman Arkham Knight | 1,61 [0,00-3,30] GiB | 1,81 [0,12-3,50] GiB |
| Wiedźmin 3 | 1,83 [0,00-3,78] GiB | 2,00 [0,05-3,95] GiB |
| Dying Light | 8,83 [7,95-9,70] GiB | 9,73 [8,85-10,61] GiB |
| Detroit Become Human | 4,16 [2,30-6,01] GiB | 4,36 [2,50-6,21] GiB |
| House Flipper | 4,47 [4,30-4,65] GiB | 4,73 [4,56-4,91] GiB |
| Rayman Origins | 0,19 [0,16-0,23] GiB | 0,20 [0,16-0,23] GiB |
| Rayman Legends | 0,50 [0,42-0,59] GiB | 0,51 [0,42-0,60] GiB |
| Spelunky | 0,016 [0,016-0,016] GiB | 0,020 [0,020-0,020] GiB |
| Bloons TD 6 | 0,38 [0,34-0,42] GiB | 0,42 [0,38-0,46] GiB |
| Bloons Adventure Time TD | 0,18 [0,18-0,19] GiB | 0,20 [0,19-0,20] GiB |

Pełne raporty tekstowe i JSON zawierają klasyfikację każdej grupy
katalog/rozszerzenie, surowe proporcje, liczbę okien i dokładne wartości w
bajtach. Największy potencjał natywnego Btrfs pokazały Dying Light i House
Flipper. Jedi, Wiedźmin 3 i Batman Knight są przykładami, w których punktowy
zysk lub jego dolna granica są małe względem rozmiaru gry.

<!-- BENCHMARK_RESULTS_END -->

### 11.2. Stan i kryteria przyjęcia schema v2

Raporty v2 mogą zasilić tabelę jako **tylko odczytowa estymacja redukcji
payloadu względem nieskompresowanego strumienia**. Nie są pomiarem dodatkowego
miejsca możliwego do odzyskania z obecnych extentów. Automatyczna rekomendacja
jest dozwolona tylko wtedy, gdy pole `reliable_for_recommendation` ma wartość
`true`.

Wykonane raporty przechodzą następującą bramkę:

- `report_type` ma dokładną wartość `game-compression-benchmark`,
  `schema_version=2`, a raport zapisuje wersję narzędzia i metodologii oraz
  SHA-256 źródła narzędzia;
- AppID, Steam build ID, `StateFlags=4`, nazwa `installdir` i hash manifestu
  są sprawdzane; manifest oraz fingerprint pełnego inwentarza muszą pozostać
  niezmienione do końca testu;
- źródła są otwierane tylko z `O_RDONLY | O_NOFOLLOW | O_NOATIME`; brak
  możliwości użycia `O_NOATIME` kończy test, zamiast cicho zmieniać atime;
- skan nie przekracza granicy systemu plików i nie podąża za symlinkami;
  hardlinki są deduplikowane po inode, a sparse/nieznany układ data-hole
  powoduje fail-closed dla rekomendacji;
- próbka ma co najmniej `min(512 MiB, rozmiar gry)` z tolerancją jednego
  zestawu ogonów, o ile użytkownik nie uruchomił jawnie mniejszego pilota;
- grupy katalog/rozszerzenie są ważone udziałem w logicznym rozmiarze gry,
  a 128-KiB jednostki są dobierane systematycznie z całej populacji klastrów;
  rozkład plików i warstw rozmiarowych jest osobno kontrolowany;
- wszystkie tryby Btrfs są liczone na tych samych jednostkach z nowym
  kontekstem kodeka dla każdego 128-KiB klastra i zaokrągleniem do wykrytego
  `sectorsize`;
- porównywane są ZSTD 1/3/6/9/15, zlib 1/3/6/9 oraz - tylko jako referencje -
  zewnętrzne ZSTD-19 i XZ-9;
- narzędzie ma limit całego benchmarku, osobny wspólny deadline zewnętrznych
  kodeków, jednego workera, limit pamięci 1 GiB, kontrolę wolnego miejsca,
  anulowanie oraz atomowy zapis raportów poza katalogiem gry;
- zmiana manifestu lub inwentarza, błędy uprawnień, niepełna istotna grupa
  albo niereprezentatywna warstwa wyłącza rekomendację;
- `estimated_incremental_disk_savings_bytes` pozostaje `null`. Realny reclaim
  wymaga pary przed/po: `compsize`, exclusive/shared i miejsca dostępnego na
  systemie plików.

Zakres `low/high` jest jawnie opisany jako heurystyczne pasmo czułości, nie
przedział ufności. XZ nie jest LZX ani trybem Btrfs.

#### Ograniczenia samoweryfikacji v2

Obecny format nie zawiera osobnego hasha kanonicznego całego JSON ani pełnej
listy każdego pliku i każdego offsetu próbki. Zapisuje hash źródła narzędzia,
hash manifestu, fingerprint inwentarza, statystyki grup i liczbę okien, ale
nie pozwala odtworzyć identycznego sample manifest wyłącznie z raportu.
Przed produkcyjnym użyciem wyników przez profil Auto warto dodać:

- kanoniczny `report_sha256`;
- pełny, prywatny manifest próbek z `(dev, ino, ścieżka, offset, długość,
  hash danych)` i wersją schematu;
- dokładne zużycie CPU/RAM oraz przyczynę zakończenia każdego procesu;
- osobny, uprzywilejowany i tylko odczytowy baseline `compsize` oraz
  exclusive/shared.

Brak tych pól nie unieważnia porównania kodeków wykonanego w tym audycie,
ale ogranicza jego odtwarzalność i bezwzględnie zabrania nazywania wyniku
„odzyskanym miejscem”.

## 12. Macierz alternatyw

Pierwsze dwa wiersze korzystają już z wyników benchmarku payloadu. Pozostałe
backendy pozostają oceną jakościową, ponieważ nie zbudowano ich prototypów.
Żaden wiersz nie jest rekomendacją wdrożeniową bez osobnego testu zgodności i
rzeczywistego pomiaru miejsca przed/po.

| Rozwiązanie | Przewidywany efekt | Start i losowy odczyt | CPU/RAM | Steam/Proton/antycheat | Aktualizacje | Ryzyko danych | Trudność / automatyzacja | Czy wolne miejsce będzie widoczne |
|---|---|---|---|---|---|---|---|---|
| Btrfs ZSTD Fast/Balanced | od <1% punktowo dla Jedi do ok. 29% dla Dying Light i 38% dla House przy ZSTD-3; zależny od gry | przezroczyste, zwykle dobry losowy odczyt | koszt przy rekompresji i dekompresji, zwykle niski RAM | najwyższa zgodność, ścieżki pozostają zwykłymi plikami | Steam zapisuje nowe extenty; property pomaga przyszłym zapisom, po update potrzebna analiza zmian | niskie dla danych, ale defrag może zerwać sharing | najłatwiejsze do automatyzacji na Btrfs | tak dla live extentów; Snapper może odroczyć wzrost wolnego miejsca |
| Btrfs ZSTD Maximum (9) | zwykle niewielki/umiarkowany dodatek do poziomu 3; największy w Dying Light: około +0,90 GiB payloadu | jak wyżej | wyraźnie większy koszt rekompresji | jak wyżej | jak wyżej | jak wyżej | mała dodatkowa złożoność | jak wyżej; wyższy poziom nie gwarantuje istotnego zysku |
| SquashFS + overlayfs | potencjalnie wysoki dla obrazu read-only, zależny od kodeka i układu bloków | dobry odczyt sekwencyjny, losowy zależny od bloków/cache | narzut dekompresji i rebuild obrazu | ryzyko dla antycheatu, ścieżek, mmap, xattr i oczekiwań Steam | patch Steam trafia do upperdir albo wymaga przebudowy całego obrazu; trudne odzyskiwanie starych bloków | średnie/wysokie przez złożony lifecycle | wysoka; potrzebne mounty, overlay i recovery | tak po usunięciu źródłowego drzewa, co jest operacją wysokiego ryzyka |
| EROFS + overlayfs | potencjalnie wysoki; EROFS jest projektowany pod skompresowany read-only i losowy odczyt | zwykle lepszy model losowego odczytu niż archiwum strumieniowe | zależny od algorytmu; RAM na cache | te same ryzyka integracji z mount/overlay i antycheatem | wymaga zarządzania warstwami lub przebudowy obrazu | średnie/wysokie | wysoka; narzędzia, mount namespace i recovery | tak, jeśli bezpiecznie zastąpi się oryginalne dane obrazem |
| FUSE z kompresją | teoretycznie zależny od dowolnego kodeka | dodatkowe przejścia userspace; losowy odczyt i mmap mogą być problemem | najwyższy narzut CPU/context switch, cache RAM | najwyższe ryzyko zgodności, szczególnie antycheat/direct I/O | pełna poprawność rename, truncate, xattr, fsync i crash recovery jest trudna | wysokie | bardzo wysoka; duża powierzchnia błędów | potencjalnie, ale dopiero po dojrzałym, trwałym formacie |
| Montowany obraz bez dedykowanego FS | zależny od FS wewnątrz obrazu | dodatkowa warstwa loop/mount; losowy dostęp zależy od formatu | zależny od formatu | Steam może nie rozumieć cyklu montowania i braku ścieżki przy starcie | resize, patch i atomowa wymiana obrazu są trudne | średnie/wysokie | wysoka; wymaga uprzywilejowanego mount lifecycle | tak, ale obraz może wymagać rezerwy i okresowej przebudowy |
| Deduplikacja reflinkami Btrfs | tylko identyczne zakresy/pliki; nie kompresuje unikalnych danych | brak dekompresji, zwykły odczyt | koszt hashowania podczas analizy; mały runtime | wysoka, bo pozostają zwykłe pliki | CoW rozdziela zmienione dane naturalnie | niskie po pełnym hashu i ponownej walidacji; defrag później może zniszczyć zysk | średnia, dobrze automatyzowalna z fail-closed | tak, jeśli duplikaty są rzeczywiście unikalnie alokowane; snapshoty komplikują pomiar |
| Usuwanie nieużywanych pakietów językowych | może być duże tylko w grach z osobnymi paczkami; brak danych ogólnych | brak narzutu runtime dla zachowanych języków | brak | Steam Verify/update może je przywrócić; niektóre gry wymagają wspólnych paczek | trzeba ponawiać po update i respektować język gry | średnie bez metadanych; wymaga jawnego potwierdzenia/odtwarzania | średnia, mocno zależna od gry | tak, bezpośrednio po bezpiecznym usunięciu, ale to modyfikacja instalacji |
| Wykrywanie duplikatów | raport sam w sobie nic nie zwalnia; może zasilać reflink dedupe | brak wpływu po samym skanie | koszt CPU/I/O na hash, bounded RAM | analiza jest zgodna; modyfikacja dopiero w osobnym etapie | wynik unieważnia się po update | niskie dla odczytu; przed dedupe wymagany pełny hash i recheck | średnia | dopiero po jawnej, bezpiecznej deduplikacji |
| Zarządzanie shader cache | zysk zależny od osobnych cache Proton/Steam, nie od plików gry | wyczyszczenie może powodować ponowne kompilacje i stutter | późniejszy koszt CPU | zwykle zgodne, ale cache ma własny lifecycle | cache odrasta po grze/aktualizacji sterownika | niskie przy poprawnym rozpoznaniu cache, uciążliwe dla UX | niska/średnia | tak, lecz często tymczasowo |
| Pozostałości po aktualizacjach/download cache | potencjalny zysk poza katalogiem gry | brak wpływu po bezpiecznym cleanup | brak | trzeba respektować aktywne pobieranie i mechanizmy Steam | naturalnie odrasta | średnie bez oficjalnego stanu klienta | średnia | tak po potwierdzonym usunięciu wyłącznie osieroconych danych |

Najbezpieczniejsza kolejność rozwoju to: poprawny pomiar Btrfs → benchmark →
kontrolowany test kopii → deduplikacja/cleanup jako osobne funkcje. Obrazy,
overlay i FUSE wymagają osobnego projektu bezpieczeństwa oraz długich testów
zgodności; nie są minimalną poprawką obecnego backendu.

## 13. Odpowiedzi na 12 pytań decyzyjnych

### 1. Czy obecny backend Btrfs ZSTD rzeczywiście zwalnia miejsce?

Tak, co najmniej w sensie zmniejszenia disk usage bieżących extentów jest to
wiarygodne dla Metro: backend wykonuje realną rekompresję, a późniejszy
`sudo compsize` pokazał około 0,8 GiB różnicy. Nie ma jednak pary
uprzywilejowanych pomiarów bezpośrednio przed i po, więc skali nie można
formalnie przypisać pojedynczej operacji Game Optimization. Dla każdej gry odpowiedź
musi wynikać z nowego pomiaru.

### 2. Dlaczego miejsce na dysku nie wzrosło po operacjach na kilku grach?

Znane są co najmniej trzy niezależne przyczyny: fałszywy pomiar przez brak
`compsize`, extenty zachowane przez Snappera oraz mała kompresowalność części
assetów. Dodatkowo starsze operacje nie wykonywały końcowego sync przed
pomiarem. Bez synchronicznej pary `compsize/btrfs du/statvfs` nie da się
przypisać udziałów procentowych.

### 3. Ile z tego wynika ze Snappera?

Nie da się policzyć udziału dla historycznych operacji użytkownika.
Kontrolowany test wykazał jednak mechanizm bezpośrednio: przed defragmentacją
live i snapshot miały po 74 997 760 B `Total`, 0 B `Exclusive` i
74 997 760 B shared; po defragmentacji oba miały po 74 997 760 B
`Exclusive` i 0 B shared. Defragmentacja zerwała sharing, więc snapshot
zachował stary zestaw extentów. To dowodzi kierunku wpływu Snappera, lecz nie
jego liczby dla Batmana/Jedi. Snapshotów użytkownika nie usunięto.

### 4. Ile wynika z błędnego pomiaru?

Dla Metro wiadomo, że pokazane `0 B` było błędne, bo oba pola
`compsize_disk_bytes` są `null`, a późniejszy odczyt pokazał około 0,8 GiB
różnicy. Dla całej biblioteki skali błędu nie da się policzyć z istniejącej
historii, ponieważ brak wiarygodnych baseline’ów.

### 5. Ile wynika z niskiej kompresowalności?

Zależy od gry i jest to główna przyczyna dla części biblioteki. W Jedi pliki
`.ucas` stanowią około 83,4% logicznego rozmiaru, a ich screening ratio to
około 99,9%; cały ważony benchmark daje tylko około 0,83-0,93 GiB punktowej
redukcji Btrfs ze 130,35 GiB. Wiedźmin 3 i Batman Knight również mają mały
procentowy zysk. Przeciwnymi przykładami są Dying Light (ZSTD-3 około
8,83 GiB z 30,52 GiB) i House Flipper (około 4,47 GiB z 11,79 GiB).
Nie istnieje jedna uczciwa wartość procentowa dla całej biblioteki.

### 6. Czy inne profile Btrfs dają zauważalnie lepszy wynik?

Zwykle poziom 9 daje tylko umiarkowany dodatek do poziomu 3. Największa
zmierzona różnica to Dying Light: 8,83 → 9,73 GiB. Dla House było to
4,47 → 4,73 GiB, dla Jedi 0,83 → 0,85 GiB, a dla Asylum około
0,37 → 0,40 GiB. Poziom 15, choć obsługiwany przez badany kernel, daje
jeszcze mniejszy przyrost względem 9 i nie powinien zastępować produkcyjnego
Maximum=9 bez pomiaru czasu/energii. zlib bywał blisko ZSTD albo nieco lepiej
w symulacji blokowej, ale nie tworzy jednej dominującej rekomendacji.

### 7. Czy Btrfs może zbliżyć się dla Jedi do 155 GB → 111 GB?

Nie ma na to poparcia dla obecnej instalacji i badanych trybów. Próba 2 GiB
z 16 392 okien dała około 0,85 GiB dla ZSTD-3/9 i około 1,3 GiB dla
zewnętrznych ZSTD-19/XZ-9; nawet górna granica heurystycznego pasma Btrfs
wynosi około 4,7 GiB, a nie 44 GB. Wynik 512 MiB był niemal identyczny.
Obserwacja użytkownika z LZX pozostaje wiarygodną obserwacją innego kodeka,
innego formatu bloków i być może innego builda gry - ten audyt jej nie
unieważnia. Pokazuje natomiast, że natywny Btrfs ZSTD/zlib nie odtwarza jej
na aktualnych danych Jedi.

### 8. Czy potrzebny jest drugi backend poza natywną kompresją Btrfs?

Warunkowo tak, jeżeli celem produktu ma być duży zysk także dla gier takich
jak Jedi. Natywny Btrfs ma jednak realną wartość dla Dying Light, House
Flipper i części mniejszych tytułów oraz najmniejszy koszt zgodności.
SquashFS/EROFS + overlay, FUSE lub montowane obrazy wymagają osobnego
prototypu aktualizacji, recovery, mmap/xattr i antycheatu. Nie należy
wdrażać ich produkcyjnie przed takim prototypem ani zastępować nimi prostego
backendu Btrfs dla dobrze kompresowalnych gier.

### 9. Jakie backendy są realne bez rozpakowywania całej gry przed startem?

Natywny Btrfs, skompresowany EROFS albo SquashFS z overlayfs, specjalistyczny
FUSE oraz montowany obraz dają dostęp losowy bez pełnego rozpakowania.
Jedynie Btrfs pozostawia jednak zwykłe zapisywalne drzewo bez dodatkowej
warstwy montowania. Pozostałe wymagają dojrzałego upperdir, recovery i
integracji start/stop.

### 10. Jak zachowają się przy Steam, Protonie, antycheacie, zapisach i losowym odczycie?

Btrfs jest najbardziej przezroczysty. Read-only image + overlay może działać
dla wielu zwykłych gier, ale aktualizacje trafiają do upperdir lub wymuszają
przebudowę; narzędzia antycheat mogą odrzucać nietypowe mounty. FUSE ma
największą powierzchnię niezgodności dla mmap, direct I/O, xattr, rename i
fsync. Zapisy gry zwykle są poza katalogiem instalacji, ale nie wolno tego
zakładać dla każdej gry. Każdy backend wymaga macierzy testów tytułów,
Protona, antycheatów i awarii zasilania.

### 11. Czy Game Optimization ma sens jako program odzyskujący miejsce?

Tak, jeśli raportuje twarde wyniki i dobiera metodę do danych zamiast obiecywać
jedną stałą redukcję. Wartość programu może łączyć bezpieczną rekompresję,
wykrywanie reflinkowych duplikatów, pakiety językowe, cache i pozostałości po
aktualizacjach. Warunkiem jest brak fałszywych `0 B` i osobny pomiar efektu
widocznego dla użytkownika.

### 12. Czy kompresja powinna pozostać główną funkcją?

Powinna pozostać ważną, lecz jedną z kilku funkcji optymalizacji storage.
Niektóre gry mają dane dobrze kompresowalne, inne prawie wcale; w tych
drugich większy i bezpieczniejszy zysk może pochodzić z deduplikacji,
nieużywanych paczek albo cache. UI może nadal oferować jeden prosty przepływ
„Analyze → rekomendacja”, a backend dobierać bezpieczną metodę.

## 14. Zalecana kolejność dalszych prac

1. Zachować raporty v1 jako jawnie odrzucone pilotaże i raporty zastąpionych
   rerunów poza zbiorem kanonicznym.
2. Dokończyć trwały model `ProcessEvidence`, prezentację
   `measurement_unavailable` i migrację historycznych fałszywych `0 B`.
3. Rozszerzyć schema v2 o kanoniczny hash raportu i pełny prywatny manifest
   próbek; nie zmieniać już wyników z tego audytu po cichu.
4. Wdrożyć minimalny helper Polkit najpierw tylko dla read-only `compsize`,
   `btrfs du`, `property get` i `statvfs`; przetestować canonicalizację,
   AppID, symlink escape i brak dowolnych poleceń.
5. Powtórzyć kontrolowany test kopii identycznie dla każdego profilu z pełną
   parą uprzywilejowanych `compsize/btrfs du/statvfs`, zachowaniem kodu
   wyjścia każdego procesu i oddzielnym testem snapshot/reflink.
6. Ustalić progi Auto na podstawie wyników per-game: minimalny przewidywany
   zysk, koszt czasu/CPU i dopuszczalny wzrost exclusive po zerwaniu sharingu.
7. Dopiero po potwierdzeniu pomiarów dodać do helpera wąską akcję
   rekompresji. Dla shared extents domyślnie pomijać/blokować ryzykowne pliki
   albo wymagać świadomej decyzji o policzonym ryzyku.
8. Zbudować odizolowany proof-of-concept jednego drugiego backendu tylko dla
   gier, dla których Btrfs ma mały potencjał. Najpierw przetestować Steam
   update/verify, Proton, mmap/xattr, antycheat, crash recovery i widoczny
   przyrost wolnego miejsca.
9. Traktować kompresję jako jedną z metod obok bezpiecznego wykrywania
   duplikatów, pakietów językowych, shader cache i pozostałości po
   aktualizacjach.

Kryterium „udane” dla operacji produkcyjnej powinno wymagać:

- zaakceptowanego raportu schema v2 z poprawnym hashem źródła, stabilnym
  manifestem i fingerprintem inventory;
- zgodności zakresu logicznego;
- wszystkich wymaganych kodów wyjścia `0`;
- potwierdzenia `compression=zstd`;
- wiarygodnych pomiarów przed i po albo jawnego
  `measurement_unavailable`;
- rzeczywistego raportu o wzroście/spadku disk usage;
- raportu o zmianie exclusive/shared i globalnego wolnego miejsca;
- oddzielnego wyniku dla payload reduction i incremental reclaim;
- braku pozostawionych procesów potomnych;
- atomowo zapisanej historii.

## 15. Źródła oficjalne

### Btrfs

- [btrfs-property - właściwość compression i składnia](https://btrfs.readthedocs.io/en/latest/btrfs-property.html)
- [Btrfs - defragmentacja, kompresja i ryzyko zerwania sharingu](https://btrfs.readthedocs.io/en/latest/Defragmentation.html)
- [btrfs(5) - transparentna kompresja, algorytmy i poziomy](https://btrfs.readthedocs.io/en/latest/btrfs-man5.html)
- [btrfs-filesystem - filesystem du, defragment, sync i usage](https://btrfs.readthedocs.io/en/stable/btrfs-filesystem.html)
- [compsize(8) - Disk Usage, Uncompressed, Referenced i wymagania ioctl](https://manpages.debian.org/trixie/btrfs-compsize/compsize.8.en.html)

### Kernel i bezpieczne otwieranie

- [Linux kernel - FIEMAP i `FIEMAP_EXTENT_SHARED`](https://www.kernel.org/doc/html/latest/filesystems/fiemap.html)
- [openat2(2) - `RESOLVE_BENEATH`, `NO_SYMLINKS`, `NO_MAGICLINKS`, `NO_XDEV`](https://www.man7.org/linux/man-pages/man2/openat2.2.html)
- [statvfs(3) - semantyka `f_bavail`](https://man7.org/linux/man-pages/man3/statvfs.3.html)

### Polkit

- [Polkit - dokumentacja projektu](https://polkit.pages.freedesktop.org/polkit/)
- [PolkitAuthority - autoryzacja asynchroniczna i interakcja użytkownika](https://polkit.pages.freedesktop.org/polkit/PolkitAuthority.html)

### Alternatywne systemy/warstwy

- [Linux kernel - OverlayFS](https://docs.kernel.org/filesystems/overlayfs.html)
- [Linux kernel - EROFS](https://docs.kernel.org/filesystems/erofs.html)
- [Linux kernel - SquashFS](https://docs.kernel.org/next/filesystems/squashfs.html)
- [Linux kernel - FUSE passthrough](https://docs.kernel.org/6.16/filesystems/fuse-passthrough.html)
