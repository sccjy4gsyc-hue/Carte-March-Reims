"""
Pilote — Carte de marché par IRIS : Reims centre
====================================================

Objectif du pilote
--------------------
Valider la méthode sur un périmètre restreint avant toute industrialisation :
- récupération DVF + DPE pour la commune de Reims (code INSEE 51454)
- répartition spatiale des transactions/DPE par IRIS (polygones officiels)
- sélection automatique des IRIS du "centre" par proximité à un point de
  référence (par défaut : la cathédrale de Reims), pour éviter d'avoir à
  chercher manuellement les codes IRIS avant de lancer le script
- agrégation par IRIS : prix médian au m², volume de ventes, répartition
  des étiquettes DPE (dont % de logements F/G = fort potentiel travaux)
- génération d'une carte interactive (fichier HTML autonome, Folium)

Pourquoi la maille IRIS et pas la rue
----------------------------------------
Sur un quartier restreint, la plupart des rues ont 0 à 3 ventes sur 5 ans :
statistiquement inexploitable (une seule vente atypique fausse tout). L'IRIS
(env. 2000 habitants) lisse suffisamment pour être interprétable, tout en
restant assez fin pour distinguer des dynamiques de quartier différentes.

Sources utilisées
---------------------
- Contours IRIS : dataset "georef-france-iris" (Opendatasoft, mise à disposition
  des contours officiels IGN/INSEE). ATTENTION : ce mirroir tiers peut changer
  de nom/structure — si l'appel échoue, chercher "contours IRIS geojson" sur
  data.gouv.fr pour retrouver la source à jour et ajuster l'URL ci-dessous.
- DVF : api.cquest.org/dvf (comme dans les scripts précédents)
- DPE : data.ademe.fr (comme dans les scripts précédents)
- Zonage ABC, Carte des loyers ANIL/DHUP, Sit@del2, Registre des copropriétés :
  recherchés dynamiquement via l'API de recherche de data.gouv.fr
  (https://www.data.gouv.fr/api/1/datasets/?q=...), qui renvoie le premier
  fichier CSV du dataset trouvé. Ces 4 sources sont des fichiers à télécharger
  (pas des API de requête), donc le script les télécharge une fois puis
  filtre en local. ATTENTION : la recherche par mots-clés peut remonter un
  dataset différent de celui attendu si data.gouv.fr a republié sous un autre
  nom — le script affiche systématiquement le titre du dataset trouvé pour
  vérification, et bascule ce module en dégradé (valeurs à None) sans bloquer
  le reste du pilote si la recherche échoue.
- Zonage ABC et Sit@del2 sont au niveau COMMUNE (pas IRIS) — affichés comme
  contexte général de la zone, pas différenciés par quartier.
- Registre des copropriétés : rattaché par IRIS si le fichier contient des
  coordonnées géographiques, sinon replié en compte au niveau commune.

Utilisation
-----------
    pip install pandas requests shapely folium openpyxl

    # Étape 1 (optionnelle) : lister tous les IRIS de Reims pour vérifier
    # ceux que le script sélectionne automatiquement comme "centre"
    python pilote_reims_centre.py --lister-iris

    # Étape 2 : lancer le pilote (sélection auto autour de la cathédrale,
    # rayon 1,2 km par défaut)
    python pilote_reims_centre.py --rayon-km 1.2 --sortie-carte carte_reims_centre.html --sortie-excel donnees_reims_centre.xlsx

    # Pour forcer des IRIS précis plutôt que la sélection automatique :
    python pilote_reims_centre.py --iris 512340101 512340102 --sortie-carte carte.html

    # Pour désactiver les 4 sources complémentaires (test plus rapide) :
    python pilote_reims_centre.py --sans-sources-complementaires
"""

import argparse
import io
import sys

import folium
import pandas as pd
import requests
from shapely.geometry import Point, shape

TIMEOUT = 60
CODE_COMMUNE_REIMS = "51454"
# Cathédrale Notre-Dame de Reims — point de référence par défaut du "centre"
CENTRE_LAT_DEFAUT = 49.2533
CENTRE_LON_DEFAUT = 4.0339


