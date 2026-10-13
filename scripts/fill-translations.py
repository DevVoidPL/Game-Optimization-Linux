#!/usr/bin/env python3
"""Fill translations that pyside6-lupdate left "unfinished" after a sync.

Run pyside6-lupdate first (see TESTING.md / translations README), then this
script, then pyside6-lrelease. It only fills entries that are unfinished:

* en: English source text is its own translation; the few QML texts written
  in Polish get their English translation from EN_FOR_POLISH_SOURCES.
* pl: Polish source text is its own translation; Couch Mode texts come from
  PL_COUCH. Other untranslated Polish entries are left unfinished and listed.
* es: untouched (Qt falls back to the source text).
"""

from __future__ import annotations

from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

TRANSLATIONS = Path(__file__).resolve().parents[1] / "src" / "game_optimization_linux" / "translations"

EN_FOR_POLISH_SOURCES = {
    "Wesprzyj GameOpti": "Support GameOpti",
    "GameOpti tworzę z pasji do Linuksa i gier. Chcę rozwijać program tak, aby był coraz lepszym narzędziem dla graczy korzystających z Linuxa.":
        "I build GameOpti out of a passion for Linux and games. I want to keep improving it into an ever better tool for Linux gamers.",
    "Sam rozwój projektu wymaga jednak czasu oraz korzystania z różnych narzędzi i usług, które pomagają mi tworzyć, testować i rozwijać GameOpti.":
        "Developing the project takes time and relies on various tools and services that help me build, test and improve GameOpti.",
    "Jeżeli GameOpti jest dla Ciebie przydatny i chciałbyś wesprzeć jego dalszy rozwój, możesz zrobić to dobrowolnie poprzez jedną z poniższych opcji. Każde wsparcie naprawdę pomaga :)":
        "If GameOpti is useful to you and you would like to support its further development, you can do so voluntarily with one of the options below. Every bit of support really helps :)",
    "Wsparcie jest całkowicie dobrowolne. GameOpti pozostanie darmowym projektem.":
        "Support is completely voluntary. GameOpti will remain a free project.",
    "Tylko jeden backend proxy może być aktywny dla danej gry. DLSS Enabler działa tylko w trybie wykrywania, dopóki instalator nie zostanie zweryfikowany.":
        "Only one proxy backend can be active for a game. DLSS Enabler works in detection-only mode until its installer is verified.",
    "DLSS Enabler (tylko wykrywanie)": "DLSS Enabler (detection only)",
    "DLSS Enabler jest wykrywany tylko w trybie obserwacji; nie zmieniono plików.":
        "DLSS Enabler is detected in observation mode only; no files were changed.",
    "Aktualizacja FSR Agility SDK": "FSR Agility SDK update",
    "Żąda aktualizacji D3D12 Agility SDK w OptiScalerze. Musi być obecny odpowiadający pakiet D3D12_OptiScaler.":
        "Requests the D3D12 Agility SDK update in OptiScaler. The matching D3D12_OptiScaler package must be present.",
    "Znacznik weryfikacyjny FSR4": "FSR4 verification marker",
    "Żądane: %1. Efektywny INI: %2. Nakładka runtime: %3.": "Requested: %1. Effective INI: %2. Runtime overlay: %3.",
    "Preferowany upscaler DirectX 11": "Preferred DirectX 11 upscaler",
    "Preferowany upscaler DirectX 12": "Preferred DirectX 12 upscaler",
    "Preferowany upscaler Vulkan": "Preferred Vulkan upscaler",
    "Pokazywane są tylko wartości zgłaszane przez zainstalowany INI OptiScaler.":
        "Only values reported by the installed OptiScaler INI are shown.",
    "Oficjalne wydanie OptiScaler": "Official OptiScaler release",
    "Wersja dostępna: %1 | Wersja zainstalowana: %2": "Available version: %1 | Installed version: %2",
    "Sprawdź online": "Check online",
    "Status instalacji": "Installation status",
    "Wersja zainstalowana": "Installed version",
    "Brak": "None",
    "Wersja dostępna": "Available version",
    "Kanał wydania": "Release channel",
    "Nightly (eksperymentalny)": "Nightly (experimental)",
    "Zmień kanał": "Change channel",
    "Sprawdź aktualizacje": "Check for updates",
    "Usuń": "Remove",
    "Tryb FSR 4": "FSR 4 mode",
    "Wybierz wspólny tryb FSR 4 / FSR 4.1. INT8 jest dostępne tylko, gdy deklaruje to zainstalowane wydanie OptiScaler.":
        "Choose the shared FSR 4 / FSR 4.1 mode. INT8 is available only when the installed OptiScaler release declares it.",
    "Automatyczny": "Automatic",
    "Wyłączone": "Disabled",
    "FSR 4.1 INT8 jest niedostępne w tym wydaniu OptiScaler. Zainstalowana wersja nie deklaruje tej funkcji.":
        "FSR 4.1 INT8 is unavailable in this OptiScaler release. The installed version does not declare this feature.",
    "FSR 4.1 INT8 będzie dostępne po sprawdzeniu wybranego wydania OptiScaler.":
        "FSR 4.1 INT8 becomes available after the selected OptiScaler release is checked.",
    "FSR 4.1 INT8 jest obecnie nieaktywne dla tego wydania.": "FSR 4.1 INT8 is currently inactive for this release.",
    "Uruchom grę": "Launch game",
    "Stan dodatku ASI jest odczytywany z zarządzanego manifestu. Kompatybilność pozostaje nieznana, dopóki upstream nie poda pasującego wpisu dla tej gry lub wersji.":
        "The ASI add-on state is read from the managed manifest. Compatibility stays unknown until upstream lists a matching entry for this game or version.",
    "Niedostępne: %1": "Unavailable: %1",
    "OptiScaler nie jest zainstalowany": "OptiScaler is not installed",
    "Konflikt": "Conflict",
    "Zainstalowano · %1": "Installed · %1",
    "Nieznana": "Unknown",
    "Nie zainstalowano": "Not installed",
    "Wersja": "Version",
    "Dostępna": "Available",
    "Zaktualizuj": "Update",
    "Zainstaluj": "Install",
    "Instalacja OptiPatcher nie powiodła się": "OptiPatcher installation failed",
    "Usunięcie OptiPatcher nie powiodło się": "OptiPatcher removal failed",
    "Ukryj zaawansowane ustawienia": "Hide advanced settings",
    "Zaawansowane ustawienia": "Advanced settings",
    "Zainstaluj i skonfiguruj OptiScaler dla tej gry. Zaawansowane ustawienia wstrzykiwania i backendu pozostają ukryte, dopóki nie zostaną wywołane.":
        "Install and configure OptiScaler for this game. Advanced injection and backend settings stay hidden until you open them.",
}
POLISH_SOURCES = set(EN_FOR_POLISH_SOURCES)

