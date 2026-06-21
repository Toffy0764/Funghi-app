"""
Indice Fungaiolo — app web Streamlit
Stima quando/dove andare a funghi (porcini, finferli, russule)
basandosi su dati meteo da Open-Meteo (gratis, no API key).

Per deploy: vedi README.md
"""

import streamlit as st
import requests
from datetime import datetime
import pandas as pd

# ----------------------------------------------------------------------------
# CONFIGURAZIONE PAGINA
# ----------------------------------------------------------------------------

st.set_page_config(
    page_title="Indice Fungaiolo",
    page_icon="🍄",
    layout="centered",
)

# ----------------------------------------------------------------------------
# PROFILI PER SPECIE
# ----------------------------------------------------------------------------

PROFILI = {
    "porcini": {
        "label": "Porcini",
        "latino": "Boletus edulis",
        "colore": "#8B6F47",
        "finestra_cumulo_giorni": 10,
        "pioggia_min_significativa": 20,
        "pioggia_ideale_min": 40,
        "pioggia_ideale_max": 100,
        "temp_ideale_min": 15,
        "temp_ideale_max": 22,
        "temp_accett_min": 10,
        "temp_accett_max": 25,
        "temp_min_notturna_limite": 10,
        "giorni_attesa_min": 4,
        "giorni_attesa_max": 10,
        "giorni_raccolta": 7,
        "peso_pioggia": 0.35,
        "peso_temperatura": 0.25,
        "peso_finestra": 0.40,
    },
    "finferli": {
        "label": "Finferli",
        "latino": "Cantharellus cibarius",
        "colore": "#C9A227",
        "finestra_cumulo_giorni": 14,
        "pioggia_min_significativa": 25,
        "pioggia_ideale_min": 45,
        "pioggia_ideale_max": 120,
        "temp_ideale_min": 16,
        "temp_ideale_max": 24,
        "temp_accett_min": 10,
        "temp_accett_max": 30,
        "temp_min_notturna_limite": 8,
        "giorni_attesa_min": 8,
        "giorni_attesa_max": 15,
        "giorni_raccolta": 14,
        "peso_pioggia": 0.30,
        "peso_temperatura": 0.30,
        "peso_finestra": 0.40,
    },
    "russule": {
        "label": "Russule",
        "latino": "Russula spp.",
        "colore": "#A13D3D",
        "finestra_cumulo_giorni": 7,
        "pioggia_min_significativa": 15,
        "pioggia_ideale_min": 25,
        "pioggia_ideale_max": 80,
        "temp_ideale_min": 14,
        "temp_ideale_max": 23,
        "temp_accett_min": 8,
        "temp_accett_max": 27,
        "temp_min_notturna_limite": 9,
        "giorni_attesa_min": 3,
        "giorni_attesa_max": 8,
        "giorni_raccolta": 5,
        "peso_pioggia": 0.35,
        "peso_temperatura": 0.25,
        "peso_finestra": 0.40,
    },
}

SOGLIA_GIORNO_PIOVOSO = 5.0


# ----------------------------------------------------------------------------
# RECUPERO DATI METEO (con cache per non martellare l'API)
# ----------------------------------------------------------------------------

@st.cache_data(ttl=1800, show_spinner=False)
def geocodifica(luogo: str):
    """Convertilo un nome di luogo in lat/lon usando il geocoder di Open-Meteo."""
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": luogo.strip(), "count": 5, "format": "json"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("results"):
        return None
    res = data["results"][0]
    return {
        "lat": res["latitude"],
        "lon": res["longitude"],
        "nome": res.get("name", luogo),
        "regione": res.get("admin1", ""),
        "elevazione_luogo": res.get("elevation"),
    }


