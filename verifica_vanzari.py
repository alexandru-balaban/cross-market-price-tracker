"""
verifica_vanzari.py - cat de REALE sunt "chilipirurile" gasite de compara.py?

Foloseste istoricul din price_monitor.db (tabelul model_prices, populat la
fiecare scanare) ca sa vada ce anunturi dispar intre doua scanari - un anunt
disparut e, cel mai probabil, VANDUT (sau scos de pe site de vanzator).
Din asta calculam, per model si tara:
  - cate anunturi distincte am urmarit in total ("urmarite")
  - cate au disparut intre timp ("disparute" - aproximare pentru "vandute")
  - rata = disparute / urmarite (%)
  - cat au stat in medie pe piata inainte sa dispara (zile)

Ideea: daca pentru un model marcat drept "chilipir" vedem ca anunturi
similare chiar dispar des si repede, chilipirul e probabil real (piata
e lichida). Daca modelul are 0 disparitii dupa multe scanari, preturile
mici de-acolo pot fi doar anunturi vechi care stau nevandute.

ATENTIE - e doar o aproximare, nu adevar absolut:
  - un anunt poate disparea si pentru ca a iesit din paginile scanate
    (unele categorii citesc doar prima pagina per model - vezi "pagini_ka"
    in compara.CATEGORII), nu neaparat ca s-a vandut;
  - un vanzator poate sterge anuntul fara sa-l vanda;
  - cu cat se aduna mai multe scanari (bot.py scaneaza automat la fiecare
    SCANARE_AUTOMATA_ORE ore), cu atat cifrele de mai jos devin mai de
    incredere - in prima zi-doua sunt doar orientative.

Rulare:
    python3 verifica_vanzari.py                  # raport pe toate modelele
    python3 verifica_vanzari.py casti             # doar o categorie
    python3 verifica_vanzari.py "Sony A7 III"     # un singur model
"""

import sys

from compara import CATEGORII, acoperire, statistici_vanzari


def fmt(v, suffix=""):
    return "-" if v is None else f"{v:g}{suffix}"


def afiseaza(tinta=None):
    prima, ultima, n = acoperire()
    if not n:
        print("Nu exista inca istoric - ruleaza compara.py (sau botul) macar o data.")
        return
    print(f"Istoric: {n} scanari intre {prima} si {ultima}.")
    if n < 10:
        print("Putine scanari deocamdata - cifrele de mai jos sunt doar orientative,")
        print("devin de incredere dupa cateva zile de scanari automate (bot.py).")
    print()

    model = categorie = None
    if tinta:
        t = tinta.strip().lower()
        if t in CATEGORII:
            categorie = t
        else:
            model = tinta

    stats = statistici_vanzari(model=model, categorie=categorie)
    if not stats:
        tinta_txt = f" pentru '{tinta}'" if tinta else ""
        print(f"Inca n-am observat niciun anunt disparand{tinta_txt}.")
        print("Normal daca abia ai inceput sa scanezi - revino dupa cateva scanari.")
        return

    print(f"{'MODEL':<28}{'tara':>5}{'urmarite':>10}{'disparute':>11}{'rata %':>8}{'zile/piata':>12}")
    print("-" * 76)
    for r in stats:
        zile = round(r["ore_medii_pe_piata"] / 24, 1) if r["ore_medii_pe_piata"] is not None else None
        print(f"{r['model'][:27]:<28}{r['sursa']:>5}{r['urmarite']:>10}{r['disparute']:>11}"
              f"{fmt(r['rata_%']):>8}{fmt(zile):>12}")


if __name__ == "__main__":
    afiseaza(" ".join(sys.argv[1:]) or None)