# (context, source, disambiguation) -> Polish
PL_COUCH = {
    ("CouchHints", "Move", None): "Ruch",
    ("CouchHints", "Scroll", None): "Przewijanie",
    ("CouchMain", "Adjust", None): "Dostosuj",
    ("CouchMain", "Move", None): "Ruch",
    ("CouchMain", "Choose", None): "Wybierz",
    ("CouchMain", "Change", None): "Zmień",
    ("CouchMain", "Select", None): "Wybierz",
    ("I18n", "Custom games", "launcher source"): "Własne",
}


# (context, source) -> Polish, for the remaining non-Couch desktop texts.
PL_DESKTOP = {
    ('CouchGameDetails', 'Retry, or choose the game window again'): 'Ponów albo wybierz okno gry ponownie',
    ('CouchGameDetails', 'Requires choosing the window again'): 'Wymaga ponownego wyboru okna',
    ('NarratorPage', 'Requires choosing the window again'): 'Wymaga ponownego wyboru okna',
    ('CouchGameDetails', 'The first start requires a one-time window choice with the mouse (preferably in Desktop, while the game is running)'): 'Pierwsze uruchomienie wymaga jednorazowego wyboru okna myszką (najlepiej w Desktop, gdy gra już działa)',
    ('CouchGameDetails', 'Choose window again'): 'Wybierz okno ponownie',
    ('NarratorPage', 'Choose window again'): 'Wybierz okno ponownie',
    ('CouchGameDetails', 'Saved'): 'Zapisane',
    ('CouchGameDetails', 'Not chosen'): 'Nie wybrano',
    ('CouchGameDetails', 'Forget the saved window for this game only'): 'Zapomnij zapisane okno tylko dla tej gry',
    ('NarratorPage', 'Forget the saved window for this game only'): 'Zapomnij zapisane okno tylko dla tej gry',
    ('NarratorPage', 'Retry'): 'Ponów',
    ('I18n', 'The capture session was closed (the chosen window disappeared or the choice was cancelled)'): 'Sesja przechwytywania została zamknięta (wybrane okno zniknęło albo wybór anulowano)',
    ('I18n', 'Check the preview - it should show the game'): 'Sprawdź podgląd - powinien pokazywać grę',
    ('CouchGameDetails', 'The first start asks you to choose the game window once; it is remembered'): 'Przy pierwszym uruchomieniu trzeba raz wybrać okno gry; wybór zostanie zapamiętany',
    ('NarratorPage', 'The first start asks you to choose the game window once; it is remembered'): 'Przy pierwszym uruchomieniu trzeba raz wybrać okno gry; wybór zostanie zapamiętany',
    ('CouchGameDetails', 'Starts automatically with the game; you can also start it now'): 'Uruchomi się automatycznie z grą; możesz też uruchomić go teraz',
    ('CouchGameDetails', 'This launcher cannot be detected; start the Narrator manually'): 'Nie można wykryć gry z tego launchera; uruchom Lektora ręcznie',
    ('CouchGameDetails', 'Voice: %1'): 'Głos: %1',
    ('I18n', 'screen capture'): 'przechwytywanie ekranu',
    ('I18n', 'subtitle OCR'): 'OCR napisów',
    ('I18n', 'translation'): 'tłumaczenie',
    ('I18n', 'voice'): 'głos',
    ('I18n', 'audio output'): 'wyjście audio',
    ('I18n', 'Error: %1'): 'Błąd: %1',
    ('I18n', 'On · missing: %1'): 'Włączony · brakuje: %1',
    ('I18n', 'On · starts with the game'): 'Włączony · uruchomi się z grą',
    ('I18n', 'On · start the Narrator manually'): 'Włączony · uruchom Lektora ręcznie',
    ('I18n', 'Off'): 'Wyłączony',
    ('I18n', 'Narrator started with the game'): 'Lektor uruchomił się z grą',
    ('I18n', 'Narrator started with the game. Choose the game window once; the choice is remembered'): 'Lektor uruchomił się z grą. Wybierz raz okno gry; wybór zostanie zapamiętany',
    ('OptiScalerSection', 'Cancel'): 'Anuluj',
    ('CouchGameDetails', 'Retry: %1'): 'Ponów: %1',
    ('CouchGameDetails', 'Official release from the internet · confirmation required'): 'Oficjalne wydanie z internetu · wymaga potwierdzenia',
    ('CouchGameDetails', 'Retry'): 'Ponów',
    ('CouchGameDetails', 'Inconsistent installation'): 'Niespójna instalacja',
    ('CouchGameDetails', 'Review in Desktop Mode'): 'Sprawdź w trybie Desktop',
    ('CouchGameDetails', 'Replace existing files?'): 'Zastąpić istniejące pliki?',
    ('CouchGameDetails', 'Installing would replace %1 existing file(s) that Game Optimization did not create:'): 'Instalacja zastąpi istniejące pliki, których nie utworzył Game Optimization (%1):',
    ('CouchGameDetails', '…and %1 more'): '…i %1 więcej',
    ('CouchGameDetails', 'Each replaced file is backed up first and can be restored later.'): 'Każdy zastępowany plik zostanie najpierw zapisany w kopii zapasowej i można go później przywrócić.',
    ('CouchGameDetails', 'Back up and replace'): 'Utwórz kopię i zastąp',
    ('I18n', 'Could not reach GitHub. Check the internet connection and try again'): 'Nie udało się połączyć z GitHubem. Sprawdź połączenie z internetem i spróbuj ponownie',
    ('I18n', 'Confirm replacing the listed files to continue'): 'Potwierdź zastąpienie wymienionych plików, aby kontynuować',
    ('I18n', 'The game was moved to another Steam library. Confirm the new location before changing OptiScaler files'): 'Gra została przeniesiona do innej biblioteki Steam. Potwierdź nową lokalizację przed zmianą plików OptiScalera',
    ('I18n', 'The OptiScaler installation is inconsistent: the recorded files were not found in the current game directory. Nothing was changed'): 'Instalacja OptiScalera jest niespójna: zapisanych plików nie znaleziono w obecnym katalogu gry. Niczego nie zmieniono',
    ('I18n', 'The game was moved to another Steam library; confirm the new location'): 'Gra została przeniesiona do innej biblioteki Steam; potwierdź nową lokalizację',
    ('I18n', 'Inconsistent installation: recorded files are missing in the current game directory'): 'Niespójna instalacja: brakuje zapisanych plików w obecnym katalogu gry',
    ('I18n', 'The game directory is not writable. In Flatpak, grant access to this Steam library, then try again'): 'Brak zapisu do katalogu gry. We Flatpaku nadaj dostęp do tej biblioteki Steam i spróbuj ponownie',
    ('I18n', 'game directory is unavailable'): 'katalog gry jest niedostępny',
    ('I18n', 'A game file or directory is not accessible'): 'Plik lub katalog gry jest niedostępny',
    ('I18n', 'Checking OptiScaler took too long'): 'Sprawdzanie OptiScalera trwało zbyt długo',
    ('I18n', 'The installation can only be relocated when every recorded file matches in the new game directory'): 'Lokalizację instalacji można zmienić tylko wtedy, gdy każdy zapisany plik zgadza się w nowym katalogu gry',
    ('I18n', 'conflicting files require confirmation'): 'konfliktujące pliki wymagają potwierdzenia',
    ('I18n', 'select a local OptiScaler archive'): 'wybierz lokalne archiwum OptiScalera',
    ('I18n', 'this 7z archive uses BCJ2 and the bundled extractor is unavailable'): 'to archiwum 7z używa BCJ2, a narzędzie do rozpakowania jest niedostępne',
    ('I18n', 'Check the official OptiScaler release before creating an installation plan'): 'Sprawdź oficjalne wydanie OptiScalera przed utworzeniem planu instalacji',
    ('I18n', 'Check the official OptiScaler release before installation'): 'Sprawdź oficjalne wydanie OptiScalera przed instalacją',
    ('I18n', 'Select an available Steam game first'): 'Najpierw wybierz dostępną grę Steam',
    ('I18n', 'OptiScaler is not installed'): 'OptiScaler nie jest zainstalowany',
    ('I18n', 'A Steam update is currently active'): 'Trwa aktualizacja Steam',
    ('I18n', 'OptiScaler operation cancelled'): 'Anulowano operację OptiScalera',
    ('I18n', 'OptiScaler is not installed for this game'): 'OptiScaler nie jest zainstalowany dla tej gry',
    ('I18n', 'OptiScaler operation was cancelled'): 'Operacja OptiScalera została anulowana',
    ('I18n', 'An OptiScaler task for this game is already active'): 'Zadanie OptiScalera dla tej gry już trwa',
    ('I18n', 'GitHub API rate limit reached; try again in %1 min'): 'Limit zapytań GitHuba - spróbuj za %1 min',
    ('I18n', 'Downloading the official OptiScaler release failed: %1'): 'Pobieranie oficjalnego wydania OptiScalera nie powiodło się: %1',
    ('I18n', 'A game file or directory is not accessible: %1'): 'Plik lub katalog gry jest niedostępny: %1',
    ('I18n', 'OptiScaler operation failed: %1'): 'Operacja OptiScalera nie powiodła się: %1',
    ('I18n', 'Failed to refresh OptiScaler status: %1'): 'Nie udało się odświeżyć stanu OptiScalera: %1',
    ('OptiScalerSection', 'Inconsistent installation'): 'Niespójna instalacja',
    ('OptiScalerSection', 'Nightly / other channel'): 'Nightly / inny kanał',
    ('OptiScalerSection', 'Choose the main game executable first'): 'Najpierw wybierz główny plik wykonywalny gry',
    ('OptiScalerSection', 'Retry'): 'Ponów',
    ('OptiScalerSection', 'The game was moved to another Steam library. The recorded OptiScaler files were found unchanged in the new location.'): 'Gra została przeniesiona do innej biblioteki Steam. Zapisane pliki OptiScalera znaleziono bez zmian w nowej lokalizacji.',
    ('OptiScalerSection', 'Inconsistent installation: the recorded OptiScaler files were not found unchanged in the current game directory. Nothing is removed automatically.'): 'Niespójna instalacja: zapisanych plików OptiScalera nie znaleziono bez zmian w obecnym katalogu gry. Nic nie jest usuwane automatycznie.',
    ('OptiScalerSection', 'Recorded: %1\nCurrent: %2'): 'Zapisano: %1\nObecnie: %2',
    ('OptiScalerSection', 'Use the new location'): 'Użyj nowej lokalizacji',
    ('OptiScalerSection', 'Installing would replace %1 existing file(s) that Game Optimization did not create.'): 'Instalacja zastąpi istniejące pliki, których nie utworzył Game Optimization (%1).',
    ('OptiScalerSection', '…and %1 more'): '…i %1 więcej',
    ('OptiScalerSection', 'Each replaced file is backed up first and can be restored later.'): 'Każdy zastępowany plik zostanie najpierw zapisany w kopii zapasowej i można go później przywrócić.',
    ('OptiScalerSection', 'Back up and replace the listed files'): 'Utwórz kopię zapasową i zastąp wymienione pliki',
    ('OptiScalerSection', 'Continue installation'): 'Kontynuuj instalację',
    ('DonatePage', '☕ Buy Me a Coffee'): '☕ Buy Me a Coffee',
    ('DonatePage', 'PayPal'): 'PayPal',
    ('GameDetailsPage', 'Launch is unavailable'): 'Uruchomienie jest niedostępne',
    ('GameDetailsPage', 'Edit custom game'): 'Edytuj własną grę',
    ('GameDetailsPage', 'Remove custom game'): 'Usuń własną grę',
    ('GameDetailsPage', 'Remove custom game?'): 'Usunąć własną grę?',
    ('GameDetailsPage', 'This removes only the Game Optimization Linux entry. Game files and artwork are not deleted.'): 'Usuwany jest tylko wpis w Game Optimization Linux. Pliki gry i grafiki nie zostaną usunięte.',
    ('GameDetailsPage', 'Remove'): 'Usuń',
    ('GameDetailsPage', 'OptiScaler'): 'OptiScaler',
    ('GameDetailsPage', 'Full optimization launch integration for this launcher is not available yet'): 'Pełna integracja uruchamiania z optymalizacją nie jest jeszcze dostępna dla tego launchera',
    ('GamesPage', 'Manual/Custom'): 'Ręczne/własne',
    ('ManualGameDialog', 'Manual game configuration is unavailable.'): 'Konfiguracja gier ręcznych jest niedostępna.',
    ('ManualGameDialog', 'Manual game was not found.'): 'Nie znaleziono gry ręcznej.',
    ('ManualGameDialog', 'Manual game storage is unavailable.'): 'Magazyn gier ręcznych jest niedostępny.',
    ('ManualGameDialog', 'The game could not be saved.'): 'Nie udało się zapisać gry.',
    ('ManualGameDialog', 'Edit custom game'): 'Edytuj własną grę',
    ('ManualGameDialog', 'Add custom game'): 'Dodaj własną grę',
    ('ManualGameDialog', 'Cancel'): 'Anuluj',
    ('ManualGameDialog', 'Save'): 'Zapisz',
    ('ManualGameDialog', 'Add game'): 'Dodaj grę',
    ('ManualGameDialog', 'Name *'): 'Nazwa *',
    ('ManualGameDialog', 'Executable / command *'): 'Plik wykonywalny / polecenie *',
    ('ManualGameDialog', 'Browse…'): 'Przeglądaj…',
    ('ManualGameDialog', 'Game directory'): 'Katalog gry',
    ('ManualGameDialog', 'Defaults to the executable directory'): 'Domyślnie katalog pliku wykonywalnego',
    ('ManualGameDialog', 'Working directory'): 'Katalog roboczy',
    ('ManualGameDialog', 'Defaults to the game directory'): 'Domyślnie katalog gry',
    ('ManualGameDialog', 'Arguments'): 'Argumenty',
    ('ManualGameDialog', 'Example: --mode "High quality"'): 'Przykład: --mode "High quality"',
    ('ManualGameDialog', 'Wine / Proton executable and options'): 'Plik wykonywalny Wine / Proton i opcje',
    ('ManualGameDialog', 'Optional; kept separate from the game executable'): 'Opcjonalne; przechowywane oddzielnie od pliku gry',
    ('ManualGameDialog', 'Wine prefix'): 'Prefiks Wine',
    ('ManualGameDialog', 'Portrait artwork'): 'Okładka pionowa',
    ('ManualGameDialog', 'Header artwork'): 'Grafika nagłówka',
    ('ManualGameDialog', 'Hide advanced'): 'Ukryj zaawansowane',
    ('ManualGameDialog', 'Advanced'): 'Zaawansowane',
    ('ManualGameDialog', 'Environment variables'): 'Zmienne środowiskowe',
    ('ManualGameDialog', 'KEY'): 'KLUCZ',
    ('ManualGameDialog', 'Value (may be empty)'): 'Wartość (może być pusta)',
    ('ManualGameDialog', 'Remove'): 'Usuń',
    ('ManualGameDialog', 'Add variable'): 'Dodaj zmienną',
    ('ManualGameDialog', 'Pre-launch command'): 'Polecenie przed uruchomieniem',
    ('ManualGameDialog', 'Parsed as argv; the game stops if this fails'): 'Przetwarzane jako argv; gra nie uruchomi się, jeśli się nie powiedzie',
    ('ManualGameDialog', 'Post-launch command'): 'Polecenie po zakończeniu',
    ('ManualGameDialog', 'Parsed as argv and run after the game exits'): 'Przetwarzane jako argv i uruchamiane po zamknięciu gry',
    ('ManualGameDialog', 'Select game executable'): 'Wybierz plik wykonywalny gry',
    ('ManualGameDialog', 'Select Wine or Proton executable'): 'Wybierz plik wykonywalny Wine lub Proton',
    ('ManualGameDialog', 'Select portrait artwork'): 'Wybierz okładkę pionową',
    ('ManualGameDialog', 'Images (*.png *.jpg *.jpeg *.webp)'): 'Obrazy (*.png *.jpg *.jpeg *.webp)',
    ('ManualGameDialog', 'Select header artwork'): 'Wybierz grafikę nagłówka',
    ('ManualGameDialog', 'Select game directory'): 'Wybierz katalog gry',
    ('ManualGameDialog', 'Select working directory'): 'Wybierz katalog roboczy',
    ('ManualGameDialog', 'Select Wine prefix'): 'Wybierz prefiks Wine',
    ('NarratorPage', 'Not negotiated'): 'Nie uzgodniono',
    ('NarratorPage', '%1 (after %2 failed)'): '%1 (po nieudanym %2)',
    ('NarratorPage', '%1 (%2 sampling interval, %3 OCR busy)'): '%1 (%2 z interwału próbkowania, %3 z zajętego OCR)',
    ('NarratorPage', '%1 (played %2 of %3)'): '%1 (odtworzono %2 z %3)',
    ('NarratorPage', 'seen %1 -> accepted %2 -> spoken %3 -> finished %4'): 'widoczne %1 -> przyjęte %2 -> wypowiedziane %3 -> zakończone %4',
    ('NarratorPage', 'empty %1, no strong line %2, lost despite strong line %3, duplicate %4, abandoned %5, superseded %6'): 'puste %1, brak pewnej linii %2, utracone mimo pewnej linii %3, duplikaty %4, porzucone %5, zastąpione %6',
    ('NarratorPage', 'None attempted'): 'Brak prób',
    ('NarratorPage', ' (GL available)'): ' (GL dostępny)',
    ('NarratorPage', ' (no GL)'): ' (bez GL)',
    ('NarratorPage', 'Advanced voice articulation'): 'Zaawansowana artykulacja głosu',
    ('NarratorPage', "Piper inference parameters. Lower values reduce variation. Leave this off to use each voice's own values."): 'Parametry wnioskowania Piper. Niższe wartości zmniejszają zmienność. Pozostaw wyłączone, aby używać wartości własnych każdego głosu.',
    ('NarratorPage', 'Phoneme duration variation'): 'Zmienność długości fonemów',
    ('NarratorPage', 'Acoustic variation'): 'Zmienność akustyczna',
    ('NarratorPage', 'Hide recent OCR decisions'): 'Ukryj ostatnie decyzje OCR',
    ('NarratorPage', 'Recent OCR decisions (%1)'): 'Ostatnie decyzje OCR (%1)',
    ('NarratorPage', '%1 s · confidence %2 · candidate %3/%4 · %5'): '%1 s · pewność %2 · kandydat %3/%4 · %5',
    ('NarratorPage', 'Raw: %1'): 'Surowe: %1',
    ('NarratorPage', 'Filtered: %1 · normalized: %2'): 'Filtrowane: %1 · znormalizowane: %2',
    ('NarratorPage', 'Reason: %1 · match: %2 · replaced: %3 · TTS: %4 · ROI: %5x%6 · %7 / %8'): 'Powód: %1 · dopasowanie: %2 · zastąpione: %3 · TTS: %4 · ROI: %5x%6 · %7 / %8',
    ('NarratorPage', 'yes'): 'tak',
    ('NarratorPage', 'no'): 'nie',
    ('NarratorPage', 'submitted'): 'wysłane',
    ('NarratorPage', 'not submitted'): 'niewysłane',
    ('NarratorPage', 'Tokens: %1/%2 · lines: %3 · dropped: %4 · minimum confidence: %5 · geometry: %6 · strong short evidence: %7 · visual: %8 · filter: %9'): 'Tokeny: %1/%2 · linie: %3 · odrzucone: %4 · minimalna pewność: %5 · geometria: %6 · silne krótkie dowody: %7 · obraz: %8 · filtr: %9',
    ('NarratorPage', 'coherent'): 'spójny',
    ('NarratorPage', 'uncertain'): 'niepewny',
    ('NarratorPage', 'unchanged'): 'bez zmian',
    ('NarratorPage', 'Subtitle on screen to speech: %1'): 'Od napisu na ekranie do mowy: %1',
    ('NarratorPage', 'Consensus wait: %1'): 'Oczekiwanie na zgodność: %1',
    ('NarratorPage', 'Phrase accepted to speech: %1'): 'Od przyjęcia frazy do mowy: %1',
    ('NarratorPage', 'Confirming frame to speech: %1'): 'Od potwierdzającej klatki do mowy: %1',
    ('NarratorPage', 'Frames skipped by rate limit: %1'): 'Klatki pominięte przez limit częstotliwości: %1',
    ('NarratorPage', 'Subtitle funnel: %1'): 'Lejek napisów: %1',
    ('NarratorPage', 'Lost at: %1'): 'Utracone na etapie: %1',
    ('NarratorPage', 'Superseded in audio queue: %1'): 'Zastąpione w kolejce audio: %1',
    ('NarratorPage', 'Playback completed: %1'): 'Odtwarzanie zakończone: %1',
    ('NarratorPage', 'Playback interrupted: %1'): 'Odtwarzanie przerwane: %1',
    ('NarratorPage', 'Last playback: %1'): 'Ostatnie odtwarzanie: %1',
    ('NarratorPage', 'Frames received: %1'): 'Odebrane klatki: %1',
    ('NarratorPage', 'Capture format: %1'): 'Format przechwytywania: %1',
    ('NarratorPage', 'Capture variants tried: %1'): 'Wypróbowane warianty przechwytywania: %1',
    ('NarratorPage', 'Capture stream errors: %1'): 'Błędy strumienia przechwytywania: %1',
    ('NarratorPage', 'Capture restarts: %1'): 'Ponowne uruchomienia przechwytywania: %1',
    ('NarratorPage', 'Filtered OCR: %1'): 'Filtrowany OCR: %1',
    ('OptiScalerSection', 'Backend runtime'): 'Środowisko backendu',
    ('OptiScalerSection', 'GOL-created files will be removed and verified original game files will be restored. Unknown modified binaries are preserved and block removal for review.'): 'Pliki utworzone przez GOL zostaną usunięte, a zweryfikowane oryginalne pliki gry przywrócone. Nieznane zmodyfikowane pliki binarne zostaną zachowane i zablokują usuwanie do sprawdzenia.',
    ('OptiScalerSection', 'FSR 4.1.1'): 'FSR 4.1.1',
    ('OptiScalerSection', 'FSR 4.1.1 INT8 - Experimental'): 'FSR 4.1.1 INT8 - eksperymentalne',
    ('OptiScalerSection', 'Disabled'): 'Wyłączone',
    ('OptiScalerSection', 'Partial installation'): 'Częściowa instalacja',
    ('OptiScalerSection', 'Status refresh failed: %1. Last known installation information is still shown.'): 'Odświeżenie stanu nie powiodło się: %1. Nadal widoczne są ostatnie znane informacje o instalacji.',
    ('OptiScalerSection', 'No diagnostic was returned'): 'Nie zwrócono diagnostyki',
    ('OptiScalerSection', 'Refresh error: %1. Last known installation information is shown.'): 'Błąd odświeżania: %1. Widoczne są ostatnie znane informacje o instalacji.',
    ('OptiScalerSection', 'The release channel could not be changed'): 'Nie udało się zmienić kanału wydania',
    ('OptiScalerSection', 'The backend could not be changed'): 'Nie udało się zmienić backendu',
    ('OptiScalerSection', 'The official release check was started. Apply the recommendation when the download is ready.'): 'Rozpoczęto sprawdzanie oficjalnego wydania. Zastosuj zalecenie, gdy pobieranie będzie gotowe.',
    ('OptiScalerSection', 'Review the file conflict beside the install action and confirm replacement before continuing.'): 'Sprawdź konflikt plików obok akcji instalacji i potwierdź zastąpienie przed kontynuacją.',
    ('OptiScalerSection', 'The OptiScaler install and configuration task could not be started'): 'Nie udało się uruchomić zadania instalacji i konfiguracji OptiScalera',
    ('OptiScalerSection', 'The OptiScaler configuration could not be saved'): 'Nie udało się zapisać konfiguracji OptiScalera',
    ('OptiScalerSection', 'Install OptiScaler + apply recommended FSR'): 'Zainstaluj OptiScaler + zastosuj zalecany FSR',
    ('OptiScalerSection', 'Install OptiScaler + apply selected FSR'): 'Zainstaluj OptiScaler + zastosuj wybrany FSR',
    ('OptiScalerSection', 'Request FSR 4.1.1 INT8'): 'Zażądaj FSR 4.1.1 INT8',
    ('OptiScalerSection', 'Upgrade to FSR 4'): 'Przejdź na FSR 4',
    ('OptiScalerSection', 'Disable FSR4 update'): 'Wyłącz aktualizację FSR4',
    ('OptiScalerSection', 'Apply automatic recommendation'): 'Zastosuj automatyczne zalecenie',
    ('OptiScalerSection', 'The OptiScaler verification task could not be started'): 'Nie udało się uruchomić zadania weryfikacji OptiScalera',
    ('OptiScalerSection', 'OptiScaler status'): 'Stan OptiScalera',
    ('OptiScalerSection', 'OptiScaler'): 'OptiScaler',
    ('OptiScalerSection', 'Stable'): 'Stabilny',
    ('OptiScalerSection', 'FSR 4'): 'FSR 4',
    ('OptiScalerSection', 'OptiPatcher'): 'OptiPatcher',
    ('OptiScalerSection', 'Status'): 'Stan',
    ('OptiScalerSummaryCard', 'FSR4 INT8 Experimental'): 'FSR4 INT8 eksperymentalne',
    ('OptiScalerSummaryCard', 'FSR 4.1.1'): 'FSR 4.1.1',
    ('OptiScalerSummaryCard', 'Disabled'): 'Wyłączone',
    ('OptiScalerSummaryCard', 'Automatic'): 'Automatycznie',
    ('OptiScalerSummaryCard', 'Installed'): 'Zainstalowano',
    ('OptiScalerSummaryCard', 'Damaged'): 'Uszkodzony',
    ('OptiScalerSummaryCard', 'Partial'): 'Częściowy',
    ('OptiScalerSummaryCard', 'Not installed'): 'Nie zainstalowano',
    ('OptiScalerSummaryCard', 'Unknown'): 'Nieznany',
    ('OptiScalerSummaryCard', 'OptiScaler'): 'OptiScaler',
    ('OptiScalerSummaryCard', 'Detecting…'): 'Wykrywanie…',
    ('OptiScalerSummaryCard', 'No'): 'Nie',
    ('OptiScalerSummaryCard', 'FSR'): 'FSR',
    ('OptiScalerSummaryCard', 'Configured'): 'Skonfigurowano',
    ('OptiScalerSummaryCard', 'Open OptiScaler'): 'Otwórz OptiScaler',
    ('OptiScalerTab', 'OptiScaler'): 'OptiScaler',
    ('Sidebar', 'Donate'): 'Wesprzyj',
    ('SystemPage', 'About and diagnostics'): 'Informacje i diagnostyka',
    ('SystemPage', 'A privacy-safe summary for support reports'): 'Podsumowanie bez danych prywatnych do zgłoszeń',
    ('SystemPage', 'Flatpak'): 'Flatpak',
    ('SystemPage', 'Native'): 'Natywna',
    ('SystemPage', 'App commit: %1'): 'Commit aplikacji: %1',
    ('SystemPage', 'Gaming environment'): 'Środowisko gier',
    ('SystemPage', 'Copy system info'): 'Kopiuj informacje o systemie',
    ('SystemPage', 'System information copied'): 'Skopiowano informacje o systemie',
    ('OptiScalerSection', 'Image scaling'): 'Skalowanie obrazu',
    ('OptiScalerSection', 'Check online'): 'Sprawdź online',
}


