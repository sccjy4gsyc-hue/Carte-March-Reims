"""
Application Streamlit — Carte de marché immobilier interactive
===================================================================

Version web du pilote (pilote_reims_centre.py) : mêmes sources de données
et même logique de calcul, mais avec une vraie interface — sélection de
la commune, du rayon, de l'année, des couches à afficher — au lieu de
paramètres en ligne de commande.

Ce fichier réutilise directement les fonctions de pilote_reims_centre.py
(aucune duplication de logique) : il doit rester dans le MÊME DOSSIER que
ce fichier pour que l'import fonctionne.

Installation
------------
    pip install streamlit streamlit-folium pandas requests shapely folium openpyxl branca

Lancement en local
-------------------
    streamlit run app_streamlit.py

Ça ouvre automatiquement un onglet de navigateur sur http://localhost:8501
— c'est déjà un "site web", juste hébergé sur votre machine.

Mise en ligne gratuite (pour avoir une URL partageable)
-----------------------------------------------------------
1. Créez un dépôt GitHub avec ce fichier + pilote_reims_centre.py +
   requirements.txt (liste des dépendances ci-dessus, une par ligne)
2. Allez sur share.streamlit.io, connectez votre compte GitHub
3. Sélectionnez le dépôt et ce fichier comme point d'entrée
4. Streamlit Community Cloud héberge l'app gratuitement et vous donne une
   URL du type https://votre-app.streamlit.app
"""

import io

import branca.colormap as cm
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import pilote_reims_centre as core

st.set_page_config(page_title="Carte marché immobilier — pilote", layout="wide")

st.title("Carte de marché immobilier par IRIS")
st.caption(
    "Pilote : agrège DVF, DPE, Zonage ABC, Carte des loyers, Sit@del2 et "
    "Registre des copropriétés sur une zone restreinte, à la maille IRIS."
)

# ---------------------------------------------------------------------------
# Barre latérale — paramètres
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Paramètres")
    code_commune = st.text_input("Code INSEE de la commune", value=core.CODE_COMMUNE_REIMS)
    centre_lat = st.number_input("Latitude du centre", value=core.CENTRE_LAT_DEFAUT, format="%.4f")
    centre_lon = st.number_input("Longitude du centre", value=core.CENTRE_LON_DEFAUT, format="%.4f")
    rayon_km = st.slider("Rayon de sélection (km)", 0.3, 5.0, 1.2, 0.1)
    annee_min = st.slider("Année minimale (DVF)", 2015, 2025, 2020)
    avec_sources_complementaires = st.checkbox(
        "Inclure Zonage ABC / Carte des loyers / Sit@del2 / Copropriétés", value=True
    )
    lancer = st.button("Lancer l'analyse", type="primary")

    st.divider()
    st.caption(
        "Astuce : lancez d'abord avec un grand rayon pour repérer les IRIS "
        "disponibles, puis resserrez pour affiner le pilote."
    )


# ---------------------------------------------------------------------------
# Récupération des données — mise en cache pour éviter de re-télécharger à
# chaque interaction avec l'interface (changement de couche affichée, etc.)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=3600)
def charger_iris(code_commune: str):
    return core.recuperer_iris_commune(code_commune)


@st.cache_data(show_spinner=False, ttl=3600)
def charger_dvf(code_commune: str, annee_min: int):
    return core.recuperer_dvf(code_commune, annee_min)


@st.cache_data(show_spinner=False, ttl=3600)
def charger_dpe(code_commune: str):
    return core.recuperer_dpe(code_commune)


@st.cache_data(show_spinner=False, ttl=3600)
def charger_sources_complementaires(code_commune: str, annee_min: int):
    contexte = {"Zonage ABC": core.recuperer_zonage_abc(code_commune)}
    loyers = core.recuperer_carte_loyers(code_commune)
    if loyers:
        contexte.update({f"Loyer — {k}": v for k, v in loyers.items() if "loyer" in k.lower()})
    contexte.update(core.recuperer_sitadel_commune(code_commune, annee_min))
    coproprietes = core.recuperer_coproprietes(code_commune)
    return contexte, coproprietes


# ---------------------------------------------------------------------------
# Carte avec choix de la couche affichée (fonction de coloration dynamique)
# ---------------------------------------------------------------------------
def construire_carte_interactive(iris_selection, agrege, centre_lat, centre_lon, metrique: str):
    carte = folium.Map(location=[centre_lat, centre_lon], zoom_start=15, tiles="cartodbpositron")
    agrege_idx = agrege.set_index("code_iris") if not agrege.empty else pd.DataFrame()

    valeurs = agrege[metrique].dropna() if not agrege.empty and metrique in agrege.columns else pd.Series(dtype=float)
    if not valeurs.empty:
        colormap = cm.linear.YlOrRd_09.scale(valeurs.min(), valeurs.max())
        colormap.caption = metrique
        colormap.add_to(carte)

    for iris in iris_selection:
        code = iris["code_iris"]
        stats = agrege_idx.loc[code] if code in agrege_idx.index else None

        valeur = stats[metrique] if stats is not None and metrique in (stats.index if stats is not None else []) else None
        couleur = colormap(valeur) if (valeur is not None and not pd.isna(valeur) and not valeurs.empty) else "#cccccc"

        popup_html = f"<b>{iris['nom_iris']}</b><br>Code IRIS : {code}<br>"
        if stats is not None:
            popup_html += (
                f"Prix médian/m² : {stats.get('prix_m2_median')} €<br>"
                f"Nb ventes DVF : {stats.get('nb_ventes_dvf')}<br>"
                f"Nb DPE : {stats.get('nb_dpe')}<br>"
                f"% logements F/G : {stats.get('pct_logements_F_G')}%"
            )
        else:
            popup_html += "Aucune donnée agrégée."

        folium.GeoJson(
            iris["geometry"].__geo_interface__,
            style_function=lambda x, c=couleur: {"color": "#555555", "weight": 1.5, "fillColor": c, "fillOpacity": 0.6},
            tooltip=iris["nom_iris"],
            popup=folium.Popup(popup_html, max_width=300),
        ).add_to(carte)

    return carte


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
if "resultats" not in st.session_state:
    st.session_state["resultats"] = None

