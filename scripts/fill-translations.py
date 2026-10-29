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
    ('DlssEnablerPanel', 'The DLSS Enabler configuration could not be saved'): 'Nie udało się zapisać konfiguracji DLSS Enablera',
    ('DlssEnablerPanelContent', 'Runtime verified'): 'Działanie potwierdzone w logu',
    ('DlssEnablerPanelContent', 'Selected · runtime not verified'): 'Wybrano · działanie niepotwierdzone',
    ('DlssEnablerPanelContent', 'Installed · not selected'): 'Zainstalowano · niewybrany',
    ('DlssEnablerPanelContent', 'Error'): 'Błąd',
    ('DlssEnablerPanelContent', 'DLSS Enabler release %1 (module %2) replaces OptiScaler for this game and bundles OptiScaler %3. FSR 4 INT8 Lite is unavailable while this backend is selected. Switching remains fully reversible.'): 'Wydanie DLSS Enabler %1 (moduł %2) zastępuje OptiScaler dla tej gry i zawiera OptiScaler %3. FSR 4 INT8 Lite jest niedostępne, gdy wybrano ten backend. Przełączanie pozostaje w pełni odwracalne.',
    ('DlssEnablerPanelContent', 'The current installation produced a fresh DLSS Enabler runtime log.'): 'Bieżąca instalacja utworzyła świeży log działania DLSS Enablera.',
    ('DlssEnablerPanelContent', 'Installed and selected. Runtime operation is not confirmed until the game produces a fresh DLSS Enabler log.'): 'Zainstalowano i wybrano. Działanie nie jest potwierdzone, dopóki gra nie utworzy świeżego logu DLSS Enablera.',
    ('DlssEnablerPanelContent', 'The game process and graphics runtime were verified with this installation.'): 'Proces gry i środowisko graficzne zostały potwierdzone dla tej instalacji.',
    ('DlssEnablerPanelContent', 'Installed and selected. Runtime operation is not confirmed until a game launch produces fresh runtime evidence.'): 'Zainstalowano i wybrano. Działanie nie jest potwierdzone, dopóki uruchomienie gry nie dostarczy świeżego dowodu działania.',
    ('DlssEnablerPanelContent', 'Native game DLSS copied as _nvngx.dll: %1'): 'Natywna biblioteka DLSS gry skopiowana jako _nvngx.dll: %1',
    ('DlssEnablerPanelContent', 'DX12 upscaler'): 'Upscaler DX12',
    ('DlssEnablerPanelContent', 'Automatic'): 'Automatyczny',
    ('DlssEnablerPanelContent', "AMD/Intel support installed: dxgi.dll, nvapi64-proxy.dll, dlss-finder.exe and the game's native DLSS copied as _nvngx.dll. Proton override: version=n,b;dxgi=n,b."): 'Zainstalowano obsługę AMD/Intel: dxgi.dll, nvapi64-proxy.dll, dlss-finder.exe oraz natywną bibliotekę DLSS gry skopiowaną jako _nvngx.dll. Override Protona: version=n,b;dxgi=n,b.',
    ('DlssEnablerPanelContent', 'Supported by the installed Enabler module and bundled OptiScaler.'): 'Obsługiwane przez zainstalowany moduł Enablera i dołączony OptiScaler.',
    ('DlssEnablerPanelContent', 'Frame generation'): 'Generowanie klatek',
    ('DlssEnablerPanelContent', 'Automatic or FSR 3, as supported by Enabler 3.00.000.0.'): 'Tryb automatyczny lub FSR 3, zgodnie z obsługą Enablera 3.00.000.0.',
    ('DlssEnablerPanelContent', 'Runtime logging'): 'Logowanie działania',
    ('DlssEnablerPanelContent', 'Keeps local Enabler and OptiScaler logs for verification and diagnostics.'): 'Zachowuje lokalne logi Enablera i OptiScalera do weryfikacji i diagnostyki.',
    ('DlssEnablerPanelContent', 'Switch this game to DLSS Enabler? GameOpti will verify the installed files and will report runtime verification only after a fresh log is produced by the game.'): 'Przełączyć tę grę na DLSS Enabler? GameOpti zweryfikuje zainstalowane pliki, a potwierdzenie działania zgłosi dopiero po utworzeniu świeżego logu przez grę.',
    ('CouchGameDetails', 'Runtime verified'): 'Działanie potwierdzone w logu',
    ('CouchGameDetails', 'Selected · runtime not verified'): 'Wybrano · działanie niepotwierdzone',
    ('CouchGameDetails', 'Error'): 'Błąd',
    ('CouchGameDetails', 'Interrupted'): 'Przerwano',
    ('CouchGameDetails', 'DX12 upscaler: %1'): 'Upscaler DX12: %1',
    ('CouchGameDetails', 'Cycle a setting supported by Enabler 3.00.000.0'): 'Zmień ustawienie obsługiwane przez Enabler 3.00.000.0',
    ('CouchGameDetails', 'Runtime logging: %1'): 'Logowanie działania: %1',
    ('CouchGameDetails', 'Diagnostics'): 'Diagnostyka',
    ('CouchGameDetails', 'Local logs are used for runtime verification'): 'Lokalne logi służą do potwierdzenia działania',
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
    ('I18n', 'Automatic recording is not available for Lutris games yet. Record a MangoHud log in the game (logging key) and use Import MangoHud log'): 'Automatyczne nagrywanie nie jest jeszcze dostępne dla gier Lutris. Nagraj log MangoHud w grze (klawisz logowania) i użyj „Importuj log MangoHud”',
    ('I18n', 'A baseline recording is already active for this game'): 'Nagrywanie pomiaru bazowego dla tej gry już trwa',
    ('I18n', 'Baseline recording exceeded the four-hour MangoHud limit'): 'Nagrywanie pomiaru bazowego przekroczyło czterogodzinny limit MangoHud',
    ('I18n', 'Close %1 first; GameOpti starts it for the measurement'): 'Najpierw zamknij %1; GameOpti uruchomi go na potrzeby pomiaru',
    ('I18n', 'GameOpti cannot see running games here, so it could not tell when the game ends'): 'GameOpti nie widzi tutaj uruchomionych gier, więc nie rozpoznałby końca gry',
    ('I18n', "Gamescope cannot reach the game inside the UMU container of the Heroic Flatpak; GameOpti turns on Heroic's Disable UMU option for this game while Gamescope is used (Proton runs without the Steam Runtime container and UMU game fixes)"): 'Gamescope nie dociera do gry wewnątrz kontenera UMU w Heroic Flatpak; gdy używany jest Gamescope, GameOpti włącza dla tej gry opcję Heroica „Wyłącz UMU” (Proton działa bez kontenera Steam Runtime i bez poprawek gier UMU)',
    ('I18n', 'No measurement is running for this game'): 'Dla tej gry nie trwa żaden pomiar',
    ('I18n', 'Recording cancelled'): 'Nagrywanie anulowane',
    ('I18n', 'The game did not start within %1 seconds after %2 was opened'): 'Gra nie uruchomiła się w ciągu %1 s od otwarcia %2',
    ('I18n', 'The game ran, but MangoHud did not write a log; check that MangoHud is available in %1 and that the game uses Vulkan, DXVK or VKD3D'): 'Gra działała, ale MangoHud nie zapisał logu; sprawdź, czy MangoHud jest dostępny w %1 i czy gra używa Vulkana, DXVK lub VKD3D',
    ('OptimizationTab', 'Automatic recording is not available for Lutris games yet'): 'Automatyczne nagrywanie nie jest jeszcze dostępne dla gier Lutris',
    ('OptimizationTab', 'Automatic recording is not available for Lutris games yet. Record a MangoHud log in the game (logging key) and use Import MangoHud log'): 'Automatyczne nagrywanie nie jest jeszcze dostępne dla gier Lutris. Nagraj log MangoHud w grze (klawisz logowania) i użyj „Importuj log MangoHud”',
    ('OptimizationTab', 'Cancel recording'): 'Anuluj nagrywanie',
    ('OptimizationTab', 'Close %1 first. GameOpti starts the game through %1 with a private MangoHud logging configuration for this one launch, records until the game exits and then removes that configuration again.'): 'Najpierw zamknij %1. GameOpti uruchomi grę przez %1 z prywatną konfiguracją logowania MangoHud tylko na to jedno uruchomienie, nagrywa do wyjścia z gry, a potem usuwa tę konfigurację.',
    ('OptimizationTab', 'Starts the game through %1 and records until you exit the game'): 'Uruchamia grę przez %1 i nagrywa do wyjścia z gry',
    ('OptimizationTab', 'Waiting for the game to start in %1...'): 'Czekanie na uruchomienie gry w %1...',
    ('OptimizationTab', 'Automatic recording is available for Steam and local games only'): 'Automatyczne nagrywanie jest dostępne tylko dla gier Steam i gier lokalnych',
    ('OptimizationTab', 'Automatic recording works only for Steam games and local games: the measurement runner is started by Steam Launch Options, while Heroic and Lutris start the game themselves. Record a MangoHud log in the game (logging key) and use Import MangoHud log'): 'Automatyczne nagrywanie działa tylko dla gier Steam i gier lokalnych: runner pomiaru uruchamiają opcje uruchamiania Steam, a Heroic i Lutris uruchamiają grę samodzielnie. Nagraj log MangoHud w grze (klawisz logowania) i użyj „Importuj log MangoHud”',
    ('I18n', 'Automatic recording works only for Steam games and local games: the measurement runner is started by Steam Launch Options, while Heroic and Lutris start the game themselves. Record a MangoHud log in the game (logging key) and use Import MangoHud log'): 'Automatyczne nagrywanie działa tylko dla gier Steam i gier lokalnych: runner pomiaru uruchamiają opcje uruchamiania Steam, a Heroic i Lutris uruchamiają grę samodzielnie. Nagraj log MangoHud w grze (klawisz logowania) i użyj „Importuj log MangoHud”',
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

ES_DLSS = {
    ('CouchGameDetails', 'DLSS Enabler'): 'DLSS Enabler',
    ('DlssEnablerPanel', 'The DLSS Enabler configuration could not be saved'): 'No se pudo guardar la configuración de DLSS Enabler',
    ('DlssEnablerPanelContent', 'Runtime verified'): 'Ejecución verificada',
    ('DlssEnablerPanelContent', 'Selected · runtime not verified'): 'Seleccionado · ejecución no verificada',
    ('DlssEnablerPanelContent', 'Installed · not selected'): 'Instalado · no seleccionado',
    ('DlssEnablerPanelContent', 'Error'): 'Error',
    ('DlssEnablerPanelContent', 'DLSS Enabler release %1 (module %2) replaces OptiScaler for this game and bundles OptiScaler %3. FSR 4 INT8 Lite is unavailable while this backend is selected. Switching remains fully reversible.'): 'DLSS Enabler %1 (módulo %2) sustituye a OptiScaler para este juego e incluye OptiScaler %3. FSR 4 INT8 Lite no está disponible mientras este backend esté seleccionado. El cambio es totalmente reversible.',
    ('DlssEnablerPanelContent', 'The current installation produced a fresh DLSS Enabler runtime log.'): 'La instalación actual produjo un registro nuevo de ejecución de DLSS Enabler.',
    ('DlssEnablerPanelContent', 'Installed and selected. Runtime operation is not confirmed until the game produces a fresh DLSS Enabler log.'): 'Instalado y seleccionado. La ejecución no se confirma hasta que el juego produzca un registro nuevo de DLSS Enabler.',
    ('DlssEnablerPanelContent', 'The game process and graphics runtime were verified with this installation.'): 'El proceso del juego y el entorno gráfico se verificaron con esta instalación.',
    ('DlssEnablerPanelContent', 'Installed and selected. Runtime operation is not confirmed until a game launch produces fresh runtime evidence.'): 'Instalado y seleccionado. La ejecución no se confirma hasta que un inicio del juego produzca pruebas nuevas.',
    ('DlssEnablerPanelContent', 'Native game DLSS copied as _nvngx.dll: %1'): 'DLSS nativo del juego copiado como _nvngx.dll: %1',
    ('DlssEnablerPanelContent', 'DX12 upscaler'): 'Escalador DX12',
    ('DlssEnablerPanelContent', 'Automatic'): 'Automático',
    ('DlssEnablerPanelContent', "AMD/Intel support installed: dxgi.dll, nvapi64-proxy.dll, dlss-finder.exe and the game's native DLSS copied as _nvngx.dll. Proton override: version=n,b;dxgi=n,b."): 'Soporte AMD/Intel instalado: dxgi.dll, nvapi64-proxy.dll, dlss-finder.exe y el DLSS nativo del juego copiado como _nvngx.dll. Sustituciones de Proton: version=n,b;dxgi=n,b.',
    ('DlssEnablerPanelContent', 'Supported by the installed Enabler module and bundled OptiScaler.'): 'Compatible con el módulo Enabler instalado y el OptiScaler incluido.',
    ('DlssEnablerPanelContent', 'Frame generation'): 'Generación de fotogramas',
    ('DlssEnablerPanelContent', 'Automatic or FSR 3, as supported by Enabler 3.00.000.0.'): 'Automático o FSR 3, según lo admite Enabler 3.00.000.0.',
    ('DlssEnablerPanelContent', 'Runtime logging'): 'Registro de ejecución',
    ('DlssEnablerPanelContent', 'Keeps local Enabler and OptiScaler logs for verification and diagnostics.'): 'Conserva registros locales de Enabler y OptiScaler para verificación y diagnóstico.',
    ('DlssEnablerPanelContent', 'Switch this game to DLSS Enabler? GameOpti will verify the installed files and will report runtime verification only after a fresh log is produced by the game.'): '¿Cambiar este juego a DLSS Enabler? GameOpti verificará los archivos instalados y solo indicará la ejecución verificada cuando el juego produzca un registro nuevo.',
    ('CouchGameDetails', 'Runtime verified'): 'Ejecución verificada',
    ('CouchGameDetails', 'Selected · runtime not verified'): 'Seleccionado · ejecución no verificada',
    ('CouchGameDetails', 'Error'): 'Error',
    ('CouchGameDetails', 'Interrupted'): 'Interrumpido',
    ('CouchGameDetails', 'DX12 upscaler: %1'): 'Escalador DX12: %1',
    ('CouchGameDetails', 'Cycle a setting supported by Enabler 3.00.000.0'): 'Cambiar un ajuste compatible con Enabler 3.00.000.0',
    ('CouchGameDetails', 'Runtime logging: %1'): 'Registro de ejecución: %1',
    ('CouchGameDetails', 'Diagnostics'): 'Diagnóstico',
    ('CouchGameDetails', 'Local logs are used for runtime verification'): 'Los registros locales se usan para verificar la ejecución',
}


DLSS_SHARED = {
    "AMD/Intel uses the DXGI loader with dlss-enabler.dll and the game's native DLSS copied as _nvngx.dll. Proton override: dxgi=n,b.": (
        "AMD/Intel używa loadera DXGI z dlss-enabler.dll oraz natywnej biblioteki DLSS gry skopiowanej jako _nvngx.dll. Override Protona: dxgi=n,b.",
        "AMD/Intel usa el cargador DXGI con dlss-enabler.dll y el DLSS nativo del juego copiado como _nvngx.dll. Sustitución de Proton: dxgi=n,b."),
    "Installed · not selected": ("Zainstalowano · niewybrany", "Instalado · no seleccionado"),
    "Installed and selected. Runtime operation is not confirmed until a game launch produces fresh runtime evidence.": (
        "Zainstalowano i wybrano. Działanie nie jest potwierdzone, dopóki uruchomienie gry nie dostarczy świeżego dowodu działania.",
        "Instalado y seleccionado. La ejecución no se confirma hasta que un inicio del juego produzca pruebas nuevas."),
    "Runtime verified": ("Potwierdzono załadowanie", "Carga verificada"),
    "Enabler and its upscaler were loaded by the game. Frame generation and image quality still require an in-game check.": (
        "Gra załadowała Enablera i jego upscaler. Generowanie klatek i jakość obrazu wymagają sprawdzenia w grze.",
        "El juego cargó Enabler y su escalador. La generación de fotogramas y la calidad de imagen aún deben comprobarse en el juego."),
    "Switch this game to DLSS Enabler? GameOpti will verify installed files and check runtime logs or loaded game modules after launch.": (
        "Przełączyć tę grę na DLSS Enabler? GameOpti zweryfikuje pliki, a po uruchomieniu sprawdzi logi lub moduły załadowane przez grę.",
        "¿Cambiar este juego a DLSS Enabler? GameOpti verificará los archivos y comprobará los registros o módulos cargados tras iniciarlo."),
    "Enabler FPS limit": ("Limit FPS Enablera", "Límite FPS de Enabler"),
    "Requires game Reflex support; applies after restarting the game.": (
        "Wymaga obsługi Reflex w grze; działa po ponownym uruchomieniu gry.",
        "Requiere soporte Reflex del juego; se aplica al reiniciarlo."),
    "On": ("Włączone", "Activado"),
    "Off": ("Wyłączone", "Desactivado"),
    "Frame generation": ("Generowanie klatek", "Generación de fotogramas"),
    "Log detail": ("Szczegółowość logu", "Detalle del registro"),
    "Information": ("Informacje", "Información"),
    "Debug": ("Diagnostyczny", "Depuración"),
    "Trace": ("Śledzenie", "Traza"),
    "Diagnostic log: %1": ("Log diagnostyczny: %1", "Registro de diagnóstico: %1"),
    "Refresh diagnostics": ("Odśwież diagnostykę", "Actualizar diagnóstico"),
    "Open game folder": ("Otwórz folder gry", "Abrir carpeta del juego"),
}


