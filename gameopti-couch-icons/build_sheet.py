#!/usr/bin/env python3
"""Generuje reference-sheet.html z tej samej geometrii co pliki SVG."""
import os
import sys

from build_icons import ACCENT, BASE, ICONS, ORDER, STROKE, symbol

HERE = os.path.dirname(os.path.abspath(__file__))

NOTES = {
    "library": "Siatka kafli. Akcent wskazuje aktywny kafel.",
    "tasks": "Lista zadań z odhaczonym pierwszym punktem.",
    "updates": "Strzałka do tacki: dostępne pobranie.",
    "settings": "Koło zębate z sześcioma zębami, żeby doliny nie zlewały się przy małym rozmiarze.",
    "launch": "Trójkąt odtwarzania w okręgu: główna akcja widoku gry.",
    "check-updates": "Strzałka odświeżania z ptaszkiem: sprawdzanie, nie pobieranie.",
    "overview": "Wykres słupkowy, najwyższy słupek w akcencie.",
    "storage": "Walec danych, akcent na środkowym podziale.",
    "optimization": "Wskaźnik wydajności z igłą w akcencie.",
    "optiscaler": "Mały kwadrat rośnie w ramce: skalowanie obrazu.",
    "narrator": "Dymek z falą dźwięku: czytane napisy.",
}


def use(name, cls="", size=None, label=True):
    dim = f' width="{size}" height="{size}"' if size else ""
    aria = f' role="img" aria-label="{ICONS[name]["label"]}"' if label else ' aria-hidden="true"'
    return f'<svg class="ic {cls}" viewBox="0 0 48 48"{dim}{aria}><use href="#i-{name}"/></svg>'


def grid_svg():
    lines = []
    for i in range(0, 49, 4):
        w = ".5" if i % 12 == 0 else ".25"
        lines.append(f'<path d="M{i} 0V48M0 {i}H48" stroke="rgba(255,255,255,.09)" stroke-width="{w}"/>')
    lines.append('<rect x="6" y="6" width="36" height="36" stroke="rgba(69,208,181,.55)" stroke-width=".4" fill="none"/>')
    lines.append('<rect x="4" y="4" width="40" height="40" stroke="rgba(255,255,255,.28)" stroke-width=".3" stroke-dasharray="1.2 1.2" fill="none"/>')
    return f'<svg class="gridsvg" viewBox="0 0 48 48" aria-hidden="true">{"".join(lines)}</svg>'


def tile(name):
    return (
        f'<figure class="tile"><div class="stage">{use(name, size=72)}</div>'
        f'<figcaption><span class="lbl">{ICONS[name]["label"]}</span>'
        f'<code>{name}.svg</code></figcaption></figure>'
    )


def big(name):
    return (
        f'<article class="card"><div class="stage stage-big">{grid_svg()}{use(name, size=176)}</div>'
        f'<h3>{ICONS[name]["label"]}</h3><p>{NOTES[name]}</p></article>'
    )


def strip(size):
    nav = "".join(use(n, size=size) for n in ORDER if ICONS[n]["group"] == "nav")
    game = "".join(use(n, size=size) for n in ORDER if ICONS[n]["group"] == "game")
    px = STROKE / 48 * size
    px_s = f"{px:.1f}".replace(".", ",").rstrip("0").rstrip(",")
    return (
        f'<div class="row"><div class="rowlabel"><strong>{size} px</strong>'
        f'<span>obrys ok. {px_s} px</span></div>'
        f'<div class="strip" style="--gap:{max(10, size // 2)}px">'
        f'<div class="grp">{nav}</div><div class="grp">{game}</div></div></div>'
    )


def build():
    symbols = "".join(symbol(n) for n in ORDER)
    nav = "".join(tile(n) for n in ORDER if ICONS[n]["group"] == "nav")
    game = "".join(tile(n) for n in ORDER if ICONS[n]["group"] == "game")
    bigs = "".join(big(n) for n in ORDER)
    strips = "".join(strip(s) for s in (24, 32, 48, 64))

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>GameOpti Couch Mode: zestaw ikon</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:#F2F5F6; --surface:#FFFFFF; --ink:#12181E; --muted:#56646F; --line:#D9E0E5;
  --stage:#151B21; --stage-line:#232C34;
  --ic-base:{BASE}; --ic-accent:{ACCENT};
  box-sizing:border-box;
  padding-top:env(safe-area-inset-top,0px);
  padding-bottom:env(safe-area-inset-bottom,0px);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg:#0D1115; --surface:#12181E; --ink:#E8EEF1; --muted:#93A1AC; --line:#222B33;
  }}
}}
:root[data-theme="dark"] {{
  --bg:#0D1115; --surface:#12181E; --ink:#E8EEF1; --muted:#93A1AC; --line:#222B33;
}}
html {{ scroll-padding-top:env(safe-area-inset-top,0px); }}
*, *::before, *::after {{ box-sizing:inherit; }}
body {{
  margin:0; background:var(--bg); color:var(--ink);
  font-family:"Manrope", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  line-height:1.5; -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:40px 24px 72px; }}
