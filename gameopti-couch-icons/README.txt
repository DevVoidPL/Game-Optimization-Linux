GameOpti - Couch Mode: zestaw ikon (11 sztuk)

svg/         dwukolorowe: baza #E6ECEF + akcent mint #45D0B5, atrybuty inline (bez CSS, bez gradientow i filtrow)
svg-mono/    ten sam kształt, stroke="currentColor" - do kolorowania w aplikacji
reference-sheet.html   podglad: wszystkie ikony, duze widoki z siatka, skalowanie 24/32/48/64
build_icons.py         jedno zrodlo geometrii; zmien BASE / ACCENT na gorze pliku i uruchom ponownie:
                       python3 build_icons.py .

Zasady: viewBox 48x48, pole robocze linii srodkowej 6..42, obrys 4, zaokraglone konce i laczenia,
bez tekstu wewnatrz ikon. Rozmiar ustawia aplikacja (24/32/40/48/64 px lub inny) - obrys skaluje sie razem z ikona.

Mapowanie: library, tasks, updates, settings | launch, check-updates, overview, storage, optimization, optiscaler, narrator