FG_SHARED = {'Off': ('Wyłączone', 'Desactivado'),
 'The operation could not be started': ('Nie udało się rozpocząć operacji', 'No se pudo iniciar la operación'),
 'Output multiplier': ('Mnożnik wyjściowy', 'Multiplicador de salida'),
 "Start at 2×. Extra generated frames do not improve the game's input rate.": ('Zacznij od 2×. Dodatkowe klatki '
                                                                               'nie poprawiają szybkości reakcji '
                                                                               'gry.',
                                                                               'Empieza con 2×. Los fotogramas '
                                                                               'adicionales no mejoran la '
                                                                               'respuesta del juego.'),
 'Performance model': ('Model wydajnościowy', 'Modelo de rendimiento'),
 'Lighter interpolation model; lower GPU cost with possible image-quality loss.': ('Lżejszy model interpolacji: '
                                                                                   'mniejszy koszt GPU, możliwe '
                                                                                   'pogorszenie obrazu.',
                                                                                   'Modelo de interpolación más '
                                                                                   'ligero: menor coste de GPU, '
                                                                                   'posible pérdida de calidad.'),
 'Motion-estimation scale': ('Skala analizy ruchu', 'Escala de estimación de movimiento'),
 'Lower values reduce cost and may increase motion artifacts.': ('Niższe wartości zmniejszają koszt i mogą '
                                                                 'nasilać artefakty ruchu.',
                                                                 'Los valores inferiores reducen el coste y '
                                                                 'pueden aumentar los artefactos de movimiento.'),
 'Base FPS cap (0 = off)': ('Limit bazowych FPS (0 = brak)', 'Límite de FPS base (0 = sin límite)'),
 'Caps real rendered frames. Start with a stable base rate and leave GPU headroom.': ('Ogranicza rzeczywiście '
                                                                                      'renderowane klatki. '
                                                                                      'Zacznij od stabilnej '
                                                                                      'wartości i zostaw zapas '
                                                                                      'GPU.',
                                                                                      'Limita los fotogramas '
                                                                                      'reales. Empieza con una '
                                                                                      'tasa estable y deja margen '
                                                                                      'de GPU.'),
 'Adaptive generation': ('Generowanie adaptacyjne', 'Generación adaptativa'),
 'MAKO adjusts generated work towards the selected output target.': ('MAKO dobiera ilość generowanych klatek do '
                                                                     'wskazanego celu wyjściowego.',
                                                                     'MAKO ajusta la generación al objetivo de '
                                                                     'salida seleccionado.'),
 'Adaptive output target': ('Docelowe FPS wyjściowe', 'Objetivo de FPS de salida'),
 'This is an output target, not a guaranteed real frame rate.': ('To cel wyjściowy, a nie gwarantowana liczba '
                                                                 'rzeczywistych klatek.',
                                                                 'Es un objetivo de salida, no una tasa real '
                                                                 'garantizada.'),
 'Maximum adaptive multiplier': ('Maksymalny mnożnik adaptacyjny', 'Multiplicador adaptativo máximo'),
 'Higher limits can increase interpolation artifacts and latency.': ('Wyższe limity mogą zwiększać artefakty '
                                                                     'interpolacji i opóźnienie.',
                                                                     'Los límites superiores pueden aumentar los '
                                                                     'artefactos y la latencia.'),
 'Steady base cap': ('Stabilny limit bazowy', 'Límite base estable'),
 "Use MAKO's adaptive base-cap policy.": ('Użyj adaptacyjnej polityki limitu bazowego MAKO.',
                                          'Usa la política adaptativa de límite base de MAKO.'),
 'Allow FP16': ('Zezwól na FP16', 'Permitir FP16'),
 'Use half precision where the selected GPU and model support it. Restart required.': ('Użyj połowicznej '
                                                                                       'precyzji, jeśli obsługuje '
                                                                                       'ją wybrane GPU i model. '
                                                                                       'Wymaga restartu gry.',
                                                                                       'Usa media precisión si la '
                                                                                       'GPU y el modelo la '
                                                                                       'admiten. Requiere '
                                                                                       'reiniciar el juego.'),
 'Preserve swapchain image count': ('Zachowaj liczbę obrazów swapchain', 'Conservar imágenes de swapchain'),
 'Compatibility option for games that fail to start; may worsen pacing. Restart required.': ('Opcja zgodności dla '
                                                                                             'gier, które nie '
                                                                                             'startują; może '
                                                                                             'pogorszyć płynność. '
                                                                                             'Wymaga restartu '
                                                                                             'gry.',
                                                                                             'Opción para juegos '
                                                                                             'que no arrancan; '
                                                                                             'puede empeorar la '
                                                                                             'cadencia. Requiere '
                                                                                             'reiniciar.'),
 'Optical-flow quality': ('Jakość przepływu optycznego', 'Calidad del flujo óptico'),
 'Automatic': ('Automatycznie', 'Automático'),
 'Quality': ('Jakość', 'Calidad'),
 'Performance': ('Wydajność', 'Rendimiento'),
 'Automatic uses half-resolution flow from 2560×1440; Quality uses full resolution.': ('Automat używa połowy '
                                                                                       'rozdzielczości przepływu '
                                                                                       'od 2560×1440; Jakość '
                                                                                       'używa pełnej.',
                                                                                       'Automático usa flujo a '
                                                                                       'media resolución desde '
                                                                                       '2560×1440; Calidad usa la '
                                                                                       'resolución completa.'),
 'Motion search': ('Wyszukiwanie ruchu', 'Búsqueda de movimiento'),
 'Standard': ('Standardowe', 'Estándar'),
 'High': ('Wysokie', 'Alta'),
 'High searches larger motion ranges at greater GPU cost.': ('Wysokie szuka większych przesunięć kosztem '
                                                             'większego obciążenia GPU.',
                                                             'Alta busca movimientos mayores con más coste de '
                                                             'GPU.'),
 'Frame pacing': ('Równomierne odstępy klatek', 'Cadencia de fotogramas'),
 'Space generated and real frames evenly. Recommended on.': ('Równomiernie rozmieść klatki rzeczywiste i '
                                                             'generowane. Zalecane włączenie.',
                                                             'Distribuye uniformemente los fotogramas reales y '
                                                             'generados. Se recomienda activarlo.'),
 'Minimum real FPS': ('Minimalne rzeczywiste FPS', 'FPS reales mínimos'),
 'Generation pauses below this real frame rate; 0 removes the floor.': ('Poniżej tej wartości generowanie '
                                                                        'zatrzymuje się; 0 usuwa próg.',
                                                                        'La generación se pausa por debajo de '
                                                                        'esta tasa real; 0 elimina el umbral.'),
 'GPU contention governor': ('Kontrola przeciążenia GPU', 'Control de saturación de GPU'),
 'Reduce generated work when the GPU cannot finish it in time.': ('Ogranicza generowanie, gdy GPU nie kończy '
                                                                  'pracy na czas.',
                                                                  'Reduce la generación cuando la GPU no termina '
                                                                  'a tiempo.'),
 'Static HUD detection': ('Wykrywanie nieruchomego HUD', 'Detección de HUD estático'),
 'Keep matching static overlays instead of warping them.': ('Zachowuje pasujące, nieruchome nakładki zamiast je '
                                                            'deformować.',
                                                            'Conserva las superposiciones estáticas coincidentes '
                                                            'en lugar de deformarlas.'),
 'Unreliable motion fallback': ('Obszary niepewnego ruchu', 'Alternativa para movimiento incierto'),
 'Blend frames': ('Mieszaj klatki', 'Mezclar fotogramas'),
 'Repeat previous frame': ('Powtórz poprzednią klatkę', 'Repetir fotograma anterior'),
 'Choose how uncertain motion regions are displayed.': ('Wybierz sposób wyświetlania obszarów z niepewnym ruchem.',
                                                        'Elige cómo mostrar las zonas con movimiento incierto.'),
 'Select the required Lossless Scaling DLL': ('Wybierz wymagany DLL Lossless Scaling',
                                              'Selecciona la DLL requerida de Lossless Scaling'),
 'Frame generation for this game': ('Generowanie klatek dla tej gry', 'Generación de fotogramas para este juego'),
 "Interpolated frames improve presentation smoothness, not the game's simulation or input rate. Start with stable real FPS.": ('Interpolowane '
                                                                                                                               'klatki '
                                                                                                                               'mogą '
                                                                                                                               'poprawić '
                                                                                                                               'płynność '
                                                                                                                               'obrazu, '
                                                                                                                               'ale '
                                                                                                                               'nie '
                                                                                                                               'symulację '
                                                                                                                               'ani '
                                                                                                                               'reakcję '
                                                                                                                               'gry. '
                                                                                                                               'Zacznij '
                                                                                                                               'od '
                                                                                                                               'stabilnych '
                                                                                                                               'rzeczywistych '
                                                                                                                               'FPS.',
                                                                                                                               'La '
                                                                                                                               'interpolación '
                                                                                                                               'puede '
                                                                                                                               'mejorar '
                                                                                                                               'la '
                                                                                                                               'fluidez '
                                                                                                                               'visual, '
                                                                                                                               'no '
                                                                                                                               'la '
                                                                                                                               'simulación '
                                                                                                                               'ni '
                                                                                                                               'la '
                                                                                                                               'respuesta. '
                                                                                                                               'Parte '
                                                                                                                               'de '
                                                                                                                               'FPS '
                                                                                                                               'reales '
                                                                                                                               'estables.'),
 'Saved selection: %1 · Component: %2 · Runtime: %3': ('Zapisano: %1 · Komponent: %2 · Runtime: %3',
                                                       'Guardado: %1 · Componente: %2 · Ejecución: %3'),
 'Not required': ('Niewymagany', 'No necesario'),
 'Missing': ('Brak', 'Falta'),
 'Layer loaded in PID %1': ('Warstwa załadowana w PID %1', 'Capa cargada en PID %1'),
 'Not verified': ('Niezweryfikowany', 'Sin verificar'),
 'Generated frames: not confirmed. Overlay FPS alone is not proof; real/output FPS are unavailable without a source that distinguishes them.': ('Generowanie: '
                                                                                                                                                'niepotwierdzone. '
                                                                                                                                                'Same '
                                                                                                                                                'FPS '
                                                                                                                                                'nakładki '
                                                                                                                                                'nie '
                                                                                                                                                'są '
                                                                                                                                                'dowodem; '
                                                                                                                                                'FPS '
                                                                                                                                                'rzeczywiste/wyjściowe '
                                                                                                                                                'są '
                                                                                                                                                'niedostępne '
                                                                                                                                                'bez '
                                                                                                                                                'źródła, '
                                                                                                                                                'które '
                                                                                                                                                'je '
                                                                                                                                                'rozróżnia.',
                                                                                                                                                'Generación: '
                                                                                                                                                'sin '
                                                                                                                                                'confirmar. '
                                                                                                                                                'Los '
                                                                                                                                                'FPS '
                                                                                                                                                'de '
                                                                                                                                                'una '
                                                                                                                                                'superposición '
                                                                                                                                                'no '
                                                                                                                                                'bastan; '
                                                                                                                                                'FPS '
                                                                                                                                                'reales/de '
                                                                                                                                                'salida '
                                                                                                                                                'no '
                                                                                                                                                'disponibles '
                                                                                                                                                'sin '
                                                                                                                                                'una '
                                                                                                                                                'fuente '
                                                                                                                                                'que '
                                                                                                                                                'los '
                                                                                                                                                'distinga.'),
 "lsfg-vk 2 uses lsfg-vk.dll from Lossless Scaling's lsfg-vk Steam branch. Only VSync pacing is supported; adaptive generation is not available in this release.": ('lsfg-vk '
                                                                                                                                                                    '2 '
                                                                                                                                                                    'używa '
                                                                                                                                                                    'lsfg-vk.dll '
                                                                                                                                                                    'z '
                                                                                                                                                                    'gałęzi '
                                                                                                                                                                    'Steam '
                                                                                                                                                                    'lsfg-vk '
                                                                                                                                                                    'programu '
                                                                                                                                                                    'Lossless '
                                                                                                                                                                    'Scaling. '
                                                                                                                                                                    'Obsługuje '
                                                                                                                                                                    'tylko '
                                                                                                                                                                    'VSync; '
                                                                                                                                                                    'to '
                                                                                                                                                                    'wydanie '
                                                                                                                                                                    'nie '
                                                                                                                                                                    'ma '
                                                                                                                                                                    'generowania '
                                                                                                                                                                    'adaptacyjnego.',
                                                                                                                                                                    'lsfg-vk '
                                                                                                                                                                    '2 '
                                                                                                                                                                    'usa '
                                                                                                                                                                    'lsfg-vk.dll '
                                                                                                                                                                    'de '
                                                                                                                                                                    'la '
                                                                                                                                                                    'rama '
                                                                                                                                                                    'lsfg-vk '
                                                                                                                                                                    'de '
                                                                                                                                                                    'Lossless '
                                                                                                                                                                    'Scaling '
                                                                                                                                                                    'en '
                                                                                                                                                                    'Steam. '
                                                                                                                                                                    'Solo '
                                                                                                                                                                    'admite '
                                                                                                                                                                    'VSync; '
                                                                                                                                                                    'esta '
                                                                                                                                                                    'versión '
                                                                                                                                                                    'no '
                                                                                                                                                                    'tiene '
                                                                                                                                                                    'generación '
                                                                                                                                                                    'adaptativa.'),
 'Standalone MAKO Renderer uses Lossless.dll from the public Lossless Scaling release. Decky is not required. Its launcher disables Steam overlay and HDR exposure for the supported SDR path.': ('Samodzielny '
                                                                                                                                                                                                  'MAKO '
                                                                                                                                                                                                  'Renderer '
                                                                                                                                                                                                  'używa '
                                                                                                                                                                                                  'Lossless.dll '
                                                                                                                                                                                                  'z '
                                                                                                                                                                                                  'publicznego '
                                                                                                                                                                                                  'wydania '
                                                                                                                                                                                                  'Lossless '
                                                                                                                                                                                                  'Scaling. '
                                                                                                                                                                                                  'Nie '
                                                                                                                                                                                                  'wymaga '
                                                                                                                                                                                                  'Decky. '
                                                                                                                                                                                                  'Jego '
                                                                                                                                                                                                  'launcher '
                                                                                                                                                                                                  'wyłącza '
                                                                                                                                                                                                  'nakładkę '
                                                                                                                                                                                                  'Steam '
                                                                                                                                                                                                  'i '
                                                                                                                                                                                                  'udostępnianie '
                                                                                                                                                                                                  'HDR '
                                                                                                                                                                                                  'w '
                                                                                                                                                                                                  'obsługiwanej '
                                                                                                                                                                                                  'ścieżce '
                                                                                                                                                                                                  'SDR.',
                                                                                                                                                                                                  'MAKO '
                                                                                                                                                                                                  'Renderer '
                                                                                                                                                                                                  'independiente '
                                                                                                                                                                                                  'usa '
                                                                                                                                                                                                  'Lossless.dll '
                                                                                                                                                                                                  'de '
                                                                                                                                                                                                  'la '
                                                                                                                                                                                                  'versión '
                                                                                                                                                                                                  'pública '
                                                                                                                                                                                                  'de '
                                                                                                                                                                                                  'Lossless '
                                                                                                                                                                                                  'Scaling. '
                                                                                                                                                                                                  'No '
                                                                                                                                                                                                  'requiere '
                                                                                                                                                                                                  'Decky. '
                                                                                                                                                                                                  'Su '
                                                                                                                                                                                                  'lanzador '
                                                                                                                                                                                                  'desactiva '
                                                                                                                                                                                                  'la '
                                                                                                                                                                                                  'superposición '
                                                                                                                                                                                                  'de '
                                                                                                                                                                                                  'Steam '
                                                                                                                                                                                                  'y '
                                                                                                                                                                                                  'la '
                                                                                                                                                                                                  'exposición '
                                                                                                                                                                                                  'HDR '
                                                                                                                                                                                                  'para '
                                                                                                                                                                                                  'la '
                                                                                                                                                                                                  'ruta '
                                                                                                                                                                                                  'SDR '
                                                                                                                                                                                                  'compatible.'),
 "afmf-linux is an independent project, not AMD's official Linux driver feature. Fixed 2× generation; published packages support 64-bit Vulkan games only.": ('afmf-linux '
                                                                                                                                                              'to '
                                                                                                                                                              'niezależny '
                                                                                                                                                              'projekt, '
                                                                                                                                                              'a '
                                                                                                                                                              'nie '
                                                                                                                                                              'oficjalna '
                                                                                                                                                              'funkcja '
                                                                                                                                                              'sterownika '
                                                                                                                                                              'AMD '
                                                                                                                                                              'dla '
                                                                                                                                                              'Linuksa. '
                                                                                                                                                              'Stałe '
                                                                                                                                                              '2×; '
                                                                                                                                                              'opublikowane '
                                                                                                                                                              'pakiety '
                                                                                                                                                              'obsługują '
                                                                                                                                                              'tylko '
                                                                                                                                                              '64-bitowe '
                                                                                                                                                              'gry '
                                                                                                                                                              'Vulkan.',
                                                                                                                                                              'afmf-linux '
                                                                                                                                                              'es '
                                                                                                                                                              'un '
                                                                                                                                                              'proyecto '
                                                                                                                                                              'independiente, '
                                                                                                                                                              'no '
                                                                                                                                                              'una '
                                                                                                                                                              'función '
                                                                                                                                                              'oficial '
                                                                                                                                                              'del '
                                                                                                                                                              'controlador '
                                                                                                                                                              'AMD '
                                                                                                                                                              'para '
                                                                                                                                                              'Linux. '
                                                                                                                                                              'Generación '
                                                                                                                                                              'fija '
                                                                                                                                                              '2×; '
                                                                                                                                                              'sus '
                                                                                                                                                              'paquetes '
                                                                                                                                                              'admiten '
                                                                                                                                                              'solo '
                                                                                                                                                              'juegos '
                                                                                                                                                              'Vulkan '
                                                                                                                                                              'de '
                                                                                                                                                              '64 '
                                                                                                                                                              'bits.'),
 'Detect graphics path': ('Wykryj ścieżkę grafiki', 'Detectar ruta gráfica'),
 'Detected executable: %1 · %2-bit · API: %3 · %4': ('Wykryty plik: %1 · %2-bit · API: %3 · %4',
                                                     'Ejecutable: %1 · %2 bits · API: %3 · %4'),
 'Shared component': ('Wspólny komponent', 'Componente compartido'),
 'Available version: %1 · Download: %2 MiB. Installation does not activate any game or global layer.': ('Dostępna '
                                                                                                        'wersja: '
                                                                                                        '%1 · '
                                                                                                        'Pobieranie: '
                                                                                                        '%2 MiB. '
                                                                                                        'Instalacja '
                                                                                                        'nie '
                                                                                                        'aktywuje '
                                                                                                        'żadnej '
                                                                                                        'gry ani '
                                                                                                        'globalnej '
                                                                                                        'warstwy.',
                                                                                                        'Versión '
                                                                                                        'disponible: '
                                                                                                        '%1 · '
                                                                                                        'Descarga: '
                                                                                                        '%2 MiB. '
                                                                                                        'Instalar '
                                                                                                        'no '
                                                                                                        'activa '
                                                                                                        'ningún '
                                                                                                        'juego ni '
                                                                                                        'capa '
                                                                                                        'global.'),
 'Update / reinstall': ('Aktualizuj / zainstaluj ponownie', 'Actualizar / reinstalar'),
 'Download and install': ('Pobierz i zainstaluj', 'Descargar e instalar'),
 'Remove component': ('Usuń komponent', 'Eliminar componente'),
 'Cancel download': ('Anuluj pobieranie', 'Cancelar descarga'),
 'Operation: %1 · %2% ': ('Operacja: %1 · %2% ', 'Operación: %1 · %2% '),
 'Your Lossless Scaling installation': ('Twoja instalacja Lossless Scaling', 'Tu instalación de Lossless Scaling'),
 'Proprietary files are never bundled. Discovery and model validation are separate for each backend; GameOpti never changes your Steam branch.': ('Pliki '
                                                                                                                                                  'własnościowe '
                                                                                                                                                  'nie '
                                                                                                                                                  'są '
                                                                                                                                                  'dołączane. '
                                                                                                                                                  'Wykrywanie '
                                                                                                                                                  'i '
                                                                                                                                                  'walidacja '
                                                                                                                                                  'modeli '
                                                                                                                                                  'są '
                                                                                                                                                  'osobne '
                                                                                                                                                  'dla '
                                                                                                                                                  'każdego '
                                                                                                                                                  'backendu; '
                                                                                                                                                  'GameOpti '
                                                                                                                                                  'nie '
                                                                                                                                                  'zmienia '
                                                                                                                                                  'gałęzi '
                                                                                                                                                  'Steam.',
                                                                                                                                                  'No '
                                                                                                                                                  'se '
                                                                                                                                                  'incluyen '
                                                                                                                                                  'archivos '
                                                                                                                                                  'propietarios. '
                                                                                                                                                  'La '
                                                                                                                                                  'detección '
                                                                                                                                                  'y '
                                                                                                                                                  'validación '
                                                                                                                                                  'son '
                                                                                                                                                  'independientes '
                                                                                                                                                  'por '
                                                                                                                                                  'backend; '
                                                                                                                                                  'GameOpti '
                                                                                                                                                  'no '
                                                                                                                                                  'cambia '
                                                                                                                                                  'la '
                                                                                                                                                  'rama '
                                                                                                                                                  'de '
                                                                                                                                                  'Steam.'),
 'Discover': ('Wykryj', 'Detectar'),
 'Browse…': ('Przeglądaj…', 'Examinar…'),
 'Validate models': ('Sprawdź modele', 'Validar modelos'),
 'Lossless Scaling on Steam': ('Lossless Scaling na Steam', 'Lossless Scaling en Steam'),
 'Models validated · file version: %1 · component: %2': ('Modele sprawdzone · wersja pliku: %1 · komponent: %2',
                                                         'Modelos validados · versión del archivo: %1 · '
                                                         'componente: %2'),
 'Models not validated for this backend': ('Modele niesprawdzone dla tego backendu',
                                           'Modelos sin validar para este backend'),
 'Per-game settings': ('Ustawienia tej gry', 'Ajustes por juego'),
 'Hide advanced': ('Ukryj zaawansowane', 'Ocultar avanzados'),
 'Advanced': ('Zaawansowane', 'Avanzados'),
 'Reset backend defaults': ('Przywróć domyślne backendu', 'Restablecer valores del backend'),
 'Reset changes this editor only. Apply saves it; other backends, proprietary files and upscalers are preserved.': ('Reset '
                                                                                                                    'zmienia '
                                                                                                                    'tylko '
                                                                                                                    'edytor. '
                                                                                                                    'Zastosuj '
                                                                                                                    'zapisuje '
                                                                                                                    'zmiany; '
                                                                                                                    'pozostałe '
                                                                                                                    'backendy, '
                                                                                                                    'pliki '
                                                                                                                    'własnościowe '
                                                                                                                    'i '
                                                                                                                    'upscalery '
                                                                                                                    'zostają '
                                                                                                                    'zachowane.',
                                                                                                                    'Restablecer '
                                                                                                                    'solo '
                                                                                                                    'cambia '
                                                                                                                    'el '
                                                                                                                    'editor. '
                                                                                                                    'Aplicar '
                                                                                                                    'guarda '
                                                                                                                    'los '
                                                                                                                    'cambios; '
                                                                                                                    'conserva '
                                                                                                                    'otros '
                                                                                                                    'backends, '
                                                                                                                    'archivos '
                                                                                                                    'propietarios '
                                                                                                                    'y '
                                                                                                                    'escaladores.'),
 'Set a stable base FPS cap in the game or the existing Optimization page. This backend has no separate base-cap control.': ('Ustaw '
                                                                                                                             'stabilny '
                                                                                                                             'limit '
                                                                                                                             'bazowych '
                                                                                                                             'FPS '
                                                                                                                             'w '
                                                                                                                             'grze '
                                                                                                                             'lub '
                                                                                                                             'na '
                                                                                                                             'stronie '
                                                                                                                             'Optymalizacja. '
                                                                                                                             'Ten '
                                                                                                                             'backend '
                                                                                                                             'nie '
                                                                                                                             'ma '
                                                                                                                             'osobnego '
                                                                                                                             'limitu '
                                                                                                                             'bazowego.',
                                                                                                                             'Configura '
                                                                                                                             'un '
                                                                                                                             'límite '
                                                                                                                             'base '
                                                                                                                             'estable '
                                                                                                                             'en '
                                                                                                                             'el '
                                                                                                                             'juego '
                                                                                                                             'o '
                                                                                                                             'en '
                                                                                                                             'Optimización. '
                                                                                                                             'Este '
                                                                                                                             'backend '
                                                                                                                             'no '
                                                                                                                             'tiene '
                                                                                                                             'un '
                                                                                                                             'límite '
                                                                                                                             'base '
                                                                                                                             'propio.'),
 'Apply and launch': ('Zapis i uruchamianie', 'Aplicar e iniciar'),
 'Close the game before Apply. Changes take effect on the next launch through the configured runner. Off disables GameOpti-managed generators for this game without uninstalling components.': ('Zamknij '
                                                                                                                                                                                                'grę '
                                                                                                                                                                                                'przed '
                                                                                                                                                                                                'Zastosuj. '
                                                                                                                                                                                                'Zmiany '
                                                                                                                                                                                                'działają '
                                                                                                                                                                                                'po '
                                                                                                                                                                                                'następnym '
                                                                                                                                                                                                'starcie '
                                                                                                                                                                                                'przez '
                                                                                                                                                                                                'skonfigurowany '
                                                                                                                                                                                                'runner. '
                                                                                                                                                                                                'Wyłączone '
                                                                                                                                                                                                'blokuje '
                                                                                                                                                                                                'generatory '
                                                                                                                                                                                                'zarządzane '
                                                                                                                                                                                                'przez '
                                                                                                                                                                                                'GameOpti '
                                                                                                                                                                                                'dla '
                                                                                                                                                                                                'tej '
                                                                                                                                                                                                'gry '
                                                                                                                                                                                                'bez '
                                                                                                                                                                                                'usuwania '
                                                                                                                                                                                                'komponentów.',
                                                                                                                                                                                                'Cierra '
                                                                                                                                                                                                'el '
                                                                                                                                                                                                'juego '
                                                                                                                                                                                                'antes '
                                                                                                                                                                                                'de '
                                                                                                                                                                                                'Aplicar. '
                                                                                                                                                                                                'Los '
                                                                                                                                                                                                'cambios '
                                                                                                                                                                                                'surten '
                                                                                                                                                                                                'efecto '
                                                                                                                                                                                                'al '
                                                                                                                                                                                                'reiniciar '
                                                                                                                                                                                                'mediante '
                                                                                                                                                                                                'el '
                                                                                                                                                                                                'runner '
                                                                                                                                                                                                'configurado. '
                                                                                                                                                                                                'Desactivado '
                                                                                                                                                                                                'bloquea '
                                                                                                                                                                                                'los '
                                                                                                                                                                                                'generadores '
                                                                                                                                                                                                'de '
                                                                                                                                                                                                'GameOpti '
                                                                                                                                                                                                'para '
                                                                                                                                                                                                'este '
                                                                                                                                                                                                'juego '
                                                                                                                                                                                                'sin '
                                                                                                                                                                                                'desinstalarlos.'),
 'I disabled in-game / OptiScaler / DLSS Enabler frame generation. Keep the working upscaler.': ('Wyłączyłem '
                                                                                                 'generowanie '
                                                                                                 'klatek w grze / '
                                                                                                 'OptiScaler / '
                                                                                                 'DLSS Enabler. '
                                                                                                 'Zachowaj '
                                                                                                 'działający '
                                                                                                 'upscaler.',
                                                                                                 'He desactivado '
                                                                                                 'la generación '
                                                                                                 'del juego / '
                                                                                                 'OptiScaler / '
                                                                                                 'DLSS Enabler. '
                                                                                                 'Conservar el '
                                                                                                 'escalador que '
                                                                                                 'funciona.'),
 'Runner: %1': ('Runner: %1', 'Runner: %1'),
 'Configured': ('Skonfigurowany', 'Configurado'),
 'Not confirmed — configure before launching': ('Niepotwierdzony — skonfiguruj przed startem',
                                                'Sin confirmar — configurar antes de iniciar'),
 'Apply changes': ('Zastosuj zmiany', 'Aplicar cambios'),
 'Apply': ('Zastosuj', 'Aplicar'),
 'Set up runner': ('Skonfiguruj runner', 'Configurar runner'),
 'Restore runner settings': ('Przywróć ustawienia runnera', 'Restaurar ajustes del runner'),
 'Injection may conflict with anti-cheat or game policies. Use offline testing; GameOpti does not bypass anti-cheat.': ('Wstrzykiwanie '
                                                                                                                        'może '
                                                                                                                        'kolidować '
                                                                                                                        'z '
                                                                                                                        'anti-cheatem '
                                                                                                                        'lub '
                                                                                                                        'zasadami '
                                                                                                                        'gry. '
                                                                                                                        'Testuj '
                                                                                                                        'offline; '
                                                                                                                        'GameOpti '
                                                                                                                        'nie '
                                                                                                                        'obchodzi '
                                                                                                                        'anti-cheata.',
                                                                                                                        'La '
                                                                                                                        'inyección '
                                                                                                                        'puede '
                                                                                                                        'entrar '
                                                                                                                        'en '
                                                                                                                        'conflicto '
                                                                                                                        'con '
                                                                                                                        'el '
                                                                                                                        'anti-cheat '
                                                                                                                        'o '
                                                                                                                        'las '
                                                                                                                        'normas '
                                                                                                                        'del '
                                                                                                                        'juego. '
                                                                                                                        'Prueba '
                                                                                                                        'sin '
                                                                                                                        'conexión; '
                                                                                                                        'GameOpti '
                                                                                                                        'no '
                                                                                                                        'evade '
                                                                                                                        'el '
                                                                                                                        'anti-cheat.'),
 'Download and install %1 %2 (%3 MiB) from the official source? Only this shared component is installed; no game is activated.': ('Pobrać '
                                                                                                                                  'i '
                                                                                                                                  'zainstalować '
                                                                                                                                  '%1 '
                                                                                                                                  '%2 '
                                                                                                                                  '(%3 '
                                                                                                                                  'MiB) '
                                                                                                                                  'z '
                                                                                                                                  'oficjalnego '
                                                                                                                                  'źródła? '
                                                                                                                                  'Zainstalowany '
                                                                                                                                  'zostanie '
                                                                                                                                  'tylko '
                                                                                                                                  'ten '
                                                                                                                                  'wspólny '
                                                                                                                                  'komponent; '
                                                                                                                                  'żadna '
                                                                                                                                  'gra '
                                                                                                                                  'nie '
                                                                                                                                  'zostanie '
                                                                                                                                  'aktywowana.',
                                                                                                                                  '¿Descargar '
                                                                                                                                  'e '
                                                                                                                                  'instalar '
                                                                                                                                  '%1 '
                                                                                                                                  '%2 '
                                                                                                                                  '(%3 '
                                                                                                                                  'MiB) '
                                                                                                                                  'de '
                                                                                                                                  'la '
                                                                                                                                  'fuente '
                                                                                                                                  'oficial? '
                                                                                                                                  'Solo '
                                                                                                                                  'se '
                                                                                                                                  'instalará '
                                                                                                                                  'este '
                                                                                                                                  'componente; '
                                                                                                                                  'no '
                                                                                                                                  'se '
                                                                                                                                  'activará '
                                                                                                                                  'ningún '
                                                                                                                                  'juego.'),
 'Remove this shared component? Games selecting it will require reinstallation or Off. Per-game settings and Lossless Scaling files are kept.': ('Usunąć '
                                                                                                                                                 'wspólny '
                                                                                                                                                 'komponent? '
                                                                                                                                                 'Gry, '
                                                                                                                                                 'które '
                                                                                                                                                 'go '
                                                                                                                                                 'wybrały, '
                                                                                                                                                 'będą '
                                                                                                                                                 'wymagały '
                                                                                                                                                 'ponownej '
                                                                                                                                                 'instalacji '
                                                                                                                                                 'lub '
                                                                                                                                                 'opcji '
                                                                                                                                                 'Wyłączone. '
                                                                                                                                                 'Ustawienia '
                                                                                                                                                 'gier '
                                                                                                                                                 'i '
                                                                                                                                                 'pliki '
                                                                                                                                                 'Lossless '
                                                                                                                                                 'Scaling '
                                                                                                                                                 'zostają '
                                                                                                                                                 'zachowane.',
                                                                                                                                                 '¿Eliminar '
                                                                                                                                                 'este '
                                                                                                                                                 'componente '
                                                                                                                                                 'compartido? '
                                                                                                                                                 'Los '
                                                                                                                                                 'juegos '
                                                                                                                                                 'que '
                                                                                                                                                 'lo '
                                                                                                                                                 'usen '
                                                                                                                                                 'requerirán '
                                                                                                                                                 'reinstalarlo '
                                                                                                                                                 'o '
                                                                                                                                                 'elegir '
                                                                                                                                                 'Desactivado. '
                                                                                                                                                 'Se '
                                                                                                                                                 'conservan '
                                                                                                                                                 'los '
                                                                                                                                                 'perfiles '
                                                                                                                                                 'y '
                                                                                                                                                 'archivos '
                                                                                                                                                 'de '
                                                                                                                                                 'Lossless '
                                                                                                                                                 'Scaling.'),
 'Set up the existing GameOpti runner for this game? Close Steam/Heroic/Lutris first. Existing launch arguments are preserved with a reversible backup.': ('Skonfigurować '
                                                                                                                                                           'istniejący '
                                                                                                                                                           'runner '
                                                                                                                                                           'GameOpti '
                                                                                                                                                           'dla '
                                                                                                                                                           'tej '
                                                                                                                                                           'gry? '
                                                                                                                                                           'Najpierw '
                                                                                                                                                           'zamknij '
                                                                                                                                                           'Steam/Heroic/Lutris. '
                                                                                                                                                           'Argumenty '
                                                                                                                                                           'uruchamiania '
                                                                                                                                                           'zostaną '
                                                                                                                                                           'zachowane '
                                                                                                                                                           'z '
                                                                                                                                                           'odwracalną '
                                                                                                                                                           'kopią '
                                                                                                                                                           'zapasową.',
                                                                                                                                                           '¿Configurar '
                                                                                                                                                           'el '
                                                                                                                                                           'runner '
                                                                                                                                                           'de '
                                                                                                                                                           'GameOpti '
                                                                                                                                                           'para '
                                                                                                                                                           'este '
                                                                                                                                                           'juego? '
                                                                                                                                                           'Cierra '
                                                                                                                                                           'Steam/Heroic/Lutris '
                                                                                                                                                           'primero. '
                                                                                                                                                           'Los '
                                                                                                                                                           'argumentos '
                                                                                                                                                           'se '
                                                                                                                                                           'conservan '
                                                                                                                                                           'con '
                                                                                                                                                           'una '
                                                                                                                                                           'copia '
                                                                                                                                                           'reversible.'),
 'Restore the launch settings saved before GameOpti runner setup? Independent later changes stop automatic restoration.': ('Przywrócić '
                                                                                                                           'ustawienia '
                                                                                                                           'startu '
                                                                                                                           'sprzed '
                                                                                                                           'konfiguracji '
                                                                                                                           'runnera '
                                                                                                                           'GameOpti? '
                                                                                                                           'Niezależne '
                                                                                                                           'późniejsze '
                                                                                                                           'zmiany '
                                                                                                                           'zatrzymają '
                                                                                                                           'automatyczne '
                                                                                                                           'przywracanie.',
                                                                                                                           '¿Restaurar '
                                                                                                                           'los '
                                                                                                                           'ajustes '
                                                                                                                           'anteriores '
                                                                                                                           'a '
                                                                                                                           'configurar '
                                                                                                                           'el '
                                                                                                                           'runner? '
                                                                                                                           'Los '
                                                                                                                           'cambios '
                                                                                                                           'independientes '
                                                                                                                           'posteriores '
                                                                                                                           'detienen '
                                                                                                                           'la '
                                                                                                                           'restauración '
                                                                                                                           'automática.'),
 'Confirm': ('Potwierdź', 'Confirmar'),
 'Cancel': ('Anuluj', 'Cancelar'),
 'Diagnostics and compatibility': ('Diagnostyka i zgodność', 'Diagnóstico y compatibilidad'),
 'Host Vulkan probe: %1. This does not prove loading inside Steam Linux Runtime or a launcher Flatpak.': ('Sonda '
                                                                                                          'Vulkan '
                                                                                                          'hosta: '
                                                                                                          '%1. To '
                                                                                                          'nie '
                                                                                                          'dowodzi '
                                                                                                          'ładowania '
                                                                                                          'w '
                                                                                                          'Steam '
                                                                                                          'Linux '
                                                                                                          'Runtime '
                                                                                                          'ani we '
                                                                                                          'Flatpaku '
                                                                                                          'launchera.',
                                                                                                          'Sonda '
                                                                                                          'Vulkan '
                                                                                                          'del '
                                                                                                          'host: '
                                                                                                          '%1. No '
                                                                                                          'demuestra '
                                                                                                          'la '
                                                                                                          'carga '
                                                                                                          'en '
                                                                                                          'Steam '
                                                                                                          'Linux '
                                                                                                          'Runtime '
                                                                                                          'ni en '
                                                                                                          'el '
                                                                                                          'Flatpak '
                                                                                                          'del '
                                                                                                          'lanzador.'),
 'Selected runtime dependencies: %1': ('Zależności wybranego środowiska: %1',
                                       'Dependencias del entorno seleccionado: %1'),
 'Not checked': ('Niesprawdzone', 'Sin comprobar'),
 'Independent installations (kept): %1': ('Niezależne instalacje (zachowane): %1',
                                          'Instalaciones independientes (conservadas): %1'),
 'MAKO uses its supported ordered layer boundary. MangoHud + vkBasalt together are unsupported there. lsfg-vk and afmf-linux combinations need a per-game test. No upscaler DLL is replaced.': ('MAKO '
                                                                                                                                                                                                'stosuje '
                                                                                                                                                                                                'obsługiwaną '
                                                                                                                                                                                                'kolejność '
                                                                                                                                                                                                'warstw. '
                                                                                                                                                                                                'MangoHud '
                                                                                                                                                                                                'i '
                                                                                                                                                                                                'vkBasalt '
                                                                                                                                                                                                'jednocześnie '
                                                                                                                                                                                                'nie '
                                                                                                                                                                                                'są '
                                                                                                                                                                                                'tam '
                                                                                                                                                                                                'obsługiwane. '
                                                                                                                                                                                                'Połączenia '
                                                                                                                                                                                                'lsfg-vk '
                                                                                                                                                                                                'i '
                                                                                                                                                                                                'afmf-linux '
                                                                                                                                                                                                'wymagają '
                                                                                                                                                                                                'próby '
                                                                                                                                                                                                'w '
                                                                                                                                                                                                'konkretnej '
                                                                                                                                                                                                'grze. '
                                                                                                                                                                                                'DLL '
                                                                                                                                                                                                'upscalera '
                                                                                                                                                                                                'nie '
                                                                                                                                                                                                'jest '
                                                                                                                                                                                                'zastępowany.',
                                                                                                                                                                                                'MAKO '
                                                                                                                                                                                                'usa '
                                                                                                                                                                                                'su '
                                                                                                                                                                                                'orden '
                                                                                                                                                                                                'de '
                                                                                                                                                                                                'capas '
                                                                                                                                                                                                'compatible. '
                                                                                                                                                                                                'No '
                                                                                                                                                                                                'admite '
                                                                                                                                                                                                'MangoHud '
                                                                                                                                                                                                'y '
                                                                                                                                                                                                'vkBasalt '
                                                                                                                                                                                                'a '
                                                                                                                                                                                                'la '
                                                                                                                                                                                                'vez. '
                                                                                                                                                                                                'Las '
                                                                                                                                                                                                'combinaciones '
                                                                                                                                                                                                'de '
                                                                                                                                                                                                'lsfg-vk '
                                                                                                                                                                                                'y '
                                                                                                                                                                                                'afmf-linux '
                                                                                                                                                                                                'requieren '
                                                                                                                                                                                                'pruebas '
                                                                                                                                                                                                'por '
                                                                                                                                                                                                'juego. '
                                                                                                                                                                                                'No '
                                                                                                                                                                                                'se '
                                                                                                                                                                                                'reemplaza '
                                                                                                                                                                                                'ninguna '
                                                                                                                                                                                                'DLL '
                                                                                                                                                                                                'de '
                                                                                                                                                                                                'escalado.'),
 'Game/session: %1 · Saved: %2 · Loaded PID: %3': ('Gra/sesja: %1 · Zapis: %2 · Załadowany PID: %3',
                                                   'Juego/sesión: %1 · Guardado: %2 · PID cargado: %3'),
 'Open configuration / logs': ('Otwórz konfigurację / logi', 'Abrir configuración / registros'),
 'Frame generation': ('Generowanie klatek', 'Generación de fotogramas'),
 'Configure lsfg-vk / MAKO / afmf-linux': ('Konfiguruj lsfg-vk / MAKO / afmf-linux',
                                           'Configurar lsfg-vk / MAKO / afmf-linux'),
 'Invalid frame-generation backend': ('Nieprawidłowy backend generowania klatek',
                                      'Backend de generación no válido'),
 'Invalid frame-generation settings': ('Nieprawidłowe ustawienia generowania klatek',
                                       'Ajustes de generación no válidos'),
 'afmf-linux upstream does not support OptiScaler injectors. Preserve OptiScaler and choose lsfg-vk/MAKO, or restore it explicitly on its own page': ('afmf-linux '
                                                                                                                                                      'nie '
                                                                                                                                                      'obsługuje '
                                                                                                                                                      'wstrzykiwania '
                                                                                                                                                      'przez '
                                                                                                                                                      'OptiScaler. '
                                                                                                                                                      'Zachowaj '
                                                                                                                                                      'OptiScaler '
                                                                                                                                                      'i '
                                                                                                                                                      'wybierz '
                                                                                                                                                      'lsfg-vk/MAKO '
                                                                                                                                                      'lub '
                                                                                                                                                      'jawnie '
                                                                                                                                                      'przywróć '
                                                                                                                                                      'oryginalne '
                                                                                                                                                      'pliki '
                                                                                                                                                      'na '
                                                                                                                                                      'jego '
                                                                                                                                                      'stronie.',
                                                                                                                                                      'afmf-linux '
                                                                                                                                                      'no '
                                                                                                                                                      'admite '
                                                                                                                                                      'inyectores '
                                                                                                                                                      'OptiScaler. '
                                                                                                                                                      'Conserva '
                                                                                                                                                      'OptiScaler '
                                                                                                                                                      'y '
                                                                                                                                                      'elige '
                                                                                                                                                      'lsfg-vk/MAKO '
                                                                                                                                                      'o '
                                                                                                                                                      'restáuralo '
                                                                                                                                                      'explícitamente '
                                                                                                                                                      'en '
                                                                                                                                                      'su '
                                                                                                                                                      'página.'),
 'Existing VK_LOADER_LAYERS_DISABLE blocks the selected frame generator; remove that per-game conflict explicitly': ('Obecne '
                                                                                                                     'VK_LOADER_LAYERS_DISABLE '
                                                                                                                     'blokuje '
                                                                                                                     'wybrany '
                                                                                                                     'generator. '
                                                                                                                     'Usuń '
                                                                                                                     'ten '
                                                                                                                     'konflikt '
                                                                                                                     'jawnie '
                                                                                                                     'w '
                                                                                                                     'ustawieniach '
                                                                                                                     'gry.',
                                                                                                                     'VK_LOADER_LAYERS_DISABLE '
                                                                                                                     'bloquea '
                                                                                                                     'el '
                                                                                                                     'generador '
                                                                                                                     'seleccionado. '
                                                                                                                     'Elimina '
                                                                                                                     'explícitamente '
                                                                                                                     'ese '
                                                                                                                     'conflicto '
                                                                                                                     'del '
                                                                                                                     'juego.'),
 'Selected frame generator requires Vulkan; PROTON_USE_WINED3D=1 selects OpenGL': ('Generator wymaga Vulkan; '
                                                                                   'PROTON_USE_WINED3D=1 wybiera '
                                                                                   'OpenGL.',
                                                                                   'El generador requiere Vulkan; '
                                                                                   'PROTON_USE_WINED3D=1 '
                                                                                   'selecciona OpenGL.'),
 'MAKO + ReShade loader coexistence has not been verified; select Off on one page and Apply': ('Niezweryfikowana '
                                                                                               'zgodność loadera '
                                                                                               'MAKO z ReShade. '
                                                                                               'Wybierz Wyłączone '
                                                                                               'na jednej ze '
                                                                                               'stron i Zastosuj.',
                                                                                               'La coexistencia '
                                                                                               'de MAKO y ReShade '
                                                                                               'no está '
                                                                                               'verificada. Elige '
                                                                                               'Desactivado en '
                                                                                               'una página y '
                                                                                               'Aplica.'),
 'MAKO supports vkBasalt or MangoHud in the game, not both. Disable one in its page, or select lsfg-vk': ('MAKO '
                                                                                                          'obsługuje '
                                                                                                          'vkBasalt '
                                                                                                          'lub '
                                                                                                          'MangoHud '
                                                                                                          'w '
                                                                                                          'grze, '
                                                                                                          'ale '
                                                                                                          'nie '
                                                                                                          'oba '
                                                                                                          'naraz. '
                                                                                                          'Wyłącz '
                                                                                                          'jedno '
                                                                                                          'na '
                                                                                                          'jego '
                                                                                                          'stronie '
                                                                                                          'lub '
                                                                                                          'wybierz '
                                                                                                          'lsfg-vk.',
                                                                                                          'MAKO '
                                                                                                          'admite '
                                                                                                          'vkBasalt '
                                                                                                          'o '
                                                                                                          'MangoHud '
                                                                                                          'en el '
                                                                                                          'juego, '
                                                                                                          'no '
                                                                                                          'ambos. '
                                                                                                          'Desactiva '
                                                                                                          'uno en '
                                                                                                          'su '
                                                                                                          'página '
                                                                                                          'o '
                                                                                                          'elige '
                                                                                                          'lsfg-vk.'),
 'Lossless Scaling files changed or were not validated for this backend': ('Pliki Lossless Scaling zmieniły się '
                                                                           'lub nie zostały sprawdzone dla tego '
                                                                           'backendu.',
                                                                           'Los archivos de Lossless Scaling '
                                                                           'cambiaron o no se validaron para este '
                                                                           'backend.'),
 'Per-game frame-generation configuration is missing': ('Brak konfiguracji generowania klatek tej gry.',
                                                        'Falta la configuración de generación de este juego.'),
 'This backend does not use Lossless Scaling': ('Ten backend nie używa Lossless Scaling.',
                                                'Este backend no usa Lossless Scaling.'),
 'Install the selected component before validating its proprietary models': ('Zainstaluj wybrany komponent przed '
                                                                             'sprawdzaniem jego modeli '
                                                                             'własnościowych.',
                                                                             'Instala el componente antes de '
                                                                             'validar sus modelos propietarios.'),
 'Unknown frame generator': ('Nieznany generator klatek', 'Generador de fotogramas desconocido'),
 'Invalid Vulkan launch path': ('Nieprawidłowa ścieżka uruchamiania Vulkan',
                                'Ruta de lanzamiento Vulkan no válida'),
 'Discover/select and validate Lossless Scaling for this backend first': ('Najpierw wykryj/wybierz i sprawdź '
                                                                          'Lossless Scaling dla tego backendu.',
                                                                          'Detecta/selecciona y valida Lossless '
                                                                          'Scaling para este backend primero.'),
 'Unknown frame-generation backend': ('Nieznany backend generowania klatek', 'Backend de generación desconocido'),
 'Invalid component ownership manifest': ('Nieprawidłowy manifest własności komponentu',
                                          'Manifiesto de propiedad del componente no válido'),
 'Component download failed size/SHA-256 verification': ('Pobrany komponent nie przeszedł weryfikacji '
                                                         'rozmiaru/SHA-256.',
                                                         'El componente descargado no superó la verificación de '
                                                         'tamaño/SHA-256.'),
 'Package does not contain a Vulkan layer': ('Pakiet nie zawiera warstwy Vulkan.',
                                             'El paquete no contiene una capa Vulkan.'),
 'Installation cancelled; previous component preserved': ('Anulowano instalację; zachowano poprzedni komponent.',
                                                          'Instalación cancelada; componente anterior '
                                                          'conservado.'),
 'Unknown files in component directory; removal stopped': ('Nieznane pliki w katalogu komponentu; usuwanie '
                                                           'zatrzymane.',
                                                           'Archivos desconocidos en el componente; eliminación '
                                                           'detenida.'),
 'Download cancelled; installation unchanged': ('Anulowano pobieranie; instalacja bez zmian.',
                                                'Descarga cancelada; instalación sin cambios.'),
 'Download exceeds verified size': ('Pobieranie przekracza zweryfikowany rozmiar.',
                                    'La descarga supera el tamaño verificado.'),
 'Unsafe component archive member': ('Niebezpieczna ścieżka w archiwum komponentu.',
                                     'Ruta insegura en el archivo del componente.'),
 'Invalid component archive': ('Nieprawidłowe archiwum komponentu.', 'Archivo del componente no válido.'),
 'Steam has no settings entry for this game; open its Properties once, close Steam, then retry': ('Steam nie ma '
                                                                                                  'wpisu ustawień '
                                                                                                  'tej gry. '
                                                                                                  'Otwórz raz jej '
                                                                                                  'Właściwości, '
                                                                                                  'zamknij Steam '
                                                                                                  'i ponów.',
                                                                                                  'Steam no tiene '
                                                                                                  'ajustes para '
                                                                                                  'este juego. '
                                                                                                  'Abre sus '
                                                                                                  'Propiedades '
                                                                                                  'una vez, '
                                                                                                  'cierra Steam y '
                                                                                                  'reintenta.'),
 'Close Steam completely before automatic runner setup; Steam keeps launch options in memory': ('Zamknij '
                                                                                                'całkowicie Steam '
                                                                                                'przed '
                                                                                                'automatyczną '
                                                                                                'konfiguracją '
                                                                                                'runnera. Steam '
                                                                                                'przechowuje '
                                                                                                'opcje startu w '
                                                                                                'pamięci.',
                                                                                                'Cierra Steam por '
                                                                                                'completo antes '
                                                                                                'de configurar el '
                                                                                                'runner. Steam '
                                                                                                'mantiene las '
                                                                                                'opciones en '
                                                                                                'memoria.'),
 'Cannot select a unique Steam user configuration for this game': ('Nie można jednoznacznie wybrać konfiguracji '
                                                                   'użytkownika Steam dla tej gry.',
                                                                   'No se puede elegir una única configuración de '
                                                                   'usuario de Steam para este juego.'),
 'Steam configuration changed while preparing runner setup': ('Konfiguracja Steam zmieniła się podczas '
                                                              'przygotowania runnera.',
                                                              'La configuración de Steam cambió al preparar el '
                                                              'runner.'),
 'Steam launch options changed independently; automatic restore stopped': ('Opcje startu Steam zmieniły się '
                                                                           'niezależnie; zatrzymano automatyczne '
                                                                           'przywracanie.',
                                                                           'Las opciones de Steam cambiaron '
                                                                           'independientemente; restauración '
                                                                           'automática detenida.'),
 'Close games before changing shared frame-generation components': ('Zamknij gry przed zmianą wspólnych '
                                                                    'komponentów generowania klatek.',
                                                                    'Cierra los juegos antes de cambiar '
                                                                    'componentes compartidos de generación.'),
 'Unknown frame-generation operation': ('Nieznana operacja generowania klatek',
                                        'Operación de generación desconocida'),
 'This launcher has no supported frame-generation runner setup': ('Ten launcher nie ma obsługiwanej konfiguracji '
                                                                  'runnera generowania klatek.',
                                                                  'Este lanzador no tiene configuración de runner '
                                                                  'compatible con la generación.'),
 'afmf-linux 1.3.1 has no published 32-bit layer': ('afmf-linux 1.3.1 nie udostępnia warstwy 32-bitowej.',
                                                    'afmf-linux 1.3.1 no publica una capa de 32 bits.'),
 'This integration requires Vulkan, DXVK or VKD3D-Proton; OpenGL is not enabled automatically': ('Integracja '
                                                                                                 'wymaga Vulkan, '
                                                                                                 'DXVK lub '
                                                                                                 'VKD3D-Proton. '
                                                                                                 'OpenGL nie jest '
                                                                                                 'zmieniany '
                                                                                                 'automatycznie.',
                                                                                                 'Esta '
                                                                                                 'integración '
                                                                                                 'requiere '
                                                                                                 'Vulkan, DXVK o '
                                                                                                 'VKD3D-Proton. '
                                                                                                 'No se cambia '
                                                                                                 'OpenGL '
                                                                                                 'automáticamente.'),
 'Graphics API is inconclusive; confirm Vulkan, DXVK or VKD3D-Proton explicitly': ('Wykryte API jest '
                                                                                   'niejednoznaczne. Potwierdź '
                                                                                   'jawnie Vulkan, DXVK lub '
                                                                                   'VKD3D-Proton.',
                                                                                   'La API no es concluyente. '
                                                                                   'Confirma explícitamente '
                                                                                   'Vulkan, DXVK o VKD3D-Proton.'),
 'Vulkan compute/storage-image capabilities were not confirmed on the host': ('Nie potwierdzono obsługi Vulkan '
                                                                              'compute/storage images na hoście.',
                                                                              'No se confirmaron capacidades '
                                                                              'Vulkan compute/storage images en '
                                                                              'el host.'),
 'Confirm internal/in-game frame generation is off before selecting an external generator; the upscaler is preserved': ('Potwierdź '
                                                                                                                        'wyłączenie '
                                                                                                                        'generowania '
                                                                                                                        'klatek '
                                                                                                                        'w '
                                                                                                                        'grze/przez '
                                                                                                                        'mody '
                                                                                                                        'przed '
                                                                                                                        'wyborem '
                                                                                                                        'zewnętrznego '
                                                                                                                        'generatora. '
                                                                                                                        'Upscaler '
                                                                                                                        'zostaje '
                                                                                                                        'zachowany.',
                                                                                                                        'Confirma '
                                                                                                                        'que '
                                                                                                                        'la '
                                                                                                                        'generación '
                                                                                                                        'interna/del '
                                                                                                                        'juego '
                                                                                                                        'está '
                                                                                                                        'desactivada '
                                                                                                                        'antes '
                                                                                                                        'de '
                                                                                                                        'elegir '
                                                                                                                        'el '
                                                                                                                        'generador '
                                                                                                                        'externo. '
                                                                                                                        'Se '
                                                                                                                        'conserva '
                                                                                                                        'el '
                                                                                                                        'escalador.'),
 'Component/dependencies are not available inside the selected launch environment; see diagnostics': ('Komponent '
                                                                                                      'lub '
                                                                                                      'zależności '
                                                                                                      'niedostępne '
                                                                                                      'w '
                                                                                                      'środowisku '
                                                                                                      'uruchamiającym '
                                                                                                      'grę. '
                                                                                                      'Sprawdź '
                                                                                                      'diagnostykę.',
                                                                                                      'El '
                                                                                                      'componente '
                                                                                                      'o sus '
                                                                                                      'dependencias '
                                                                                                      'no están '
                                                                                                      'disponibles '
                                                                                                      'en el '
                                                                                                      'entorno de '
                                                                                                      'ejecución. '
                                                                                                      'Consulta '
                                                                                                      'el '
                                                                                                      'diagnóstico.'),
 'Private component directory is occupied by unowned files': ('Prywatny katalog komponentu jest zajęty przez '
                                                              'pliki bez potwierdzonego właściciela.',
                                                              'El directorio privado está ocupado por archivos '
                                                              'sin propiedad confirmada.'),
 'Component belongs to another installer': ('Komponent należy do innego instalatora.',
                                            'El componente pertenece a otro instalador.'),
 'Unknown files in component directory; update/removal stopped': ('Nieznane pliki w katalogu komponentu; '
                                                                  'aktualizacja/usuwanie zatrzymane.',
                                                                  'Archivos desconocidos en el componente; '
                                                                  'actualización/eliminación detenidas.'),
 'Runtime error — inspect diagnostics': ('Błąd runtime — sprawdź diagnostykę',
                                         'Error de ejecución — consulta el diagnóstico'),
 'The matching session log confirms initialized interpolation and submitted generated frames. This does not measure image quality or input latency.': ('Log '
                                                                                                                                                       'właściwej '
                                                                                                                                                       'sesji '
                                                                                                                                                       'potwierdza '
                                                                                                                                                       'inicjalizację '
                                                                                                                                                       'interpolacji '
                                                                                                                                                       'i '
                                                                                                                                                       'wysłane '
                                                                                                                                                       'klatki '
                                                                                                                                                       'generowane. '
                                                                                                                                                       'Nie '
                                                                                                                                                       'jest '
                                                                                                                                                       'to '
                                                                                                                                                       'pomiar '
                                                                                                                                                       'jakości '
                                                                                                                                                       'obrazu '
                                                                                                                                                       'ani '
                                                                                                                                                       'opóźnienia '
                                                                                                                                                       'sterowania.',
                                                                                                                                                       'El '
                                                                                                                                                       'registro '
                                                                                                                                                       'de '
                                                                                                                                                       'la '
                                                                                                                                                       'sesión '
                                                                                                                                                       'confirma '
                                                                                                                                                       'interpolación '
                                                                                                                                                       'iniciada '
                                                                                                                                                       'y '
                                                                                                                                                       'fotogramas '
                                                                                                                                                       'generados '
                                                                                                                                                       'enviados. '
                                                                                                                                                       'No '
                                                                                                                                                       'mide '
                                                                                                                                                       'calidad '
                                                                                                                                                       'visual '
                                                                                                                                                       'ni '
                                                                                                                                                       'latencia '
                                                                                                                                                       'de '
                                                                                                                                                       'entrada.'),
 'Selected executable is missing or incompatible with the saved frame generator': ('Wybrany plik gry nie istnieje '
                                                                                   'lub nie pasuje do zapisanego '
                                                                                   'generatora klatek.',
                                                                                   'El ejecutable no existe o no '
                                                                                   'es compatible con el '
                                                                                   'generador guardado.'),
 'This MAKO integration uses its supported SDR launcher. Disable HDR explicitly or select another generator; GameOpti will not lower it silently': ('Ta '
                                                                                                                                                    'integracja '
                                                                                                                                                    'MAKO '
                                                                                                                                                    'używa '
                                                                                                                                                    'obsługiwanego '
                                                                                                                                                    'launchera '
                                                                                                                                                    'SDR. '
                                                                                                                                                    'Wyłącz '
                                                                                                                                                    'HDR '
                                                                                                                                                    'jawnie '
                                                                                                                                                    'lub '
                                                                                                                                                    'wybierz '
                                                                                                                                                    'inny '
                                                                                                                                                    'generator; '
                                                                                                                                                    'GameOpti '
                                                                                                                                                    'nie '
                                                                                                                                                    'obniży '
                                                                                                                                                    'ustawienia '
                                                                                                                                                    'po '
                                                                                                                                                    'cichu.',
                                                                                                                                                    'Esta '
                                                                                                                                                    'integración '
                                                                                                                                                    'MAKO '
                                                                                                                                                    'usa '
                                                                                                                                                    'el '
                                                                                                                                                    'lanzador '
                                                                                                                                                    'SDR '
                                                                                                                                                    'compatible. '
                                                                                                                                                    'Desactiva '
                                                                                                                                                    'HDR '
                                                                                                                                                    'explícitamente '
                                                                                                                                                    'o '
                                                                                                                                                    'elige '
                                                                                                                                                    'otro '
                                                                                                                                                    'generador; '
                                                                                                                                                    'GameOpti '
                                                                                                                                                    'no '
                                                                                                                                                    'lo '
                                                                                                                                                    'reducirá '
                                                                                                                                                    'silenciosamente.'),
 'The game is currently running': ('Gra jest teraz uruchomiona.', 'El juego está en ejecución.'),
 'Launcher Flatpak access setup failed; see the host permission error': ('Nie udało się udostępnić plików '
                                                                         'Flatpakowi launchera. Sprawdź błąd '
                                                                         'uprawnień hosta.',
                                                                         'No se pudo dar acceso al Flatpak del '
                                                                         'lanzador. Revisa el error de permisos '
                                                                         'del host.'),
 'Launcher override location could not be verified; no automatic restore ownership recorded': ('Nie potwierdzono '
                                                                                               'lokalizacji '
                                                                                               'nadpisań '
                                                                                               'launchera. Nie '
                                                                                               'zapisano '
                                                                                               'własności do '
                                                                                               'automatycznego '
                                                                                               'przywracania.',
                                                                                               'No se confirmó la '
                                                                                               'ubicación de las '
                                                                                               'reglas del '
                                                                                               'lanzador. No se '
                                                                                               'registró '
                                                                                               'propiedad para '
                                                                                               'restauración '
                                                                                               'automática.'),
 'Invalid component transaction journal': ('Nieprawidłowy dziennik transakcji komponentu',
                                           'Diario de transacción del componente no válido'),
 'Unknown files in interrupted transaction; recovery stopped': ('Nieznane pliki w przerwanej transakcji. '
                                                                'Zatrzymano odzyskiwanie.',
                                                                'Archivos desconocidos en la transacción '
                                                                'interrumpida. Recuperación detenida.'),
 'Modified files in interrupted transaction; recovery stopped': ('Zmodyfikowane pliki w przerwanej transakcji. '
                                                                 'Zatrzymano odzyskiwanie.',
                                                                 'Archivos modificados en la transacción '
                                                                 'interrumpida. Recuperación detenida.'),
 'Complex Steam launch options require review; nothing was overwritten': ('Złożone opcje startu Steam wymagają '
                                                                          'przeglądu. Niczego nie nadpisano.',
                                                                          'Las opciones complejas de Steam '
                                                                          'requieren revisión. No se sobrescribió '
                                                                          'nada.'),
 'Existing GameOpti runner options are invalid or use another AppID; nothing was overwritten': ('Obecne opcje '
                                                                                                'runnera GameOpti '
                                                                                                'są nieprawidłowe '
                                                                                                'lub dotyczą '
                                                                                                'innego AppID. '
                                                                                                'Niczego nie '
                                                                                                'nadpisano.',
                                                                                                'Las opciones del '
                                                                                                'runner no son '
                                                                                                'válidas o usan '
                                                                                                'otro AppID. No '
                                                                                                'se sobrescribió '
                                                                                                'nada.'),
 'Close the launcher before changing frame-generation access': ('Zamknij launcher przed zmianą dostępu do '
                                                                'generowania klatek.',
                                                                'Cierra el lanzador antes de cambiar el acceso '
                                                                'para generación.'),
 "afmf-linux is an independent project, not AMD's official Linux driver feature. Fixed 2× generation; published packages need a 64-bit application using Vulkan 1.1 or newer.": ('afmf-linux '
                                                                                                                                                                                 'to '
                                                                                                                                                                                 'niezależny '
                                                                                                                                                                                 'projekt, '
                                                                                                                                                                                 'a '
                                                                                                                                                                                 'nie '
                                                                                                                                                                                 'oficjalna '
                                                                                                                                                                                 'funkcja '
                                                                                                                                                                                 'sterownika '
                                                                                                                                                                                 'AMD '
                                                                                                                                                                                 'dla '
                                                                                                                                                                                 'Linuksa. '
                                                                                                                                                                                 'Stałe '
                                                                                                                                                                                 '2×; '
                                                                                                                                                                                 'pakiety '
                                                                                                                                                                                 'wymagają '
                                                                                                                                                                                 '64-bitowej '
                                                                                                                                                                                 'aplikacji '
                                                                                                                                                                                 'używającej '
                                                                                                                                                                                 'Vulkan '
                                                                                                                                                                                 '1.1 '
                                                                                                                                                                                 'lub '
                                                                                                                                                                                 'nowszego.',
                                                                                                                                                                                 'afmf-linux '
                                                                                                                                                                                 'es '
                                                                                                                                                                                 'un '
                                                                                                                                                                                 'proyecto '
                                                                                                                                                                                 'independiente, '
                                                                                                                                                                                 'no '
                                                                                                                                                                                 'una '
                                                                                                                                                                                 'función '
                                                                                                                                                                                 'oficial '
                                                                                                                                                                                 'del '
                                                                                                                                                                                 'controlador '
                                                                                                                                                                                 'AMD '
                                                                                                                                                                                 'para '
                                                                                                                                                                                 'Linux. '
                                                                                                                                                                                 'Generación '
                                                                                                                                                                                 'fija '
                                                                                                                                                                                 '2×; '
                                                                                                                                                                                 'los '
                                                                                                                                                                                 'paquetes '
                                                                                                                                                                                 'requieren '
                                                                                                                                                                                 'una '
                                                                                                                                                                                 'aplicación '
                                                                                                                                                                                 'de '
                                                                                                                                                                                 '64 '
                                                                                                                                                                                 'bits '
                                                                                                                                                                                 'con '
                                                                                                                                                                                 'Vulkan '
                                                                                                                                                                                 '1.1 '
                                                                                                                                                                                 'o '
                                                                                                                                                                                 'posterior.'),
 "GameOpti's shared component": ('Wspólny komponent GameOpti', 'Componente compartido de GameOpti'),
 'Independent installations were detected and will be preserved. GameOpti uses a separate verified component; its launch plan disables competing generators for this game.': ('Wykryto '
                                                                                                                                                                              'niezależne '
                                                                                                                                                                              'instalacje, '
                                                                                                                                                                              'które '
                                                                                                                                                                              'zostaną '
                                                                                                                                                                              'zachowane. '
                                                                                                                                                                              'GameOpti '
                                                                                                                                                                              'używa '
                                                                                                                                                                              'osobnego, '
                                                                                                                                                                              'sprawdzonego '
                                                                                                                                                                              'komponentu; '
                                                                                                                                                                              'jego '
                                                                                                                                                                              'plan '
                                                                                                                                                                              'startu '
                                                                                                                                                                              'wyłącza '
                                                                                                                                                                              'konkurencyjne '
                                                                                                                                                                              'generatory '
                                                                                                                                                                              'dla '
                                                                                                                                                                              'tej '
                                                                                                                                                                              'gry.',
                                                                                                                                                                              'Se '
                                                                                                                                                                              'detectaron '
                                                                                                                                                                              'instalaciones '
                                                                                                                                                                              'independientes '
                                                                                                                                                                              'y '
                                                                                                                                                                              'se '
                                                                                                                                                                              'conservarán. '
                                                                                                                                                                              'GameOpti '
                                                                                                                                                                              'usa '
                                                                                                                                                                              'un '
                                                                                                                                                                              'componente '
                                                                                                                                                                              'separado '
                                                                                                                                                                              'y '
                                                                                                                                                                              'verificado; '
                                                                                                                                                                              'su '
                                                                                                                                                                              'plan '
                                                                                                                                                                              'desactiva '
                                                                                                                                                                              'generadores '
                                                                                                                                                                              'competidores '
                                                                                                                                                                              'para '
                                                                                                                                                                              'este '
                                                                                                                                                                              'juego.'),
 'Cap real FPS in the game. This backend has no separate base-cap control. A Gamescope output limit is not a base-FPS cap.': ('Ogranicz '
                                                                                                                              'rzeczywiste '
                                                                                                                              'FPS '
                                                                                                                              'w '
                                                                                                                              'grze. '
                                                                                                                              'Ten '
                                                                                                                              'backend '
                                                                                                                              'nie '
                                                                                                                              'ma '
                                                                                                                              'osobnego '
                                                                                                                              'limitu '
                                                                                                                              'bazowego. '
                                                                                                                              'Limit '
                                                                                                                              'wyjściowy '
                                                                                                                              'Gamescope '
                                                                                                                              'nie '
                                                                                                                              'jest '
                                                                                                                              'limitem '
                                                                                                                              'bazowych '
                                                                                                                              'FPS.',
                                                                                                                              'Limita '
                                                                                                                              'los '
                                                                                                                              'FPS '
                                                                                                                              'reales '
                                                                                                                              'en '
                                                                                                                              'el '
                                                                                                                              'juego. '
                                                                                                                              'Este '
                                                                                                                              'backend '
                                                                                                                              'no '
                                                                                                                              'tiene '
                                                                                                                              'un '
                                                                                                                              'límite '
                                                                                                                              'base '
                                                                                                                              'propio. '
                                                                                                                              'El '
                                                                                                                              'límite '
                                                                                                                              'de '
                                                                                                                              'salida '
                                                                                                                              'de '
                                                                                                                              'Gamescope '
                                                                                                                              'no '
                                                                                                                              'es '
                                                                                                                              'un '
                                                                                                                              'límite '
                                                                                                                              'de '
                                                                                                                              'FPS '
                                                                                                                              'base.'),
 "Runner setup also grants this launcher Flatpak read-only access to the selected component and access to this game's private configuration/log folder. Restore removes only GameOpti's grants; shared grants remain while another game needs them.": ('Konfiguracja '
                                                                                                                                                                                                                                                       'runnera '
                                                                                                                                                                                                                                                       'udostępnia '
                                                                                                                                                                                                                                                       'też '
                                                                                                                                                                                                                                                       'Flatpakowi '
                                                                                                                                                                                                                                                       'launchera '
                                                                                                                                                                                                                                                       'wybrany '
                                                                                                                                                                                                                                                       'komponent '
                                                                                                                                                                                                                                                       'tylko '
                                                                                                                                                                                                                                                       'do '
                                                                                                                                                                                                                                                       'odczytu '
                                                                                                                                                                                                                                                       'oraz '
                                                                                                                                                                                                                                                       'prywatny '
                                                                                                                                                                                                                                                       'katalog '
                                                                                                                                                                                                                                                       'konfiguracji/logów '
                                                                                                                                                                                                                                                       'tej '
                                                                                                                                                                                                                                                       'gry. '
                                                                                                                                                                                                                                                       'Przywracanie '
                                                                                                                                                                                                                                                       'usuwa '
                                                                                                                                                                                                                                                       'tylko '
                                                                                                                                                                                                                                                       'uprawnienia '
                                                                                                                                                                                                                                                       'GameOpti; '
                                                                                                                                                                                                                                                       'wspólne '
                                                                                                                                                                                                                                                       'pozostają, '
                                                                                                                                                                                                                                                       'gdy '
                                                                                                                                                                                                                                                       'potrzebuje '
                                                                                                                                                                                                                                                       'ich '
                                                                                                                                                                                                                                                       'inna '
                                                                                                                                                                                                                                                       'gra.',
                                                                                                                                                                                                                                                       'Configurar '
                                                                                                                                                                                                                                                       'el '
                                                                                                                                                                                                                                                       'runner '
                                                                                                                                                                                                                                                       'también '
                                                                                                                                                                                                                                                       'da '
                                                                                                                                                                                                                                                       'al '
                                                                                                                                                                                                                                                       'Flatpak '
                                                                                                                                                                                                                                                       'del '
                                                                                                                                                                                                                                                       'lanzador '
                                                                                                                                                                                                                                                       'acceso '
                                                                                                                                                                                                                                                       'de '
                                                                                                                                                                                                                                                       'lectura '
                                                                                                                                                                                                                                                       'al '
                                                                                                                                                                                                                                                       'componente '
                                                                                                                                                                                                                                                       'y '
                                                                                                                                                                                                                                                       'acceso '
                                                                                                                                                                                                                                                       'a '
                                                                                                                                                                                                                                                       'la '
                                                                                                                                                                                                                                                       'carpeta '
                                                                                                                                                                                                                                                       'privada '
                                                                                                                                                                                                                                                       'de '
                                                                                                                                                                                                                                                       'configuración/registros '
                                                                                                                                                                                                                                                       'de '
                                                                                                                                                                                                                                                       'este '
                                                                                                                                                                                                                                                       'juego. '
                                                                                                                                                                                                                                                       'Restaurar '
                                                                                                                                                                                                                                                       'elimina '
                                                                                                                                                                                                                                                       'solo '
                                                                                                                                                                                                                                                       'permisos '
                                                                                                                                                                                                                                                       'de '
                                                                                                                                                                                                                                                       'GameOpti; '
                                                                                                                                                                                                                                                       'los '
                                                                                                                                                                                                                                                       'compartidos '
                                                                                                                                                                                                                                                       'se '
                                                                                                                                                                                                                                                       'mantienen '
                                                                                                                                                                                                                                                       'mientras '
                                                                                                                                                                                                                                                       'otro '
                                                                                                                                                                                                                                                       'juego '
                                                                                                                                                                                                                                                       'los '
                                                                                                                                                                                                                                                       'necesite.'),
 'Steam Flatpak also needs the existing GameOpti runner folder and the org.freedesktop.Flatpak host bridge. Setup adds these per-user permissions only after confirmation; Restore preserves prior independent permissions.': ('Steam '
                                                                                                                                                                                                                               'Flatpak '
                                                                                                                                                                                                                               'potrzebuje '
                                                                                                                                                                                                                               'też '
                                                                                                                                                                                                                               'katalogu '
                                                                                                                                                                                                                               'istniejącego '
                                                                                                                                                                                                                               'runnera '
                                                                                                                                                                                                                               'GameOpti '
                                                                                                                                                                                                                               'i '
                                                                                                                                                                                                                               'mostu '
                                                                                                                                                                                                                               'hosta '
                                                                                                                                                                                                                               'org.freedesktop.Flatpak. '
                                                                                                                                                                                                                               'Konfiguracja '
                                                                                                                                                                                                                               'dodaje '
                                                                                                                                                                                                                               'te '
                                                                                                                                                                                                                               'uprawnienia '
                                                                                                                                                                                                                               'użytkownika '
                                                                                                                                                                                                                               'po '
                                                                                                                                                                                                                               'potwierdzeniu; '
                                                                                                                                                                                                                               'przywracanie '
                                                                                                                                                                                                                               'zachowuje '
                                                                                                                                                                                                                               'wcześniejsze '
                                                                                                                                                                                                                               'niezależne '
                                                                                                                                                                                                                               'uprawnienia.',
                                                                                                                                                                                                                               'Steam '
                                                                                                                                                                                                                               'Flatpak '
                                                                                                                                                                                                                               'también '
                                                                                                                                                                                                                               'necesita '
                                                                                                                                                                                                                               'la '
                                                                                                                                                                                                                               'carpeta '
                                                                                                                                                                                                                               'del '
                                                                                                                                                                                                                               'runner '
                                                                                                                                                                                                                               'de '
                                                                                                                                                                                                                               'GameOpti '
                                                                                                                                                                                                                               'y '
                                                                                                                                                                                                                               'el '
                                                                                                                                                                                                                               'puente '
                                                                                                                                                                                                                               'al '
                                                                                                                                                                                                                               'host '
                                                                                                                                                                                                                               'org.freedesktop.Flatpak. '
                                                                                                                                                                                                                               'Configurar '
                                                                                                                                                                                                                               'añade '
                                                                                                                                                                                                                               'estos '
                                                                                                                                                                                                                               'permisos '
                                                                                                                                                                                                                               'de '
                                                                                                                                                                                                                               'usuario '
                                                                                                                                                                                                                               'tras '
                                                                                                                                                                                                                               'confirmar; '
                                                                                                                                                                                                                               'Restaurar '
                                                                                                                                                                                                                               'conserva '
                                                                                                                                                                                                                               'los '
                                                                                                                                                                                                                               'permisos '
                                                                                                                                                                                                                               'independientes '
                                                                                                                                                                                                                               'previos.'),
 'Required Vulkan API version was not confirmed for the selected backend': ('Nie potwierdzono wymaganej wersji '
                                                                            'API Vulkan dla wybranego backendu.',
                                                                            'No se confirmó la versión requerida '
                                                                            'de Vulkan para este backend.'),
 'Interrupted component transaction; retry installation to recover': ('Przerwana transakcja komponentu. Ponów '
                                                                      'instalację, aby odzyskać stan.',
                                                                      'Transacción interrumpida. Reintenta la '
                                                                      'instalación para recuperar el estado.'),
 'Selected frame generator is not installed': ('Wybrany generator klatek nie jest zainstalowany.',
                                               'El generador seleccionado no está instalado.'),
 "MAKO's launcher would select its independent MangoHud configuration. Preserve it and select another generator, or disable the in-game HUD explicitly": ('Launcher '
                                                                                                                                                          'MAKO '
                                                                                                                                                          'wybrałby '
                                                                                                                                                          'swoją '
                                                                                                                                                          'niezależną '
                                                                                                                                                          'konfigurację '
                                                                                                                                                          'MangoHud. '
                                                                                                                                                          'Zachowaj '
                                                                                                                                                          'ją '
                                                                                                                                                          'i '
                                                                                                                                                          'wybierz '
                                                                                                                                                          'inny '
                                                                                                                                                          'generator '
                                                                                                                                                          'lub '
                                                                                                                                                          'jawnie '
                                                                                                                                                          'wyłącz '
                                                                                                                                                          'HUD '
                                                                                                                                                          'w '
                                                                                                                                                          'grze.',
                                                                                                                                                          'El '
                                                                                                                                                          'lanzador '
                                                                                                                                                          'de '
                                                                                                                                                          'MAKO '
                                                                                                                                                          'elegiría '
                                                                                                                                                          'su '
                                                                                                                                                          'configuración '
                                                                                                                                                          'independiente '
                                                                                                                                                          'de '
                                                                                                                                                          'MangoHud. '
                                                                                                                                                          'Consérvala '
                                                                                                                                                          'y '
                                                                                                                                                          'elige '
                                                                                                                                                          'otro '
                                                                                                                                                          'generador '
                                                                                                                                                          'o '
                                                                                                                                                          'desactiva '
                                                                                                                                                          'explícitamente '
                                                                                                                                                          'el '
                                                                                                                                                          'HUD '
                                                                                                                                                          'del '
                                                                                                                                                          'juego.'),
 "Standalone MAKO's host MangoHud integration is unavailable inside a launcher Flatpak. Disable the in-game HUD explicitly or select another generator": ('Integracja '
                                                                                                                                                          'MangoHud '
                                                                                                                                                          'hosta '
                                                                                                                                                          'w '
                                                                                                                                                          'samodzielnym '
                                                                                                                                                          'MAKO '
                                                                                                                                                          'nie '
                                                                                                                                                          'działa '
                                                                                                                                                          'we '
                                                                                                                                                          'Flatpaku '
                                                                                                                                                          'launchera. '
                                                                                                                                                          'Jawnie '
                                                                                                                                                          'wyłącz '
                                                                                                                                                          'HUD '
                                                                                                                                                          'w '
                                                                                                                                                          'grze '
                                                                                                                                                          'lub '
                                                                                                                                                          'wybierz '
                                                                                                                                                          'inny '
                                                                                                                                                          'generator.',
                                                                                                                                                          'La '
                                                                                                                                                          'integración '
                                                                                                                                                          'del '
                                                                                                                                                          'MangoHud '
                                                                                                                                                          'del '
                                                                                                                                                          'host '
                                                                                                                                                          'de '
                                                                                                                                                          'MAKO '
                                                                                                                                                          'independiente '
                                                                                                                                                          'no '
                                                                                                                                                          'está '
                                                                                                                                                          'disponible '
                                                                                                                                                          'en '
                                                                                                                                                          'el '
                                                                                                                                                          'Flatpak '
                                                                                                                                                          'del '
                                                                                                                                                          'lanzador. '
                                                                                                                                                          'Desactiva '
                                                                                                                                                          'explícitamente '
                                                                                                                                                          'el '
                                                                                                                                                          'HUD '
                                                                                                                                                          'del '
                                                                                                                                                          'juego '
                                                                                                                                                          'o '
                                                                                                                                                          'elige '
                                                                                                                                                          'otro '
                                                                                                                                                          'generador.'),
 'Installation': ('Instalacja', 'Instalación'),
 'Checking…': ('Sprawdzanie…', 'Comprobando…'),
 'Requirements': ('Wymagania', 'Requisitos'),
 'Ready': ('Gotowe', 'Listo'),
 'Action needed': ('Wymaga działania', 'Requiere atención'),
 'Saved settings': ('Zapisane ustawienia', 'Ajustes guardados'),
 'Not saved': ('Niezapisane', 'Sin guardar'),
 'Runtime of saved profile': ('Runtime zapisanego profilu', 'Ejecución del perfil guardado'),
 '1 · Choose a generator': ('1 · Wybierz generator', '1 · Elige un generador'),
 'Choose → Install → Resolve requirements → Configure → Apply': ('Wybierz → Zainstaluj → Spełnij wymagania → '
                                                                 'Skonfiguruj → Zastosuj',
                                                                 'Elegir → Instalar → Resolver requisitos → '
                                                                 'Configurar → Aplicar'),
 'No managed generator': ('Bez zarządzanego generatora', 'Sin generador gestionado'),
 'Fixed multiplier · Lossless Scaling': ('Stały mnożnik · Lossless Scaling',
                                         'Multiplicador fijo · Lossless Scaling'),
 'Fixed / adaptive · Lossless Scaling': ('Stały / adaptacyjny · Lossless Scaling',
                                         'Fijo / adaptativo · Lossless Scaling'),
 'Independent Vulkan layer · no purchase': ('Niezależna warstwa Vulkan · bez zakupu',
                                            'Capa Vulkan independiente · sin compra'),
 'Component missing': ('Brak komponentu', 'Falta el componente'),
 'OptiScaler conflict': ('Konflikt z OptiScaler', 'Conflicto con OptiScaler'),
 'Lossless Scaling required': ('Wymagane Lossless Scaling', 'Requiere Lossless Scaling'),
 'Component installed': ('Komponent zainstalowany', 'Componente instalado'),
 '2 · Install the shared component': ('2 · Zainstaluj wspólny komponent', '2 · Instala el componente compartido'),
 'Installed: %1. Downloading does not activate frame generation.': ('Zainstalowano: %1. Pobranie nie włącza '
                                                                    'generowania klatek.',
                                                                    'Instalado: %1. Descargar no activa la '
                                                                    'generación.'),
 '3 · Resolve requirements': ('3 · Spełnij wymagania', '3 · Resuelve los requisitos'),
 'afmf-linux cannot be used with the installed OptiScaler. Restore/remove it in its existing panel, or choose another generator. Nothing is removed automatically.': ('Nie '
                                                                                                                                                                      'można '
                                                                                                                                                                      'używać '
                                                                                                                                                                      'afmf-linux '
                                                                                                                                                                      'z '
                                                                                                                                                                      'zainstalowanym '
                                                                                                                                                                      'OptiScalerem. '
                                                                                                                                                                      'Przywróć/usuń '
                                                                                                                                                                      'go '
                                                                                                                                                                      'na '
                                                                                                                                                                      'jego '
                                                                                                                                                                      'obecnym '
                                                                                                                                                                      'panelu '
                                                                                                                                                                      'albo '
                                                                                                                                                                      'wybierz '
                                                                                                                                                                      'inny '
                                                                                                                                                                      'generator. '
                                                                                                                                                                      'Nic '
                                                                                                                                                                      'nie '
                                                                                                                                                                      'zostanie '
                                                                                                                                                                      'usunięte '
                                                                                                                                                                      'automatycznie.',
                                                                                                                                                                      'No '
                                                                                                                                                                      'se '
                                                                                                                                                                      'puede '
                                                                                                                                                                      'usar '
                                                                                                                                                                      'afmf-linux '
                                                                                                                                                                      'con '
                                                                                                                                                                      'OptiScaler '
                                                                                                                                                                      'instalado. '
                                                                                                                                                                      'Restáuralo/elimínalo '
                                                                                                                                                                      'en '
                                                                                                                                                                      'su '
                                                                                                                                                                      'panel '
                                                                                                                                                                      'existente '
                                                                                                                                                                      'o '
                                                                                                                                                                      'elige '
                                                                                                                                                                      'otro '
                                                                                                                                                                      'generador. '
                                                                                                                                                                      'No '
                                                                                                                                                                      'se '
                                                                                                                                                                      'elimina '
                                                                                                                                                                      'nada '
                                                                                                                                                                      'automáticamente.'),
 'Open OptiScaler restore / remove': ('Otwórz przywracanie / usuwanie OptiScaler',
                                      'Abrir restauración / eliminación de OptiScaler'),
 'Frame generation requires separately purchased Lossless Scaling. The free backend download does not include it. Generation with lsfg-vk / MAKO has not been tested here.': ('Generowanie '
                                                                                                                                                                              'wymaga '
                                                                                                                                                                              'osobno '
                                                                                                                                                                              'kupionego '
                                                                                                                                                                              'Lossless '
                                                                                                                                                                              'Scaling. '
                                                                                                                                                                              'Bezpłatny '
                                                                                                                                                                              'backend '
                                                                                                                                                                              'go '
                                                                                                                                                                              'nie '
                                                                                                                                                                              'zawiera. '
                                                                                                                                                                              'Generowanie '
                                                                                                                                                                              'przez '
                                                                                                                                                                              'lsfg-vk '
                                                                                                                                                                              '/ '
                                                                                                                                                                              'MAKO '
                                                                                                                                                                              'nie '
                                                                                                                                                                              'zostało '
                                                                                                                                                                              'tutaj '
                                                                                                                                                                              'przetestowane.',
                                                                                                                                                                              'La '
                                                                                                                                                                              'generación '
                                                                                                                                                                              'requiere '
                                                                                                                                                                              'comprar '
                                                                                                                                                                              'Lossless '
                                                                                                                                                                              'Scaling '
                                                                                                                                                                              'por '
                                                                                                                                                                              'separado. '
                                                                                                                                                                              'El '
                                                                                                                                                                              'backend '
                                                                                                                                                                              'gratuito '
                                                                                                                                                                              'no '
                                                                                                                                                                              'lo '
                                                                                                                                                                              'incluye. '
                                                                                                                                                                              'No '
                                                                                                                                                                              'se '
                                                                                                                                                                              'ha '
                                                                                                                                                                              'probado '
                                                                                                                                                                              'aquí '
                                                                                                                                                                              'la '
                                                                                                                                                                              'generación '
                                                                                                                                                                              'con '
                                                                                                                                                                              'lsfg-vk '
                                                                                                                                                                              '/ '
                                                                                                                                                                              'MAKO.'),
 "lsfg-vk 2.0 needs lsfg-vk.dll from Lossless Scaling's lsfg-vk Steam branch. Only VSync pacing is supported.": ('lsfg-vk '
                                                                                                                 '2.0 '
                                                                                                                 'wymaga '
                                                                                                                 'lsfg-vk.dll '
                                                                                                                 'z '
                                                                                                                 'gałęzi '
                                                                                                                 'Steam '
                                                                                                                 'lsfg-vk '
                                                                                                                 'programu '
                                                                                                                 'Lossless '
                                                                                                                 'Scaling. '
                                                                                                                 'Obsługuje '
                                                                                                                 'tylko '
                                                                                                                 'odstępy '
                                                                                                                 'klatek '
                                                                                                                 'oparte '
                                                                                                                 'na '
                                                                                                                 'VSync.',
                                                                                                                 'lsfg-vk '
                                                                                                                 '2.0 '
                                                                                                                 'requiere '
                                                                                                                 'lsfg-vk.dll '
                                                                                                                 'de '
                                                                                                                 'la '
                                                                                                                 'rama '
                                                                                                                 'lsfg-vk '
                                                                                                                 'de '
                                                                                                                 'Lossless '
                                                                                                                 'Scaling '
                                                                                                                 'en '
                                                                                                                 'Steam. '
                                                                                                                 'Solo '
                                                                                                                 'admite '
                                                                                                                 'cadencia '
                                                                                                                 'VSync.'),
 'MAKO Renderer 4.6 needs Lossless.dll from the public Lossless Scaling release. Decky is not required.': ('MAKO '
                                                                                                           'Renderer '
                                                                                                           '4.6 '
                                                                                                           'wymaga '
                                                                                                           'Lossless.dll '
                                                                                                           'z '
                                                                                                           'publicznego '
                                                                                                           'wydania '
                                                                                                           'Lossless '
                                                                                                           'Scaling. '
                                                                                                           'Decky '
                                                                                                           'nie '
                                                                                                           'jest '
                                                                                                           'potrzebne.',
                                                                                                           'MAKO '
                                                                                                           'Renderer '
                                                                                                           '4.6 '
                                                                                                           'requiere '
                                                                                                           'Lossless.dll '
                                                                                                           'de la '
                                                                                                           'versión '
                                                                                                           'pública '
                                                                                                           'de '
                                                                                                           'Lossless '
                                                                                                           'Scaling. '
                                                                                                           'No '
                                                                                                           'necesita '
                                                                                                           'Decky.'),
 'GameOpti never changes the Steam branch. File validation is separate for each backend and is not proof of generated frames.': ('GameOpti '
                                                                                                                                 'nigdy '
                                                                                                                                 'nie '
                                                                                                                                 'zmienia '
                                                                                                                                 'gałęzi '
                                                                                                                                 'Steam. '
                                                                                                                                 'Walidacja '
                                                                                                                                 'pliku '
                                                                                                                                 'jest '
                                                                                                                                 'osobna '
                                                                                                                                 'dla '
                                                                                                                                 'każdego '
                                                                                                                                 'backendu '
                                                                                                                                 'i '
                                                                                                                                 'nie '
                                                                                                                                 'potwierdza '
                                                                                                                                 'generowania '
                                                                                                                                 'klatek.',
                                                                                                                                 'GameOpti '
                                                                                                                                 'nunca '
                                                                                                                                 'cambia '
                                                                                                                                 'la '
                                                                                                                                 'rama '
                                                                                                                                 'de '
                                                                                                                                 'Steam. '
                                                                                                                                 'La '
                                                                                                                                 'validación '
                                                                                                                                 'del '
                                                                                                                                 'archivo '
                                                                                                                                 'es '
                                                                                                                                 'independiente '
                                                                                                                                 'por '
                                                                                                                                 'backend '
                                                                                                                                 'y '
                                                                                                                                 'no '
                                                                                                                                 'demuestra '
                                                                                                                                 'generación.'),
 'Validate again': ('Sprawdź ponownie', 'Validar de nuevo'),
 'afmf-linux needs no Lossless Scaling purchase. It is an independent project: fixed 2×, 64-bit applications using Vulkan 1.1 or newer.': ('afmf-linux '
                                                                                                                                           'nie '
                                                                                                                                           'wymaga '
                                                                                                                                           'zakupu '
                                                                                                                                           'Lossless '
                                                                                                                                           'Scaling. '
                                                                                                                                           'To '
                                                                                                                                           'niezależny '
                                                                                                                                           'projekt: '
                                                                                                                                           'stałe '
                                                                                                                                           '2×, '
                                                                                                                                           'aplikacje '
                                                                                                                                           '64-bitowe '
                                                                                                                                           'używające '
                                                                                                                                           'Vulkan '
                                                                                                                                           '1.1 '
                                                                                                                                           'lub '
                                                                                                                                           'nowszego.',
                                                                                                                                           'afmf-linux '
                                                                                                                                           'no '
                                                                                                                                           'requiere '
                                                                                                                                           'comprar '
                                                                                                                                           'Lossless '
                                                                                                                                           'Scaling. '
                                                                                                                                           'Es '
                                                                                                                                           'independiente: '
                                                                                                                                           '2× '
                                                                                                                                           'fijo, '
                                                                                                                                           'aplicaciones '
                                                                                                                                           'de '
                                                                                                                                           '64 '
                                                                                                                                           'bits '
                                                                                                                                           'con '
                                                                                                                                           'Vulkan '
                                                                                                                                           '1.1 '
                                                                                                                                           'o '
                                                                                                                                           'posterior.'),
 'Executable: %1 · %2-bit · detected API: %3': ('Plik gry: %1 · %2-bit · wykryte API: %3',
                                                'Ejecutable: %1 · %2 bits · API detectada: %3'),
 'Detection is inconclusive. Select the actual Vulkan / DXVK / VKD3D-Proton path above.': ('Wykrywanie jest '
                                                                                           'niejednoznaczne. '
                                                                                           'Wybierz powyżej '
                                                                                           'rzeczywistą ścieżkę '
                                                                                           'Vulkan / DXVK / '
                                                                                           'VKD3D-Proton.',
                                                                                           'La detección no es '
                                                                                           'concluyente. '
                                                                                           'Selecciona arriba la '
                                                                                           'ruta real Vulkan / '
                                                                                           'DXVK / VKD3D-Proton.'),
 'GPU requirements are not confirmed. Recheck and inspect the Vulkan probe in diagnostics.': ('Nie potwierdzono '
                                                                                              'wymagań GPU. '
                                                                                              'Sprawdź ponownie i '
                                                                                              'odczytaj sondę '
                                                                                              'Vulkan w '
                                                                                              'diagnostyce.',
                                                                                              'No se confirmaron '
                                                                                              'los requisitos de '
                                                                                              'GPU. Vuelve a '
                                                                                              'comprobar y '
                                                                                              'consulta la sonda '
                                                                                              'Vulkan en '
                                                                                              'diagnóstico.'),
 'The launching environment cannot read the component or its dependencies. Set up/repair the runner, then recheck; details are in diagnostics.': ('Środowisko '
                                                                                                                                                  'uruchamiające '
                                                                                                                                                  'grę '
                                                                                                                                                  'nie '
                                                                                                                                                  'odczytuje '
                                                                                                                                                  'komponentu '
                                                                                                                                                  'lub '
                                                                                                                                                  'jego '
                                                                                                                                                  'zależności. '
                                                                                                                                                  'Skonfiguruj/napraw '
                                                                                                                                                  'runner '
                                                                                                                                                  'i '
                                                                                                                                                  'sprawdź '
                                                                                                                                                  'ponownie; '
                                                                                                                                                  'szczegóły '
                                                                                                                                                  'są '
                                                                                                                                                  'w '
                                                                                                                                                  'diagnostyce.',
                                                                                                                                                  'El '
                                                                                                                                                  'entorno '
                                                                                                                                                  'que '
                                                                                                                                                  'inicia '
                                                                                                                                                  'el '
                                                                                                                                                  'juego '
                                                                                                                                                  'no '
                                                                                                                                                  'puede '
                                                                                                                                                  'leer '
                                                                                                                                                  'el '
                                                                                                                                                  'componente '
                                                                                                                                                  'o '
                                                                                                                                                  'sus '
                                                                                                                                                  'dependencias. '
                                                                                                                                                  'Configura/repara '
                                                                                                                                                  'el '
                                                                                                                                                  'runner '
                                                                                                                                                  'y '
                                                                                                                                                  'comprueba '
                                                                                                                                                  'de '
                                                                                                                                                  'nuevo; '
                                                                                                                                                  'hay '
                                                                                                                                                  'detalles '
                                                                                                                                                  'en '
                                                                                                                                                  'diagnóstico.'),
 "This game's Steam manifest cannot be verified. Check that its library is available, then recheck.": ('Nie można '
                                                                                                       'sprawdzić '
                                                                                                       'manifestu '
                                                                                                       'Steam tej '
                                                                                                       'gry. '
                                                                                                       'Sprawdź '
                                                                                                       'dostępność '
                                                                                                       'jej '
                                                                                                       'biblioteki '
                                                                                                       'i ponów '
                                                                                                       'odczyt.',
                                                                                                       'No se '
                                                                                                       'puede '
                                                                                                       'verificar '
                                                                                                       'el '
                                                                                                       'manifiesto '
                                                                                                       'Steam de '
                                                                                                       'este '
                                                                                                       'juego. '
                                                                                                       'Comprueba '
                                                                                                       'que su '
                                                                                                       'biblioteca '
                                                                                                       'esté '
                                                                                                       'disponible '
                                                                                                       'y '
                                                                                                       'reintenta.'),
 "Steam is updating this game's files. Finish or pause the operation in Steam, then recheck.": ('Steam '
                                                                                                'aktualizuje '
                                                                                                'pliki tej gry. '
                                                                                                'Zakończ lub '
                                                                                                'wstrzymaj '
                                                                                                'operację w Steam '
                                                                                                'i sprawdź '
                                                                                                'ponownie.',
                                                                                                'Steam está '
                                                                                                'actualizando los '
                                                                                                'archivos de este '
                                                                                                'juego. Termina o '
                                                                                                'pausa la '
                                                                                                'operación en '
                                                                                                'Steam y vuelve a '
                                                                                                'comprobar.'),
 'Steam reports a paused update, not an active file write.': ('Steam zgłasza wstrzymaną aktualizację, a nie '
                                                              'aktywny zapis plików.',
                                                              'Steam informa de una actualización pausada, no de '
                                                              'una escritura activa de archivos.'),
 'Runner configured. Apply saves settings for the next launch.': ('Runner skonfigurowany. Zastosuj zapisuje '
                                                                  'ustawienia na następny start.',
                                                                  'Runner configurado. Aplicar guarda los ajustes '
                                                                  'para el próximo inicio.'),
 'Runner not confirmed. Set it up before starting the game.': ('Runner niepotwierdzony. Skonfiguruj go przed '
                                                               'uruchomieniem gry.',
                                                               'Runner sin confirmar. Configúralo antes de '
                                                               'iniciar el juego.'),
 'Recheck requirements': ('Sprawdź wymagania ponownie', 'Comprobar requisitos'),
 'Repair runner setup': ('Napraw konfigurację runnera', 'Reparar configuración del runner'),
 'Open Steam downloads': ('Otwórz pobieranie Steam', 'Abrir descargas de Steam'),
 '4 · Configure': ('4 · Skonfiguruj', '4 · Configura'),
 'Install the component above to edit its settings. Requirements remain visible while controls are unavailable.': ('Zainstaluj '
                                                                                                                   'komponent '
                                                                                                                   'powyżej, '
                                                                                                                   'aby '
                                                                                                                   'zmieniać '
                                                                                                                   'jego '
                                                                                                                   'ustawienia. '
                                                                                                                   'Wymagania '
                                                                                                                   'pozostają '
                                                                                                                   'widoczne, '
                                                                                                                   'gdy '
                                                                                                                   'kontrolki '
                                                                                                                   'są '
                                                                                                                   'niedostępne.',
                                                                                                                   'Instala '
                                                                                                                   'el '
                                                                                                                   'componente '
                                                                                                                   'de '
                                                                                                                   'arriba '
                                                                                                                   'para '
                                                                                                                   'editar '
                                                                                                                   'sus '
                                                                                                                   'ajustes. '
                                                                                                                   'Los '
                                                                                                                   'requisitos '
                                                                                                                   'siguen '
                                                                                                                   'visibles '
                                                                                                                   'cuando '
                                                                                                                   'los '
                                                                                                                   'controles '
                                                                                                                   'no '
                                                                                                                   'están '
                                                                                                                   'disponibles.'),
 '5 · Apply saved settings': ('5 · Zastosuj ustawienia', '5 · Aplica los ajustes'),
 'Close the game before Apply. Saving does not start frame generation. Restart through the configured runner to test it.': ('Zamknij '
                                                                                                                            'grę '
                                                                                                                            'przed '
                                                                                                                            'Zastosuj. '
                                                                                                                            'Zapis '
                                                                                                                            'nie '
                                                                                                                            'uruchamia '
                                                                                                                            'generowania '
                                                                                                                            'klatek. '
                                                                                                                            'Przetestuj '
                                                                                                                            'je '
                                                                                                                            'po '
                                                                                                                            'ponownym '
                                                                                                                            'starcie '
                                                                                                                            'przez '
                                                                                                                            'skonfigurowany '
                                                                                                                            'runner.',
                                                                                                                            'Cierra '
                                                                                                                            'el '
                                                                                                                            'juego '
                                                                                                                            'antes '
                                                                                                                            'de '
                                                                                                                            'Aplicar. '
                                                                                                                            'Guardar '
                                                                                                                            'no '
                                                                                                                            'inicia '
                                                                                                                            'la '
                                                                                                                            'generación. '
                                                                                                                            'Reinicia '
                                                                                                                            'mediante '
                                                                                                                            'el '
                                                                                                                            'runner '
                                                                                                                            'configurado '
                                                                                                                            'para '
                                                                                                                            'probarla.'),
 "Before testing afmf-linux, turn off the game's own frame generation. OptiScaler must be restored/removed explicitly.": ('Przed '
                                                                                                                          'testem '
                                                                                                                          'afmf-linux '
                                                                                                                          'wyłącz '
                                                                                                                          'generowanie '
                                                                                                                          'klatek '
                                                                                                                          'w '
                                                                                                                          'samej '
                                                                                                                          'grze. '
                                                                                                                          'OptiScaler '
                                                                                                                          'trzeba '
                                                                                                                          'jawnie '
                                                                                                                          'przywrócić/usunąć.',
                                                                                                                          'Antes '
                                                                                                                          'de '
                                                                                                                          'probar '
                                                                                                                          'afmf-linux, '
                                                                                                                          'desactiva '
                                                                                                                          'la '
                                                                                                                          'generación '
                                                                                                                          'del '
                                                                                                                          'propio '
                                                                                                                          'juego. '
                                                                                                                          'OptiScaler '
                                                                                                                          'debe '
                                                                                                                          'restaurarse/eliminarse '
                                                                                                                          'explícitamente.'),
 'Resolve the requirements in step 3 before Apply.': ('Przed Zastosuj spełnij wymagania z kroku 3.',
                                                      'Resuelve los requisitos del paso 3 antes de Aplicar.'),
 "Off prevents managed generators on the next launch. Installed components and each backend's saved values are kept.": ('Wyłączone '
                                                                                                                        'blokuje '
                                                                                                                        'zarządzane '
                                                                                                                        'generatory '
                                                                                                                        'przy '
                                                                                                                        'następnym '
                                                                                                                        'starcie. '
                                                                                                                        'Zainstalowane '
                                                                                                                        'komponenty '
                                                                                                                        'i '
                                                                                                                        'zapisane '
                                                                                                                        'wartości '
                                                                                                                        'każdego '
                                                                                                                        'backendu '
                                                                                                                        'pozostają '
                                                                                                                        'zachowane.',
                                                                                                                        'Desactivado '
                                                                                                                        'bloquea '
                                                                                                                        'los '
                                                                                                                        'generadores '
                                                                                                                        'gestionados '
                                                                                                                        'en '
                                                                                                                        'el '
                                                                                                                        'próximo '
                                                                                                                        'inicio. '
                                                                                                                        'Se '
                                                                                                                        'conservan '
                                                                                                                        'los '
                                                                                                                        'componentes '
                                                                                                                        'instalados '
                                                                                                                        'y '
                                                                                                                        'los '
                                                                                                                        'valores '
                                                                                                                        'de '
                                                                                                                        'cada '
                                                                                                                        'backend.'),
 'Open OptiScaler': ('Otwórz OptiScaler', 'Abrir OptiScaler'),
 "Before testing afmf-linux, turn off the game's own frame generation.": ('Przed testem afmf-linux wyłącz '
                                                                          'generowanie klatek w samej grze.',
                                                                          'Antes de probar afmf-linux, desactiva '
                                                                          'la generación del propio juego.'),
 'A Steam update is currently active': ('Steam aktualizuje pliki tej gry. Sprawdź stan aktualizacji i ponów.',
                                        'Steam está actualizando este juego. Comprueba su estado y reintenta.')}