@st.cache_data(ttl=1800, show_spinner=False)
def scarica_dati_meteo(lat: float, lon: float):
    """
    Scarica dati storici (20gg) e forecast (7gg) da Open-Meteo.
    Prova prima ICON-D2 (modello DWD, risoluzione ~2km, ottimo per Alpi/Nord Italia,
    ma copertura limitata a Centro Europa e forecast breve). Se non disponibile o
    incompleto per questo punto, ricade sul best-match generico di Open-Meteo.
    Restituisce (dati, nome_modello_usato, elevazione_metri).
    L'elevazione è la quota della cella di griglia usata dal modello (non la quota
    esatta richiesta): utile per capire quanto il dato è rappresentativo della zona.
    """
    base_params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone": "auto",
        "past_days": 20,
        "forecast_days": 7,
    }
    url = "https://api.open-meteo.com/v1/forecast"

    def estrai(data):
        daily = data["daily"]
        risultato = {}
        for i, data_str in enumerate(daily["time"]):
            risultato[data_str] = {
                "pioggia_mm": daily["precipitation_sum"][i] or 0.0,
                "temp_max": daily["temperature_2m_max"][i],
                "temp_min": daily["temperature_2m_min"][i],
                "temp_media": daily["temperature_2m_mean"][i],
            }
        return risultato

    # Tentativo 1: ICON-D2 ad alta risoluzione (2km), valido per Centro Europa/Nord Italia
    try:
        params_icon = dict(base_params, models="icon_d2")
        r = requests.get(url, params=params_icon, timeout=20)
        r.raise_for_status()
        data = r.json()
        risultato = estrai(data)
        elevazione = data.get("elevation")
        # Controlla che non manchino valori essenziali (es. fuori area di copertura
        # ICON-D2 spesso torna giorni con valori nulli)
        valori_nulli = sum(
            1 for v in risultato.values()
            if v["temp_media"] is None or v["pioggia_mm"] is None
        )
        if len(risultato) >= 20 and valori_nulli == 0:
            return risultato, "ICON-D2 (DWD, ~2km)", elevazione
    except (requests.exceptions.RequestException, KeyError, ValueError):
        pass  # ricade sul best-match sotto

    # Fallback: best-match generico di Open-Meteo (copertura globale)
    r = requests.get(url, params=base_params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return estrai(data), "Best-match globale", data.get("elevation")


# ----------------------------------------------------------------------------
# LOGICA DI PUNTEGGIO
# ----------------------------------------------------------------------------

def cumulo_pioggia(date_ordinate, dati, idx_finale, finestra_giorni):
    idx_iniziale = max(0, idx_finale - finestra_giorni + 1)
    return sum(dati[date_ordinate[i]]["pioggia_mm"] for i in range(idx_iniziale, idx_finale + 1))


def punteggio_pioggia(cumulo_mm, p):
    p_min = p["pioggia_min_significativa"]
    p_ideale_min = p["pioggia_ideale_min"]
    p_ideale_max = p["pioggia_ideale_max"]
    if cumulo_mm < p_min:
        return max(0, (cumulo_mm / p_min) * 40)
    elif cumulo_mm <= p_ideale_min:
        frazione = (cumulo_mm - p_min) / (p_ideale_min - p_min)
        return 40 + frazione * 40
    elif cumulo_mm <= p_ideale_max:
        return 100
    else:
        eccesso = cumulo_mm - p_ideale_max
        return max(60, 100 - eccesso * 0.3)


def punteggio_temperatura(temp_media, temp_min, p):
    t_id_min, t_id_max = p["temp_ideale_min"], p["temp_ideale_max"]
    t_ac_min, t_ac_max = p["temp_accett_min"], p["temp_accett_max"]
    t_notte_limite = p["temp_min_notturna_limite"]

    if t_id_min <= temp_media <= t_id_max:
        p_media = 100
    elif t_ac_min <= temp_media < t_id_min:
        frazione = (temp_media - t_ac_min) / (t_id_min - t_ac_min)
        p_media = 50 + frazione * 50
    elif t_id_max < temp_media <= t_ac_max:
        frazione = (t_ac_max - temp_media) / (t_ac_max - t_id_max)
        p_media = 50 + frazione * 50
    else:
        p_media = 20

    if temp_min >= t_notte_limite:
        p_notte = 100
    else:
        deficit = t_notte_limite - temp_min
        p_notte = max(0, 100 - deficit * 15)

    return p_media * 0.7 + p_notte * 0.3


def punteggio_finestra_temporale(giorni, p):
    g_min, g_max, g_racc = p["giorni_attesa_min"], p["giorni_attesa_max"], p["giorni_raccolta"]
    if giorni is None or giorni < 0:
        return 0
    if giorni < g_min:
        return (giorni / g_min) * 50
    elif giorni <= g_max:
        return 100
    elif giorni <= g_max + g_racc:
        frazione = (giorni - g_max) / g_racc
        return 100 - frazione * 60
    else:
        return 20


def trova_giorni_da_ultima_pioggia_forte(date_ordinate, dati, idx_corrente, p):
    finestra = p["finestra_cumulo_giorni"]
    soglia = p["pioggia_min_significativa"]
    ultimo_idx = None
    for idx in range(0, idx_corrente + 1):
        cumulo = cumulo_pioggia(date_ordinate, dati, idx, finestra)
        giorno_piovoso = dati[date_ordinate[idx]]["pioggia_mm"] >= SOGLIA_GIORNO_PIOVOSO
        if cumulo >= soglia and giorno_piovoso:
            ultimo_idx = idx
    if ultimo_idx is None:
        return None
    return idx_corrente - ultimo_idx


def calcola_punteggi_giornalieri(dati, profilo):
    date_ordinate = sorted(dati.keys())
    righe = []
    for idx, data_str in enumerate(date_ordinate):
        cumulo = cumulo_pioggia(date_ordinate, dati, idx, profilo["finestra_cumulo_giorni"])
        giorni = trova_giorni_da_ultima_pioggia_forte(date_ordinate, dati, idx, profilo)
        p_pioggia = punteggio_pioggia(cumulo, profilo)
        p_temp = punteggio_temperatura(dati[data_str]["temp_media"], dati[data_str]["temp_min"], profilo)
        p_finestra = punteggio_finestra_temporale(giorni, profilo)
        finale = (
            p_pioggia * profilo["peso_pioggia"]
            + p_temp * profilo["peso_temperatura"]
            + p_finestra * profilo["peso_finestra"]
        )
        righe.append({
            "data": data_str,
            "pioggia_cumulata": round(cumulo, 1),
            "temp_media": dati[data_str]["temp_media"],
            "temp_min": dati[data_str]["temp_min"],
            "temp_max": dati[data_str]["temp_max"],
            "giorni_da_pioggia": giorni,
            "punteggio": round(finale, 1),
        })
    return righe


def etichetta(p):
    if p >= 75:
        return "Ottimo", "🟢"
    elif p >= 55:
        return "Buono", "🟡"
    elif p >= 35:
        return "Possibile", "🟠"
    else:
        return "Scarso", "🔴"


# ----------------------------------------------------------------------------
# STORICO (persistente tra le sessioni, salvato su disco come CSV)
# ----------------------------------------------------------------------------

import os
STORICO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storico_ricerche.csv")


def carica_storico():
    if os.path.isfile(STORICO_PATH):
        try:
            return pd.read_csv(STORICO_PATH)
        except Exception:
            return pd.DataFrame(columns=["luogo", "regione", "data", "specie", "punteggio", "registrato_il"])
    return pd.DataFrame(columns=["luogo", "regione", "data", "specie", "punteggio", "registrato_il"])


def salva_in_storico(luogo, regione, oggi_str, punteggi_oggi):
    df = carica_storico()
    nuove_righe = []
    for specie_key, punteggio in punteggi_oggi.items():
        nuove_righe.append({
            "luogo": luogo,
            "regione": regione,
            "data": oggi_str,
            "specie": specie_key,
            "punteggio": punteggio,
            "registrato_il": datetime.now().isoformat(timespec="seconds"),
        })
    nuovo_df = pd.DataFrame(nuove_righe)
    # rimuovi eventuali righe duplicate per stesso luogo+data+specie
    if not df.empty:
        df = df[~((df["luogo"] == luogo) & (df["data"] == oggi_str) & (df["specie"].isin(nuovo_df["specie"])))]
    df = pd.concat([df, nuovo_df], ignore_index=True)
    df.to_csv(STORICO_PATH, index=False)
    return df


# ----------------------------------------------------------------------------
# INTERFACCIA
# ----------------------------------------------------------------------------

st.markdown(
    """
    <style>
    .stApp { background-color: #2B2418; }
    h1, h2, h3 { font-family: Georgia, serif; color: #F0E6D2 !important; }
    p, span, div, label { color: #F0E6D2; }
    .stTextInput input { background-color: #241F16; color: #F0E6D2; border: 1px solid #4A4232; }
    .specie-card {
        background: #241F16;
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 14px;
        border: 1px solid #3A3326;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🍄 Indice Fungaiolo")
st.caption(
    "Incrocia pioggia e temperatura degli ultimi 20 giorni con le previsioni a 7 giorni "
    "per stimare le condizioni di porcini, finferli e russule."
)

luogo_default = st.session_state.pop("_ripeti_luogo", "")
luogo_input = st.text_input("Luogo", value=luogo_default, placeholder="Es. Asiago, Monte Baldo, Aspromonte…")
cerca = st.button("Cerca", type="primary", use_container_width=True)
cerca_automatica = bool(luogo_default)

if (cerca or cerca_automatica) and luogo_input.strip():
    with st.spinner("Cerco il luogo e scarico i dati meteo…"):
        geo = geocodifica(luogo_input)
        if geo is None:
            st.error(
                f"Nessun luogo trovato per **{luogo_input}**. "
                "Provo con un nome più semplice (es. solo la città o il paese principale della zona)."
            )
            st.session_state.pop("risultato", None)
        else:
            try:
                dati, modello_usato, elevazione = scarica_dati_meteo(geo["lat"], geo["lon"])
                date_ordinate = sorted(dati.keys())
                oggi_candidata = datetime.now().strftime("%Y-%m-%d")
                if oggi_candidata in dati:
                    oggi_str = oggi_candidata
                else:
                    candidati = [d for d in date_ordinate if d <= oggi_candidata]
                    oggi_str = candidati[-1] if candidati else date_ordinate[-1]

                serie = {key: calcola_punteggi_giornalieri(dati, profilo) for key, profilo in PROFILI.items()}

                st.session_state["risultato"] = {
                    "geo": geo,
                    "oggi_str": oggi_str,
                    "serie": serie,
                    "modello_usato": modello_usato,
                    "elevazione": elevazione,
                }

                punteggi_oggi = {}
                for key, righe in serie.items():
                    riga_oggi = next((r for r in righe if r["data"] == oggi_str), righe[-1])
                    punteggi_oggi[key] = riga_oggi["punteggio"]
                salva_in_storico(geo["nome"], geo["regione"], oggi_str, punteggi_oggi)

            except requests.exceptions.RequestException as e:
                st.error(f"Errore nel recupero dei dati meteo: {e}")

# --- Mostra risultati se presenti in sessione ---
if "risultato" in st.session_state:
    r = st.session_state["risultato"]
    geo = r["geo"]
    oggi_str = r["oggi_str"]
    serie = r["serie"]
    modello_usato = r.get("modello_usato", "Best-match globale")
    elevazione_modello = r.get("elevazione")
    elevazione_luogo = geo.get("elevazione_luogo")

    data_leggibile = datetime.strptime(oggi_str, "%Y-%m-%d").strftime("%-d %B")
    st.subheader(f"{geo['nome']}{', ' + geo['regione'] if geo['regione'] else ''}")
    st.caption(f"Condizioni aggiornate al {data_leggibile}")

    lat, lon = geo["lat"], geo["lon"]
    mappa_url = f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=14/{lat}/{lon}"
    st.markdown(
        f"📍 Coordinate usate per il calcolo: **{lat:.4f}, {lon:.4f}** &nbsp;·&nbsp; "
        f"[Vedi il punto esatto sulla mappa]({mappa_url})"
    )
    if modello_usato.startswith("ICON-D2"):
        st.caption(
            f"🎯 Modello: **{modello_usato}** — alta risoluzione (~2km), ottimo per zone alpine/Nord Italia. "
            "Non è una singola stazione fisica, ma una cella di griglia molto piccola centrata su queste coordinate."
        )
    else:
        st.caption(
            f"🌍 Modello: **{modello_usato}** — ICON-D2 non disponibile per questo punto "
            "(fuori area Centro Europa o dati incompleti), uso il modello globale generico."
        )

    if elevazione_modello is not None:
        riga_quota = f"⛰️ Altitudine della cella meteo usata: **{elevazione_modello:.0f} m**"
        if elevazione_luogo is not None:
            diff = elevazione_modello - elevazione_luogo
            riga_quota += f" (il luogo cercato è a ~{elevazione_luogo:.0f} m)"
            if abs(diff) >= 150:
                st.warning(
                    riga_quota + f" — differenza di **{diff:+.0f} m**: a questa quota la temperatura "
                    f"reale nel punto preciso che ti interessa può discostarsi di circa "
                    f"{abs(diff) * 0.65 / 100:.1f}°C da quella calcolata (gradiente medio ~0.65°C/100m)."
                )
            else:
                st.caption(riga_quota + f" (differenza {diff:+.0f} m, trascurabile)")
        else:
            st.caption(riga_quota)


    for key, profilo in PROFILI.items():
        righe = serie[key]
        riga_oggi = next((x for x in righe if x["data"] == oggi_str), righe[-1])
        testo_etichetta, emoji = etichetta(riga_oggi["punteggio"])

        with st.container():
            st.markdown(f"<div class='specie-card'>", unsafe_allow_html=True)
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown(
                    f"<h3 style='margin-bottom:0'>{profilo['label']}</h3>"
                    f"<p style='font-style:italic; color:#8A8270; font-size:12px; margin-top:-6px'>{profilo['latino']}</p>",
                    unsafe_allow_html=True,
                )
                st.metric("Punteggio", f"{int(riga_oggi['punteggio'])}/100", testo_etichetta)
            with col2:
                st.write(f"**Pioggia cumulata:** {riga_oggi['pioggia_cumulata']} mm")
                st.write(f"**Temp. media:** {riga_oggi['temp_media']:.1f}°C")
                if riga_oggi["giorni_da_pioggia"] is not None:
                    st.write(f"**Giorni da ultima pioggia forte:** {riga_oggi['giorni_da_pioggia']}")
                else:
                    st.write("_Nessuna pioggia significativa recente_")

                # giorno migliore nei prossimi giorni
                futuri = [x for x in righe if x["data"] >= oggi_str]
                if futuri:
                    migliore = max(futuri, key=lambda x: x["punteggio"])
                    if migliore["data"] != oggi_str and migliore["punteggio"] > riga_oggi["punteggio"]:
                        data_mig = datetime.strptime(migliore["data"], "%Y-%m-%d").strftime("%A %-d %B")
                        st.info(f"📅 Giorno migliore in arrivo: **{data_mig}** (punteggio {int(migliore['punteggio'])})")

            with st.expander("Diario di campo (28 giorni)"):
                df_grafico = pd.DataFrame(righe)
                df_grafico["data"] = pd.to_datetime(df_grafico["data"])
                df_grafico = df_grafico.set_index("data")[["punteggio"]]
                st.bar_chart(df_grafico, color=profilo["colore"], use_container_width=True)

            st.markdown("</div>", unsafe_allow_html=True)

# --- Storico ---
storico_df = carica_storico()
if not storico_df.empty:
    st.markdown("---")
    st.subheader("Luoghi cercati di recente")
    luoghi_recenti = (
        storico_df.sort_values("registrato_il", ascending=False)
        .drop_duplicates(subset=["luogo"])
        .head(15)
    )
    for _, row in luoghi_recenti.iterrows():
        punteggi_luogo = storico_df[(storico_df["luogo"] == row["luogo"]) & (storico_df["data"] == row["data"])]
        riassunto = "  ·  ".join(
            f"{PROFILI[r['specie']]['label']}: {int(r['punteggio'])}" for _, r in punteggi_luogo.iterrows()
        )
        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.write(f"**{row['luogo']}**{', ' + row['regione'] if pd.notna(row['regione']) and row['regione'] else ''}")
            st.caption(riassunto)
        with col_b:
            if st.button("Ripeti", key=f"ripeti_{row['luogo']}"):
                st.session_state["_ripeti_luogo"] = row["luogo"]
                st.rerun()