h1 {{ font-size:clamp(1.7rem,4vw,2.4rem); font-weight:800; letter-spacing:-.02em; margin:0 0 10px; line-height:1.15; }}
h2 {{ font-size:1.25rem; font-weight:700; letter-spacing:-.01em; margin:0 0 4px; }}
h3 {{ font-size:1rem; font-weight:700; margin:14px 0 2px; }}
p {{ margin:0; color:var(--muted); }}
.lead {{ max-width:62ch; font-size:1.02rem; }}
section {{ margin-top:56px; }}
.sechead {{ display:flex; flex-wrap:wrap; align-items:end; justify-content:space-between; gap:12px; margin-bottom:20px; }}
.spec {{ display:flex; flex-wrap:wrap; gap:10px 28px; margin:26px 0 0; padding:0; }}
.spec div {{ min-width:0; }}
.spec dt {{ font-size:.8rem; color:var(--muted); }}
.spec dd {{ margin:0; font-weight:700; display:flex; align-items:center; gap:8px; }}
.sw {{ width:14px; height:14px; border-radius:50%; border:1px solid var(--line); flex:none; }}

.ic {{ fill:none; stroke-width:4; stroke-linecap:round; stroke-linejoin:round; display:block; flex:none; max-width:100%; }}
.b {{ stroke:var(--ic-base); }}
.a {{ stroke:var(--ic-accent); }}

.groupname {{ font-size:.9rem; font-weight:700; color:var(--muted); margin:26px 0 12px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:14px; }}
.tile {{ margin:0; }}
.stage {{ background:var(--stage); border:1px solid var(--stage-line); border-radius:18px; display:grid; place-items:center; aspect-ratio:1.15/1; position:relative; overflow:hidden; }}
.tile figcaption {{ padding:10px 2px 0; display:flex; flex-direction:column; gap:1px; }}
.lbl {{ font-weight:700; }}
code {{ font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.78rem; color:var(--muted); }}

.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:22px; }}
.card {{ min-width:0; }}
.stage-big {{ aspect-ratio:1/1; }}
.gridsvg {{ position:absolute; top:50%; left:50%; width:176px; height:176px; transform:translate(-50%,-50%); display:none; pointer-events:none; }}
.show-grid .gridsvg {{ display:block; }}
.btn {{ font:inherit; font-weight:700; font-size:.9rem; color:var(--ink); background:var(--surface); border:1px solid var(--line); border-radius:999px; padding:8px 16px; cursor:pointer; }}
.btn[aria-pressed="true"] {{ border-color:var(--ic-accent); box-shadow:inset 0 0 0 1px var(--ic-accent); }}
.btn:focus-visible {{ outline:3px solid var(--ic-accent); outline-offset:2px; }}

.rows {{ display:flex; flex-direction:column; gap:14px; }}
.row {{ display:grid; grid-template-columns:110px 1fr; gap:16px; align-items:center; }}
.rowlabel {{ display:flex; flex-direction:column; }}
.rowlabel span {{ font-size:.8rem; color:var(--muted); }}
.strip {{ background:var(--stage); border:1px solid var(--stage-line); border-radius:16px; padding:18px 22px; display:flex; flex-wrap:wrap; gap:14px 34px; align-items:center; }}
.grp {{ display:flex; flex-wrap:wrap; gap:14px var(--gap); align-items:center; }}
@media (max-width:640px) {{
  .wrap {{ padding:28px 16px 56px; }}
  .row {{ grid-template-columns:1fr; gap:6px; }}
  .rowlabel {{ flex-direction:row; gap:10px; align-items:baseline; }}
}}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false"><defs>{symbols}</defs></svg>
<main class="wrap">
  <header>
    <h1>Ikony Couch Mode dla GameOpti</h1>
    <p class="lead">Jedenaście ikon w jednym języku: ten sam obrys, te same zaokrąglone końcówki, jasna baza i jeden mintowy akcent na ikonę. Każda jest osobnym wektorem, który skaluje aplikacja.</p>
    <dl class="spec">
      <div><dt>Siatka</dt><dd>48 × 48, pole robocze 6–42</dd></div>
      <div><dt>Obrys</dt><dd>4 jednostki, końcówki i łączenia zaokrąglone</dd></div>
      <div><dt>Baza</dt><dd><span class="sw" style="background:{BASE}"></span>{BASE}</dd></div>
      <div><dt>Akcent</dt><dd><span class="sw" style="background:{ACCENT}"></span>{ACCENT}</dd></div>
    </dl>
  </header>

  <section aria-labelledby="s1">
    <div class="sechead"><div><h2 id="s1">1. Wszystkie ikony razem</h2><p>Podgląd na ciemnym tle, jak w Couch Mode.</p></div></div>
    <div class="groupname">Główna nawigacja</div>
    <div class="tiles">{nav}</div>
    <div class="groupname">Widok szczegółów gry</div>
    <div class="tiles">{game}</div>
  </section>

  <section aria-labelledby="s2" id="bigsec">
    <div class="sechead">
      <div><h2 id="s2">2. Każda ikona osobno</h2><p>Widok 176 px, jeden obrys na wszystkich ikonach.</p></div>
      <button class="btn" id="gridBtn" type="button" aria-pressed="false">Pokaż siatkę</button>
    </div>
    <div class="cards">{bigs}</div>
  </section>

  <section aria-labelledby="s3">
    <div class="sechead"><div><h2 id="s3">3. Skalowanie: 24, 32, 48, 64 px</h2><p>Ta sama geometria na każdym rozmiarze; obrys skaluje się razem z ikoną. Rozmiary w CSS px.</p></div></div>
    <div class="rows">{strips}</div>
  </section>
</main>
<script>
(function () {{
  var btn = document.getElementById('gridBtn');
  var sec = document.getElementById('bigsec');
  btn.addEventListener('click', function () {{
    var on = sec.classList.toggle('show-grid');
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? 'Ukryj siatkę' : 'Pokaż siatkę';
  }});
}})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "reference-sheet.html")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(build())
    print("OK ->", out)