if lancer:
    with st.spinner("Récupération des contours IRIS..."):
        iris_list = charger_iris(code_commune)
    if not iris_list:
        st.error("Aucun IRIS récupéré pour ce code commune. Vérifiez le code INSEE.")
    else:
        iris_selection = core.selectionner_iris_par_proximite(iris_list, centre_lat, centre_lon, rayon_km)
        if not iris_selection:
            st.warning("Aucun IRIS dans ce rayon — augmentez le rayon ou vérifiez les coordonnées du centre.")
        else:
            with st.spinner(f"Récupération DVF/DPE pour {len(iris_selection)} IRIS..."):
                dvf = charger_dvf(code_commune, annee_min)
                dpe = charger_dpe(code_commune)
                dvf_iris = (
                    core.rattacher_a_iris(dvf, iris_selection, "lat", "lon")
                    if not dvf.empty and "lat" in dvf.columns
                    else pd.DataFrame()
                )
                dpe_iris = (
                    core.rattacher_a_iris(dpe, iris_selection, "latitude", "longitude")
                    if not dpe.empty
                    else pd.DataFrame()
                )
                agrege = core.agreger_par_iris(dvf_iris, dpe_iris)

            contexte_commune, coproprietes = {}, pd.DataFrame()
            if avec_sources_complementaires:
                with st.spinner("Récupération Zonage ABC / Loyers / Sit@del2 / Copropriétés..."):
                    contexte_commune, coproprietes = charger_sources_complementaires(code_commune, annee_min)

            st.session_state["resultats"] = {
                "iris_selection": iris_selection,
                "agrege": agrege,
                "dvf_iris": dvf_iris,
                "dpe_iris": dpe_iris,
                "contexte_commune": contexte_commune,
                "coproprietes": coproprietes,
            }

# ---------------------------------------------------------------------------
# Affichage des résultats (persiste entre les interactions grâce à session_state)
# ---------------------------------------------------------------------------
resultats = st.session_state["resultats"]

if resultats is None:
    st.info("Réglez les paramètres dans la barre latérale puis cliquez sur *Lancer l'analyse*.")
else:
    iris_selection = resultats["iris_selection"]
    agrege = resultats["agrege"]

    col_carte, col_donnees = st.columns([2, 1])

    with col_carte:
        metriques_disponibles = [
            c for c in ["prix_m2_median", "nb_ventes_dvf", "pct_logements_F_G", "nb_dpe"]
            if c in agrege.columns
        ]
        metrique = st.selectbox(
            "Couche à afficher sur la carte",
            metriques_disponibles,
            format_func=lambda x: {
                "prix_m2_median": "Prix médian au m²",
                "nb_ventes_dvf": "Nombre de ventes",
                "pct_logements_F_G": "% logements F/G (DPE)",
                "nb_dpe": "Nombre de DPE",
            }.get(x, x),
        )
        carte = construire_carte_interactive(iris_selection, agrege, centre_lat, centre_lon, metrique)
        st_folium(carte, width=None, height=600)

    with col_donnees:
        st.subheader("Contexte commune")
        if resultats["contexte_commune"]:
            for cle, valeur in resultats["contexte_commune"].items():
                if valeur not in (None, {}):
                    st.metric(cle, valeur)
        else:
            st.caption("Sources complémentaires désactivées ou vides.")

    st.subheader("Synthèse par IRIS")
    st.dataframe(agrege, use_container_width=True)

    # Export à la volée (pas besoin de relancer le script en ligne de commande)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        agrege.to_excel(writer, sheet_name="Synthèse par IRIS", index=False)
        if resultats["contexte_commune"]:
            pd.DataFrame([resultats["contexte_commune"]]).to_excel(writer, sheet_name="Contexte commune", index=False)
        if not resultats["dvf_iris"].empty:
            resultats["dvf_iris"].to_excel(writer, sheet_name="DVF détail", index=False)
        if not resultats["dpe_iris"].empty:
            resultats["dpe_iris"].to_excel(writer, sheet_name="DPE détail", index=False)
        if not resultats["coproprietes"].empty:
            resultats["coproprietes"].to_excel(writer, sheet_name="Copropriétés", index=False)

    st.download_button(
        "Télécharger le détail (Excel)",
        data=buffer.getvalue(),
        file_name=f"donnees_{code_commune}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