GPU_SHARED = {
    "Checking system readiness…": ("Sprawdzanie gotowości systemu…", "Comprobando el sistema…"),
    "Saved GameOpti protection: On": ("Zapisana ochrona GameOpti: włączona", "Protección guardada de GameOpti: activada"),
    "Saved GameOpti protection: Off": ("Zapisana ochrona GameOpti: wyłączona", "Protección guardada de GameOpti: desactivada"),
    "Unsaved editor changes": ("Niezapisane zmiany w edytorze", "Cambios del editor sin guardar"),
    "Checking game process…": ("Sprawdzanie procesu gry…", "Comprobando el proceso del juego…"),
    "Game process: protection verified in GameOpti scope": ("Proces gry: potwierdzono ochronę w scope GameOpti", "Proceso del juego: protección verificada en el scope de GameOpti"),
    "Game process: independent protection verified, including when GameOpti is Off": ("Proces gry: potwierdzono niezależną ochronę, także przy wyłączonym profilu GameOpti", "Proceso del juego: protección independiente verificada, incluso con GameOpti desactivado"),
    "Game process: protection absent in the observed cgroup chain": ("Proces gry: brak ochrony w zaobserwowanym łańcuchu cgroup", "Proceso del juego: sin protección en la cadena cgroup observada"),
    "Game process: protection unverified · incomplete GPU or cgroup evidence": ("Proces gry: ochrona niepotwierdzona · niepełne dane GPU lub cgroup", "Proceso del juego: protección sin verificar · datos incompletos de GPU o cgroup"),
    "No matching game process detected · runtime protection unverified": ("Nie wykryto pasującego procesu gry · ochrona podczas działania niepotwierdzona", "No se detectó el proceso del juego · protección en ejecución sin verificar"),
    "Observation: %1 · protection is best effort, not a measured benefit": ("Odczyt: %1 · ochrona jest warunkowa, nie dowodzi zmierzonej korzyści", "Observación: %1 · la protección es de mejor esfuerzo, no un beneficio medido"),
    "Measured benefit: unverified until a controlled comparison. Gamescope or desktop protection can remain active in both Off and On.": ("Zmierzona korzyść: niepotwierdzona bez kontrolowanego porównania. Ochrona Gamescope lub pulpitu może działać przy profilu wyłączonym i włączonym.", "Beneficio medido: sin verificar hasta una comparación controlada. La protección de Gamescope o del escritorio puede seguir activa tanto en Off como en On."),
    "dmem.current covers the whole cgroup, including other processes sharing it; it is not the game's private VRAM usage.": ("dmem.current obejmuje całą cgroup, również inne współdzielące ją procesy; nie jest prywatnym zużyciem VRAM gry.", "dmem.current abarca todo el cgroup, incluidos otros procesos que lo comparten; no es el uso privado de VRAM del juego."),
    "Both recordings show the same protection state. An unprotected-versus-protected comparison was not established.": ("W obu nagraniach zaobserwowano ten sam stan ochrony. Nie uzyskano porównania braku ochrony z ochroną.", "Ambas grabaciones muestran el mismo estado de protección. No se estableció una comparación sin protección frente a protección."),
    "Measured improvement unverified · recordings need actual game-process evidence of different protection or allocator settings.": ("Zmierzona poprawa niepotwierdzona · nagrania wymagają danych rzeczywistego procesu gry wykazujących różnicę ochrony lub alokatora.", "Mejora medida sin verificar · las grabaciones requieren evidencia del proceso real del juego de protección o asignador diferentes."),
    "Game detected, but protection was not confirmed. Inspect its cgroup and ancestor limits.": ("Wykryto grę, ale nie potwierdzono ochrony. Sprawdź jej cgroup i limity przodków.", "Juego detectado, pero protección sin confirmar. Revisa su cgroup y los límites de sus antecesores."),
    "The game is outside the managed scope. Any observed protection belongs to its current launcher or desktop integration.": ("Gra znajduje się poza zarządzanym scope. Wykryta ochrona pochodzi z jej obecnego launchera lub integracji pulpitu.", "El juego está fuera del scope gestionado. La protección observada corresponde a su lanzador o integración del escritorio actual."),
    "Without a desktop foreground booster or Gamescope, protection lasts for the game session, including when unfocused.": ("Bez boostera pierwszego planu pulpitu lub Gamescope ochrona trwa przez całą sesję gry, również po utracie fokusu.", "Sin un booster de primer plano del escritorio o Gamescope, la protección dura toda la sesión, incluso sin foco."),
    "UPLOAD profile unavailable: no actual Proton launch with verified 32/64-bit vkd3d support was found. Launch normally once, then refresh this page.": ("Profil UPLOAD niedostępny: brak rzeczywistego startu Protona z potwierdzoną obsługą vkd3d 32/64-bit. Uruchom raz normalnie i odśwież stronę.", "Perfil UPLOAD no disponible: no se encontró un inicio real de Proton con soporte vkd3d de 32/64 bits verificado. Inicia normalmente una vez y actualiza esta página."),
    "UPLOAD profile unavailable: a discrete GPU with a large visible VRAM BAR was not verified. Shared-memory GPUs keep the default allocator.": ("Profil UPLOAD niedostępny: nie potwierdzono dedykowanego GPU z dużym widocznym BAR VRAM. GPU z pamięcią współdzieloną zachowują domyślny alokator.", "Perfil UPLOAD no disponible: no se verificó una GPU dedicada con BAR grande de VRAM visible. Las GPU con memoria compartida mantienen el asignador predeterminado."),
    "Shared-memory GPU": ("GPU z pamięcią współdzieloną", "GPU con memoria compartida"),
    "The running driver exposes no dmem region. Protection needs a supported kernel and GPU driver plus dmemcg-booster; GameOpti will not install experimental kernels or drivers.": ("Uruchomiony sterownik nie udostępnia regionu dmem. Ochrona wymaga obsługi w kernelu i sterowniku GPU oraz dmemcg-booster; GameOpti nie instaluje eksperymentalnych kerneli ani sterowników.", "El controlador en uso no expone una región dmem. La protección requiere kernel y controlador compatibles más dmemcg-booster; GameOpti no instala kernels ni controladores experimentales."),
    "vkd3d and no_upload_hvv observed in the game · memory savings still need measurement": ("Wykryto vkd3d i no_upload_hvv w grze · oszczędność pamięci wymaga pomiaru", "vkd3d y no_upload_hvv observados en el juego · el ahorro de memoria requiere medición"),
    "Sampled frametime spikes: %1 · RAM PSI full: %2 µs · GPU errors: %3": ("Próbki z przycięciami: %1 · RAM PSI full: %2 µs · błędy GPU: %3", "Muestras con picos: %1 · RAM PSI full: %2 µs · errores GPU: %3"),
    "Spikes count logged samples above max(50 ms, 2× median); they are not a count of every rendered frame. RAM PSI is system-wide. Kernel errors cover the recording interval and may come from other applications.": ("Przycięcia liczą zapisane próbki powyżej max(50 ms, 2× mediana), a nie wszystkie wyrenderowane klatki. RAM PSI dotyczy całego systemu. Błędy kernela obejmują czas nagrania i mogą pochodzić z innych aplikacji.", "Los picos cuentan muestras registradas superiores a max(50 ms, 2× mediana), no todos los fotogramas renderizados. RAM PSI abarca todo el sistema. Los errores del kernel cubren el intervalo grabado y pueden proceder de otras aplicaciones."),
    "Last launch returned code %1. Inspect its session log; this alone does not establish a GPU-memory fault.": ("Ostatni start zwrócił kod %1. Sprawdź log sesji; sam kod nie dowodzi usterki pamięci GPU.", "El último inicio devolvió el código %1. Revisa el registro de sesión; el código por sí solo no demuestra un fallo de memoria de GPU."),
    "GPU memory": ("Pamięć GPU", "Memoria de GPU"),
    "The GPU-memory operation could not be started": ("Nie udało się rozpocząć operacji pamięci GPU", "No se pudo iniciar la operación de memoria de GPU"),
    "Protect the game's GPU allocations under memory pressure. This does not increase physical VRAM or reduce texture quality.": ("Chroń alokacje GPU gry przy presji pamięci. Nie zwiększa to fizycznej pamięci VRAM ani nie obniża jakości tekstur.", "Protege las asignaciones de GPU del juego bajo presión de memoria. No aumenta la VRAM física ni reduce la calidad de las texturas."),
    "System ready · dmemcg-booster is running": ("System gotowy · dmemcg-booster działa", "Sistema listo · dmemcg-booster está activo"),
    "System preparation required for GameOpti protection": ("Ochrona GameOpti wymaga przygotowania systemu", "La protección de GameOpti requiere preparar el sistema"),
    "Running kernel: %1 · memory regions: %2": ("Uruchomiony kernel: %1 · regiony pamięci: %2", "Kernel en uso: %1 · regiones de memoria: %2"),
    "TTM eviction protection: running-module evidence found (CachyOS backport / upstream mechanism).": ("Ochrona przed eksmisją TTM: potwierdzono mechanizm w uruchomionym module (backport CachyOS / upstream).", "Protección de expulsión TTM: evidencia en el módulo en uso (backport de CachyOS / mecanismo upstream)."),
    "TTM eviction improvements are unverified in this running build. A kernel version alone is insufficient.": ("Ulepszenia eksmisji TTM nie są potwierdzone w tym uruchomionym buildzie. Sam numer kernela nie wystarcza.", "Las mejoras de expulsión TTM no están verificadas en esta compilación en uso. La versión del kernel por sí sola no basta."),
    "Prepare host (Polkit)": ("Przygotuj host (Polkit)", "Preparar host (Polkit)"),
    "Restore GameOpti system setup": ("Przywróć zmiany systemowe GameOpti", "Restaurar cambios de sistema de GameOpti"),
    "Setup uses the distro's dmemcg-booster and systemd. Restore keeps installed packages and independent service settings; it removes only GameOpti's setup changes. Per-game Off does not stop system services.": ("Przygotowanie używa pakietu dmemcg-booster dystrybucji i systemd. Przywracanie zachowuje pakiety oraz niezależne ustawienia usług i cofa tylko zmiany GameOpti. Wyłączenie profilu gry nie zatrzymuje usług systemowych.", "La preparación usa dmemcg-booster de la distribución y systemd. La restauración conserva paquetes y ajustes independientes de servicios; solo revierte cambios de GameOpti. Desactivar el perfil no detiene servicios del sistema."),
    "This launcher cannot invoke the host scope. Use native Steam, Heroic, Lutris or a configured manual game. A launcher Flatpak needs its own supported integration; protection is not simulated.": ("Ten launcher nie może wywołać scope hosta. Użyj natywnego Steam, Heroic, Lutris lub skonfigurowanej gry ręcznej. Launcher we Flatpaku potrzebuje własnej obsługiwanej integracji; ochrona nie jest symulowana.", "Este lanzador no puede invocar el scope del host. Usa Steam, Heroic o Lutris nativos, o un juego manual configurado. Un lanzador Flatpak necesita su propia integración compatible; no se simula la protección."),
    "Protect this game's GPU memory": ("Chroń pamięć GPU tej gry", "Proteger la memoria de GPU de este juego"),
    "A private systemd scope is created before game allocations. Plasma/Gamescope may adjust foreground protection. Existing allocations are not moved: restart the game after Apply.": ("Osobny scope systemd powstaje przed alokacjami gry. Plasma/Gamescope mogą regulować ochronę pierwszego planu. Istniejące alokacje nie są przenoszone: po Zastosuj uruchom grę ponownie.", "Se crea un scope privado de systemd antes de las asignaciones del juego. Plasma/Gamescope pueden ajustar la protección en primer plano. Las asignaciones existentes no se mueven: reinicia el juego tras Aplicar."),
    "Graphics API / launch path": ("API graficzne / ścieżka uruchamiania", "API gráfica / ruta de inicio"),
    "Automatic": ("Automatycznie", "Automático"),
    "Detected: %1 · vkd3d: %2": ("Wykryto: %1 · vkd3d: %2", "Detectado: %1 · vkd3d: %2"),
    "not established by an actual launch": ("niepotwierdzone rzeczywistym uruchomieniem", "no establecido mediante un inicio real"),
    "Memory-pressure profile (separate from protection)": ("Profil presji pamięci (osobno od ochrony)", "Perfil de presión de memoria (separado de la protección)"),
    "Default allocator behaviour": ("Domyślne działanie alokatora", "Comportamiento predeterminado del asignador"),
    "D3D12: keep UPLOAD heaps in system memory": ("D3D12: sterty UPLOAD w pamięci systemowej", "D3D12: mantener heaps UPLOAD en memoria del sistema"),
    "Optional no_upload_hvv needs verified vkd3d support and a discrete GPU with ReBAR. It can free VRAM but slow GPU access. vkd3d already reserves only part of VRAM for UPLOAD. DXVK's automatic defragmentation stays at its default. Reported-VRAM overrides are not memory savings.": ("Opcjonalne no_upload_hvv wymaga potwierdzonej obsługi w vkd3d i dedykowanego GPU z ReBAR. Może zwolnić VRAM, ale spowolnić dostęp GPU. vkd3d już domyślnie rezerwuje tylko część VRAM dla UPLOAD. Automatyczna defragmentacja DXVK pozostaje domyślna. Zmiana raportowanej VRAM nie oszczędza pamięci.", "no_upload_hvv es opcional y requiere soporte verificado de vkd3d y GPU dedicada con ReBAR. Puede liberar VRAM pero ralentizar el acceso de la GPU. vkd3d ya reserva solo parte de la VRAM para UPLOAD. La desfragmentación automática de DXVK mantiene su valor predeterminado. Cambiar la VRAM declarada no ahorra memoria."),
    "Apply for next launch": ("Zastosuj przy następnym uruchomieniu", "Aplicar al próximo inicio"),
    "Reset editor to defaults": ("Przywróć domyślne w edytorze", "Restablecer valores del editor"),
    "Applying GPU-memory operation…": ("Trwa operacja pamięci GPU…", "Aplicando operación de memoria de GPU…"),
    "Effective protection verified in the actual game process": ("Potwierdzono skuteczną ochronę w rzeczywistym procesie gry", "Protección efectiva verificada en el proceso real del juego"),
    "Configured · actual game protection not verified": ("Skonfigurowano · ochrona rzeczywistej gry niepotwierdzona", "Configurado · protección real del juego sin verificar"),
    "GameOpti profile Off · independent system protection may still apply": ("Profil GameOpti wyłączony · niezależna ochrona systemowa może nadal działać", "Perfil de GameOpti desactivado · puede seguir activa la protección independiente del sistema"),
    "Measure Off and On with the same scene and settings using the existing Baseline / Comparison recording below. GPU telemetry is saved beside MangoHud logs: VRAM/GTT, DRM residency where exposed, RAM pressure and process RSS. Per-process Vulkan budget can be unavailable. Compare FPS, p95/p99 and stutters; lower VRAM alone is not proof of improvement.": ("Nagraj Wył. i Wł. w tej samej scenie i ustawieniach, używając istniejącego pomiaru bazowego/porównawczego poniżej. Telemetria GPU jest zapisywana obok logów MangoHud: VRAM/GTT, dostępna rezydencja DRM, presja RAM i RSS procesów. Budżet Vulkan procesu może być niedostępny. Porównuj FPS, p95/p99 i przycięcia; niższa VRAM sama nie dowodzi poprawy.", "Graba Desactivado y Activado con la misma escena y ajustes usando la medición base/comparativa existente. La telemetría se guarda junto a los registros MangoHud: VRAM/GTT, residencia DRM disponible, presión de RAM y RSS. El presupuesto Vulkan por proceso puede no estar disponible. Compara FPS, p95/p99 y tirones; menos VRAM por sí sola no demuestra una mejora."),
    "I compared the same scene, duration and graphics settings": ("Porównałem tę samą scenę, czas i ustawienia grafiki", "Comparé la misma escena, duración y ajustes gráficos"),
    "Compare GPU-memory recordings": ("Porównaj nagrania pamięci GPU", "Comparar grabaciones de memoria de GPU"),
    "Measured improvement in this pair: FPS ≥ +5%, p99 ≤ −5%, p95 did not regress by more than 5%. Repeat the test; one pair is not a guarantee.": ("Zmierzona poprawa w tej parze: FPS ≥ +5%, p99 ≤ −5%, regresja p95 nie przekroczyła 5%. Powtórz test; jedna para nie daje gwarancji.", "Mejora medida en esta pareja: FPS ≥ +5%, p99 ≤ −5%, p95 no empeoró más del 5%. Repite la prueba; una pareja no es garantía."),
    "Comparable recordings found; the improvement criteria were not met.": ("Znaleziono porównywalne nagrania; kryteria poprawy nie zostały spełnione.", "Se encontraron grabaciones comparables; no se cumplieron los criterios de mejora."),
    "Measured improvement unverified · two valid recordings with different memory profiles are required.": ("Poprawa pomiarowa niepotwierdzona · wymagane dwa poprawne nagrania z różnymi profilami pamięci.", "Mejora medida sin verificar · se necesitan dos grabaciones válidas con perfiles de memoria distintos."),
    "Before": ("Przed", "Antes"), "After": ("Po", "Después"),
    "Peak device VRAM / GTT: %1 / %2 MiB · process RSS sum: %3 MiB": ("Szczyt VRAM / GTT urządzenia: %1 / %2 MiB · suma RSS procesów: %3 MiB", "Pico VRAM / GTT del dispositivo: %1 / %2 MiB · suma RSS de procesos: %3 MiB"),
    "Device totals include other applications. Summed RSS can count shared pages more than once. Missing metrics are shown as —; GPU budget and inaccessible driver errors are not invented.": ("Sumy urządzenia obejmują inne aplikacje. Suma RSS może wielokrotnie liczyć strony współdzielone. Brakujące metryki oznaczono —; budżet GPU i niedostępne błędy sterownika nie są zgadywane.", "Los totales del dispositivo incluyen otras aplicaciones. La suma RSS puede contar páginas compartidas varias veces. Las métricas ausentes aparecen como —; no se inventan presupuestos GPU ni errores inaccesibles del controlador."),
    "Advanced controls and measurements": ("Ustawienia zaawansowane i pomiary", "Controles avanzados y mediciones"),
}