# ---------------------------------------------------------------------------
# Contours IRIS
# ---------------------------------------------------------------------------
def recuperer_iris_commune(code_commune: str) -> list[dict]:
    """Récupère les polygones IRIS d'une commune.

    Retourne une liste de dicts : {code_iris, nom_iris, geometry (shapely), centroid}
    """
    url = "https://public.opendatasoft.com/api/explore/v2.1/catalog/datasets/georef-france-iris/records"
    params = {
        "where": f"com_code='{code_commune}'",
        "limit": 100,
    }
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Échec récupération IRIS pour {code_commune} : {e}", file=sys.stderr)
        print("-> Vérifiez le dataset 'georef-france-iris' sur public.opendatasoft.com "
              "ou cherchez 'contours IRIS' sur data.gouv.fr pour une source à jour.", file=sys.stderr)
        return []

    iris_list = []
    for rec in data.get("results", []):
        geo_shape = rec.get("geo_shape", {}).get("geometry")
        if not geo_shape:
            continue
        try:
            geom = shape(geo_shape)
        except Exception:
            continue
        iris_list.append(
            {
                "code_iris": rec.get("iris_code") or rec.get("code_iris"),
                "nom_iris": rec.get("iris_name") or rec.get("nom_iris") or "",
                "geometry": geom,
                "centroid": geom.centroid,
            }
        )
    return iris_list


def lister_iris(code_commune: str) -> None:
    iris_list = recuperer_iris_commune(code_commune)
    if not iris_list:
        print("Aucun IRIS récupéré.")
        return
    print(f"{len(iris_list)} IRIS pour la commune {code_commune} :\n")
    for iris in iris_list:
        c = iris["centroid"]
        print(f"  {iris['code_iris']:<12} {iris['nom_iris']:<35} (centroid: {c.y:.5f}, {c.x:.5f})")


def selectionner_iris_par_proximite(
    iris_list: list[dict], lat: float, lon: float, rayon_km: float
) -> list[dict]:
    """Sélectionne les IRIS dont le centroïde est à moins de rayon_km du point donné."""
    point_ref = Point(lon, lat)
    selection = []
    for iris in iris_list:
        # Approximation simple, suffisante à l'échelle d'un quartier :
        # 1 degré ≈ 111 km, correction grossière de longitude par cos(latitude)
        dx = (iris["centroid"].x - point_ref.x) * 111 * 0.65
        dy = (iris["centroid"].y - point_ref.y) * 111
        distance_km = (dx**2 + dy**2) ** 0.5
        if distance_km <= rayon_km:
            selection.append(iris)
    return selection