def _finish(translation: ET.Element, text: str) -> None:
    translation.text = text
    translation.attrib.pop("type", None)


def fill(code: str) -> list[tuple[str, str]]:
    path = TRANSLATIONS / f"game_optimization_{code}.ts"
    tree = ET.parse(path)
    left: list[tuple[str, str]] = []
    for context in tree.getroot().findall("context"):
        name = context.findtext("name") or ""
        for message in context.findall("message"):
            translation = message.find("translation")
            if translation is None or translation.get("type") != "unfinished":
                continue
            source = message.findtext("source") or ""
            key = (name, source, message.findtext("comment"))
            if code == "en":
                _finish(translation, EN_FOR_POLISH_SOURCES.get(source, source))
            elif code == "pl" and key in PL_COUCH:
                _finish(translation, PL_COUCH[key])
            elif code == "pl" and (name, source) in PL_DESKTOP:
                _finish(translation, PL_DESKTOP[(name, source)])
            elif code == "pl" and source in POLISH_SOURCES:
                _finish(translation, source)
            else:
                left.append((name, source))
    text = ET.tostring(tree.getroot(), encoding="unicode")
    path.write_text('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' + text + "\n", encoding="utf-8")
    return left


if __name__ == "__main__":
    for code in sys.argv[1:] or ("en", "pl"):
        remaining = fill(code)
        couch = [item for item in remaining if item[0].startswith("Couch")]
        print(f"{code}: {len(remaining)} left unfinished ({len(couch)} in Couch Mode)")