POST_SHARED = {
    "Saved effects: %1": ("Zapisane efekty: %1", "Efectos guardados: %1"),
    "Module observed in game PID %1 at %2. This alone does not verify the image effects.": ("Moduł wykryto w procesie gry PID %1 o %2. Samo to nie potwierdza efektów obrazu.", "Módulo observado en el proceso del juego PID %1 a las %2. Esto por sí solo no verifica los efectos de imagen."),
    "Close the game before applying, installing or removing the integration.": ("Zamknij grę przed zastosowaniem, instalacją lub usunięciem integracji.", "Cierra el juego antes de aplicar, instalar o eliminar la integración."),
    "No effects selected. The backend stays installed.": ("Nie wybrano efektów. Backend pozostaje zainstalowany.", "No hay efectos seleccionados. El backend sigue instalado."),
    "The upscaler profile records a loader which is now missing. Verify its installation before installing ReShade: %1": ("Profil upscalera wskazuje loader, którego teraz brakuje. Zweryfikuj instalację przed instalowaniem ReShade: %1", "El perfil del escalador registra un cargador que falta. Verifica su instalación antes de instalar ReShade: %1"),
    "OptiScaler upstream documents plugin/LoadReshade loading. This managed installation keeps it unchanged until coexistence is verified for the installed release.": ("Dokumentacja OptiScaler opisuje ładowanie przez plugins/LoadReshade. Zarządzana instalacja pozostawia je bez zmian do czasu weryfikacji współdziałania z zainstalowanym wydaniem.", "OptiScaler documenta la carga por plugins/LoadReshade. La instalación gestionada no la modifica hasta verificar la coexistencia con la versión instalada."),
    "Adaptive sharpening; can duplicate upscaler sharpening.": ("Adaptacyjne wyostrzanie; może dublować ostrość upscalera.", "Nitidez adaptativa; puede duplicar la del escalador."),
    "Adjust colour intensity while protecting already saturated colours.": ("Zmiana intensywności barw z ochroną już nasyconych kolorów.", "Ajusta la intensidad protegiendo los colores ya saturados."),
    "Adjust contrast with a colour curve.": ("Zmiana kontrastu za pomocą krzywej kolorów.", "Ajusta el contraste mediante una curva de color."),
    "Apply saves the configuration. Restart the game to load changed effects, order or parameters. The shortcut only toggles the effects already loaded.": ("Zastosuj zapisuje konfigurację. Uruchom grę ponownie, aby wczytać zmiany efektów, kolejności lub parametrów. Skrót przełącza tylko efekty już załadowane.", "Aplicar guarda la configuración. Reinicia el juego para cargar cambios de efectos, orden o parámetros. El atajo solo alterna los efectos ya cargados."),
    "Apply settings": ("Zastosuj ustawienia", "Aplicar ajustes"),
    "Automatic API detection is inconclusive. Choose the API used by the game; this does not mean the game is unsupported.": ("Automatyczne wykrywanie API jest niejednoznaczne. Wybierz API używane przez grę; nie oznacza to braku obsługi gry.", "La detección automática de API no es concluyente. Elige la API del juego; esto no significa que no sea compatible."),
    "Colour grading from your selected PNG/CUBE LUT.": ("Korekcja kolorów według wybranego LUT PNG/CUBE.", "Gradación de color con la LUT PNG/CUBE elegida."),
    "Colour-edge antialiasing; no depth access required.": ("Wygładzanie krawędzi koloru; nie wymaga dostępu do głębi.", "Suavizado de bordes de color; no requiere acceso a profundidad."),
    "Diagnostics and installation": ("Diagnostyka i instalacja", "Diagnóstico e instalación"),
    "Enable this effect to adjust its parameters.": ("Włącz efekt, aby zmieniać jego parametry.", "Activa el efecto para ajustar sus parámetros."),
    "Exposure, gamma and colour adjustment. Useful for a visible A/B test.": ("Regulacja ekspozycji, gammy i kolorów. Przydatna do wyraźnego porównania A/B.", "Ajustes de exposición, gamma y color. Útil para una comparación A/B visible."),
    "Fast edge smoothing; may soften fine detail.": ("Szybkie wygładzanie krawędzi; może zmiękczać drobne szczegóły.", "Suavizado rápido de bordes; puede difuminar detalles finos."),
    "Fine-detail sharpening with a configurable clamp.": ("Wyostrzanie drobnych szczegółów z regulowanym limitem.", "Nitidez de detalles finos con límite ajustable."),
    "Game: %1 · session: %2": ("Gra: %1 · sesja: %2", "Juego: %1 · sesión: %2"),
    "Keep OptiScaler and use vkBasalt, or restore/remove the upscaler on its own page first. No verified chaining is configured.": ("Zachowaj OptiScaler i użyj vkBasalt albo najpierw przywróć/usuń upscaler na jego stronie. Zweryfikowane łączenie loaderów nie jest skonfigurowane.", "Conserva OptiScaler y usa vkBasalt, o restaura/elimina el escalador en su página. No se ha configurado un encadenamiento verificado."),
    "Luminance sharpening with noise suppression.": ("Wyostrzanie luminancji z tłumieniem szumu.", "Nitidez de luminancia con reducción de ruido."),
    "Neutral configuration: vkBasalt is not activated without selected effects.": ("Konfiguracja neutralna: vkBasalt nie jest aktywowany bez wybranych efektów.", "Configuración neutra: vkBasalt no se activa sin efectos seleccionados."),
    "No fresh runtime evidence for these saved settings. Restart through the configured launcher, then refresh diagnostics.": ("Brak świeżego dowodu uruchomienia z zapisanymi ustawieniami. Uruchom grę ponownie przez skonfigurowany launcher, a potem odśwież diagnostykę.", "Sin pruebas recientes de ejecución con estos ajustes. Reinicia mediante el lanzador configurado y actualiza el diagnóstico."),
    "No runner session": ("Brak sesji runnera", "Sin sesión del runner"),
    "Parameters": ("Parametry", "Parámetros"),
    "Presets": ("Presety", "Preajustes"),
    "ReShade loader conflict. Existing files stay intact: %1": ("Konflikt loadera ReShade. Istniejące pliki są zachowane: %1", "Conflicto del cargador ReShade. Se conservan los archivos existentes: %1"),
    "Reset changes the editor only: no effects, default parameter values and Home shortcut. Backend/API, imported files, backups and upscalers are kept. Press Apply to save.": ("Reset zmienia tylko edytor: brak efektów, domyślne parametry i skrót Home. Backend/API, importy, kopie i upscalery pozostają zachowane. Zapisz przyciskiem Zastosuj.", "Restablecer solo cambia el editor: sin efectos, parámetros predeterminados y atajo Inicio. Se conservan backend/API, importaciones, copias y escaladores. Pulsa Aplicar para guardar."),
    "Reset this effect's parameters": ("Przywróć domyślne parametry efektu", "Restablecer parámetros del efecto"),
    "Restore default post-processing settings": ("Przywróć domyślne ustawienia efektów", "Restablecer ajustes de posprocesado"),
    "Shader / configuration error": ("Błąd shadera / konfiguracji", "Error de shader / configuración"),
    "Steam runner is missing or its launch options are invalid. Copy the command below into Steam launch options.": ("Brakuje runnera Steam lub opcje uruchamiania są nieprawidłowe. Skopiuj poniższe polecenie do opcji uruchamiania Steam.", "Falta el runner Steam o sus opciones no son válidas. Copia el comando inferior en las opciones de lanzamiento de Steam."),
    "Unsaved changes": ("Niezapisane zmiany", "Cambios sin guardar"),
    "Main executable path relative to the game folder (keyboard)": ("Ścieżka głównego pliku względem folderu gry (klawiatura)", "Ruta del ejecutable principal relativa a la carpeta del juego (teclado)"),
    "Post-processing": ("Efekty obrazu", "Posprocesado"),
    "Configure ReShade / vkBasalt": ("Konfiguruj ReShade / vkBasalt", "Configurar ReShade / vkBasalt"),
    "D-pad: choose / adjust · A: confirm · B: close": ("Krzyżak: wybór / zmiana · A: potwierdź · B: zamknij", "Cruceta: elegir / ajustar · A: confirmar · B: cerrar"),
    "Close": ("Zamknij", "Cerrar"),
    "The post-processing task could not be started": ("Nie można rozpocząć zadania efektów obrazu", "No se pudo iniciar la tarea de posprocesado"),
    "Sharpness": ("Ostrość", "Nitidez"),
    "Denoise": ("Odszumianie", "Reducción de ruido"),
    "Subpixel filtering": ("Filtrowanie subpikseli", "Filtrado subpíxel"),
    "Edge threshold": ("Próg krawędzi", "Umbral de bordes"),
    "Darkness threshold": ("Próg ciemnych obszarów", "Umbral de zonas oscuras"),
    "Search steps": ("Kroki wyszukiwania", "Pasos de búsqueda"),
    "Diagonal steps": ("Kroki po przekątnej", "Pasos diagonales"),
    "Corner rounding": ("Zaokrąglanie narożników", "Redondeo de esquinas"),
    "Vibrance": ("Intensywność barw", "Intensidad de color"),
    "Gamma": ("Gamma", "Gamma"),
    "Exposure": ("Ekspozycja", "Exposición"),
    "Saturation": ("Nasycenie", "Saturación"),
    "Bleach": ("Odbarwienie", "Decoloración"),
    "Contrast": ("Kontrast", "Contraste"),
    "Sharpening limit": ("Limit wyostrzania", "Límite de nitidez"),
    "Denoised Luma Sharpening": ("Wyostrzanie luminancji z odszumianiem", "Nitidez de luminancia con reducción de ruido"),
    "SMAA (colour edges)": ("SMAA (krawędzie koloru)", "SMAA (bordes de color)"),
    "Optional image effects, not an FPS optimisation. Defaults are neutral. Use one backend per game.": ("Opcjonalne efekty obrazu - bez obietnicy wzrostu FPS. Ustawienia domyślne są neutralne. Wybierz jeden backend dla gry.", "Efectos de imagen opcionales, sin prometer más FPS. Los valores predeterminados son neutros. Usa un backend por juego."),
    "Sharpening can duplicate the upscaler's sharpening. Depth effects (AO/DoF) are not enabled: depth access must be verified in each game.": ("Wyostrzanie może dublować ostrość upscalera. Efekty głębi (AO/DoF) są wyłączone: dostęp do głębi wymaga sprawdzenia w każdej grze.", "La nitidez puede duplicar la del escalador. AO/DoF están desactivados: el acceso a profundidad debe verificarse en cada juego."),
    "Working…": ("Przetwarzanie…", "Procesando…"),
    "Error": ("Błąd", "Error"),
    "Runtime loaded · effects not verified": ("Załadowano w grze · efekty niezweryfikowane", "Cargado en ejecución · efectos sin verificar"),
    "Installed · runtime not verified": ("Zainstalowano · uruchomienie niezweryfikowane", "Instalado · ejecución sin verificar"),
    "Off": ("Wyłączone", "Desactivado"),
    "Steam Linux Runtime not verified; launch and check the log": ("Steam Linux Runtime niezweryfikowany; uruchom grę i sprawdź log", "Steam Linux Runtime sin verificar; inicia el juego y revisa el registro"),
    "Executable: %1 · %2-bit · API: %3": ("Plik wykonywalny: %1 · %2-bit · API: %3", "Ejecutable: %1 · %2 bits · API: %3"),
    "Backend": ("Backend", "Backend"),
    "Graphics path": ("Ścieżka graficzna", "Ruta gráfica"),
    "vkBasalt: Vulkan or DXVK/VKD3D. ReShade: Windows D3D/OpenGL. Auto uses EXE imports; select the actual API if detection is inconclusive.": ("vkBasalt: Vulkan lub DXVK/VKD3D. ReShade: Windows D3D/OpenGL. Automatyka czyta importy EXE; przy niepewnym wyniku wybierz rzeczywiste API.", "vkBasalt: Vulkan o DXVK/VKD3D. ReShade: D3D/OpenGL de Windows. La detección lee las importaciones EXE; selecciona la API real si no es concluyente."),
    "Automatic": ("Automatycznie", "Automático"),
    "Effect toggle": ("Skrót przełączania efektów", "Atajo de efectos"),
    "ReShade: Ctrl + selected key toggles effects; Home opens its in-game interface.": ("ReShade: Ctrl + wybrany klawisz przełącza efekty; Home otwiera interfejs w grze.", "ReShade: Ctrl + tecla elegida alterna los efectos; Inicio abre su interfaz en el juego."),
    "vkBasalt uses the selected X11 key. Restart the game after changing configuration; Wayland-native keyboard toggling is not guaranteed.": ("vkBasalt używa wybranego klawisza X11. Po zmianach uruchom grę ponownie; skrót w natywnym Waylandzie nie jest gwarantowany.", "vkBasalt usa la tecla X11 elegida. Reinicia el juego tras cambiar la configuración; el atajo no está garantizado en Wayland nativo."),
    "Neutral": ("Neutralny", "Neutro"),
    "Gentle colour": ("Delikatne kolory", "Color suave"),
    "AA + light sharpening": ("AA + lekkie wyostrzanie", "AA + nitidez ligera"),
    "Effect order: %1": ("Kolejność efektów: %1", "Orden de efectos: %1"),
    "None": ("Brak", "Ninguno"),
    "Move effect up": ("Przenieś efekt wyżej", "Subir efecto"),
    "Move effect down": ("Przenieś efekt niżej", "Bajar efecto"),
    "Anti-cheat files detected. Single-player/offline only; do not enter online modes with these modifications.": ("Wykryto pliki anti-cheat. Używaj tylko w trybie jednoosobowym/offline; nie wchodź do trybów online z tymi modyfikacjami.", "Se detectaron archivos antitrampas. Solo para un jugador/sin conexión; no entres en modos en línea con estas modificaciones."),
    "Existing files are preserved. Check loader conflicts: %1": ("Istniejące pliki pozostają zachowane. Sprawdź kolizje loaderów: %1", "Se conservan los archivos existentes. Revisa los conflictos de cargadores: %1"),
    "Install/apply verified components for this game? Existing loader DLLs will not be overwritten. Turning Off preserves custom presets in the game folder.": ("Zainstalować/zastosować zweryfikowane składniki dla tej gry? Istniejące DLL loaderów nie zostaną nadpisane. Wyłączenie zachowa własne presety w folderze gry.", "¿Instalar/aplicar componentes verificados para este juego? No se sobrescribirán DLL de cargadores existentes. Desactivar conserva los presets propios en la carpeta del juego."),
    "Confirm": ("Potwierdź", "Confirmar"),
    "Install / apply": ("Zainstaluj / zastosuj", "Instalar / aplicar"),
    "Off / restore": ("Wyłącz / przywróć", "Desactivar / restaurar"),
    "Import ReShade preset": ("Importuj preset ReShade", "Importar preset de ReShade"),
    "Select LUT": ("Wybierz LUT", "Elegir LUT"),
    "Apply launcher profile": ("Zastosuj profil launchera", "Aplicar perfil del lanzador"),
    "Launch through the GameOpti Steam runner or apply the Heroic/Lutris launcher profile. A host layer alone does not prove loading in a container. Restart the game after changes.": ("Uruchamiaj przez runner Steam GameOpti lub zastosuj profil Heroic/Lutris. Sama warstwa na hoście nie dowodzi ładowania w kontenerze. Po zmianach uruchom grę ponownie.", "Inicia mediante el runner Steam de GameOpti o aplica el perfil Heroic/Lutris. Una capa en el anfitrión no prueba su carga en un contenedor. Reinicia el juego tras los cambios."),
}