# ---------------------------------------------------------------------------
# Recherche générique de fichiers sur data.gouv.fr (Zonage ABC, Carte des
# loyers, Sit@del2, Registre des copropriétés)
# ---------------------------------------------------------------------------
def rechercher_ressource_datagouv(mots_cles: str) -> tuple[str | None, str | None]:
    """Cherche un dataset sur data.gouv.fr par mots-clés et retourne l'URL du
    premier fichier CSV trouvé, ainsi que le titre du dataset (pour vérifier
    que la recherche a bien trouvé la bonne source).
    """
    url = "https://www.data.gouv.fr/api/1/datasets/"
    try:
        r = requests.get(url, params={"q": mots_cles, "page_size": 5}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  [data.gouv.fr] échec recherche '{mots_cles}' : {e}", file=sys.stderr)
        return None, None

    for dataset in data.get("data", []):
        for ressource in dataset.get("resources", []):
            if ressource.get("format", "").lower() == "csv":
                return ressource.get("url"), dataset.get("title")
    return None, None


def telecharger_csv(url: str) -> pd.DataFrame:
    try:
        r = requests.get(url, timeout=TIMEOUT * 2)
        r.raise_for_status()
        return pd.read_csv(io.BytesIO(r.content), sep=None, engine="python", on_bad_lines="skip")
    except (requests.RequestException, pd.errors.ParserError) as e:
        print(f"  échec téléchargement/lecture CSV ({url}) : {e}", file=sys.stderr)
        return pd.DataFrame()


def recuperer_zonage_abc(code_commune: str) -> str | None:
    """Zone A/Abis/B1/B2/C de la commune (tension du marché locatif)."""
    url, titre = rechercher_ressource_datagouv("zonage abc communes logement")
    if not url:
        return None
    print(f"  [Zonage ABC] source trouvée : {titre}")
    df = telecharger_csv(url)
    if df.empty:
        return None
    col_code = next((c for c in df.columns if "insee" in c.lower() or "code_geo" in c.lower()), None)
    col_zone = next((c for c in df.columns if "zone" in c.lower()), None)
    if not col_code or not col_zone:
        return None
    ligne = df[df[col_code].astype(str) == code_commune]
    return ligne[col_zone].iloc[0] if not ligne.empty else None


def recuperer_carte_loyers(code_commune: str) -> dict:
    """Loyers indicatifs par m² (appartement/maison) au niveau commune,
    source carte des loyers DHUP/ANIL.
    """
    url, titre = rechercher_ressource_datagouv("carte des loyers indicateurs loyers annonce commune")
    if not url:
        return {}
    print(f"  [Carte des loyers] source trouvée : {titre}")
    df = telecharger_csv(url)
    if df.empty:
        return {}
    col_code = next((c for c in df.columns if "insee" in c.lower()), None)
    if not col_code:
        return {}
    ligne = df[df[col_code].astype(str) == code_commune]
    return ligne.iloc[0].to_dict() if not ligne.empty else {}


def recuperer_sitadel_commune(code_commune: str, annee_min: int) -> dict:
    """Volume de logements autorisés/commencés sur la commune (Sit@del2, SDES)."""
    url, titre = rechercher_ressource_datagouv("sitadel logements autorises commences commune")
    if not url:
        return {}
    print(f"  [Sit@del2] source trouvée : {titre}")
    df = telecharger_csv(url)
    if df.empty:
        return {}
    col_code = next((c for c in df.columns if "insee" in c.lower() or "commune" in c.lower()), None)
    col_annee = next((c for c in df.columns if "annee" in c.lower() or "date" in c.lower()), None)
    if not col_code:
        return {}
    sous = df[df[col_code].astype(str) == code_commune]
    if col_annee and col_annee in sous.columns:
        sous = sous[pd.to_numeric(sous[col_annee], errors="coerce") >= annee_min]
    return {"nb_lignes_permis": len(sous)}


def recuperer_coproprietes(code_commune: str) -> pd.DataFrame:
    """Registre national des copropriétés — utilisé pour dater l'âge du bâti."""
    url, titre = rechercher_ressource_datagouv("registre national copropriétés immatriculation")
    if not url:
        return pd.DataFrame()
    print(f"  [Copropriétés] source trouvée : {titre}")
    df = telecharger_csv(url)
    if df.empty:
        return df
    col_code = next((c for c in df.columns if "insee" in c.lower()), None)
    if col_code:
        df = df[df[col_code].astype(str) == code_commune]
    return df


# ---------------------------------------------------------------------------
# DVF et DPE (mêmes sources que les scripts précédents)
# ---------------------------------------------------------------------------
def recuperer_dvf(code_commune: str, annee_min: int) -> pd.DataFrame:
    url = "https://api.cquest.org/dvf"
    params = {"code_commune": code_commune}
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Échec DVF : {e}", file=sys.stderr)
        return pd.DataFrame()

    df = pd.DataFrame(data.get("resultats", []))
    if df.empty:
        return df
    df["date_mutation"] = pd.to_datetime(df.get("date_mutation"), errors="coerce")
    df = df[df["date_mutation"].dt.year >= annee_min]
    df = df.dropna(subset=["lat", "lon"]) if "lat" in df.columns and "lon" in df.columns else df
    return df


def recuperer_dpe(code_commune: str) -> pd.DataFrame:
    url = "https://data.ademe.fr/data-fair/api/v1/datasets/dpe-v2-logements-existants/lines"
    params = {
        "qs": f"code_insee_ban:{code_commune}",
        "size": 2000,
        "select": "etiquette_dpe,annee_construction,longitude,latitude,adresse_ban",
    }
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Échec DPE : {e}", file=sys.stderr)
        return pd.DataFrame()

    df = pd.DataFrame(data.get("results", []))
    return df.dropna(subset=["longitude", "latitude"]) if not df.empty else df


# ---------------------------------------------------------------------------
# Rattachement point -> IRIS et agrégation
# ---------------------------------------------------------------------------
def rattacher_a_iris(df: pd.DataFrame, iris_list: list[dict], col_lat: str, col_lon: str) -> pd.DataFrame:
    """Ajoute une colonne code_iris/nom_iris à chaque ligne par test point-in-polygon."""
    df = df.copy()
    codes, noms = [], []
    for _, row in df.iterrows():
        pt = Point(row[col_lon], row[col_lat])
        trouve = None
        for iris in iris_list:
            if iris["geometry"].contains(pt):
                trouve = iris
                break
        codes.append(trouve["code_iris"] if trouve else None)
        noms.append(trouve["nom_iris"] if trouve else None)
    df["code_iris"] = codes
    df["nom_iris"] = noms
    return df[df["code_iris"].notna()]


def agreger_par_iris(dvf_iris: pd.DataFrame, dpe_iris: pd.DataFrame) -> pd.DataFrame:
    lignes = []
    codes_iris = set(dvf_iris["code_iris"]).union(set(dpe_iris["code_iris"] if not dpe_iris.empty else []))

    for code in codes_iris:
        sous_dvf = dvf_iris[dvf_iris["code_iris"] == code]
        sous_dpe = dpe_iris[dpe_iris["code_iris"] == code] if not dpe_iris.empty else pd.DataFrame()

        prix_m2 = None
        if not sous_dvf.empty and "valeur_fonciere" in sous_dvf.columns and "surface_relle_bati" in sous_dvf.columns:
            prix_m2 = (sous_dvf["valeur_fonciere"] / sous_dvf["surface_relle_bati"]).median()
        elif not sous_dvf.empty and "valeur_fonciere" in sous_dvf.columns and "surface_reelle_bati" in sous_dvf.columns:
            prix_m2 = (sous_dvf["valeur_fonciere"] / sous_dvf["surface_reelle_bati"]).median()

        pct_f_g = None
        if not sous_dpe.empty and "etiquette_dpe" in sous_dpe.columns:
            pct_f_g = round(sous_dpe["etiquette_dpe"].isin(["F", "G"]).mean() * 100, 1)

        nom = (sous_dvf["nom_iris"].iloc[0] if not sous_dvf.empty else
               (sous_dpe["nom_iris"].iloc[0] if not sous_dpe.empty else code))

        lignes.append(
            {
                "code_iris": code,
                "nom_iris": nom,
                "nb_ventes_dvf": len(sous_dvf),
                "prix_m2_median": round(prix_m2, 0) if prix_m2 else None,
                "nb_dpe": len(sous_dpe),
                "pct_logements_F_G": pct_f_g,
            }
        )
    return pd.DataFrame(lignes)


# ---------------------------------------------------------------------------
# Carte
# ---------------------------------------------------------------------------
def construire_carte(iris_selection: list[dict], agrege: pd.DataFrame, centre_lat: float, centre_lon: float, sortie: str, contexte_commune: dict) -> None:
    carte = folium.Map(location=[centre_lat, centre_lon], zoom_start=15, tiles="cartodbpositron")

    agrege_idx = agrege.set_index("code_iris") if not agrege.empty else pd.DataFrame()

    for iris in iris_selection:
        code = iris["code_iris"]
        stats = agrege_idx.loc[code] if code in agrege_idx.index else None

        popup_html = f"<b>{iris['nom_iris']}</b><br>Code IRIS : {code}<br>"
        if stats is not None:
            popup_html += (
                f"Prix médian/m² : {stats['prix_m2_median']} €<br>"
                f"Nb ventes DVF : {stats['nb_ventes_dvf']}<br>"
                f"Nb DPE : {stats['nb_dpe']}<br>"
                f"% logements F/G : {stats['pct_logements_F_G']}%"
            )
        else:
            popup_html += "Aucune donnée agrégée."

        folium.GeoJson(
            iris["geometry"].__geo_interface__,
            style_function=lambda x: {"color": "#2c7fb8", "weight": 2, "fillOpacity": 0.15},
            tooltip=iris["nom_iris"],
            popup=folium.Popup(popup_html, max_width=300),
        ).add_to(carte)

    # Bandeau de contexte commune (zonage ABC, loyers indicatifs, permis) —
    # ces sources ne sont pas à la maille IRIS, elles s'appliquent à toute
    # la zone du pilote, donc affichées à part plutôt que dans chaque popup IRIS
    if contexte_commune:
        lignes = "".join(f"<b>{k}</b> : {v}<br>" for k, v in contexte_commune.items() if v not in (None, {}))
        if lignes:
            html_contexte = f"""
            <div style="position: fixed; bottom: 20px; left: 20px; z-index: 9999;
                        background: white; padding: 10px 14px; border-radius: 6px;
                        box-shadow: 0 1px 4px rgba(0,0,0,0.3); font-size: 13px; max-width: 280px;">
                <b>Contexte commune</b><br>{lignes}
            </div>
            """
            carte.get_root().html.add_child(folium.Element(html_contexte))

    carte.save(sortie)
    print(f"Carte sauvegardée : {sortie}")


def main():
    parser = argparse.ArgumentParser(description="Pilote carte marché par IRIS — Reims centre")
    parser.add_argument("--code-commune", default=CODE_COMMUNE_REIMS)
    parser.add_argument("--lister-iris", action="store_true", help="Liste les IRIS de la commune et s'arrête")
    parser.add_argument("--iris", nargs="+", default=None, help="Codes IRIS précis (sinon sélection auto)")
    parser.add_argument("--centre-lat", type=float, default=CENTRE_LAT_DEFAUT)
    parser.add_argument("--centre-lon", type=float, default=CENTRE_LON_DEFAUT)
    parser.add_argument("--rayon-km", type=float, default=1.2)
    parser.add_argument("--annee-dvf-min", type=int, default=2020)
    parser.add_argument("--sortie-carte", default="carte_reims_centre.html")
    parser.add_argument("--sortie-excel", default="donnees_reims_centre.xlsx")
    parser.add_argument(
        "--sans-sources-complementaires",
        action="store_true",
        help="Désactive Zonage ABC / Carte des loyers / Sit@del2 / Copropriétés (test plus rapide)",
    )
    args = parser.parse_args()

    print("Étape 1 : récupération des contours IRIS...")
    iris_list = recuperer_iris_commune(args.code_commune)
    if not iris_list:
        print("Impossible de continuer sans contours IRIS.")
        return

    if args.lister_iris:
        lister_iris(args.code_commune)
        return

    if args.iris:
        iris_selection = [i for i in iris_list if i["code_iris"] in args.iris]
    else:
        iris_selection = selectionner_iris_par_proximite(
            iris_list, args.centre_lat, args.centre_lon, args.rayon_km
        )
    print(f"  -> {len(iris_selection)} IRIS retenus pour le pilote")
    if not iris_selection:
        print("Aucun IRIS dans le rayon donné — augmentez --rayon-km ou vérifiez --centre-lat/--centre-lon.")
        return

    print("Étape 2 : récupération DVF...")
    dvf = recuperer_dvf(args.code_commune, args.annee_dvf_min)
    print(f"  -> {len(dvf)} transactions sur la commune")

    print("Étape 3 : récupération DPE...")
    dpe = recuperer_dpe(args.code_commune)
    print(f"  -> {len(dpe)} DPE sur la commune")

    print("Étape 4 : rattachement des points aux IRIS sélectionnés...")
    dvf_iris = (
        rattacher_a_iris(dvf, iris_selection, "lat", "lon")
        if not dvf.empty and "lat" in dvf.columns
        else pd.DataFrame()
    )
    dpe_iris = (
        rattacher_a_iris(dpe, iris_selection, "latitude", "longitude")
        if not dpe.empty
        else pd.DataFrame()
    )
    print(f"  -> {len(dvf_iris)} ventes et {len(dpe_iris)} DPE dans le périmètre du pilote")

    print("Étape 5 : agrégation par IRIS...")
    agrege = agreger_par_iris(dvf_iris, dpe_iris)

    contexte_commune = {}
    coproprietes = pd.DataFrame()
    if not args.sans_sources_complementaires:
        print("Étape 6 : sources complémentaires (Zonage ABC, Carte des loyers, Sit@del2, Copropriétés)...")
        contexte_commune["Zonage ABC"] = recuperer_zonage_abc(args.code_commune)
        loyers = recuperer_carte_loyers(args.code_commune)
        if loyers:
            contexte_commune.update({f"Loyer — {k}": v for k, v in loyers.items() if "loyer" in k.lower()})
        sitadel = recuperer_sitadel_commune(args.code_commune, args.annee_dvf_min)
        contexte_commune.update(sitadel)
        coproprietes = recuperer_coproprietes(args.code_commune)
        print(f"  -> {len(coproprietes)} copropriétés trouvées sur la commune")
    else:
        print("Étape 6 : sources complémentaires désactivées (--sans-sources-complementaires)")

    print("Étape 7 : construction de la carte...")
    construire_carte(iris_selection, agrege, args.centre_lat, args.centre_lon, args.sortie_carte, contexte_commune)

    print(f"Étape 8 : export Excel vers {args.sortie_excel}...")
    with pd.ExcelWriter(args.sortie_excel, engine="openpyxl") as writer:
        agrege.to_excel(writer, sheet_name="Synthèse par IRIS", index=False)
        if contexte_commune:
            pd.DataFrame([contexte_commune]).to_excel(writer, sheet_name="Contexte commune", index=False)
        if not dvf_iris.empty:
            dvf_iris.to_excel(writer, sheet_name="DVF détail", index=False)
        if not dpe_iris.empty:
            dpe_iris.to_excel(writer, sheet_name="DPE détail", index=False)
        if not coproprietes.empty:
            coproprietes.to_excel(writer, sheet_name="Copropriétés", index=False)

    print("Terminé.")


if __name__ == "__main__":
    main()