def _finish(translation: ET.Element, text: str) -> None:
    translation.text = text
    translation.attrib.pop("type", None)


MANGOHUD_SHARED = {'Existing MangoHud configuration is not managed by GameOpti': ('Istniejąca konfiguracja MangoHud nie jest zarządzana przez GameOpti', 'GameOpti no gestiona la configuración existente de MangoHud'), 'Edit value': ('Edytuj wartość', 'Editar valor'), 'Illustrative preview': ('Podgląd poglądowy', 'Vista ilustrativa'),
 'Sample values · approximate layout · not measurements': ('Przykładowe wartości · przybliżony układ · bez '
                                                           'pomiarów',
                                                           'Valores de ejemplo · diseño aproximado · sin '
                                                           'mediciones'),
 'sample': ('przykład', 'ejemplo'),
 'MangoHud shows no metrics with these settings': ('Te ustawienia nie wyświetlają żadnych metryk',
                                                   'Esta configuración no muestra métricas'),
 'Some values need their GPU, CPU, VRAM or FPS row enabled.': ('Niektóre wartości wymagają włączenia wiersza '
                                                               'GPU, CPU, VRAM lub FPS.',
                                                               'Algunos valores requieren activar su fila de '
                                                               'GPU, CPU, VRAM o FPS.'),
 'Real Vulkan preview': ('Rzeczywisty podgląd Vulkan', 'Vista real de Vulkan'),
 'Close': ('Zamknij', 'Cerrar'),
 "Opens host MangoHud + vkcube with this draft. No profile is saved; this is separate from the game's launcher runtime.": ('Uruchamia '
                                                                                                                           'MangoHud '
                                                                                                                           'i '
                                                                                                                           'vkcube '
                                                                                                                           'na '
                                                                                                                           'hoście '
                                                                                                                           'ze '
                                                                                                                           'szkicem '
                                                                                                                           'ustawień. '
                                                                                                                           'Nie '
                                                                                                                           'zapisuje '
                                                                                                                           'profilu; '
                                                                                                                           'to '
                                                                                                                           'osobny '
                                                                                                                           'test '
                                                                                                                           'poza '
                                                                                                                           'środowiskiem '
                                                                                                                           'launchera '
                                                                                                                           'gry.',
                                                                                                                           'Abre '
                                                                                                                           'MangoHud '
                                                                                                                           'y '
                                                                                                                           'vkcube '
                                                                                                                           'del '
                                                                                                                           'sistema '
                                                                                                                           'con '
                                                                                                                           'este '
                                                                                                                           'borrador. '
                                                                                                                           'No '
                                                                                                                           'guarda '
                                                                                                                           'el '
                                                                                                                           'perfil; '
                                                                                                                           'es '
                                                                                                                           'una '
                                                                                                                           'prueba '
                                                                                                                           'aparte '
                                                                                                                           'del '
                                                                                                                           'entorno '
                                                                                                                           'del '
                                                                                                                           'lanzador '
                                                                                                                           'del '
                                                                                                                           'juego.'),
 'Average FPS': ('Średnie FPS', 'FPS medios'),
 'Rolling statistics from MangoHud samples.': ('Bieżąca statystyka z próbek MangoHud.',
                                               'Estadística continua de las muestras de MangoHud.'),
 '1% low (MangoHud)': ('1% low (MangoHud)', '1% low (MangoHud)'),
 "MangoHud's documented 1% metric; needs enough samples.": ('Udokumentowana metryka 1% MangoHud; wymaga '
                                                            'zebrania próbek.',
                                                            'Métrica del 1% documentada por MangoHud; '
                                                            'necesita suficientes muestras.'),
 'Detailed frametime': ('Szczegóły czasu klatki', 'Tiempo de fotograma detallado'),
 'Minimum, average and maximum on the frametime graph.': ('Minimum, średnia i maksimum na wykresie czasu '
                                                          'klatki.',
                                                          'Mínimo, media y máximo en el gráfico de tiempo de '
                                                          'fotograma.'),
 'GPU memory clock': ('Taktowanie pamięci GPU', 'Frecuencia de memoria de GPU'),
 'Needs the VRAM row and driver support.': ('Wymaga wiersza VRAM i obsługi w sterowniku.',
                                            'Requiere la fila de VRAM y soporte del controlador.'),
 'GPU throttling': ('Ograniczanie taktowania GPU', 'Limitación de GPU'),
 'Shown only while a limit is active. No indicator does not mean measured zero.': ('Wskaźnik pojawia się '
                                                                                   'tylko podczas '
                                                                                   'ograniczenia. Jego brak '
                                                                                   'nie oznacza zmierzonego '
                                                                                   'zera.',
                                                                                   'Solo aparece cuando hay '
                                                                                   'una limitación activa. '
                                                                                   'Su ausencia no equivale '
                                                                                   'a un cero medido.'),
 'Disk I/O': ('Odczyt i zapis dysku', 'E/S de disco'),
 'Reads and writes of the monitored process, not the whole disk.': ('Odczyt i zapis monitorowanego procesu, '
                                                                    'nie całego dysku.',
                                                                    'Lecturas y escrituras del proceso '
                                                                    'monitorizado, no de todo el disco.'),
 'Swap usage': ('Użycie swap', 'Uso de swap'),
 'System swap allocation.': ('Przydzielona pamięć wymiany w systemie.',
                             'Memoria de intercambio asignada en el sistema.'),
 'Graphics API / version': ('API graficzne / wersja', 'API gráfica / versión'),
 'Vulkan driver': ('Sterownik Vulkan', 'Controlador Vulkan'),
 'Wine synchronization': ('Synchronizacja Wine', 'Sincronización de Wine'),
 'Wine synchronization method, not display VSync.': ('Metoda synchronizacji Wine, nie synchronizacja pionowa '
                                                     'obrazu.',
                                                     'Método de sincronización de Wine, no VSync de '
                                                     'pantalla.'),
 'Presentation mode': ('Tryb prezentacji', 'Modo de presentación'),
 'Swapchain mode; not input latency.': ('Tryb prezentacji obrazu; nie mierzy opóźnienia wejścia.',
                                        'Modo de presentación de la cadena de imágenes; no mide la latencia '
                                        'de entrada.'),
 'vkBasalt status': ('Stan vkBasalt', 'Estado de vkBasalt'),
 'Loaded-layer status, not proof of a visible effect.': ('Stan załadowania warstwy; nie potwierdza '
                                                         'widocznego efektu.',
                                                         'Estado de carga de la capa; no confirma un efecto '
                                                         'visible.'),
 'GPU load': ('Obciążenie GPU', 'Carga de GPU'),
 'CPU load': ('Obciążenie CPU', 'Carga de CPU'),
 'Reset this section': ('Resetuj tę sekcję', 'Restablecer esta sección'),
 'Unsaved · preview only': ('Niezapisane · tylko podgląd', 'Sin guardar · solo vista previa'),
 'Profile saved · restart game to apply': ('Profil zapisany · uruchom grę ponownie',
                                           'Perfil guardado · reinicia el juego para aplicarlo'),
 'Reset editor': ('Resetuj edytor', 'Restablecer editor'),
 'Apply': ('Zastosuj', 'Aplicar'),
 'Colour picker': ('Wybór koloru', 'Selector de color'),
 'Hex colour': ('Kolor HEX', 'Color HEX'),
 'Done': ('Gotowe', 'Listo'),
 'Unavailable in this runtime or MangoHud version. Your saved selection is preserved.': ('Niedostępne w tym '
                                                                                         'środowisku lub '
                                                                                         'wersji MangoHud. '
                                                                                         'Zapisany wybór '
                                                                                         'pozostaje '
                                                                                         'zachowany.',
                                                                                         'No disponible en '
                                                                                         'este entorno o '
                                                                                         'versión de '
                                                                                         'MangoHud. Se '
                                                                                         'conserva la '
                                                                                         'selección '
                                                                                         'guardada.'),
 'Monitoring graphs': ('Wykresy monitoringu', 'Gráficos de monitorización'),
 'Enable recording controls': ('Włącz sterowanie nagrywaniem', 'Activar controles de grabación'),
 'Recordings stay local and work with Performance Analysis. Start logging with the shortcut; enabling controls does not start a recording.': ('Nagrania '
                                                                                                                                              'są '
                                                                                                                                              'lokalne '
                                                                                                                                              'i '
                                                                                                                                              'współpracują '
                                                                                                                                              'z '
                                                                                                                                              'Analizą '
                                                                                                                                              'wydajności. '
                                                                                                                                              'Skrót '
                                                                                                                                              'rozpoczyna '
                                                                                                                                              'rejestrację; '
                                                                                                                                              'samo '
                                                                                                                                              'włączenie '
                                                                                                                                              'kontrolek '
                                                                                                                                              'jej '
                                                                                                                                              'nie '
                                                                                                                                              'uruchamia.',
                                                                                                                                              'Las '
                                                                                                                                              'grabaciones '
                                                                                                                                              'son '
                                                                                                                                              'locales '
                                                                                                                                              'y '
                                                                                                                                              'funcionan '
                                                                                                                                              'con '
                                                                                                                                              'Análisis '
                                                                                                                                              'de '
                                                                                                                                              'rendimiento. '
                                                                                                                                              'El '
                                                                                                                                              'atajo '
                                                                                                                                              'inicia '
                                                                                                                                              'el '
                                                                                                                                              'registro; '
                                                                                                                                              'activar '
                                                                                                                                              'los '
                                                                                                                                              'controles '
                                                                                                                                              'no '
                                                                                                                                              'inicia '
                                                                                                                                              'la '
                                                                                                                                              'grabación.'),
 'Recording duration': ('Czas nagrania', 'Duración de grabación'),
 'Sample interval': ('Odstęp próbek', 'Intervalo de muestras'),
 'Logging shortcut': ('Skrót rejestracji', 'Atajo de registro'),
 'Advanced / configuration': ('Zaawansowane / konfiguracja', 'Avanzado / configuración'),
 'FPS limiting is managed in Optimization. Existing limiter and presentation preferences are preserved.': ('Limitem '
                                                                                                           'FPS '
                                                                                                           'zarządza '
                                                                                                           'Optymalizacja. '
                                                                                                           'Zachowujemy '
                                                                                                           'istniejące '
                                                                                                           'ustawienia '
                                                                                                           'limitera '
                                                                                                           'i '
                                                                                                           'prezentacji.',
                                                                                                           'El '
                                                                                                           'límite '
                                                                                                           'de '
                                                                                                           'FPS '
                                                                                                           'se '
                                                                                                           'gestiona '
                                                                                                           'en '
                                                                                                           'Optimización. '
                                                                                                           'Se '
                                                                                                           'conservan '
                                                                                                           'las '
                                                                                                           'preferencias '
                                                                                                           'del '
                                                                                                           'limitador '
                                                                                                           'y '
                                                                                                           'de '
                                                                                                           'presentación.'),
 'Open Optimization': ('Otwórz Optymalizację', 'Abrir Optimización'),
 'HUD shortcut': ('Skrót nakładki', 'Atajo del HUD'),
 '32-bit MangoHud layer not detected. A 32-bit game needs the matching layer in its launcher runtime.': ('Nie '
                                                                                                         'wykryto '
                                                                                                         '32-bitowej '
                                                                                                         'warstwy '
                                                                                                         'MangoHud. '
                                                                                                         'Gra '
                                                                                                         '32-bitowa '
                                                                                                         'wymaga '
                                                                                                         'takiej '
                                                                                                         'warstwy '
                                                                                                         'w '
                                                                                                         'środowisku '
                                                                                                         'launchera.',
                                                                                                         'No '
                                                                                                         'se '
                                                                                                         'detectó '
                                                                                                         'la '
                                                                                                         'capa '
                                                                                                         'MangoHud '
                                                                                                         'de '
                                                                                                         '32 '
                                                                                                         'bits. '
                                                                                                         'Un '
                                                                                                         'juego '
                                                                                                         'de '
                                                                                                         '32 '
                                                                                                         'bits '
                                                                                                         'necesita '
                                                                                                         'esa '
                                                                                                         'capa '
                                                                                                         'en '
                                                                                                         'el '
                                                                                                         'entorno '
                                                                                                         'de '
                                                                                                         'su '
                                                                                                         'lanzador.'),
 'The new monitoring controls require verified MangoHud 0.8.4 or newer. Update the launcher environment, then refresh.': ('Nowe '
                                                                                                                          'kontrolki '
                                                                                                                          'wymagają '
                                                                                                                          'potwierdzonej '
                                                                                                                          'wersji '
                                                                                                                          'MangoHud '
                                                                                                                          '0.8.4 '
                                                                                                                          'lub '
                                                                                                                          'nowszej. '
                                                                                                                          'Zaktualizuj '
                                                                                                                          'środowisko '
                                                                                                                          'launchera '
                                                                                                                          'i '
                                                                                                                          'odśwież.',
                                                                                                                          'Los '
                                                                                                                          'nuevos '
                                                                                                                          'controles '
                                                                                                                          'requieren '
                                                                                                                          'MangoHud '
                                                                                                                          '0.8.4 '
                                                                                                                          'o '
                                                                                                                          'posterior '
                                                                                                                          'verificado. '
                                                                                                                          'Actualiza '
                                                                                                                          'el '
                                                                                                                          'entorno '
                                                                                                                          'del '
                                                                                                                          'lanzador '
                                                                                                                          'y '
                                                                                                                          'vuelve '
                                                                                                                          'a '
                                                                                                                          'comprobarlo.'),
 'Recording': ('Nagrywanie', 'Grabación'),
 'GameOpti green': ('Zieleń GameOpti', 'Verde GameOpti'),
 'Mint accents, balanced telemetry and FPS statistics.': ('Miętowe akcenty, wybrane czujniki i statystyki '
                                                          'FPS.',
                                                          'Acentos menta, telemetría equilibrada y '
                                                          'estadísticas de FPS.'),
 'Minimal HUD': ('Minimalna nakładka', 'HUD mínimo'),
 'FPS and frametime with a light background.': ('FPS i czas klatki z delikatnym tłem.',
                                                'FPS y tiempo de fotograma con un fondo sutil.'),
 'Diagnostic': ('Diagnostyczny', 'Diagnóstico'),
 'Clocks, memory, graphs and rendering information.': ('Taktowanie, pamięć, wykresy i informacje o '
                                                       'renderowaniu.',
                                                       'Frecuencias, memoria, gráficos e información de '
                                                       'renderizado.'),
 'MangoHud editor': ('Edytor MangoHud', 'Editor de MangoHud'),
 'Runtime not checked': ('Środowisko niesprawdzone', 'Entorno sin comprobar'),
 'Version unknown': ('Wersja nieznana', 'Versión desconocida'),
 'Refresh': ('Odśwież', 'Actualizar'),
 'Components detected · game runtime loading not verified': ('Wykryto komponenty · załadowanie w grze '
                                                             'niepotwierdzone',
                                                             'Componentes detectados · carga en el juego sin '
                                                             'verificar'),
 'Apply the saved profile to the launcher from Overview.': ('Zastosuj zapisany profil do launchera z '
                                                            'zakładki Przegląd.',
                                                            'Aplica el perfil guardado al lanzador desde '
                                                            'Resumen.'),
 'Enable MangoHud for this game': ('Włącz MangoHud dla tej gry', 'Activar MangoHud para este juego'),
 'Choose a starting style, then adjust it. Nothing changes on disk before Apply.': ('Wybierz styl i dostosuj '
                                                                                    'go. Pliki zmienią się '
                                                                                    'dopiero po naciśnięciu '
                                                                                    'Zastosuj.',
                                                                                    'Elige un estilo y '
                                                                                    'ajústalo. Nada cambia '
                                                                                    'en el disco hasta '
                                                                                    'pulsar Aplicar.'),
 'Other presets': ('Pozostałe presety', 'Otros preajustes'),
 'Offsets are relative to this anchor.': ('Przesunięcia liczone są od tego punktu.',
                                          'Los desplazamientos se calculan desde este punto.'),
 'Horizontal offset': ('Przesunięcie poziome', 'Desplazamiento horizontal'),
 'Vertical offset': ('Przesunięcie pionowe', 'Desplazamiento vertical'),
 'Layout details': ('Szczegóły układu', 'Detalles del diseño'),
 'Row spacing': ('Odstęp wierszy', 'Espaciado de filas'),
 'Gamescope mangoapp monitors the compositor. Game API, driver, presentation, per-process memory/I/O and vkBasalt status are unavailable as game telemetry here; saved selections are kept.': ('Gamescope '
                                                                                                                                                                                               'mangoapp '
                                                                                                                                                                                               'monitoruje '
                                                                                                                                                                                               'kompozytor. '
                                                                                                                                                                                               'API, '
                                                                                                                                                                                               'sterownik, '
                                                                                                                                                                                               'prezentacja, '
                                                                                                                                                                                               'pamięć/odczyt/zapis '
                                                                                                                                                                                               'procesu '
                                                                                                                                                                                               'i '
                                                                                                                                                                                               'stan '
                                                                                                                                                                                               'vkBasalt '
                                                                                                                                                                                               'nie '
                                                                                                                                                                                               'są '
                                                                                                                                                                                               'tu '
                                                                                                                                                                                               'telemetrią '
                                                                                                                                                                                               'gry; '
                                                                                                                                                                                               'zapisany '
                                                                                                                                                                                               'wybór '
                                                                                                                                                                                               'pozostaje '
                                                                                                                                                                                               'zachowany.',
                                                                                                                                                                                               'Gamescope '
                                                                                                                                                                                               'mangoapp '
                                                                                                                                                                                               'monitoriza '
                                                                                                                                                                                               'el '
                                                                                                                                                                                               'compositor. '
                                                                                                                                                                                               'La '
                                                                                                                                                                                               'API, '
                                                                                                                                                                                               'el '
                                                                                                                                                                                               'controlador, '
                                                                                                                                                                                               'la '
                                                                                                                                                                                               'presentación, '
                                                                                                                                                                                               'la '
                                                                                                                                                                                               'memoria/E/S '
                                                                                                                                                                                               'del '
                                                                                                                                                                                               'proceso '
                                                                                                                                                                                               'y '
                                                                                                                                                                                               'vkBasalt '
                                                                                                                                                                                               'no '
                                                                                                                                                                                               'están '
                                                                                                                                                                                               'disponibles '
                                                                                                                                                                                               'aquí '
                                                                                                                                                                                               'como '
                                                                                                                                                                                               'telemetría '
                                                                                                                                                                                               'del '
                                                                                                                                                                                               'juego; '
                                                                                                                                                                                               'se '
                                                                                                                                                                                               'conserva '
                                                                                                                                                                                               'la '
                                                                                                                                                                                               'selección.'),
 'In-game telemetry depends on driver and process access. Missing counters are unavailable, not measured zero. An FPS counter does not identify generated frames or input latency.': ('Telemetria '
                                                                                                                                                                                      'zależy '
                                                                                                                                                                                      'od '
                                                                                                                                                                                      'sterownika '
                                                                                                                                                                                      'i '
                                                                                                                                                                                      'dostępu '
                                                                                                                                                                                      'do '
                                                                                                                                                                                      'procesu. '
                                                                                                                                                                                      'Brak '
                                                                                                                                                                                      'odczytu '
                                                                                                                                                                                      'nie '
                                                                                                                                                                                      'oznacza '
                                                                                                                                                                                      'zmierzonego '
                                                                                                                                                                                      'zera. '
                                                                                                                                                                                      'Licznik '
                                                                                                                                                                                      'FPS '
                                                                                                                                                                                      'nie '
                                                                                                                                                                                      'rozróżnia '
                                                                                                                                                                                      'generowanych '
                                                                                                                                                                                      'klatek '
                                                                                                                                                                                      'ani '
                                                                                                                                                                                      'nie '
                                                                                                                                                                                      'mierzy '
                                                                                                                                                                                      'opóźnienia '
                                                                                                                                                                                      'wejścia.',
                                                                                                                                                                                      'La '
                                                                                                                                                                                      'telemetría '
                                                                                                                                                                                      'depende '
                                                                                                                                                                                      'del '
                                                                                                                                                                                      'controlador '
                                                                                                                                                                                      'y '
                                                                                                                                                                                      'del '
                                                                                                                                                                                      'acceso '
                                                                                                                                                                                      'al '
                                                                                                                                                                                      'proceso. '
                                                                                                                                                                                      'Un '
                                                                                                                                                                                      'contador '
                                                                                                                                                                                      'ausente '
                                                                                                                                                                                      'no '
                                                                                                                                                                                      'es '
                                                                                                                                                                                      'un '
                                                                                                                                                                                      'cero '
                                                                                                                                                                                      'medido. '
                                                                                                                                                                                      'El '
                                                                                                                                                                                      'contador '
                                                                                                                                                                                      'de '
                                                                                                                                                                                      'FPS '
                                                                                                                                                                                      'no '
                                                                                                                                                                                      'identifica '
                                                                                                                                                                                      'fotogramas '
                                                                                                                                                                                      'generados '
                                                                                                                                                                                      'ni '
                                                                                                                                                                                      'latencia '
                                                                                                                                                                                      'de '
                                                                                                                                                                                      'entrada.'),
 'Frame statistics': ('Statystyki klatek', 'Estadísticas de fotogramas'),
 'GPU': ('GPU', 'GPU'),
 'CPU': ('CPU', 'CPU'),
 'Memory and I/O': ('Pamięć i odczyt/zapis', 'Memoria y E/S'),
 'API and status': ('API i stan', 'API y estado'),
 'D-pad: choose / adjust · A: confirm · B: close · changes need Apply': ('Krzyżak: wybór / regulacja · A: '
                                                                         'potwierdź · B: zamknij · zmiany '
                                                                         'wymagają Zastosuj',
                                                                         'Cruceta: elegir / ajustar · A: '
                                                                         'confirmar · B: cerrar · los '
                                                                         'cambios requieren Aplicar'),
 'The game is 32-bit, but its launcher runtime has no 32-bit MangoHud layer. Install the matching MangoHud package or extension, then refresh.': ('Gra '
                                                                                                                                                  'jest '
                                                                                                                                                  '32-bitowa, '
                                                                                                                                                  'a '
                                                                                                                                                  'w '
                                                                                                                                                  'środowisku '
                                                                                                                                                  'jej '
                                                                                                                                                  'launchera '
                                                                                                                                                  'brakuje '
                                                                                                                                                  '32-bitowej '
                                                                                                                                                  'warstwy '
                                                                                                                                                  'MangoHud. '
                                                                                                                                                  'Zainstaluj '
                                                                                                                                                  'odpowiedni '
                                                                                                                                                  'pakiet '
                                                                                                                                                  'lub '
                                                                                                                                                  'rozszerzenie '
                                                                                                                                                  'i '
                                                                                                                                                  'odśwież.',
                                                                                                                                                  'El '
                                                                                                                                                  'juego '
                                                                                                                                                  'es '
                                                                                                                                                  'de '
                                                                                                                                                  '32 '
                                                                                                                                                  'bits, '
                                                                                                                                                  'pero '
                                                                                                                                                  'su '
                                                                                                                                                  'lanzador '
                                                                                                                                                  'no '
                                                                                                                                                  'tiene '
                                                                                                                                                  'la '
                                                                                                                                                  'capa '
                                                                                                                                                  'MangoHud '
                                                                                                                                                  'de '
                                                                                                                                                  '32 '
                                                                                                                                                  'bits. '
                                                                                                                                                  'Instala '
                                                                                                                                                  'el '
                                                                                                                                                  'paquete '
                                                                                                                                                  'o '
                                                                                                                                                  'extensión '
                                                                                                                                                  'correspondiente '
                                                                                                                                                  'y '
                                                                                                                                                  'vuelve '
                                                                                                                                                  'a '
                                                                                                                                                  'comprobarlo.'),
 'MangoHud is not available inside this launcher Flatpak. Install its matching MangoHud extension, then refresh.': ('MangoHud '
                                                                                                                    'jest '
                                                                                                                    'niedostępny '
                                                                                                                    'we '
                                                                                                                    'Flatpaku '
                                                                                                                    'tego '
                                                                                                                    'launchera. '
                                                                                                                    'Zainstaluj '
                                                                                                                    'zgodne '
                                                                                                                    'rozszerzenie '
                                                                                                                    'MangoHud '
                                                                                                                    'i '
                                                                                                                    'odśwież.',
                                                                                                                    'MangoHud '
                                                                                                                    'no '
                                                                                                                    'está '
                                                                                                                    'disponible '
                                                                                                                    'dentro '
                                                                                                                    'del '
                                                                                                                    'Flatpak '
                                                                                                                    'de '
                                                                                                                    'este '
                                                                                                                    'lanzador. '
                                                                                                                    'Instala '
                                                                                                                    'la '
                                                                                                                    'extensión '
                                                                                                                    'MangoHud '
                                                                                                                    'correspondiente '
                                                                                                                    'y '
                                                                                                                    'vuelve '
                                                                                                                    'a '
                                                                                                                    'comprobarlo.')}


TWEAKS_SHARED = {'Display and input': ('Obraz i sterowanie', 'Pantalla y controles'),
 'Latency': ('Opóźnienia', 'Latencia'),
 'Diagnostics': ('Diagnostyka', 'Diagnóstico'),
 'Compatibility': ('Zgodność', 'Compatibilidad'),
 'Native Wayland': ('Natywny Wayland', 'Wayland nativo'),
 'Disable HDR reporting': ('Wyłącz raportowanie HDR', 'Desactivar detección de HDR'),
 'Prefer SDL controller input': ('Preferuj obsługę kontrolera przez SDL',
                                 'Preferir entrada SDL del mando'),
 'Low Latency Layer': ('Low Latency Layer', 'Low Latency Layer'),
 'Expose Reflex through the layer': ('Udostępnij Reflex przez warstwę',
                                     'Ofrecer Reflex mediante la capa'),
 'Disable ntsync': ('Wyłącz ntsync', 'Desactivar ntsync'),
 'Use WineD3D': ('Użyj WineD3D', 'Usar WineD3D'),
 'Proton logging': ('Logowanie Proton', 'Registro de Proton'),
 'Disable esync': ('Wyłącz esync', 'Desactivar esync'),
 'Disable fsync': ('Wyłącz fsync', 'Desactivar fsync'),
 'Disable NVAPI': ('Wyłącz NVAPI', 'Desactivar NVAPI'),
 'Hide NVIDIA GPU': ('Ukryj GPU NVIDIA', 'Ocultar GPU NVIDIA'),
 'Force large-address awareness': ('Wymuś obsługę dużej przestrzeni adresowej',
                                   'Forzar espacio de direcciones ampliado'),
 'Use old OpenGL string': ('Użyj starszego ciągu wersji OpenGL',
                           'Usar cadena de versión OpenGL antigua'),
 'Steam Deck spoof': ('Udawanie Steam Decka', 'Simular Steam Deck'),
 'FSR 4 upgrade': ('Aktualizacja do FSR 4', 'Actualización a FSR 4'),
 'RDNA3 WMMA workaround': ('Obejście WMMA dla RDNA3', 'Solución alternativa WMMA para RDNA3'),
 'Requires a Wayland session and winewayland in the selected runner. Keep Default with Gamescope. Some overlays and launchers may need XWayland.': ('Wymaga '
                                                                                                                                                    'sesji '
                                                                                                                                                    'Wayland '
                                                                                                                                                    'i '
                                                                                                                                                    'winewayland '
                                                                                                                                                    'w '
                                                                                                                                                    'wybranym '
                                                                                                                                                    'runnerze. '
                                                                                                                                                    'Z '
                                                                                                                                                    'Gamescope '
                                                                                                                                                    'zostaw '
                                                                                                                                                    'Domyślne. '
                                                                                                                                                    'Niektóre '
                                                                                                                                                    'nakładki '
                                                                                                                                                    'i '
                                                                                                                                                    'launchery '
                                                                                                                                                    'mogą '
                                                                                                                                                    'wymagać '
                                                                                                                                                    'XWayland.',
                                                                                                                                                    'Requiere '
                                                                                                                                                    'una '
                                                                                                                                                    'sesión '
                                                                                                                                                    'Wayland '
                                                                                                                                                    'y '
                                                                                                                                                    'winewayland '
                                                                                                                                                    'en '
                                                                                                                                                    'el '
                                                                                                                                                    'ejecutor '
                                                                                                                                                    'seleccionado. '
                                                                                                                                                    'Con '
                                                                                                                                                    'Gamescope, '
                                                                                                                                                    'deja '
                                                                                                                                                    'Predeterminado. '
                                                                                                                                                    'Algunas '
                                                                                                                                                    'capas '
                                                                                                                                                    'y '
                                                                                                                                                    'lanzadores '
                                                                                                                                                    'pueden '
                                                                                                                                                    'necesitar '
                                                                                                                                                    'XWayland.'),
 'Workaround for incorrect HDR detection in DXVK/DXGI. Default keeps automatic detection. Configure HDR in the display, compositor and game; this switch cannot add HDR support.': ('Obejście '
                                                                                                                                                                                    'błędnej '
                                                                                                                                                                                    'detekcji '
                                                                                                                                                                                    'HDR '
                                                                                                                                                                                    'w '
                                                                                                                                                                                    'DXVK/DXGI. '
                                                                                                                                                                                    'Domyślne '
                                                                                                                                                                                    'zachowuje '
                                                                                                                                                                                    'automatyczne '
                                                                                                                                                                                    'wykrywanie. '
                                                                                                                                                                                    'Skonfiguruj '
                                                                                                                                                                                    'HDR '
                                                                                                                                                                                    'w '
                                                                                                                                                                                    'monitorze, '
                                                                                                                                                                                    'kompozytorze '
                                                                                                                                                                                    'i '
                                                                                                                                                                                    'grze; '
                                                                                                                                                                                    'ten '
                                                                                                                                                                                    'przełącznik '
                                                                                                                                                                                    'nie '
                                                                                                                                                                                    'dodaje '
                                                                                                                                                                                    'obsługi '
                                                                                                                                                                                    'HDR.',
                                                                                                                                                                                    'Solución '
                                                                                                                                                                                    'para '
                                                                                                                                                                                    'la '
                                                                                                                                                                                    'detección '
                                                                                                                                                                                    'incorrecta '
                                                                                                                                                                                    'de '
                                                                                                                                                                                    'HDR '
                                                                                                                                                                                    'en '
                                                                                                                                                                                    'DXVK/DXGI. '
                                                                                                                                                                                    'Predeterminado '
                                                                                                                                                                                    'conserva '
                                                                                                                                                                                    'la '
                                                                                                                                                                                    'detección '
                                                                                                                                                                                    'automática. '
                                                                                                                                                                                    'Configura '
                                                                                                                                                                                    'HDR '
                                                                                                                                                                                    'en '
                                                                                                                                                                                    'la '
                                                                                                                                                                                    'pantalla, '
                                                                                                                                                                                    'el '
                                                                                                                                                                                    'compositor '
                                                                                                                                                                                    'y '
                                                                                                                                                                                    'el '
                                                                                                                                                                                    'juego; '
                                                                                                                                                                                    'esta '
                                                                                                                                                                                    'opción '
                                                                                                                                                                                    'no '
                                                                                                                                                                                    'añade '
                                                                                                                                                                                    'compatibilidad '
                                                                                                                                                                                    'HDR.'),
 "Use the runner's SDL controller path instead of HIDRAW/Steam Input. Mappings, vibration and special controller features may change. Use for input compatibility.": ('Użyj '
                                                                                                                                                                      'ścieżki '
                                                                                                                                                                      'SDL '
                                                                                                                                                                      'runnera '
                                                                                                                                                                      'zamiast '
                                                                                                                                                                      'HIDRAW/Steam '
                                                                                                                                                                      'Input. '
                                                                                                                                                                      'Mapowanie, '
                                                                                                                                                                      'wibracje '
                                                                                                                                                                      'i '
                                                                                                                                                                      'funkcje '
                                                                                                                                                                      'kontrolera '
                                                                                                                                                                      'mogą '
                                                                                                                                                                      'się '
                                                                                                                                                                      'zmienić. '
                                                                                                                                                                      'Opcja '
                                                                                                                                                                      'służy '
                                                                                                                                                                      'rozwiązywaniu '
                                                                                                                                                                      'problemów '
                                                                                                                                                                      'ze '
                                                                                                                                                                      'sterowaniem.',
                                                                                                                                                                      'Usa '
                                                                                                                                                                      'la '
                                                                                                                                                                      'entrada '
                                                                                                                                                                      'SDL '
                                                                                                                                                                      'del '
                                                                                                                                                                      'ejecutor '
                                                                                                                                                                      'en '
                                                                                                                                                                      'lugar '
                                                                                                                                                                      'de '
                                                                                                                                                                      'HIDRAW/Steam '
                                                                                                                                                                      'Input. '
                                                                                                                                                                      'Pueden '
                                                                                                                                                                      'cambiar '
                                                                                                                                                                      'las '
                                                                                                                                                                      'asignaciones, '
                                                                                                                                                                      'la '
                                                                                                                                                                      'vibración '
                                                                                                                                                                      'y '
                                                                                                                                                                      'las '
                                                                                                                                                                      'funciones '
                                                                                                                                                                      'del '
                                                                                                                                                                      'mando. '
                                                                                                                                                                      'Úsalo '
                                                                                                                                                                      'para '
                                                                                                                                                                      'resolver '
                                                                                                                                                                      'problemas '
                                                                                                                                                                      'de '
                                                                                                                                                                      'entrada.'),
 "Requires the bundled Vulkan layer for the game's architecture and a game using Anti-Lag 2 or Reflex. Enable that option in the game. Layer loading alone does not prove lower latency.": ('Wymaga '
                                                                                                                                                                                            'dołączonej '
                                                                                                                                                                                            'warstwy '
                                                                                                                                                                                            'Vulkan '
                                                                                                                                                                                            'dla '
                                                                                                                                                                                            'architektury '
                                                                                                                                                                                            'gry '
                                                                                                                                                                                            'oraz '
                                                                                                                                                                                            'gry '
                                                                                                                                                                                            'korzystającej '
                                                                                                                                                                                            'z '
                                                                                                                                                                                            'Anti-Lag '
                                                                                                                                                                                            '2 '
                                                                                                                                                                                            'lub '
                                                                                                                                                                                            'Reflex. '
                                                                                                                                                                                            'Włącz '
                                                                                                                                                                                            'tę '
                                                                                                                                                                                            'opcję '
                                                                                                                                                                                            'w '
                                                                                                                                                                                            'grze. '
                                                                                                                                                                                            'Samo '
                                                                                                                                                                                            'załadowanie '
                                                                                                                                                                                            'warstwy '
                                                                                                                                                                                            'nie '
                                                                                                                                                                                            'potwierdza '
                                                                                                                                                                                            'niższego '
                                                                                                                                                                                            'opóźnienia.',
                                                                                                                                                                                            'Requiere '
                                                                                                                                                                                            'la '
                                                                                                                                                                                            'capa '
                                                                                                                                                                                            'Vulkan '
                                                                                                                                                                                            'incluida '
                                                                                                                                                                                            'para '
                                                                                                                                                                                            'la '
                                                                                                                                                                                            'arquitectura '
                                                                                                                                                                                            'del '
                                                                                                                                                                                            'juego '
                                                                                                                                                                                            'y '
                                                                                                                                                                                            'un '
                                                                                                                                                                                            'juego '
                                                                                                                                                                                            'que '
                                                                                                                                                                                            'use '
                                                                                                                                                                                            'Anti-Lag '
                                                                                                                                                                                            '2 '
                                                                                                                                                                                            'o '
                                                                                                                                                                                            'Reflex. '
                                                                                                                                                                                            'Activa '
                                                                                                                                                                                            'esa '
                                                                                                                                                                                            'opción '
                                                                                                                                                                                            'en '
                                                                                                                                                                                            'el '
                                                                                                                                                                                            'juego. '
                                                                                                                                                                                            'Cargar '
                                                                                                                                                                                            'la '
                                                                                                                                                                                            'capa '
                                                                                                                                                                                            'no '
                                                                                                                                                                                            'demuestra '
                                                                                                                                                                                            'una '
                                                                                                                                                                                            'menor '
                                                                                                                                                                                            'latencia.'),
 'Select Reflex instead of Anti-Lag 2 in Low Latency Layer. Requires the layer, compatible DXVK-NVAPI and game support. No GPU identity spoofing is applied; the game may not expose Reflex.': ('Wybierz '
                                                                                                                                                                                                'Reflex '
                                                                                                                                                                                                'zamiast '
                                                                                                                                                                                                'Anti-Lag '
                                                                                                                                                                                                '2 '
                                                                                                                                                                                                'w '
                                                                                                                                                                                                'Low '
                                                                                                                                                                                                'Latency '
                                                                                                                                                                                                'Layer. '
                                                                                                                                                                                                'Wymaga '
                                                                                                                                                                                                'warstwy, '
                                                                                                                                                                                                'zgodnego '
                                                                                                                                                                                                'DXVK-NVAPI '
                                                                                                                                                                                                'i '
                                                                                                                                                                                                'obsługi '
                                                                                                                                                                                                'w '
                                                                                                                                                                                                'grze. '
                                                                                                                                                                                                'Tożsamość '
                                                                                                                                                                                                'GPU '
                                                                                                                                                                                                'pozostaje '
                                                                                                                                                                                                'bez '
                                                                                                                                                                                                'zmian; '
                                                                                                                                                                                                'gra '
                                                                                                                                                                                                'może '
                                                                                                                                                                                                'nie '
                                                                                                                                                                                                'udostępnić '
                                                                                                                                                                                                'Reflex.',
                                                                                                                                                                                                'Selecciona '
                                                                                                                                                                                                'Reflex '
                                                                                                                                                                                                'en '
                                                                                                                                                                                                'lugar '
                                                                                                                                                                                                'de '
                                                                                                                                                                                                'Anti-Lag '
                                                                                                                                                                                                '2 '
                                                                                                                                                                                                'en '
                                                                                                                                                                                                'Low '
                                                                                                                                                                                                'Latency '
                                                                                                                                                                                                'Layer. '
                                                                                                                                                                                                'Requiere '
                                                                                                                                                                                                'la '
                                                                                                                                                                                                'capa, '
                                                                                                                                                                                                'DXVK-NVAPI '
                                                                                                                                                                                                'compatible '
                                                                                                                                                                                                'y '
                                                                                                                                                                                                'soporte '
                                                                                                                                                                                                'del '
                                                                                                                                                                                                'juego. '
                                                                                                                                                                                                'No '
                                                                                                                                                                                                'se '
                                                                                                                                                                                                'simula '
                                                                                                                                                                                                'otra '
                                                                                                                                                                                                'GPU; '
                                                                                                                                                                                                'el '
                                                                                                                                                                                                'juego '
                                                                                                                                                                                                'podría '
                                                                                                                                                                                                'no '
                                                                                                                                                                                                'mostrar '
                                                                                                                                                                                                'Reflex.'),
 'Synchronization workaround for runners supporting ntsync. Default lets the runner choose. Disabling fsync or esync alone does not disable ntsync; the kernel and /dev/ntsync must also support it.': ('Obejście '
                                                                                                                                                                                                        'synchronizacji '
                                                                                                                                                                                                        'dla '
                                                                                                                                                                                                        'runnerów '
                                                                                                                                                                                                        'obsługujących '
                                                                                                                                                                                                        'ntsync. '
                                                                                                                                                                                                        'Domyślne '
                                                                                                                                                                                                        'pozostawia '
                                                                                                                                                                                                        'wybór '
                                                                                                                                                                                                        'runnerowi. '
                                                                                                                                                                                                        'Samo '
                                                                                                                                                                                                        'wyłączenie '
                                                                                                                                                                                                        'fsync '
                                                                                                                                                                                                        'lub '
                                                                                                                                                                                                        'esync '
                                                                                                                                                                                                        'nie '
                                                                                                                                                                                                        'wyłącza '
                                                                                                                                                                                                        'ntsync; '
                                                                                                                                                                                                        'wymagane '
                                                                                                                                                                                                        'jest '
                                                                                                                                                                                                        'też '
                                                                                                                                                                                                        'wsparcie '
                                                                                                                                                                                                        'jądra '
                                                                                                                                                                                                        'i '
                                                                                                                                                                                                        '/dev/ntsync.',
                                                                                                                                                                                                        'Solución '
                                                                                                                                                                                                        'de '
                                                                                                                                                                                                        'sincronización '
                                                                                                                                                                                                        'para '
                                                                                                                                                                                                        'ejecutores '
                                                                                                                                                                                                        'con '
                                                                                                                                                                                                        'ntsync. '
                                                                                                                                                                                                        'Predeterminado '
                                                                                                                                                                                                        'deja '
                                                                                                                                                                                                        'la '
                                                                                                                                                                                                        'elección '
                                                                                                                                                                                                        'al '
                                                                                                                                                                                                        'ejecutor. '
                                                                                                                                                                                                        'Desactivar '
                                                                                                                                                                                                        'fsync '
                                                                                                                                                                                                        'o '
                                                                                                                                                                                                        'esync '
                                                                                                                                                                                                        'no '
                                                                                                                                                                                                        'desactiva '
                                                                                                                                                                                                        'ntsync; '
                                                                                                                                                                                                        'también '
                                                                                                                                                                                                        'se '
                                                                                                                                                                                                        'requiere '
                                                                                                                                                                                                        'soporte '
                                                                                                                                                                                                        'del '
                                                                                                                                                                                                        'kernel '
                                                                                                                                                                                                        'y '
                                                                                                                                                                                                        '/dev/ntsync.'),
 'OpenGL fallback for Direct3D 9/10/11 instead of DXVK. Does not replace the Direct3D 12 renderer. Vulkan layers and external Vulkan frame generation cannot process this path.': ('Awaryjny '
                                                                                                                                                                                   'OpenGL '
                                                                                                                                                                                   'dla '
                                                                                                                                                                                   'Direct3D '
                                                                                                                                                                                   '9/10/11 '
                                                                                                                                                                                   'zamiast '
                                                                                                                                                                                   'DXVK. '
                                                                                                                                                                                   'Nie '
                                                                                                                                                                                   'zastępuje '
                                                                                                                                                                                   'renderera '
                                                                                                                                                                                   'Direct3D '
                                                                                                                                                                                   '12. '
                                                                                                                                                                                   'Warstwy '
                                                                                                                                                                                   'Vulkan '
                                                                                                                                                                                   'i '
                                                                                                                                                                                   'zewnętrzne '
                                                                                                                                                                                   'generowanie '
                                                                                                                                                                                   'klatek '
                                                                                                                                                                                   'Vulkan '
                                                                                                                                                                                   'nie '
                                                                                                                                                                                   'obsługują '
                                                                                                                                                                                   'tej '
                                                                                                                                                                                   'ścieżki.',
                                                                                                                                                                                   'Alternativa '
                                                                                                                                                                                   'OpenGL '
                                                                                                                                                                                   'para '
                                                                                                                                                                                   'Direct3D '
                                                                                                                                                                                   '9/10/11 '
                                                                                                                                                                                   'en '
                                                                                                                                                                                   'lugar '
                                                                                                                                                                                   'de '
                                                                                                                                                                                   'DXVK. '
                                                                                                                                                                                   'No '
                                                                                                                                                                                   'sustituye '
                                                                                                                                                                                   'el '
                                                                                                                                                                                   'renderizador '
                                                                                                                                                                                   'Direct3D '
                                                                                                                                                                                   '12. '
                                                                                                                                                                                   'Las '
                                                                                                                                                                                   'capas '
                                                                                                                                                                                   'Vulkan '
                                                                                                                                                                                   'y '
                                                                                                                                                                                   'la '
                                                                                                                                                                                   'generación '
                                                                                                                                                                                   'de '
                                                                                                                                                                                   'fotogramas '
                                                                                                                                                                                   'Vulkan '
                                                                                                                                                                                   'externa '
                                                                                                                                                                                   'no '
                                                                                                                                                                                   'procesan '
                                                                                                                                                                                   'esta '
                                                                                                                                                                                   'ruta.'),
 'Write steam-APPID.log for the next launch. Logging can reduce performance and logs may contain personal paths. Turn it back to Default after diagnosis.': ('Zapisz '
                                                                                                                                                             'steam-APPID.log '
                                                                                                                                                             'przy '
                                                                                                                                                             'następnym '
                                                                                                                                                             'uruchomieniu. '
                                                                                                                                                             'Logowanie '
                                                                                                                                                             'może '
                                                                                                                                                             'zmniejszać '
                                                                                                                                                             'wydajność, '
                                                                                                                                                             'a '
                                                                                                                                                             'logi '
                                                                                                                                                             'mogą '
                                                                                                                                                             'zawierać '
                                                                                                                                                             'prywatne '
                                                                                                                                                             'ścieżki. '
                                                                                                                                                             'Po '
                                                                                                                                                             'diagnozie '
                                                                                                                                                             'przywróć '
                                                                                                                                                             'Domyślne.',
                                                                                                                                                             'Escribe '
                                                                                                                                                             'steam-APPID.log '
                                                                                                                                                             'en '
                                                                                                                                                             'el '
                                                                                                                                                             'próximo '
                                                                                                                                                             'inicio. '
                                                                                                                                                             'El '
                                                                                                                                                             'registro '
                                                                                                                                                             'puede '
                                                                                                                                                             'reducir '
                                                                                                                                                             'el '
                                                                                                                                                             'rendimiento '
                                                                                                                                                             'e '
                                                                                                                                                             'incluir '
                                                                                                                                                             'rutas '
                                                                                                                                                             'personales. '
                                                                                                                                                             'Vuelve '
                                                                                                                                                             'a '
                                                                                                                                                             'Predeterminado '
                                                                                                                                                             'tras '
                                                                                                                                                             'el '
                                                                                                                                                             'diagnóstico.'),
 'Disable esync for compatibility testing. Newer runners may prefer ntsync or fsync, so this override may not affect the chosen path.': ('Wyłącz '
                                                                                                                                         'esync '
                                                                                                                                         'w '
                                                                                                                                         'celu '
                                                                                                                                         'testowania '
                                                                                                                                         'zgodności. '
                                                                                                                                         'Nowsze '
                                                                                                                                         'runnery '
                                                                                                                                         'mogą '
                                                                                                                                         'preferować '
                                                                                                                                         'ntsync '
                                                                                                                                         'lub '
                                                                                                                                         'fsync, '
                                                                                                                                         'więc '
                                                                                                                                         'ta '
                                                                                                                                         'opcja '
                                                                                                                                         'może '
                                                                                                                                         'nie '
                                                                                                                                         'wpłynąć '
                                                                                                                                         'na '
                                                                                                                                         'wybraną '
                                                                                                                                         'ścieżkę.',
                                                                                                                                         'Desactiva '
                                                                                                                                         'esync '
                                                                                                                                         'para '
                                                                                                                                         'probar '
                                                                                                                                         'compatibilidad. '
                                                                                                                                         'Los '
                                                                                                                                         'ejecutores '
                                                                                                                                         'nuevos '
                                                                                                                                         'pueden '
                                                                                                                                         'preferir '
                                                                                                                                         'ntsync '
                                                                                                                                         'o '
                                                                                                                                         'fsync, '
                                                                                                                                         'por '
                                                                                                                                         'lo '
                                                                                                                                         'que '
                                                                                                                                         'esta '
                                                                                                                                         'opción '
                                                                                                                                         'podría '
                                                                                                                                         'no '
                                                                                                                                         'afectar '
                                                                                                                                         'a '
                                                                                                                                         'la '
                                                                                                                                         'ruta '
                                                                                                                                         'elegida.'),
 "Disable fsync for compatibility testing. Default keeps the runner's selection; newer runners may use ntsync instead.": ('Wyłącz '
                                                                                                                          'fsync '
                                                                                                                          'w '
                                                                                                                          'celu '
                                                                                                                          'testowania '
                                                                                                                          'zgodności. '
                                                                                                                          'Domyślne '
                                                                                                                          'zachowuje '
                                                                                                                          'wybór '
                                                                                                                          'runnera; '
                                                                                                                          'nowsze '
                                                                                                                          'runnery '
                                                                                                                          'mogą '
                                                                                                                          'korzystać '
                                                                                                                          'z '
                                                                                                                          'ntsync.',
                                                                                                                          'Desactiva '
                                                                                                                          'fsync '
                                                                                                                          'para '
                                                                                                                          'probar '
                                                                                                                          'compatibilidad. '
                                                                                                                          'Predeterminado '
                                                                                                                          'conserva '
                                                                                                                          'la '
                                                                                                                          'elección '
                                                                                                                          'del '
                                                                                                                          'ejecutor; '
                                                                                                                          'los '
                                                                                                                          'más '
                                                                                                                          'nuevos '
                                                                                                                          'pueden '
                                                                                                                          'usar '
                                                                                                                          'ntsync.'),
 'Disable NVAPI for compatibility testing. Conflicts with Reflex through NVAPI and can remove DLSS or other NVIDIA features.': ('Wyłącz '
                                                                                                                                'NVAPI '
                                                                                                                                'w '
                                                                                                                                'celu '
                                                                                                                                'testowania '
                                                                                                                                'zgodności. '
                                                                                                                                'Koliduje '
                                                                                                                                'z '
                                                                                                                                'Reflex '
                                                                                                                                'przez '
                                                                                                                                'NVAPI '
                                                                                                                                'i '
                                                                                                                                'może '
                                                                                                                                'wyłączyć '
                                                                                                                                'DLSS '
                                                                                                                                'lub '
                                                                                                                                'inne '
                                                                                                                                'funkcje '
                                                                                                                                'NVIDIA.',
                                                                                                                                'Desactiva '
                                                                                                                                'NVAPI '
                                                                                                                                'para '
                                                                                                                                'probar '
                                                                                                                                'compatibilidad. '
                                                                                                                                'Entra '
                                                                                                                                'en '
                                                                                                                                'conflicto '
                                                                                                                                'con '
                                                                                                                                'Reflex '
                                                                                                                                'mediante '
                                                                                                                                'NVAPI '
                                                                                                                                'y '
                                                                                                                                'puede '
                                                                                                                                'eliminar '
                                                                                                                                'DLSS '
                                                                                                                                'u '
                                                                                                                                'otras '
                                                                                                                                'funciones '
                                                                                                                                'NVIDIA.'),
 "Hide NVIDIA identity to work around a game's vendor-specific path. Can hide DLSS/Reflex. Not a general performance setting.": ('Ukryj '
                                                                                                                                 'tożsamość '
                                                                                                                                 'NVIDIA, '
                                                                                                                                 'aby '
                                                                                                                                 'ominąć '
                                                                                                                                 'ścieżkę '
                                                                                                                                 'gry '
                                                                                                                                 'zależną '
                                                                                                                                 'od '
                                                                                                                                 'producenta. '
                                                                                                                                 'Może '
                                                                                                                                 'ukryć '
                                                                                                                                 'DLSS/Reflex. '
                                                                                                                                 'To '
                                                                                                                                 'nie '
                                                                                                                                 'jest '
                                                                                                                                 'ogólne '
                                                                                                                                 'ustawienie '
                                                                                                                                 'wydajności.',
                                                                                                                                 'Oculta '
                                                                                                                                 'la '
                                                                                                                                 'identidad '
                                                                                                                                 'NVIDIA '
                                                                                                                                 'para '
                                                                                                                                 'evitar '
                                                                                                                                 'una '
                                                                                                                                 'ruta '
                                                                                                                                 'del '
                                                                                                                                 'juego '
                                                                                                                                 'específica '
                                                                                                                                 'del '
                                                                                                                                 'fabricante. '
                                                                                                                                 'Puede '
                                                                                                                                 'ocultar '
                                                                                                                                 'DLSS/Reflex. '
                                                                                                                                 'No '
                                                                                                                                 'es '
                                                                                                                                 'un '
                                                                                                                                 'ajuste '
                                                                                                                                 'general '
                                                                                                                                 'de '
                                                                                                                                 'rendimiento.'),
 'Legacy 32-bit compatibility option. Many Proton versions already enable large-address awareness. Keep Default unless diagnosing a specific game.': ('Starsza '
                                                                                                                                                      'opcja '
                                                                                                                                                      'zgodności '
                                                                                                                                                      'gier '
                                                                                                                                                      '32-bitowych. '
                                                                                                                                                      'Wiele '
                                                                                                                                                      'wersji '
                                                                                                                                                      'Proton '
                                                                                                                                                      'już '
                                                                                                                                                      'włącza '
                                                                                                                                                      'dużą '
                                                                                                                                                      'przestrzeń '
                                                                                                                                                      'adresową. '
                                                                                                                                                      'Zostaw '
                                                                                                                                                      'Domyślne '
                                                                                                                                                      'poza '
                                                                                                                                                      'diagnozą '
                                                                                                                                                      'konkretnej '
                                                                                                                                                      'gry.',
                                                                                                                                                      'Opción '
                                                                                                                                                      'antigua '
                                                                                                                                                      'de '
                                                                                                                                                      'compatibilidad '
                                                                                                                                                      'de '
                                                                                                                                                      '32 '
                                                                                                                                                      'bits. '
                                                                                                                                                      'Muchas '
                                                                                                                                                      'versiones '
                                                                                                                                                      'de '
                                                                                                                                                      'Proton '
                                                                                                                                                      'ya '
                                                                                                                                                      'amplían '
                                                                                                                                                      'el '
                                                                                                                                                      'espacio '
                                                                                                                                                      'de '
                                                                                                                                                      'direcciones. '
                                                                                                                                                      'Deja '
                                                                                                                                                      'Predeterminado '
                                                                                                                                                      'salvo '
                                                                                                                                                      'al '
                                                                                                                                                      'diagnosticar '
                                                                                                                                                      'un '
                                                                                                                                                      'juego '
                                                                                                                                                      'concreto.'),
 'Legacy OpenGL version-string workaround for old games. Relevant to WineD3D/OpenGL, not DXVK or Direct3D 12.': ('Starsze '
                                                                                                                 'obejście '
                                                                                                                 'ciągu '
                                                                                                                 'wersji '
                                                                                                                 'OpenGL '
                                                                                                                 'dla '
                                                                                                                 'starych '
                                                                                                                 'gier. '
                                                                                                                 'Dotyczy '
                                                                                                                 'WineD3D/OpenGL, '
                                                                                                                 'nie '
                                                                                                                 'DXVK '
                                                                                                                 'ani '
                                                                                                                 'Direct3D '
                                                                                                                 '12.',
                                                                                                                 'Solución '
                                                                                                                 'antigua '
                                                                                                                 'de '
                                                                                                                 'la '
                                                                                                                 'cadena '
                                                                                                                 'de '
                                                                                                                 'versión '
                                                                                                                 'OpenGL '
                                                                                                                 'para '
                                                                                                                 'juegos '
                                                                                                                 'antiguos. '
                                                                                                                 'Afecta '
                                                                                                                 'a '
                                                                                                                 'WineD3D/OpenGL, '
                                                                                                                 'no '
                                                                                                                 'a '
                                                                                                                 'DXVK '
                                                                                                                 'ni '
                                                                                                                 'Direct3D '
                                                                                                                 '12.'),
 "Existing experimental override: exposes SteamDeck=1 and can alter a game's interface or graphics choices. Does not turn this computer into a Steam Deck.": ('Istniejące '
                                                                                                                                                              'eksperymentalne '
                                                                                                                                                              'nadpisanie: '
                                                                                                                                                              'udostępnia '
                                                                                                                                                              'SteamDeck=1 '
                                                                                                                                                              'i '
                                                                                                                                                              'może '
                                                                                                                                                              'zmienić '
                                                                                                                                                              'interfejs '
                                                                                                                                                              'lub '
                                                                                                                                                              'ustawienia '
                                                                                                                                                              'grafiki '
                                                                                                                                                              'gry. '
                                                                                                                                                              'Nie '
                                                                                                                                                              'zmienia '
                                                                                                                                                              'komputera '
                                                                                                                                                              'w '
                                                                                                                                                              'Steam '
                                                                                                                                                              'Decka.',
                                                                                                                                                              'Opción '
                                                                                                                                                              'experimental '
                                                                                                                                                              'existente: '
                                                                                                                                                              'expone '
                                                                                                                                                              'SteamDeck=1 '
                                                                                                                                                              'y '
                                                                                                                                                              'puede '
                                                                                                                                                              'cambiar '
                                                                                                                                                              'la '
                                                                                                                                                              'interfaz '
                                                                                                                                                              'o '
                                                                                                                                                              'las '
                                                                                                                                                              'decisiones '
                                                                                                                                                              'gráficas '
                                                                                                                                                              'del '
                                                                                                                                                              'juego. '
                                                                                                                                                              'No '
                                                                                                                                                              'convierte '
                                                                                                                                                              'este '
                                                                                                                                                              'equipo '
                                                                                                                                                              'en '
                                                                                                                                                              'Steam '
                                                                                                                                                              'Deck.'),
 'Existing experimental profile option. Requires an independently verified runner, upscaler, driver and GPU combination. Configure working upscalers on their dedicated page.': ('Istniejąca '
                                                                                                                                                                                 'eksperymentalna '
                                                                                                                                                                                 'opcja '
                                                                                                                                                                                 'profilu. '
                                                                                                                                                                                 'Wymaga '
                                                                                                                                                                                 'osobnego '
                                                                                                                                                                                 'potwierdzenia '
                                                                                                                                                                                 'zgodności '
                                                                                                                                                                                 'runnera, '
                                                                                                                                                                                 'upscalera, '
                                                                                                                                                                                 'sterownika '
                                                                                                                                                                                 'i '
                                                                                                                                                                                 'GPU. '
                                                                                                                                                                                 'Działające '
                                                                                                                                                                                 'skalowanie '
                                                                                                                                                                                 'konfiguruj '
                                                                                                                                                                                 'na '
                                                                                                                                                                                 'jego '
                                                                                                                                                                                 'stronie.',
                                                                                                                                                                                 'Opción '
                                                                                                                                                                                 'experimental '
                                                                                                                                                                                 'existente '
                                                                                                                                                                                 'del '
                                                                                                                                                                                 'perfil. '
                                                                                                                                                                                 'Requiere '
                                                                                                                                                                                 'verificar '
                                                                                                                                                                                 'por '
                                                                                                                                                                                 'separado '
                                                                                                                                                                                 'el '
                                                                                                                                                                                 'ejecutor, '
                                                                                                                                                                                 'el '
                                                                                                                                                                                 'escalador, '
                                                                                                                                                                                 'el '
                                                                                                                                                                                 'controlador '
                                                                                                                                                                                 'y '
                                                                                                                                                                                 'la '
                                                                                                                                                                                 'GPU. '
                                                                                                                                                                                 'Configura '
                                                                                                                                                                                 'los '
                                                                                                                                                                                 'escaladores '
                                                                                                                                                                                 'funcionales '
                                                                                                                                                                                 'en '
                                                                                                                                                                                 'su '
                                                                                                                                                                                 'página '
                                                                                                                                                                                 'específica.'),
 'Existing experimental DXIL-SPIRV option. Use only with a documented RDNA3 shader issue and matching components. Other DXIL_SPIRV_CONFIG tokens are preserved.': ('Istniejąca '
                                                                                                                                                                   'eksperymentalna '
                                                                                                                                                                   'opcja '
                                                                                                                                                                   'DXIL-SPIRV. '
                                                                                                                                                                   'Używaj '
                                                                                                                                                                   'wyłącznie '
                                                                                                                                                                   'przy '
                                                                                                                                                                   'udokumentowanym '
                                                                                                                                                                   'problemie '
                                                                                                                                                                   'shaderów '
                                                                                                                                                                   'RDNA3 '
                                                                                                                                                                   'i '
                                                                                                                                                                   'zgodnych '
                                                                                                                                                                   'komponentach. '
                                                                                                                                                                   'Pozostałe '
                                                                                                                                                                   'tokeny '
                                                                                                                                                                   'DXIL_SPIRV_CONFIG '
                                                                                                                                                                   'zostają '
                                                                                                                                                                   'zachowane.',
                                                                                                                                                                   'Opción '
                                                                                                                                                                   'experimental '
                                                                                                                                                                   'existente '
                                                                                                                                                                   'de '
                                                                                                                                                                   'DXIL-SPIRV. '
                                                                                                                                                                   'Úsala '
                                                                                                                                                                   'solo '
                                                                                                                                                                   'ante '
                                                                                                                                                                   'un '
                                                                                                                                                                   'problema '
                                                                                                                                                                   'documentado '
                                                                                                                                                                   'de '
                                                                                                                                                                   'shaders '
                                                                                                                                                                   'RDNA3 '
                                                                                                                                                                   'y '
                                                                                                                                                                   'con '
                                                                                                                                                                   'componentes '
                                                                                                                                                                   'compatibles. '
                                                                                                                                                                   'Se '
                                                                                                                                                                   'conservan '
                                                                                                                                                                   'los '
                                                                                                                                                                   'demás '
                                                                                                                                                                   'elementos '
                                                                                                                                                                   'de '
                                                                                                                                                                   'DXIL_SPIRV_CONFIG.'),
 'Runner support found': ('Znaleziono obsługę w runnerze', 'Soporte encontrado en el ejecutor'),
 'Not found in selected runner': ('Nie znaleziono w wybranym runnerze',
                                  'No encontrado en el ejecutor seleccionado'),
 'Support not confirmed': ('Obsługa niepotwierdzona', 'Soporte sin confirmar'),
 'Native Wine Wayland conflicts with the managed Gamescope path. Keep Wayland at Default or change Gamescope in Optimization.': ('Natywny '
                                                                                                                                 'Wine '
                                                                                                                                 'Wayland '
                                                                                                                                 'koliduje '
                                                                                                                                 'ze '
                                                                                                                                 'ścieżką '
                                                                                                                                 'Gamescope. '
                                                                                                                                 'Zostaw '
                                                                                                                                 'Wayland '
                                                                                                                                 'jako '
                                                                                                                                 'Domyślne '
                                                                                                                                 'lub '
                                                                                                                                 'zmień '
                                                                                                                                 'Gamescope '
                                                                                                                                 'w '
                                                                                                                                 'Optymalizacji.',
                                                                                                                                 'Wine '
                                                                                                                                 'Wayland '
                                                                                                                                 'nativo '
                                                                                                                                 'entra '
                                                                                                                                 'en '
                                                                                                                                 'conflicto '
                                                                                                                                 'con '
                                                                                                                                 'la '
                                                                                                                                 'ruta '
                                                                                                                                 'Gamescope '
                                                                                                                                 'gestionada. '
                                                                                                                                 'Deja '
                                                                                                                                 'Wayland '
                                                                                                                                 'en '
                                                                                                                                 'Predeterminado '
                                                                                                                                 'o '
                                                                                                                                 'cambia '
                                                                                                                                 'Gamescope '
                                                                                                                                 'en '
                                                                                                                                 'Optimización.'),
 'Reflex mode requires Low Latency Layer to be Enabled.': ('Tryb Reflex wymaga włączonej Low Latency '
                                                           'Layer.',
                                                           'El modo Reflex requiere Low Latency Layer '
                                                           'activada.'),
 'Low Latency Layer needs Vulkan. Return WineD3D to Default before enabling it.': ('Low Latency Layer '
                                                                                   'wymaga Vulkan. '
                                                                                   'Przywróć Domyślne '
                                                                                   'dla WineD3D przed '
                                                                                   'jej włączeniem.',
                                                                                   'Low Latency Layer '
                                                                                   'necesita Vulkan. '
                                                                                   'Vuelve a '
                                                                                   'Predeterminado en '
                                                                                   'WineD3D antes de '
                                                                                   'activarla.'),
 'Reflex mode conflicts with Disable NVAPI.': ('Tryb Reflex koliduje z wyłączeniem NVAPI.',
                                               'El modo Reflex entra en conflicto con Desactivar '
                                               'NVAPI.'),
 'The launch environment disables Low Latency Layer. Review DISABLE_LOW_LATENCY_LAYER in the launcher; GameOpti preserves that setting.': ('Środowisko '
                                                                                                                                           'uruchamiania '
                                                                                                                                           'wyłącza '
                                                                                                                                           'Low '
                                                                                                                                           'Latency '
                                                                                                                                           'Layer. '
                                                                                                                                           'Sprawdź '
                                                                                                                                           'DISABLE_LOW_LATENCY_LAYER '
                                                                                                                                           'w '
                                                                                                                                           'launcherze; '
                                                                                                                                           'GameOpti '
                                                                                                                                           'zachowuje '
                                                                                                                                           'to '
                                                                                                                                           'ustawienie.',
                                                                                                                                           'El '
                                                                                                                                           'entorno '
                                                                                                                                           'de '
                                                                                                                                           'inicio '
                                                                                                                                           'desactiva '
                                                                                                                                           'Low '
                                                                                                                                           'Latency '
                                                                                                                                           'Layer. '
                                                                                                                                           'Revisa '
                                                                                                                                           'DISABLE_LOW_LATENCY_LAYER '
                                                                                                                                           'en '
                                                                                                                                           'el '
                                                                                                                                           'lanzador; '
                                                                                                                                           'GameOpti '
                                                                                                                                           'conserva '
                                                                                                                                           'ese '
                                                                                                                                           'ajuste.'),
 'Another latency layer or external frame generator is selected. Test one latency path at a time before enabling this layer.': ('Wybrano '
                                                                                                                                'inną '
                                                                                                                                'warstwę '
                                                                                                                                'opóźnień '
                                                                                                                                'lub '
                                                                                                                                'zewnętrzny '
                                                                                                                                'generator '
                                                                                                                                'klatek. '
                                                                                                                                'Przetestuj '
                                                                                                                                'osobno '
                                                                                                                                'jedną '
                                                                                                                                'ścieżkę '
                                                                                                                                'opóźnień '
                                                                                                                                'przed '
                                                                                                                                'włączeniem '
                                                                                                                                'tej '
                                                                                                                                'warstwy.',
                                                                                                                                'Hay '
                                                                                                                                'otra '
                                                                                                                                'capa '
                                                                                                                                'de '
                                                                                                                                'latencia '
                                                                                                                                'o '
                                                                                                                                'generador '
                                                                                                                                'externo '
                                                                                                                                'seleccionado. '
                                                                                                                                'Prueba '
                                                                                                                                'una '
                                                                                                                                'sola '
                                                                                                                                'ruta '
                                                                                                                                'de '
                                                                                                                                'latencia '
                                                                                                                                'antes '
                                                                                                                                'de '
                                                                                                                                'activar '
                                                                                                                                'esta '
                                                                                                                                'capa.'),
 'Proton Tweaks are unavailable': ('Opcje Proton Tweaks są niedostępne',
                                   'Proton Tweaks no está disponible'),
 'Could not preview changes': ('Nie udało się wyświetlić zmian',
                               'No se pudieron previsualizar los cambios'),
 'Could not save Proton Tweaks': ('Nie udało się zapisać Proton Tweaks',
                                  'No se pudo guardar Proton Tweaks'),
 'No GameOpti overrides': ('Brak nadpisań GameOpti', 'Sin ajustes de GameOpti'),
 'Proton Tweaks': ('Proton Tweaks', 'Proton Tweaks'),
 'Unsaved changes': ('Niezapisane zmiany', 'Cambios sin guardar'),
 'Saved settings': ('Zapisane ustawienia', 'Ajustes guardados'),
 'Per-game tweaks': ('Ustawienia dla tej gry', 'Ajustes por juego'),
 "Default removes only GameOpti's override. Every change requires a fresh game launch. Compatibility workarounds and diagnostics are not general FPS improvements.": ('Domyślne '
                                                                                                                                                                      'usuwa '
                                                                                                                                                                      'tylko '
                                                                                                                                                                      'nadpisanie '
                                                                                                                                                                      'GameOpti. '
                                                                                                                                                                      'Każda '
                                                                                                                                                                      'zmiana '
                                                                                                                                                                      'wymaga '
                                                                                                                                                                      'ponownego '
                                                                                                                                                                      'uruchomienia '
                                                                                                                                                                      'gry. '
                                                                                                                                                                      'Obejścia '
                                                                                                                                                                      'zgodności '
                                                                                                                                                                      'i '
                                                                                                                                                                      'diagnostyka '
                                                                                                                                                                      'nie '
                                                                                                                                                                      'są '
                                                                                                                                                                      'ogólnymi '
                                                                                                                                                                      'sposobami '
                                                                                                                                                                      'zwiększania '
                                                                                                                                                                      'FPS.',
                                                                                                                                                                      'Predeterminado '
                                                                                                                                                                      'elimina '
                                                                                                                                                                      'solo '
                                                                                                                                                                      'el '
                                                                                                                                                                      'ajuste '
                                                                                                                                                                      'de '
                                                                                                                                                                      'GameOpti. '
                                                                                                                                                                      'Cada '
                                                                                                                                                                      'cambio '
                                                                                                                                                                      'requiere '
                                                                                                                                                                      'reiniciar '
                                                                                                                                                                      'el '
                                                                                                                                                                      'juego. '
                                                                                                                                                                      'Las '
                                                                                                                                                                      'soluciones '
                                                                                                                                                                      'de '
                                                                                                                                                                      'compatibilidad '
                                                                                                                                                                      'y '
                                                                                                                                                                      'el '
                                                                                                                                                                      'diagnóstico '
                                                                                                                                                                      'no '
                                                                                                                                                                      'son '
                                                                                                                                                                      'mejoras '
                                                                                                                                                                      'generales '
                                                                                                                                                                      'de '
                                                                                                                                                                      'FPS.'),
 'Selected runner unknown': ('Nieznany wybrany runner', 'Ejecutor seleccionado desconocido'),
 'Launcher Flatpak': ('Flatpak launchera', 'Flatpak del lanzador'),
 'Host / Steam runtime': ('Host / środowisko Steam', 'Host / entorno Steam'),
 'Components found in a runner do not prove that the game loaded them. Driver, architecture, game and sandbox requirements still apply.': ('Znalezienie '
                                                                                                                                           'komponentów '
                                                                                                                                           'w '
                                                                                                                                           'runnerze '
                                                                                                                                           'nie '
                                                                                                                                           'potwierdza '
                                                                                                                                           'załadowania '
                                                                                                                                           'ich '
                                                                                                                                           'przez '
                                                                                                                                           'grę. '
                                                                                                                                           'Nadal '
                                                                                                                                           'obowiązują '
                                                                                                                                           'wymagania '
                                                                                                                                           'sterownika, '
                                                                                                                                           'architektury, '
                                                                                                                                           'gry '
                                                                                                                                           'i '
                                                                                                                                           'piaskownicy.',
                                                                                                                                           'Encontrar '
                                                                                                                                           'componentes '
                                                                                                                                           'en '
                                                                                                                                           'el '
                                                                                                                                           'ejecutor '
                                                                                                                                           'no '
                                                                                                                                           'demuestra '
                                                                                                                                           'que '
                                                                                                                                           'el '
                                                                                                                                           'juego '
                                                                                                                                           'los '
                                                                                                                                           'haya '
                                                                                                                                           'cargado. '
                                                                                                                                           'Siguen '
                                                                                                                                           'aplicándose '
                                                                                                                                           'los '
                                                                                                                                           'requisitos '
                                                                                                                                           'del '
                                                                                                                                           'controlador, '
                                                                                                                                           'la '
                                                                                                                                           'arquitectura, '
                                                                                                                                           'el '
                                                                                                                                           'juego '
                                                                                                                                           'y '
                                                                                                                                           'el '
                                                                                                                                           'aislamiento.'),
 'Launch integration configured · runtime not verified': ('Integracja uruchamiania skonfigurowana · '
                                                          'działanie niezweryfikowane',
                                                          'Integración de inicio configurada · '
                                                          'ejecución sin verificar'),
 'Launch integration needs attention. Use Overview → Launch integration before testing.': ('Integracja '
                                                                                           'uruchamiania '
                                                                                           'wymaga '
                                                                                           'uwagi. '
                                                                                           'Przed '
                                                                                           'testem '
                                                                                           'otwórz '
                                                                                           'Przegląd → '
                                                                                           'Integracja '
                                                                                           'uruchamiania.',
                                                                                           'La '
                                                                                           'integración '
                                                                                           'de inicio '
                                                                                           'requiere '
                                                                                           'atención. '
                                                                                           'Antes de '
                                                                                           'probar, '
                                                                                           'abre '
                                                                                           'Resumen → '
                                                                                           'Integración '
                                                                                           'de inicio.'),
 'Refresh': ('Odśwież', 'Actualizar'),
 'Launch integration': ('Integracja uruchamiania', 'Integración de inicio'),
 'Advanced compatibility options': ('Zaawansowane opcje zgodności',
                                    'Opciones avanzadas de compatibilidad'),
 'Default': ('Domyślne', 'Predeterminado'),
 'Enabled': ('Włączone', 'Activado'),
 'Disabled': ('Wyłączone', 'Desactivado'),
 'Proton log folder: %1': ('Folder logów Proton: %1', 'Carpeta de registros de Proton: %1'),
 'Open log folder': ('Otwórz folder logów', 'Abrir carpeta de registros'),
 'Reset this section': ('Resetuj tę sekcję', 'Restablecer esta sección'),
 'Reset all tweaks': ('Resetuj wszystkie tweaks', 'Restablecer todos los ajustes'),
 'Resets change the editor first. Review and Apply to save. Other integrations and launcher-owned variables are preserved.': ('Reset '
                                                                                                                              'zmienia '
                                                                                                                              'najpierw '
                                                                                                                              'edytor. '
                                                                                                                              'Sprawdź '
                                                                                                                              'podgląd '
                                                                                                                              'i '
                                                                                                                              'zastosuj, '
                                                                                                                              'aby '
                                                                                                                              'zapisać. '
                                                                                                                              'Pozostałe '
                                                                                                                              'integracje '
                                                                                                                              'i '
                                                                                                                              'zmienne '
                                                                                                                              'launchera '
                                                                                                                              'zostają '
                                                                                                                              'zachowane.',
                                                                                                                              'El '
                                                                                                                              'restablecimiento '
                                                                                                                              'cambia '
                                                                                                                              'primero '
                                                                                                                              'el '
                                                                                                                              'editor. '
                                                                                                                              'Revisa '
                                                                                                                              'y '
                                                                                                                              'aplica '
                                                                                                                              'para '
                                                                                                                              'guardar. '
                                                                                                                              'Se '
                                                                                                                              'conservan '
                                                                                                                              'las '
                                                                                                                              'otras '
                                                                                                                              'integraciones '
                                                                                                                              'y '
                                                                                                                              'las '
                                                                                                                              'variables '
                                                                                                                              'del '
                                                                                                                              'lanzador.'),
 'Changes for the next launch': ('Zmiany dla następnego uruchomienia', 'Cambios para el próximo inicio'),
 'Saved GameOpti overrides': ('Zapisane nadpisania GameOpti', 'Ajustes guardados de GameOpti'),
 'Default (remove override)': ('Domyślne (usuń nadpisanie)', 'Predeterminado (eliminar ajuste)'),
 'These overrides are not supported by the selected runner: %1': ('Wybrany runner nie obsługuje tych '
                                                                  'nadpisań: %1',
                                                                  'El ejecutor seleccionado no admite '
                                                                  'estos ajustes: %1'),
 'Applying saves configuration; it does not verify runtime operation or a performance benefit. Heroic/Lutris also need Apply to launcher in Overview.': ('Zastosowanie '
                                                                                                                                                         'zapisuje '
                                                                                                                                                         'konfigurację; '
                                                                                                                                                         'nie '
                                                                                                                                                         'potwierdza '
                                                                                                                                                         'działania '
                                                                                                                                                         'ani '
                                                                                                                                                         'poprawy '
                                                                                                                                                         'wydajności. '
                                                                                                                                                         'Heroic/Lutris '
                                                                                                                                                         'wymaga '
                                                                                                                                                         'też '
                                                                                                                                                         '„Zastosuj '
                                                                                                                                                         'w '
                                                                                                                                                         'launcherze” '
                                                                                                                                                         'w '
                                                                                                                                                         'Przeglądzie.',
                                                                                                                                                         'Aplicar '
                                                                                                                                                         'guarda '
                                                                                                                                                         'la '
                                                                                                                                                         'configuración; '
                                                                                                                                                         'no '
                                                                                                                                                         'verifica '
                                                                                                                                                         'su '
                                                                                                                                                         'funcionamiento '
                                                                                                                                                         'ni '
                                                                                                                                                         'una '
                                                                                                                                                         'mejora '
                                                                                                                                                         'de '
                                                                                                                                                         'rendimiento. '
                                                                                                                                                         'Heroic/Lutris '
                                                                                                                                                         'también '
                                                                                                                                                         'necesita '
                                                                                                                                                         'Aplicar '
                                                                                                                                                         'al '
                                                                                                                                                         'lanzador '
                                                                                                                                                         'en '
                                                                                                                                                         'Resumen.'),
 'Preview changes': ('Podgląd zmian', 'Vista previa de cambios'),
 'Apply': ('Zastosuj', 'Aplicar'),
 'Game architecture: %1 · layer binaries: %2': ('Architektura gry: %1 · biblioteki warstwy: %2',
                                                'Arquitectura del juego: %1 · binarios de la capa: %2'),
 'Unknown': ('Nieznana', 'Desconocida'),
 'Resolve the marked conflicts before applying': ('Rozwiąż wskazane konflikty przed zastosowaniem',
                                                  'Resuelve los conflictos indicados antes de aplicar')}
TWEAKS_SHARED["Launcher setting: %1=%2. Default preserves it."] = ("Ustawienie launchera: %1=%2. Domyślne je zachowuje.", "Ajuste del lanzador: %1=%2. Predeterminado lo conserva.")

TWEAKS_SHARED.update({
    "Apply the saved profile to the launcher after Apply. Unsaved editor changes are not included.": ("Po zastosowaniu profilu zastosuj go w launcherze. Niezapisane zmiany edytora nie zostaną uwzględnione.", "Tras aplicar el perfil, aplícalo al lanzador. Los cambios sin guardar del editor no se incluyen."),
    "Steam → Properties → General → Launch Options. Keep your other launch arguments when configuring the existing runner.": ("Steam → Właściwości → Ogólne → Opcje uruchamiania. Konfigurując istniejący runner, zachowaj pozostałe argumenty uruchamiania.", "Steam → Propiedades → General → Parámetros de lanzamiento. Conserva los demás argumentos al configurar el ejecutor existente."),
    "Apply to launcher": ("Zastosuj w launcherze", "Aplicar al lanzador"),
    "Copy runner command": ("Kopiuj polecenie runnera", "Copiar comando del ejecutor"),
})

LAUNCH_DETAILS_SHARED = {
 "External frame generation": ("Zewnętrzne generowanie klatek", "Generación externa de fotogramas"),
 "Steam Deck mode (requested)": ("Tryb Steam Deck (żądany)", "Modo Steam Deck (solicitado)"),
 "Proton FSR4 upgrade (requested)": ("Aktualizacja FSR4 przez Proton (żądana)", "Actualización FSR4 de Proton (solicitada)"),
 "Default launch": ("Domyślne uruchamianie", "Inicio predeterminado"),
 "Selected features: %1": ("Wybrane funkcje: %1", "Funciones seleccionadas: %1"),
 "Technical launch details": ("Techniczne szczegóły uruchamiania", "Detalles técnicos de inicio"),
 "This is a template, not Steam's final command. Steam supplies the game command and runtime environment at launch. <preserved> marks values restored for the game.": ("To szablon, nie ostateczna komenda Steam. Steam dostarcza polecenie gry i środowisko podczas startu. <preserved> oznacza wartości przywracane dla gry.", "Es una plantilla, no el comando final de Steam. Steam proporciona el comando y el entorno al iniciar. <preserved> indica valores restaurados para el juego."),
 "Steam Runtime loader entries are isolated from host Gamescope and restored for Steam/Proton. Unrelated user libraries, driver settings and layers are preserved.": ("Wpisy loadera Steam Runtime są izolowane od hostowego Gamescope i przywracane dla Steam/Proton. Pozostałe biblioteki użytkownika, ustawienia sterownika i warstwy zostają zachowane.", "Los ajustes del cargador de Steam Runtime se aíslan de Gamescope del host y se restauran para Steam/Proton. Se conservan las otras bibliotecas, los ajustes del controlador y las capas del usuario."),
 "Off still needs suppression flags: they prevent known implicit frame-generation layers from activating in Gamescope or the game. These flags do not enable frame generation. Vulkan filters use plain glob patterns; shell quoting only groups each argument.": ("Wyłączenie nadal wymaga flag blokujących: zapobiegają aktywacji znanych niejawnych warstw generowania klatek w Gamescope lub grze. Te flagi nie włączają FG. Filtry Vulkan używają zwykłych wzorców glob; cudzysłowy powłoki tylko grupują argumenty.", "Desactivado sigue necesitando indicadores de bloqueo: impiden que las capas implícitas conocidas de generación se activen en Gamescope o el juego. No activan la generación. Los filtros Vulkan usan patrones glob normales; las comillas solo agrupan cada argumento."),
 "Managed game environment (preview):": ("Zarządzane środowisko gry (podgląd):", "Entorno gestionado del juego (vista previa):"),
}

LAUNCH_DETAILS_SHARED.update({
 "%1 blocks %2: its discovered layer had an inherited activation request or could load automatically.": ("%1 blokuje %2: wykryta warstwa miała odziedziczone żądanie aktywacji lub mogła ładować się automatycznie.", "%1 bloquea %2: su capa detectada tenía una solicitud de activación heredada o podía cargarse automáticamente."),
 "VK_INSTANCE_LAYERS removes only a discovered generator's explicit request; other layers are retained.": ("VK_INSTANCE_LAYERS usuwa tylko jawne żądanie wykrytego generatora; pozostałe warstwy zostają zachowane.", "VK_INSTANCE_LAYERS elimina solo la solicitud explícita de un generador detectado; conserva las otras capas."),
 "%1 removes only the specific conflicting generator request; unrelated filters are retained.": ("%1 usuwa wyłącznie konkretne żądanie kolidującego generatora; pozostałe filtry zostają zachowane.", "%1 elimina solo la solicitud específica del generador en conflicto; conserva los demás filtros."),
 "VK_LOADER_LAYERS_DISABLE targets a discovered generator without a usable disable gate; existing filters are retained.": ("VK_LOADER_LAYERS_DISABLE wskazuje wykryty generator bez dostępnego przełącznika wyłączenia; istniejące filtry zostają zachowane.", "VK_LOADER_LAYERS_DISABLE bloquea un generador detectado sin un interruptor de desactivación utilizable; conserva los filtros existentes."),
 "%1 belongs to post-processing, not frame generation: vkBasalt is suppressed while the other post-processing backend is selected.": ("%1 należy do post-processingu, nie do generowania klatek: vkBasalt jest blokowany, gdy wybrano drugi backend efektów.", "%1 pertenece al posprocesado, no a la generación: se bloquea vkBasalt mientras está seleccionado el otro sistema de efectos."),
 "Missing or unrequested opt-in generators add no suppression. Saved Off never requests GameOpti generation. Targeted entries below only resolve concrete layer conflicts; they are not proof that generation is active.": ("Brakujące generatory i generatory opt-in bez żądania aktywacji nie dodają blokad. Zapisane Wyłączone nigdy nie żąda generowania przez GameOpti. Poniższe wpisy rozwiązują wyłącznie konkretne konflikty warstw; nie dowodzą aktywnej generacji.", "Los generadores ausentes o de activación opcional sin solicitud no añaden bloqueos. Desactivado nunca solicita generación de GameOpti. Las entradas siguientes solo resuelven conflictos concretos; no demuestran generación activa."),
 "GameMode's client library is added beside existing preloads so Steam Runtime can import it. The game retains its original preload entries; the compositor does not receive this game injection.": ("Biblioteka klienta GameMode jest dodawana obok istniejących wpisów preload, aby Steam Runtime mógł ją zaimportować. Gra zachowuje pierwotne wpisy; kompozytor nie otrzymuje tej iniekcji gry.", "La biblioteca cliente de GameMode se añade junto a las precargas existentes para que Steam Runtime la importe. El juego conserva sus entradas originales; el compositor no recibe esta inyección."),
})



RAPIDOCR_SHARED = {'OCR engine': ('Silnik OCR', 'Motor OCR'),
 'Tesseract (default)': ('Tesseract (domyślny)', 'Tesseract (predeterminado)'),
 'RapidOCR (optional)': ('RapidOCR (opcjonalny)', 'RapidOCR (opcional)'),
 'RapidOCR models (English and Polish)': ('Modele RapidOCR (angielski i polski)',
                                          'Modelos RapidOCR (inglés y polaco)'),
 'Uses the original subtitle region. Download the models below; changing the engine does not change translation or voice.': ('Korzysta '
                                                                                                                             'z '
                                                                                                                             'oryginalnego '
                                                                                                                             'obszaru '
                                                                                                                             'napisów. '
                                                                                                                             'Pobierz '
                                                                                                                             'modele '
                                                                                                                             'poniżej. '
                                                                                                                             'Zmiana '
                                                                                                                             'silnika '
                                                                                                                             'nie '
                                                                                                                             'zmienia '
                                                                                                                             'tłumaczenia '
                                                                                                                             'ani '
                                                                                                                             'głosu.',
                                                                                                                             'Usa '
                                                                                                                             'la '
                                                                                                                             'región '
                                                                                                                             'original '
                                                                                                                             'de '
                                                                                                                             'subtítulos. '
                                                                                                                             'Descarga '
                                                                                                                             'los '
                                                                                                                             'modelos '
                                                                                                                             'abajo; '
                                                                                                                             'cambiar '
                                                                                                                             'el '
                                                                                                                             'motor '
                                                                                                                             'no '
                                                                                                                             'cambia '
                                                                                                                             'la '
                                                                                                                             'traducción '
                                                                                                                             'ni '
                                                                                                                             'la '
                                                                                                                             'voz.'),
 'Uses the existing text masks and automatic bright-background handling.': ('Korzysta z '
                                                                            'dotychczasowych masek '
                                                                            'tekstu i '
                                                                            'automatycznej obsługi '
                                                                            'jasnego tła.',
                                                                            'Usa las máscaras de '
                                                                            'texto existentes y el '
                                                                            'tratamiento '
                                                                            'automático de fondos '
                                                                            'claros.'),
 'RapidOCR models ready': ('Modele RapidOCR gotowe', 'Modelos RapidOCR listos'),
 'Download models (27 MB)': ('Pobierz modele (27 MB)',
                                      'Descargar modelos (27 MB)'),
 'Select Tesseract or RapidOCR; models are installed in Narrator settings': ('Wybierz Tesseract '
                                                                             'lub RapidOCR. Modele '
                                                                             'zainstalujesz w '
                                                                             'ustawieniach '
                                                                             'Lektora.',
                                                                             'Elige Tesseract o '
                                                                             'RapidOCR; los '
                                                                             'modelos se instalan '
                                                                             'en los ajustes del '
                                                                             'Narrador.'),
 'RapidOCR English and Polish subtitle OCR is ready': ('RapidOCR jest gotowy do odczytu '
                                                       'angielskich i polskich napisów',
                                                       'RapidOCR está listo para reconocer '
                                                       'subtítulos en inglés y polaco'),
 'Install the verified RapidOCR models and runtime': ('Zainstaluj zweryfikowane modele RapidOCR i '
                                                      'jego środowisko wykonawcze',
                                                      'Instala los modelos verificados de RapidOCR '
                                                      'y su entorno de ejecución'),
 'Install the verified RapidOCR models through the application': ('Zainstaluj zweryfikowane modele '
                                                                  'RapidOCR przez aplikację',
                                                                  'Instala los modelos verificados '
                                                                  'de RapidOCR desde la '
                                                                  'aplicación'),
 'Optional CPU OCR engine; install its models before selecting it': ('Opcjonalny silnik OCR '
                                                                     'korzystający z CPU. Przed '
                                                                     'użyciem zainstaluj jego '
                                                                     'modele.',
                                                                     'Motor OCR opcional para CPU; '
                                                                     'instala sus modelos antes de '
                                                                     'usarlo.')}


def fill(code: str) -> list[tuple[str, str]]:
    path = TRANSLATIONS / f"game_optimization_{code}.ts"
    tree = ET.parse(path)
    left: list[tuple[str, str]] = []
    for context in tree.getroot().findall("context"):
        name = context.findtext("name") or ""
        for message in context.findall("message"):
            translation = message.find("translation")
            shared_source = message.findtext("source") or ""
            if (translation is not None and code in {"pl", "es"}
                    and name in {"NarratorPage", "CouchGameDetails", "CouchSettings", "I18n"}
                    and shared_source in RAPIDOCR_SHARED):
                _finish(translation, RAPIDOCR_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if (translation is not None and code in {"pl", "es"}
                    and name in {"MangoHudTab", "MangoHudPreview", "MangoHudNumber", "CouchGameDetails", "I18n"} and shared_source in MANGOHUD_SHARED):
                _finish(translation, MANGOHUD_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if (translation is not None and code in {"pl", "es"}
                    and name in {"FrameGenerationPanel", "FrameGenerationSetting", "FrameGenerationMessages", "CouchGameDetails"} and shared_source in FG_SHARED):
                _finish(translation, FG_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if (translation is not None and code in {"pl", "es"}
                    and name in {"GpuMemoryCard", "CouchGameDetails"} and shared_source in GPU_SHARED):
                _finish(translation, GPU_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if (translation is not None and code in {"pl", "es"}
                    and name in {"PostProcessingPanel", "PostProcessingEffect", "CouchGameDetails"} and shared_source in POST_SHARED):
                _finish(translation, POST_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if (translation is not None and code in {"pl", "es"}
                    and name in {"DlssEnablerPanelContent", "CouchGameDetails"}
                    and shared_source in DLSS_SHARED):
                _finish(translation, DLSS_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if translation is not None and code in {"pl", "es"} and (name == "ProtonTweaksSection" or (name == "CouchGameDetails" and shared_source == "Proton Tweaks")) and shared_source in TWEAKS_SHARED:
                _finish(translation, TWEAKS_SHARED[shared_source][0 if code == "pl" else 1])
                continue
            if translation is not None and code in {"pl", "es"} and name == "LaunchEnvironmentDetails" and shared_source in LAUNCH_DETAILS_SHARED:
                _finish(translation, LAUNCH_DETAILS_SHARED[shared_source][0 if code == "pl" else 1])
                continue
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
            elif code == "es" and (name, source) in ES_DLSS:
                _finish(translation, ES_DLSS[(name, source)])
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
