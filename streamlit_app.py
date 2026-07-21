"""
Indice Fungaiolo — app web Streamlit
Stima quando/dove andare a funghi (porcini, finferli, russule)
basandosi su dati meteo da Open-Meteo (gratis, no API key).

Per deploy: vedi README.md
"""

import streamlit as st
import requests
from datetime import datetime
import re
import concurrent.futures
import folium
from streamlit_folium import st_folium


def parse_coordinate(testo: str):
    """
    Prova a interpretare il testo come una coppia di coordinate (lat, lon).
    Accetta formati:
      '46.023, 11.567'       (decimale internazionale)
      '46.023 11.567'        (decimale con spazio)
      '46,023; 11,567'       (decimale europeo)
      '46.0234,11.5678'      (decimale senza spazi)
      '46°05\'37.69"N 11°28\'22.00"E'  (DMS con simboli)
    Restituisce (lat, lon) se riconosciuto, None altrimenti.
    """
    testo = testo.strip()

    # --- Formato DMS: gradi°minuti'secondi"N/S gradi°minuti'secondi"E/W ---
    dms_pattern = re.compile(
        r"""(\d+)[°º][\s]*(\d+)[''′][\s]*(\d+(?:[.,]\d+)?)[\s]*[""″][\s]*([NSns])
            [\s,]+
            (\d+)[°º][\s]*(\d+)[''′][\s]*(\d+(?:[.,]\d+)?)[\s]*[""″][\s]*([EWOewo])""",
        re.VERBOSE,
    )
    m = dms_pattern.search(testo)
    if m:
        def dms_to_dd(gradi, minuti, secondi, emisfero):
            dd = float(gradi) + float(minuti) / 60 + float(secondi.replace(",", ".")) / 3600
            if emisfero.upper() in ("S", "W", "O"):
                dd = -dd
            return dd
        lat = dms_to_dd(m.group(1), m.group(2), m.group(3), m.group(4))
        lon = dms_to_dd(m.group(5), m.group(6), m.group(7), m.group(8))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon

    # --- Formato europeo: punto e virgola come separatore ---
    if ";" in testo:
        parti = testo.split(";")
        if len(parti) == 2:
            try:
                lat = float(parti[0].strip().replace(",", "."))
                lon = float(parti[1].strip().replace(",", "."))
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
            except ValueError:
                pass
        return None

    # --- Formato decimale con punto: split su virgola o spazio ---
    parti = re.split(r"[,\s]+", testo)
    if len(parti) == 2:
        try:
            lat = float(parti[0])
            lon = float(parti[1])
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except ValueError:
            pass
    return None


def reverse_geocodifica(lat: float, lon: float):
    """
    Trova il nome della località più vicina alle coordinate date,
    usando Nominatim (OpenStreetMap) — gratuito, senza API key.
    Restituisce (nome, regione) oppure (None, None) se non trovato.
    Nominatim richiede uno User-Agent non vuoto per policy di utilizzo.
    """
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "zoom": 10,          # livello "città/paese" (non strada)
            "addressdetails": 1,
        }
        headers = {"User-Agent": "IndiceF ungaiolo/1.0"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        addr = data.get("address", {})
        # Prova a ricavare il nome più specifico disponibile a livello "paese/comune"
        nome = (
            addr.get("village")
            or addr.get("town")
            or addr.get("municipality")
            or addr.get("city")
            or addr.get("county")
            or data.get("name")
            or data.get("display_name", "").split(",")[0]
        )
        regione = addr.get("state", "")
        return nome, regione
    except Exception:
        return None, None


def risolvi_luogo(testo: str):
    """
    Risolve l'input utente in un dizionario geo.
    Prova prima il parsing come coordinate; se non funziona, usa il geocoder.
    Se le coordinate sono riconosciute, tenta il reverse geocoding per trovare
    il nome della località più vicina (utile per lo storico e per il contesto).
    """
    coordinate = parse_coordinate(testo)
    if coordinate:
        lat, lon = coordinate
        nome_vicino, regione_vicina = reverse_geocodifica(lat, lon)
        # Se il reverse geocoding trova un nome, lo usa come etichetta
        # ma indica che proviene da coordinate (così l'utente sa cosa ha cercato)
        if nome_vicino:
            nome = f"{nome_vicino} ({lat:.4f}, {lon:.4f})"
            regione = regione_vicina or ""
        else:
            nome = f"{lat:.5f}, {lon:.5f}"
            regione = ""
        return {
            "lat": lat,
            "lon": lon,
            "nome": nome,
            "regione": regione,
            "elevazione_luogo": None,
            "da_coordinate": True,
        }
    geo = geocodifica(testo)
    if geo:
        geo["da_coordinate"] = False
    return geo
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
    "russule": {
        "label": "Russule",
        "latino": "Russula spp.",
        "colore": "#A13D3D",
        # Pioggia: minimo 20mm (fonte: "almeno 20mm"), ideale 40-80mm ben distribuiti
        "finestra_cumulo_giorni": 7,
        "pioggia_min_significativa": 20,
        "pioggia_ideale_min": 40,
        "pioggia_ideale_max": 80,
        # Temperatura: ideale 15-22°C, accettabile fino a 25°C max, min notturna 12°C
        "temp_ideale_min": 15,
        "temp_ideale_max": 22,
        "temp_accett_min": 10,
        "temp_accett_max": 25,
        "temp_min_notturna_limite": 12,
        # Tempi: comparsa rapida (3-6gg), buttata principale 7-12gg
        "giorni_attesa_min": 3,
        "giorni_attesa_max": 12,
        "giorni_raccolta": 5,
        "peso_pioggia": 0.35,
        "peso_temperatura": 0.30,
        "peso_finestra": 0.35,
    },
}

SOGLIA_GIORNO_PIOVOSO = 5.0

# ----------------------------------------------------------------------------
# MODELLO STATISTICO PORCINI (pioggia residua + temperatura mediana)
# ----------------------------------------------------------------------------
# Modello a due parametri: la fruttificazione avviene quando pioggia residua
# e temperatura mediana dell'aria si trovano entrambe in un "range di
# produzione" per un numero di giorni consecutivi sufficiente a completare
# la riproduzione del micelio (8-14 giorni secondo la tabella).
#
# Pioggia residua = 100% pioggia ultimi 10gg + 50% pioggia dei 10gg precedenti
# (decadimento a gradino, approssimazione di un decadimento più graduale).
#
# Temperatura mediana = media delle temperature medie giornaliere dell'aria
# nell'ultima settimana (si avvicina alla temperatura del terreno, più
# rilevante per il micelio della temperatura dell'aria del solo giorno).
#
# Le due "famiglie" di porcini hanno range leggermente diversi:
# - Edulis/Pinophilus: i porcini "di bosco" classici (faggio, abete, castagno)
# - Aereus/Aestivalis: i porcini estivi (quercia, castagno, clima più caldo)

# Tabella: pioggia_residua_mm -> (temp_min, temp_ottimale, temp_max) in °C
TABELLA_EDULIS_PINOPHILUS = [
    (55, 10, 11, 12),
    (70, 10, 13, 15),
    (80, 11, 14, 16),
    (100, 11, 15, 17),
    (120, 12, 16, 18),
    (140, 13, 17, 18),
    (160, 14, 18, 19),
    (190, 15, 19, 19),
]

TABELLA_AEREUS_AESTIVALIS = [
    (55, 14, 15, 16),
    (70, 14, 16, 17),
    (80, 14, 17, 18),
    (100, 15, 18, 19),
    (120, 16, 19, 20),
    (140, 17, 20, 21),
    (160, 19, 21, 22),
    (190, 21, 22, 23),
]

# Tabella: pioggia_residua_mm -> (giorni con temp.min, giorni con temp.ottimale, giorni con temp.max)
# Giorni necessari per completare la riproduzione del micelio.
TABELLA_GIORNI_RIPRODUZIONE = [
    (55, 10, 8, 8),
    (70, 10, 9, 8),
    (80, 11, 9, 8),
    (100, 12, 10, 9),
    (120, 12, 10, 10),
    (140, 13, 11, 11),
    (160, 14, 12, 12),
]

PIOGGIA_RESIDUA_MIN_TABELLA = 55
PIOGGIA_RESIDUA_MAX_TABELLA = 190


def _interpola_tabella(tabella, pioggia_residua):
    """
    Interpola linearmente nella tabella (pioggia -> min, ottimale, max) per
    un valore di pioggia residua qualsiasi. Sotto/sopra i limiti della tabella,
    usa il valore estremo (clamp) anziché extrapolare.
    """
    p = max(PIOGGIA_RESIDUA_MIN_TABELLA, min(PIOGGIA_RESIDUA_MAX_TABELLA, pioggia_residua))
    for i in range(len(tabella) - 1):
        p_a, min_a, ott_a, max_a = tabella[i]
        p_b, min_b, ott_b, max_b = tabella[i + 1]
        if p_a <= p <= p_b:
            frazione = 0 if p_b == p_a else (p - p_a) / (p_b - p_a)
            t_min = min_a + frazione * (min_b - min_a)
            t_ott = ott_a + frazione * (ott_b - ott_a)
            t_max = max_a + frazione * (max_b - max_a)
            return t_min, t_ott, t_max
    # fuori range gestito dal clamp sopra, qui solo per sicurezza
    _, t_min, t_ott, t_max = tabella[0] if p <= tabella[0][0] else tabella[-1]
    return t_min, t_ott, t_max


def _giorni_riproduzione_necessari(pioggia_residua, temp_mediana, t_min, t_ott, t_max):
    """
    Stima i giorni di riproduzione del micelio necessari, interpolando la
    tabella giorni in base a quanto la temperatura mediana è vicina al
    minimo, ottimale o massimo del range corrente.
    """
    p = max(PIOGGIA_RESIDUA_MIN_TABELLA, min(160, pioggia_residua))
    riga = None
    for i in range(len(TABELLA_GIORNI_RIPRODUZIONE) - 1):
        p_a, *_ = TABELLA_GIORNI_RIPRODUZIONE[i]
        p_b, *_ = TABELLA_GIORNI_RIPRODUZIONE[i + 1]
        if p_a <= p <= p_b:
            riga = TABELLA_GIORNI_RIPRODUZIONE[i]
            break
    if riga is None:
        riga = TABELLA_GIORNI_RIPRODUZIONE[-1] if p > 160 else TABELLA_GIORNI_RIPRODUZIONE[0]
    _, gg_min, gg_ott, gg_max = riga

    if temp_mediana <= t_min:
        return gg_min
    elif temp_mediana >= t_max:
        return gg_max
    elif temp_mediana <= t_ott:
        frazione = (temp_mediana - t_min) / (t_ott - t_min) if t_ott != t_min else 0
        return gg_min + frazione * (gg_ott - gg_min)
    else:
        frazione = (temp_mediana - t_ott) / (t_max - t_ott) if t_max != t_ott else 0
        return gg_ott + frazione * (gg_max - gg_ott)


def pioggia_residua_giorno(date_ordinate, dati, idx):
    """Pioggia residua: 100% ultimi 10gg + 50% dei 10gg precedenti (11°-20° giorno prima).
    Approssimazione semplificata di Zoffoli."""
    idx_inizio_recenti = max(0, idx - 9)
    recenti = sum(dati[date_ordinate[i]]["pioggia_mm"] for i in range(idx_inizio_recenti, idx + 1))

    idx_fine_precedenti = idx_inizio_recenti - 1
    idx_inizio_precedenti = max(0, idx_fine_precedenti - 9)
    if idx_fine_precedenti >= 0:
        precedenti = sum(dati[date_ordinate[i]]["pioggia_mm"] for i in range(idx_inizio_precedenti, idx_fine_precedenti + 1))
    else:
        precedenti = 0.0

    return recenti + 0.5 * precedenti


def pioggia_residua_progressiva(date_ordinate, dati, idx):
    """
    Pioggia residua con decadimento progressivo (formula precisa di Zoffoli):
    - Ultimi 10 giorni: 100%
    - Giorno 11: 90%, giorno 12: 80%, ... giorno 20: 10%
    Ogni giorno oltre il decimo perde un ulteriore 10% rispetto al precedente.
    """
    totale = 0.0
    for offset in range(20):  # da 0 (oggi) a 19 (20 giorni fa)
        idx_giorno = idx - offset
        if idx_giorno < 0:
            break
        pioggia = dati[date_ordinate[idx_giorno]]["pioggia_mm"]
        if offset < 10:
            peso = 1.0  # 100% per i primi 10 giorni
        else:
            peso = max(0.0, 1.0 - (offset - 9) * 0.1)  # 90%, 80%, ... 10%
        totale += pioggia * peso
    return round(totale, 1)


def temperatura_mediana_settimana(date_ordinate, dati, idx):
    """Media delle temperature medie giornaliere dell'aria nell'ultima settimana (7gg)."""
    idx_inizio = max(0, idx - 6)
    valori = [dati[date_ordinate[i]]["temp_media"] for i in range(idx_inizio, idx + 1)
              if dati[date_ordinate[i]]["temp_media"] is not None]
    return sum(valori) / len(valori) if valori else 0


def temperatura_suolo_media_settimana(date_ordinate, dati, idx):
    """
    Media delle temperature del suolo (media tra 0-7cm e 7-28cm) degli ultimi 7 giorni.
    Restituisce (temp_superficiale, temp_profonda, temp_media_suolo) oppure None se
    i dati non sono disponibili.
    """
    idx_inizio = max(0, idx - 6)
    sup_valori = []
    prof_valori = []
    for i in range(idx_inizio, idx + 1):
        s = dati[date_ordinate[i]].get("temp_suolo_superficiale")
        p = dati[date_ordinate[i]].get("temp_suolo_profonda")
        if s is not None:
            sup_valori.append(s)
        if p is not None:
            prof_valori.append(p)
    if not sup_valori and not prof_valori:
        return None, None, None
    t_sup = round(sum(sup_valori) / len(sup_valori), 1) if sup_valori else None
    t_prof = round(sum(prof_valori) / len(prof_valori), 1) if prof_valori else None
    # Media tra i due strati come stima della temperatura del suolo rilevante per il micelio
    valori_medi = [v for v in [t_sup, t_prof] if v is not None]
    t_media = round(sum(valori_medi) / len(valori_medi), 1) if valori_medi else None
    return t_sup, t_prof, t_media


def umidita_e_vento_settimana(date_ordinate, dati, idx):
    """
    Calcola umidità media relativa e vento massimo degli ultimi 7 giorni.
    Restituisce (umidita_media_%, vento_max_kmh, commento_umidita, commento_vento).
    """
    idx_inizio = max(0, idx - 6)
    um_valori = []
    vento_valori = []
    for i in range(idx_inizio, idx + 1):
        u = dati[date_ordinate[i]].get("umidita_media")
        v = dati[date_ordinate[i]].get("vento_max")
        if u is not None:
            um_valori.append(u)
        if v is not None:
            vento_valori.append(v)

    umidita = round(sum(um_valori) / len(um_valori), 1) if um_valori else None
    vento = round(max(vento_valori), 1) if vento_valori else None

    # Commento umidità
    if umidita is None:
        commento_u = "n/d"
    elif umidita >= 80:
        commento_u = "🟢 Ottima (≥80%)"
    elif umidita >= 65:
        commento_u = "🟡 Buona (65-80%)"
    elif umidita >= 50:
        commento_u = "🟠 Discreta (50-65%)"
    else:
        commento_u = "🔴 Bassa (<50%) — suolo tende ad asciugarsi"

    # Commento vento
    if vento is None:
        commento_v = "n/d"
    elif vento <= 20:
        commento_v = "🟢 Calmo (≤20 km/h)"
    elif vento <= 40:
        commento_v = "🟡 Moderato (20-40 km/h)"
    elif vento <= 60:
        commento_v = "🟠 Forte (40-60 km/h) — asciuga il suolo"
    else:
        commento_v = "🔴 Molto forte (>60 km/h) — penalizzante"

    return umidita, vento, commento_u, commento_v


def calcola_stato_porcini(dati, tabella_temperature):
    """
    Calcola, per ogni giorno, pioggia residua, temperatura mediana, range
    corrente (min/ottimale/max), se il giorno è "in range", e il conteggio
    progressivo dei giorni di riproduzione del micelio (con la regola delle
    interruzioni: una pausa di 1-3 giorni fuori range non azzera il conteggio,
    oltre 3 giorni consecutivi fuori range il conteggio riparte da zero).

    Include anche il calcolo dello shock termico (Zoffoli: sbalzo di 8/15°C
    associato a pioggia significativa come innesco della buttata).
    Lo shock termico genera un bonus al punteggio finale (0-15 punti).
    """
    date_ordinate = sorted(dati.keys())
    righe = []
    giorni_consecutivi_in_range = 0
    giorni_consecutivi_fuori_range = 0

    FINESTRA_SHOCK = 5        # giorni su cui calcolare lo sbalzo termico
    SHOCK_MINIMO = 8.0        # °C sotto cui nessun bonus
    SHOCK_OTTIMALE = 15.0     # °C sopra cui bonus massimo
    BONUS_MAX_SHOCK = 15.0    # punti bonus massimi

    for idx, data_str in enumerate(date_ordinate):
        p_residua = pioggia_residua_giorno(date_ordinate, dati, idx)
        p_residua_prog = pioggia_residua_progressiva(date_ordinate, dati, idx)
        t_mediana = temperatura_mediana_settimana(date_ordinate, dati, idx)
        t_min, t_ott, t_max = _interpola_tabella(tabella_temperature, p_residua)

        in_range_pioggia = p_residua >= PIOGGIA_RESIDUA_MIN_TABELLA
        in_range_temp = t_min <= t_mediana <= t_max
        in_range = in_range_pioggia and in_range_temp

        if in_range:
            giorni_consecutivi_fuori_range = 0
            giorni_consecutivi_in_range += 1
        else:
            giorni_consecutivi_fuori_range += 1
            if giorni_consecutivi_fuori_range > 3:
                giorni_consecutivi_in_range = 0

        giorni_necessari = _giorni_riproduzione_necessari(p_residua, t_mediana, t_min, t_ott, t_max)
        fase_completata = giorni_consecutivi_in_range >= giorni_necessari

        # --- Calcolo shock termico ---
        # Sbalzo = differenza tra temp. massima e minima nei FINESTRA_SHOCK giorni precedenti.
        # Il bonus si applica solo se c'è stata anche pioggia significativa nella stessa finestra
        # (lo shock termico senza pioggia non innesca la buttata secondo Zoffoli).
        idx_inizio = max(0, idx - FINESTRA_SHOCK + 1)
        temp_medie_finestra = [
            dati[date_ordinate[i]]["temp_media"]
            for i in range(idx_inizio, idx + 1)
            if dati[date_ordinate[i]]["temp_media"] is not None
        ]
        pioggia_finestra = sum(
            dati[date_ordinate[i]]["pioggia_mm"]
            for i in range(idx_inizio, idx + 1)
        )
        if len(temp_medie_finestra) >= 2:
            sbalzo = max(temp_medie_finestra) - min(temp_medie_finestra)
        else:
            sbalzo = 0.0

        pioggia_sufficiente = pioggia_finestra >= SOGLIA_GIORNO_PIOVOSO * 2  # almeno 10mm nella finestra

        if sbalzo >= SHOCK_MINIMO and pioggia_sufficiente:
            if sbalzo >= SHOCK_OTTIMALE:
                bonus_shock = BONUS_MAX_SHOCK
            else:
                frazione = (sbalzo - SHOCK_MINIMO) / (SHOCK_OTTIMALE - SHOCK_MINIMO)
                bonus_shock = round(frazione * BONUS_MAX_SHOCK, 1)
        else:
            bonus_shock = 0.0

        # Temperatura del suolo (media ultimi 7gg, se disponibile)
        t_sup, t_prof, t_suolo = temperatura_suolo_media_settimana(date_ordinate, dati, idx)

        righe.append({
            "data": data_str,
            "pioggia_residua": round(p_residua, 1),
            "pioggia_residua_progressiva": p_residua_prog,
            "temp_mediana": round(t_mediana, 1),
            "temp_suolo_superficiale": t_sup,
            "temp_suolo_profonda": t_prof,
            "temp_suolo_media": t_suolo,
            "temp_range_min": round(t_min, 1),
            "temp_range_ott": round(t_ott, 1),
            "temp_range_max": round(t_max, 1),
            "in_range": in_range,
            "giorni_in_range_consecutivi": giorni_consecutivi_in_range,
            "giorni_necessari": round(giorni_necessari, 1),
            "fase_completata": fase_completata,
            "sbalzo_termico": round(sbalzo, 1),
            "bonus_shock": bonus_shock,
            "pioggia_finestra_shock": round(pioggia_finestra, 1),
        })

    return righe


def stato_colore_porcini(riga):
    """
    Traduce lo stato del giorno nei 4 colori del modello originale:
    Rosso: non ci sono le condizioni per la riproduzione
    Giallo: condizioni giuste per la riproduzione (in corso)
    Verde: condizioni giuste per la buttata (fase completata, ancora in range)
    Blu: buttata in esaurimento (fase completata ma appena uscito dal range)
    """
    if riga["fase_completata"] and riga["in_range"]:
        return "Verde", "🟢", "Condizioni per la buttata"
    elif riga["fase_completata"] and not riga["in_range"]:
        return "Blu", "🔵", "Buttata in esaurimento"
    elif riga["in_range"]:
        return "Giallo", "🟡", "In riproduzione, non ancora pronto"
    else:
        return "Rosso", "🔴", "Condizioni non favorevoli"


# ----------------------------------------------------------------------------
# MODELLO STATISTICO GALLETTI (Cantharellus cibarius)
# ----------------------------------------------------------------------------
# Stessa logica dei porcini (pioggia residua + temperatura mediana + giorni
# di riproduzione del micelio), ma con tabelle diverse dal libro di Zoffoli.
# I finferli hanno range di temperatura più basso rispetto ai porcini,
# quindi spesso escono 1-2 giorni prima.

TABELLA_GALLETTI = [
    (55,  9, 10, 15),
    (70,  9, 12, 16),
    (80,  9, 12, 17),
    (100, 11, 12, 18),
    (120, 12, 13, 18),
    (140, 13, 14, 18),
    (160, 16, 18, 19),
]

TABELLA_GIORNI_RIPRODUZIONE_GALLETTI = [
    (55,  9, 12, 10, 8,  15, 7),
    (70,  9, 12, 12, 8,  16, 7),
    (80,  9, 13, 12, 9,  17, 8),
    (100, 11, 13, 12, 10, 18, 9),
    (120, 12, 13, 13, 11, 18, 10),
    (140, 13, 14, 14, 12, 18, 11),
    (160, 16, 15, 18, 13, 19, 12),
]
# Formato: (pioggia, t_min, gg_min, t_ott, gg_ott, t_max, gg_max)

PIOGGIA_GALLETTI_MIN = 55
PIOGGIA_GALLETTI_MAX = 160


def _giorni_riproduzione_finferli(pioggia_residua, temp_mediana, t_min, t_ott, t_max):
    """Giorni di riproduzione micelio finferli, interpolando la tabella dedicata."""
    p = max(PIOGGIA_GALLETTI_MIN, min(PIOGGIA_GALLETTI_MAX, pioggia_residua))
    riga = None
    for i in range(len(TABELLA_GIORNI_RIPRODUZIONE_GALLETTI) - 1):
        p_a = TABELLA_GIORNI_RIPRODUZIONE_GALLETTI[i][0]
        p_b = TABELLA_GIORNI_RIPRODUZIONE_GALLETTI[i + 1][0]
        if p_a <= p <= p_b:
            riga = TABELLA_GIORNI_RIPRODUZIONE_GALLETTI[i]
            break
    if riga is None:
        riga = TABELLA_GIORNI_RIPRODUZIONE_GALLETTI[-1] if p > PIOGGIA_GALLETTI_MAX \
               else TABELLA_GIORNI_RIPRODUZIONE_GALLETTI[0]
    _, _, gg_min, _, gg_ott, _, gg_max = riga

    if temp_mediana <= t_min:
        return gg_min
    elif temp_mediana >= t_max:
        return gg_max
    elif temp_mediana <= t_ott:
        frazione = (temp_mediana - t_min) / (t_ott - t_min) if t_ott != t_min else 0
        return gg_min + frazione * (gg_ott - gg_min)
    else:
        frazione = (temp_mediana - t_ott) / (t_max - t_ott) if t_max != t_ott else 0
        return gg_ott + frazione * (gg_max - gg_ott)


def calcola_stato_finferli(dati):
    """
    Calcola lo stato giornaliero per i finferli secondo il modello statistico
    del libro (tabelle pioggia residua + temperatura mediana + giorni micelio).
    Stessa logica interruzioni dei porcini (pausa ≤3gg non azzera, >3gg azzera).
    """
    date_ordinate = sorted(dati.keys())
    righe = []
    giorni_consecutivi_in_range = 0
    giorni_consecutivi_fuori_range = 0

    for idx, data_str in enumerate(date_ordinate):
        p_residua = pioggia_residua_giorno(date_ordinate, dati, idx)
        t_mediana = temperatura_mediana_settimana(date_ordinate, dati, idx)

        # Interpola la tabella finferli (stessa funzione _interpola_tabella,
        # ma clampata sui limiti della tabella finferli)
        p_clamp = max(PIOGGIA_GALLETTI_MIN, min(PIOGGIA_GALLETTI_MAX, p_residua))
        t_min, t_ott, t_max = _interpola_tabella(TABELLA_GALLETTI, p_clamp)

        in_range_pioggia = p_residua >= PIOGGIA_GALLETTI_MIN
        in_range_temp = t_min <= t_mediana <= t_max
        in_range = in_range_pioggia and in_range_temp

        if in_range:
            giorni_consecutivi_fuori_range = 0
            giorni_consecutivi_in_range += 1
        else:
            giorni_consecutivi_fuori_range += 1
            if giorni_consecutivi_fuori_range > 3:
                giorni_consecutivi_in_range = 0

        giorni_necessari = _giorni_riproduzione_finferli(p_residua, t_mediana, t_min, t_ott, t_max)
        fase_completata = giorni_consecutivi_in_range >= giorni_necessari

        righe.append({
            "data": data_str,
            "pioggia_residua": round(p_residua, 1),
            "temp_mediana": round(t_mediana, 1),
            "temp_range_min": round(t_min, 1),
            "temp_range_ott": round(t_ott, 1),
            "temp_range_max": round(t_max, 1),
            "in_range": in_range,
            "giorni_in_range_consecutivi": giorni_consecutivi_in_range,
            "giorni_necessari": round(giorni_necessari, 1),
            "fase_completata": fase_completata,
        })

    return righe


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
    Include anche temperatura del suolo (0-7cm e 7-28cm) per confronto con la
    temperatura mediana dell'aria calcolata secondo Zoffoli.
    """
    base_params = {
        "latitude": lat,
        "longitude": lon,
        "daily": [
            "precipitation_sum",
            "temperature_2m_max",
            "temperature_2m_min",
            "temperature_2m_mean",
            "windspeed_10m_max",
        ],
        "hourly": [
            "soil_temperature_0_to_7cm",
            "soil_temperature_7_to_28cm",
            "relativehumidity_2m",
        ],
        "timezone": "auto",
        "past_days": 20,
        "forecast_days": 7,
    }
    url = "https://api.open-meteo.com/v1/forecast"

    def estrai(data):
        daily = data["daily"]
        hourly = data.get("hourly", {})

        # Calcola medie giornaliere dai dati orari
        suolo_sup_giorno = {}
        suolo_prof_giorno = {}
        umidita_giorno = {}
        if hourly.get("time"):
            for i, ora_str in enumerate(hourly["time"]):
                giorno = ora_str[:10]
                s = hourly.get("soil_temperature_0_to_7cm", [])[i] if i < len(hourly.get("soil_temperature_0_to_7cm", [])) else None
                p = hourly.get("soil_temperature_7_to_28cm", [])[i] if i < len(hourly.get("soil_temperature_7_to_28cm", [])) else None
                u = hourly.get("relativehumidity_2m", [])[i] if i < len(hourly.get("relativehumidity_2m", [])) else None
                if s is not None:
                    suolo_sup_giorno.setdefault(giorno, []).append(s)
                if p is not None:
                    suolo_prof_giorno.setdefault(giorno, []).append(p)
                if u is not None:
                    umidita_giorno.setdefault(giorno, []).append(u)

        risultato = {}
        for i, data_str in enumerate(daily["time"]):
            sup_valori = suolo_sup_giorno.get(data_str, [])
            prof_valori = suolo_prof_giorno.get(data_str, [])
            um_valori = umidita_giorno.get(data_str, [])
            risultato[data_str] = {
                "pioggia_mm": daily["precipitation_sum"][i] or 0.0,
                "temp_max": daily["temperature_2m_max"][i],
                "temp_min": daily["temperature_2m_min"][i],
                "temp_media": daily["temperature_2m_mean"][i],
                "vento_max": daily.get("windspeed_10m_max", [None] * (i+1))[i],
                "temp_suolo_superficiale": round(sum(sup_valori) / len(sup_valori), 1) if sup_valori else None,
                "temp_suolo_profonda": round(sum(prof_valori) / len(prof_valori), 1) if prof_valori else None,
                "umidita_media": round(sum(um_valori) / len(um_valori), 1) if um_valori else None,
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
# DIARIO USCITE
# ----------------------------------------------------------------------------

DIARIO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diario_uscite.csv")
COLONNE_DIARIO = [
    "data_uscita", "luogo", "quota", "specie_trovate",
    "quantita", "punteggio_app_porcini", "punteggio_app_finferli",
    "punteggio_app_russule", "note", "registrato_il"
]
QUANTITA_OPZIONI = ["Ottima 🟢", "Buona 🟡", "Scarsa 🟠", "Nessuna 🔴"]
SPECIE_OPZIONI = ["Porcini Edulis/Pinophilus", "Porcini Aereus/Aestivalis",
                  "Finferli", "Russule", "Altro"]


def carica_diario():
    if os.path.isfile(DIARIO_PATH):
        try:
            return pd.read_csv(DIARIO_PATH)
        except Exception:
            return pd.DataFrame(columns=COLONNE_DIARIO)
    return pd.DataFrame(columns=COLONNE_DIARIO)


def salva_uscita(riga):
    df = carica_diario()
    nuovo = pd.DataFrame([riga])
    df = pd.concat([df, nuovo], ignore_index=True)
    df.to_csv(DIARIO_PATH, index=False)


def elimina_uscita(idx):
    df = carica_diario()
    df = df.drop(index=idx).reset_index(drop=True)
    df.to_csv(DIARIO_PATH, index=False)


# ----------------------------------------------------------------------------
# INTERFACCIA
# ----------------------------------------------------------------------------

st.markdown(
    """
    <style>
    /* Sfondo generale */
    .stApp { background-color: #2B2418; }

    /* Tutto il testo bianco/chiaro */
    h1, h2, h3, h4, h5, h6 { font-family: Georgia, serif; color: #F0E6D2 !important; }
    p, span, div, label, li, a { color: #F0E6D2 !important; }
    .stMarkdown, .stText, .stCaption { color: #F0E6D2 !important; }
    [data-testid="stMetricLabel"] { color: #B5AD98 !important; }
    [data-testid="stMetricValue"] { color: #F0E6D2 !important; }
    [data-testid="stMetricDelta"] { color: #8BC34A !important; }

    /* Campi di testo */
    .stTextInput input, .stNumberInput input {
        background-color: #3A3326 !important;
        color: #F0E6D2 !important;
        border: 1px solid #6B5D45 !important;
    }
    .stTextInput input::placeholder,
    .stNumberInput input::placeholder { color: #8A7A60 !important; }

    /* Textarea */
    .stTextArea textarea {
        background-color: #3A3326 !important;
        color: #F0E6D2 !important;
        border: 1px solid #6B5D45 !important;
    }
    .stTextArea textarea::placeholder { color: #8A7A60 !important; }

    /* Selectbox */
    .stSelectbox > div > div,
    .stSelectbox > div > div > div {
        background-color: #3A3326 !important;
        color: #F0E6D2 !important;
        border: 1px solid #6B5D45 !important;
    }
    .stSelectbox svg { fill: #F0E6D2 !important; }

    /* Multiselect */
    .stMultiSelect > div > div {
        background-color: #3A3326 !important;
        color: #F0E6D2 !important;
        border: 1px solid #6B5D45 !important;
    }
    .stMultiSelect span { color: #F0E6D2 !important; }
    .stMultiSelect [data-baseweb="tag"] {
        background-color: #5C7A4F !important;
        color: #F0E6D2 !important;
    }
    .stMultiSelect input { color: #F0E6D2 !important; }

    /* Radio buttons */
    .stRadio label { color: #F0E6D2 !important; }
    .stRadio div { color: #F0E6D2 !important; }

    /* Date input */
    .stDateInput input {
        background-color: #3A3326 !important;
        color: #F0E6D2 !important;
        border: 1px solid #6B5D45 !important;
    }

    /* Expander */
    .streamlit-expanderHeader {
        background-color: #3A3326 !important;
        color: #F0E6D2 !important;
        border: 1px solid #6B5D45 !important;
        border-radius: 8px !important;
    }
    .streamlit-expanderContent {
        background-color: #2B2418 !important;
        border: 1px solid #4A4232 !important;
    }

    /* Dropdown menu opzioni */
    [data-baseweb="popover"] { background-color: #3A3326 !important; }
    [data-baseweb="menu"] { background-color: #3A3326 !important; }
    [data-baseweb="option"] { background-color: #3A3326 !important; color: #F0E6D2 !important; }
    [data-baseweb="option"]:hover { background-color: #5C7A4F !important; }
    [data-baseweb="select"] { background-color: #3A3326 !important; }
    [data-baseweb="select"] * { color: #F0E6D2 !important; }
    ul[role="listbox"] { background-color: #3A3326 !important; border: 1px solid #6B5D45 !important; }
    ul[role="listbox"] li { background-color: #3A3326 !important; color: #F0E6D2 !important; }
    ul[role="listbox"] li:hover { background-color: #5C7A4F !important; }
    li[role="option"] { background-color: #3A3326 !important; color: #F0E6D2 !important; }
    li[role="option"]:hover { background-color: #5C7A4F !important; }
    /* Forza sfondo scuro su tutti i contenitori Streamlit */
    [data-testid="stAppViewContainer"] { background-color: #2B2418 !important; }
    [data-testid="stVerticalBlock"] { background-color: transparent !important; }
    .css-1d391kg, .css-fg4pbf { background-color: #3A3326 !important; color: #F0E6D2 !important; }
    /* Overlay dropdown */
    div[role="listbox"] { background-color: #3A3326 !important; }
    div[role="option"] { background-color: #3A3326 !important; color: #F0E6D2 !important; }
    div[role="option"]:hover { background-color: #5C7A4F !important; color: #F0E6D2 !important; }

    /* Progress bar */
    .stProgress > div > div { background-color: #5C7A4F !important; }

    /* Bottoni */
    .stButton > button {
        border: 1px solid #6B5D45 !important;
        color: #F0E6D2 !important;
    }

    /* Info/Warning/Success/Error box */
    .stInfo { background-color: #1A2A3A !important; color: #F0E6D2 !important; }
    .stWarning { background-color: #3A2A10 !important; color: #F0E6D2 !important; }
    .stSuccess { background-color: #1A3A1A !important; color: #F0E6D2 !important; }
    .stError { background-color: #3A1A1A !important; color: #F0E6D2 !important; }

    /* Note diario uscite - testo forzato nero */
    div.nota-uscita, div.nota-uscita p, div.nota-uscita * {
        color: rgb(26,26,26) !important;
    }

    /* Specie card */
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

# Injection JS per stilizzare i dropdown dinamicamente (si aggiornano dopo apertura)
st.markdown("""
<script>
function fixDropdowns() {
    const style = document.createElement('style');
    style.textContent = `
        ul[role="listbox"], div[data-baseweb="menu"], div[data-baseweb="popover"] {
            background-color: #3A3326 !important;
            border: 1px solid #6B5D45 !important;
        }
        li[role="option"], div[role="option"], [data-baseweb="option"] {
            background-color: #3A3326 !important;
            color: #F0E6D2 !important;
        }
        li[role="option"]:hover, div[role="option"]:hover {
            background-color: #5C7A4F !important;
        }
    `;
    if (!document.getElementById('dropdown-fix')) {
        style.id = 'dropdown-fix';
        document.head.appendChild(style);
    }
}
// Applica subito e ogni volta che si apre un dropdown
fixDropdowns();
document.addEventListener('click', () => setTimeout(fixDropdowns, 100));
</script>
""", unsafe_allow_html=True)
st.caption(
    "Incrocia pioggia e temperatura degli ultimi 20 giorni con le previsioni a 7 giorni "
    "per stimare le condizioni di porcini, finferli e russule."
)

luogo_default = st.session_state.pop("_ripeti_luogo", "")
luogo_input = st.text_input(
    "Luogo o coordinate",
    value=luogo_default,
    placeholder="Es. Asiago  oppure  46.023, 11.567",
)

quota_bosco = st.number_input(
    "Quota del tuo bosco (m) — opzionale",
    min_value=0,
    max_value=3000,
    value=0,
    step=50,
    help="Se inserisci la quota reale del bosco, l'app corregge la temperatura "
         "rispetto alla quota della cella meteo (gradiente 0.65°C/100m). "
         "Lascia 0 per usare i dati meteo senza correzione.",
)
quota_bosco = int(quota_bosco) if quota_bosco > 0 else None

cerca = st.button("Cerca", type="primary", use_container_width=True)
cerca_automatica = bool(luogo_default)

if (cerca or cerca_automatica) and luogo_input.strip():
    with st.spinner("Cerco il luogo e scarico i dati meteo…"):
        geo = risolvi_luogo(luogo_input)
        if geo is None:
            st.error(
                f"Nessun luogo trovato per **{luogo_input}**. "
                "Prova con un nome più semplice oppure inserisci le coordinate (es. 46.023, 11.567)."
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

                # Correzione altimetrica della temperatura
                # Se l'utente ha inserito una quota bosco E abbiamo la quota della cella meteo,
                # calcoliamo la differenza e correggiamo tutte le temperature.
                # Gradiente adiabatico standard: -0.65°C ogni 100m di quota.
                # Se il bosco è più in alto della cella → temperatura reale più bassa (correzione negativa)
                # Se il bosco è più in basso della cella → temperatura reale più alta (correzione positiva)
                correzione_temp = 0.0
                dati_corretti = dati  # default: nessuna correzione
                if quota_bosco is not None and elevazione is not None:
                    diff_quota = quota_bosco - elevazione  # positivo = bosco più in alto
                    correzione_temp = -(diff_quota * 0.65 / 100)  # negativo se bosco più in alto
                    # Applica correzione a una copia dei dati
                    dati_corretti = {}
                    for data_str, valori in dati.items():
                        dati_corretti[data_str] = {
                            "pioggia_mm": valori["pioggia_mm"],
                            "temp_max": round(valori["temp_max"] + correzione_temp, 2) if valori["temp_max"] is not None else None,
                            "temp_min": round(valori["temp_min"] + correzione_temp, 2) if valori["temp_min"] is not None else None,
                            "temp_media": round(valori["temp_media"] + correzione_temp, 2) if valori["temp_media"] is not None else None,
                        }

                # Calcola con dati originali (per mostrare il confronto)
                serie_originale = {key: calcola_punteggi_giornalieri(dati, profilo) for key, profilo in PROFILI.items()}
                serie_porcini_edulis_orig = calcola_stato_porcini(dati, TABELLA_EDULIS_PINOPHILUS)
                serie_porcini_aereus_orig = calcola_stato_porcini(dati, TABELLA_AEREUS_AESTIVALIS)
                serie_finferli_orig = calcola_stato_finferli(dati)

                # Calcola con dati corretti per quota
                serie = {key: calcola_punteggi_giornalieri(dati_corretti, profilo) for key, profilo in PROFILI.items()}
                serie_porcini_edulis = calcola_stato_porcini(dati_corretti, TABELLA_EDULIS_PINOPHILUS)
                serie_porcini_aereus = calcola_stato_porcini(dati_corretti, TABELLA_AEREUS_AESTIVALIS)
                serie_finferli = calcola_stato_finferli(dati_corretti)

                st.session_state["risultato"] = {
                    "geo": geo,
                    "oggi_str": oggi_str,
                    "serie": serie,
                    "serie_originale": serie_originale,
                    "serie_porcini_edulis": serie_porcini_edulis,
                    "serie_porcini_aereus": serie_porcini_aereus,
                    "serie_porcini_edulis_orig": serie_porcini_edulis_orig,
                    "serie_porcini_aereus_orig": serie_porcini_aereus_orig,
                    "serie_finferli": serie_finferli,
                    "serie_finferli_orig": serie_finferli_orig,
                    "modello_usato": modello_usato,
                    "elevazione": elevazione,
                    "quota_bosco": quota_bosco,
                    "correzione_temp": round(correzione_temp, 2),
                    "_dati_grezzi": dati,
                }

                punteggi_oggi = {}
                for key, righe in serie.items():
                    riga_oggi = next((r for r in righe if r["data"] == oggi_str), righe[-1])
                    punteggi_oggi[key] = riga_oggi["punteggio"]

                # Per lo storico, traduciamo lo stato porcini in un punteggio 0-100 indicativo
                # (Rosso=10, Giallo=50, Verde=90, Blu=70) così resta confrontabile con gli altri
                mappa_punteggio_stato = {"Rosso": 10, "Giallo": 50, "Verde": 90, "Blu": 70}
                riga_oggi_edulis = next((r for r in serie_porcini_edulis if r["data"] == oggi_str), serie_porcini_edulis[-1])
                colore_edulis, _, _ = stato_colore_porcini(riga_oggi_edulis)
                punteggi_oggi["porcini"] = mappa_punteggio_stato[colore_edulis]

                riga_oggi_finferli = next((r for r in serie_finferli if r["data"] == oggi_str), serie_finferli[-1])
                colore_finferli, _, _ = stato_colore_porcini(riga_oggi_finferli)
                punteggi_oggi["finferli"] = mappa_punteggio_stato[colore_finferli]

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
    quota_bosco = r.get("quota_bosco")
    correzione_temp = r.get("correzione_temp", 0.0)

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
            f"🎯 Modello: **{modello_usato}** — alta risoluzione (~2km), ottimo per zone alpine/Nord Italia."
        )
    else:
        st.caption(
            f"🌍 Modello: **{modello_usato}** — ICON-D2 non disponibile, uso il modello globale generico."
        )

    if elevazione_modello is not None:
        riga_quota = f"⛰️ Altitudine cella meteo: **{elevazione_modello:.0f} m**"
        if quota_bosco is not None:
            diff_quota = quota_bosco - elevazione_modello
            segno = "+" if correzione_temp > 0 else ""
            if correzione_temp != 0:
                st.info(
                    f"{riga_quota} · Quota tuo bosco: **{quota_bosco} m** "
                    f"(differenza {diff_quota:+.0f} m) · "
                    f"Correzione temperatura applicata: **{segno}{correzione_temp:.2f}°C** "
                    f"su tutte le specie"
                )
            else:
                st.caption(f"{riga_quota} · Quota bosco: **{quota_bosco} m** — differenza trascurabile, nessuna correzione applicata")
        elif elevazione_luogo is not None:
            diff = elevazione_modello - elevazione_luogo
            if abs(diff) >= 150:
                st.warning(
                    f"{riga_quota} (luogo cercato ~{elevazione_luogo:.0f} m) — "
                    f"differenza **{diff:+.0f} m**: considera di inserire la quota del tuo bosco per una correzione precisa."
                )
            else:
                st.caption(f"{riga_quota} (differenza {diff:+.0f} m dalla quota del luogo, trascurabile)")
        else:
            st.caption(riga_quota)

    # Mostra confronto temperature se è stata applicata una correzione
    if quota_bosco is not None and correzione_temp != 0 and "serie_porcini_edulis_orig" in r:
        riga_orig = next((x for x in r["serie_porcini_edulis_orig"] if x["data"] == oggi_str), None)
        riga_corr = next((x for x in r["serie_porcini_edulis"] if x["data"] == oggi_str), None)
        if riga_orig and riga_corr:
            with st.expander("🌡️ Confronto temperature: cella meteo vs quota bosco"):
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown(f"**Cella meteo ({elevazione_modello:.0f} m)**")
                    st.write(f"Temp. mediana: {riga_orig['temp_mediana']}°C")
                with col2:
                    st.markdown(f"**Quota bosco ({quota_bosco} m)**")
                    st.write(f"Temp. mediana corretta: {riga_corr['temp_mediana']}°C")
                st.caption(
                    f"Correzione applicata: {'+' if correzione_temp > 0 else ''}{correzione_temp:.2f}°C "
                    f"(gradiente 0.65°C/100m × {quota_bosco - elevazione_modello:+.0f} m)"
                )

    # --- Card Porcini: modello statistico dedicato (pioggia residua + temperatura mediana) ---
    serie_porcini_edulis = r.get("serie_porcini_edulis", [])
    serie_porcini_aereus = r.get("serie_porcini_aereus", [])

    if serie_porcini_edulis:
        riga_oggi_edulis = next((x for x in serie_porcini_edulis if x["data"] == oggi_str), serie_porcini_edulis[-1])
        riga_oggi_aereus = next((x for x in serie_porcini_aereus if x["data"] == oggi_str), serie_porcini_aereus[-1])
        colore_edulis, emoji_edulis, testo_edulis = stato_colore_porcini(riga_oggi_edulis)
        colore_aereus, emoji_aereus, testo_aereus = stato_colore_porcini(riga_oggi_aereus)

        with st.container():
            st.markdown(f"<div class='specie-card'>", unsafe_allow_html=True)
            st.markdown(
                "<h3 style='margin-bottom:0'>Porcini</h3>"
                "<p style='font-style:italic; color:#8A8270; font-size:12px; margin-top:-6px'>"
                "Modello statistico: pioggia residua + temperatura mediana</p>",
                unsafe_allow_html=True,
            )
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Edulis / Pinophilus** *(porcini di bosco)*")
                st.markdown(f"### {emoji_edulis} {colore_edulis}")
                st.caption(testo_edulis)
            with col2:
                st.markdown(f"**Aereus / Aestivalis** *(porcini estivi)*")
                st.markdown(f"### {emoji_aereus} {colore_aereus}")
                st.caption(testo_aereus)

            st.write(f"**Pioggia residua:** {riga_oggi_edulis['pioggia_residua']} mm")
            p_prog = riga_oggi_edulis.get("pioggia_residua_progressiva")
            if p_prog is not None:
                diff_p = round(p_prog - riga_oggi_edulis["pioggia_residua"], 1)
                segno = "+" if diff_p > 0 else ""
                st.caption(
                    f"Formula progressiva (decadimento 10%/giorno oltre 10°): **{p_prog} mm** "
                    f"({segno}{diff_p} mm rispetto alla formula semplificata)"
                )
            st.write(f"**Temperatura mediana aria (Zoffoli, ultimi 7gg):** {riga_oggi_edulis['temp_mediana']}°C")

            # Temperatura del suolo (confronto con mediana aria)
            t_sup = riga_oggi_edulis.get("temp_suolo_superficiale")
            t_prof = riga_oggi_edulis.get("temp_suolo_profonda")
            t_suolo = riga_oggi_edulis.get("temp_suolo_media")
            if t_suolo is not None:
                diff = round(t_suolo - riga_oggi_edulis["temp_mediana"], 1)
                segno = "+" if diff > 0 else ""
                st.write(
                    f"**Temperatura del suolo (Open-Meteo):** "
                    f"{t_sup}°C (0-7cm) · {t_prof}°C (7-28cm) · "
                    f"media {t_suolo}°C "
                    f"({'più calda' if diff > 0 else 'più fredda'} di {abs(diff)}°C rispetto alla mediana aria)"
                )
            else:
                st.caption("Temperatura del suolo: non disponibile per questo punto")
            st.write(
                f"**Range attuale Edulis/Pinophilus:** {riga_oggi_edulis['temp_range_min']}–"
                f"{riga_oggi_edulis['temp_range_max']}°C (ottimale {riga_oggi_edulis['temp_range_ott']}°C)"
            )
            st.write(
                f"**Giorni in range consecutivi:** {riga_oggi_edulis['giorni_in_range_consecutivi']} "
                f"su {riga_oggi_edulis['giorni_necessari']:.0f} necessari per completare la riproduzione del micelio"
            )

            # --- Shock termico ---
            sbalzo = riga_oggi_edulis.get("sbalzo_termico", 0)
            bonus = riga_oggi_edulis.get("bonus_shock", 0)
            pioggia_shock = riga_oggi_edulis.get("pioggia_finestra_shock", 0)
            if sbalzo >= 8 and pioggia_shock >= 10:
                st.success(
                    f"⚡ **Shock termico rilevato:** sbalzo di **{sbalzo}°C** negli ultimi 5 giorni "
                    f"con {pioggia_shock} mm di pioggia → **bonus +{bonus} punti** al punteggio"
                )
            elif sbalzo >= 5:
                st.info(
                    f"⚡ Sbalzo termico parziale: {sbalzo}°C negli ultimi 5 giorni "
                    f"({'pioggia sufficiente' if pioggia_shock >= 10 else f'pioggia insufficiente ({pioggia_shock} mm)'}) "
                    f"→ nessun bonus"
                )
            else:
                st.caption(f"⚡ Shock termico: sbalzo {sbalzo}°C negli ultimi 5 giorni — sotto la soglia minima (8°C)")

            with st.expander("Diario di campo Porcini (28 giorni)"):
                df_porcini = pd.DataFrame([
                    {"data": x["data"], "pioggia_residua": x["pioggia_residua"], "temp_mediana": x["temp_mediana"]}
                    for x in serie_porcini_edulis
                ])
                df_porcini["data"] = pd.to_datetime(df_porcini["data"])
                df_porcini = df_porcini.set_index("data")
                st.line_chart(df_porcini, use_container_width=True)
                st.caption(
                    "Linea pioggia residua (mm) e temperatura mediana (°C) — confronta con i range "
                    "indicati sopra per capire l'andamento delle ultime settimane."
                )

            st.caption(
                "🔴 Non favorevole · 🟡 In riproduzione (attendere) · 🟢 Condizioni per la buttata · "
                "🔵 Buttata in esaurimento"
            )
            st.markdown("</div>", unsafe_allow_html=True)

    # --- Blocco microclima (umidità e vento) — valido per tutte le specie ---
    if "serie_porcini_edulis" in r and r["serie_porcini_edulis"]:
        dati_grezzi = r.get("_dati_grezzi", {})
        if dati_grezzi:
            date_ord = sorted(dati_grezzi.keys())
            idx_oggi = next((i for i, d in enumerate(date_ord) if d == oggi_str), len(date_ord) - 1)
            umidita, vento, commento_u, commento_v = umidita_e_vento_settimana(date_ord, dati_grezzi, idx_oggi)

            st.markdown("---")
            st.markdown("**🌤️ Condizioni microclima (ultimi 7 giorni)**")
            col_u, col_v = st.columns(2)
            with col_u:
                st.metric("💧 Umidità relativa media", f"{umidita}%" if umidita else "n/d")
                st.caption(commento_u)
            with col_v:
                st.metric("💨 Vento massimo", f"{vento} km/h" if vento else "n/d")
                st.caption(commento_v)
            st.markdown("---")

    # --- Card Finferli: modello statistico dedicato ---
    serie_finferli = r.get("serie_finferli", [])
    if serie_finferli:
        riga_oggi_g = next((x for x in serie_finferli if x["data"] == oggi_str), serie_finferli[-1])
        colore_g, emoji_g, testo_g = stato_colore_porcini(riga_oggi_g)

        with st.container():
            st.markdown("<div class='specie-card'>", unsafe_allow_html=True)
            st.markdown(
                "<h3 style='margin-bottom:0'>Finferli</h3>"
                "<p style='font-style:italic; color:#8A8270; font-size:12px; margin-top:-6px'>"
                "Cantharellus cibarius — modello statistico Zoffoli</p>",
                unsafe_allow_html=True,
            )
            col1, col2 = st.columns([1, 2])
            with col1:
                st.markdown(f"### {emoji_g} {colore_g}")
                st.caption(testo_g)
            with col2:
                st.write(f"**Pioggia residua:** {riga_oggi_g['pioggia_residua']} mm")
                st.write(f"**Temperatura mediana (ultima settimana):** {riga_oggi_g['temp_mediana']}°C")
                st.write(
                    f"**Range attuale:** {riga_oggi_g['temp_range_min']}–"
                    f"{riga_oggi_g['temp_range_max']}°C "
                    f"(ottimale {riga_oggi_g['temp_range_ott']}°C)"
                )
                st.write(
                    f"**Giorni in range consecutivi:** {riga_oggi_g['giorni_in_range_consecutivi']} "
                    f"su {riga_oggi_g['giorni_necessari']:.0f} necessari"
                )

            with st.expander("Diario di campo Finferli (28 giorni)"):
                df_g = pd.DataFrame([
                    {"data": x["data"], "pioggia_residua": x["pioggia_residua"],
                     "temp_mediana": x["temp_mediana"]}
                    for x in serie_finferli
                ])
                df_g["data"] = pd.to_datetime(df_g["data"])
                df_g = df_g.set_index("data")
                st.line_chart(df_g, use_container_width=True)
                st.caption("Pioggia residua (mm) e temperatura mediana (°C) — confronta con il range indicato sopra.")

            st.caption(
                "🔴 Non favorevole · 🟡 In riproduzione · 🟢 Condizioni per la buttata · 🔵 In esaurimento"
            )
            st.markdown("</div>", unsafe_allow_html=True)

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

# --- Fascia altimetrica ottimale ---
if "risultato" in st.session_state:
    r = st.session_state["risultato"]
    elevazione_modello = r.get("elevazione")
    oggi_str = r["oggi_str"]

    st.markdown("---")
    st.subheader("⛰️ Fascia altimetrica ottimale")
    st.caption("Calcola le condizioni per ogni quota nella fascia che ti interessa, "
               "correggendo la temperatura con il gradiente altimetrico (0.65°C/100m).")

    col_min, col_max, col_step = st.columns(3)
    with col_min:
        quota_min = st.number_input("Quota minima (m)", min_value=0, max_value=2900,
                                     value=600, step=100)
    with col_max:
        quota_max = st.number_input("Quota massima (m)", min_value=100, max_value=3000,
                                     value=1600, step=100)
    with col_step:
        passo = st.selectbox("Passo (m)", [50, 100, 200], index=1)

    if st.button("Calcola fascia altimetrica", type="secondary", use_container_width=True):
        if quota_min >= quota_max:
            st.error("La quota minima deve essere inferiore alla quota massima.")
        elif elevazione_modello is None:
            st.warning("Quota della cella meteo non disponibile — impossibile calcolare la correzione altimetrica.")
        else:
            dati = st.session_state["risultato"].get("_dati_grezzi")
            if dati is None:
                st.info("Premi prima 'Cerca' con un luogo per caricare i dati meteo, poi calcola la fascia.")
            else:
                quote = list(range(int(quota_min), int(quota_max) + 1, int(passo)))

                # Mappa colore → valore numerico per ordinamento visivo
                COLORE_VALORE = {"Verde": 3, "Blu": 2, "Giallo": 1, "Rosso": 0}
                COLORE_HEX = {"Verde": "#4CAF50", "Blu": "#2196F3",
                              "Giallo": "#FFC107", "Rosso": "#F44336"}
                PUNTEGGIO_LABEL = {3: "🟢 Buttata", 2: "🔵 In esaurimento",
                                   1: "🟡 In riproduzione", 0: "🔴 Non favorevole"}

                righe_fascia = []
                for quota in quote:
                    diff = quota - elevazione_modello
                    corr = -(diff * 0.65 / 100)
                    dati_q = {}
                    for data_str, valori in dati.items():
                        dati_q[data_str] = {
                            "pioggia_mm": valori["pioggia_mm"],
                            "temp_max": round(valori["temp_max"] + corr, 2) if valori["temp_max"] is not None else None,
                            "temp_min": round(valori["temp_min"] + corr, 2) if valori["temp_min"] is not None else None,
                            "temp_media": round(valori["temp_media"] + corr, 2) if valori["temp_media"] is not None else None,
                        }

                    # Porcini Edulis
                    righe_e = calcola_stato_porcini(dati_q, TABELLA_EDULIS_PINOPHILUS)
                    oggi_e = next((x for x in righe_e if x["data"] == oggi_str), righe_e[-1])
                    col_e, _, _ = stato_colore_porcini(oggi_e)

                    # Porcini Aereus
                    righe_a = calcola_stato_porcini(dati_q, TABELLA_AEREUS_AESTIVALIS)
                    oggi_a = next((x for x in righe_a if x["data"] == oggi_str), righe_a[-1])
                    col_a, _, _ = stato_colore_porcini(oggi_a)

                    # Finferli e Russule
                    stati_generici = {}
                    for key, profilo in PROFILI.items():
                        serie_q = calcola_punteggi_giornalieri(dati_q, profilo)
                        oggi_q = next((x for x in serie_q if x["data"] == oggi_str), serie_q[-1])
                        stati_generici[key] = oggi_q["punteggio"]

                    righe_fascia.append({
                        "quota": quota,
                        "temp_mediana": oggi_e["temp_mediana"],
                        "pioggia_residua": oggi_e["pioggia_residua"],
                        "porcini_edulis": col_e,
                        "porcini_aereus": col_a,
                        "finferli": stati_generici.get("finferli", 0),
                        "russule": stati_generici.get("russule", 0),
                    })

                # Grafico porcini Edulis
                st.markdown("**Porcini Edulis/Pinophilus**")
                for rr in righe_fascia:
                    colore_hex = COLORE_HEX[rr["porcini_edulis"]]
                    valore = COLORE_VALORE[rr["porcini_edulis"]]
                    label = PUNTEGGIO_LABEL[valore]
                    st.markdown(
                        f"<div style='display:flex;align-items:center;gap:10px;margin:3px 0'>"
                        f"<span style='width:60px;text-align:right;font-size:13px'><b>{rr['quota']}m</b></span>"
                        f"<div style='height:22px;width:{max(4, valore/3*100):.0f}%;background:{colore_hex};"
                        f"border-radius:4px;min-width:4px'></div>"
                        f"<span style='font-size:12px;color:#B5AD98'>{label} · {rr['temp_mediana']}°C</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                st.markdown("<br>**Porcini Aereus/Aestivalis**", unsafe_allow_html=True)
                for rr in righe_fascia:
                    colore_hex = COLORE_HEX[rr["porcini_aereus"]]
                    valore = COLORE_VALORE[rr["porcini_aereus"]]
                    label = PUNTEGGIO_LABEL[valore]
                    st.markdown(
                        f"<div style='display:flex;align-items:center;gap:10px;margin:3px 0'>"
                        f"<span style='width:60px;text-align:right;font-size:13px'><b>{rr['quota']}m</b></span>"
                        f"<div style='height:22px;width:{max(4, valore/3*100):.0f}%;background:{colore_hex};"
                        f"border-radius:4px;min-width:4px'></div>"
                        f"<span style='font-size:12px;color:#B5AD98'>{label} · {rr['temp_mediana']}°C</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                for key, profilo in PROFILI.items():
                    st.markdown(f"<br>**{profilo['label']}**", unsafe_allow_html=True)
                    for rr in righe_fascia:
                        punteggio = rr[key]
                        et, _ = etichetta(punteggio)
                        colore_hex = {"Ottimo": "#4CAF50", "Buono": "#8BC34A",
                                      "Possibile": "#FFC107", "Scarso": "#F44336"}.get(et, "#888")
                        st.markdown(
                            f"<div style='display:flex;align-items:center;gap:10px;margin:3px 0'>"
                            f"<span style='width:60px;text-align:right;font-size:13px'><b>{rr['quota']}m</b></span>"
                            f"<div style='height:22px;width:{max(4, punteggio):.0f}%;background:{colore_hex};"
                            f"border-radius:4px;min-width:4px'></div>"
                            f"<span style='font-size:12px;color:#B5AD98'>{et} ({int(punteggio)}/100) · {rr['temp_mediana']}°C</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

# --- Screening zone verdi ---
st.markdown("---")
st.subheader("🗺️ Screening zone verdi — Porcini")
st.caption(
    "Scansiona una griglia di punti su una regione e mostra su mappa le aree "
    "con condizioni favorevoli per i porcini oggi. "
    "Ogni punto = cella ICON-D2 (~15km di distanza). Attesa: 30-60 secondi."
)

REGIONI_NORD_ITALIA = {
    "Veneto":               (45.0, 47.1, 10.6, 12.8),
    "Lombardia":            (44.7, 46.7,  8.5, 11.4),
    "Piemonte":             (44.0, 46.5,  6.6,  9.2),
    "Trentino-Alto Adige":  (45.7, 47.1, 10.4, 12.5),
    "Friuli-Venezia Giulia":(45.6, 46.7, 12.3, 13.9),
    "Valle d'Aosta":        (45.5, 45.9,  6.8,  7.9),
    "Liguria":              (43.8, 44.7,  6.6,  9.9),
    "Emilia-Romagna":       (43.7, 45.1,  9.2, 12.8),
}

COLORI_FOLIUM = {
    "Verde": "green",
    "Blu":   "blue",
    "Giallo":"orange",
    "Rosso": "red",
}

def griglia_punti(lat_min, lat_max, lon_min, lon_max, passo_gradi=0.13):
    """Genera una griglia di punti (lat, lon) nella bounding box con il passo dato.
    0.13 gradi ≈ 14km in latitudine, abbastanza per avere 40-80 punti su una regione."""
    punti = []
    lat = lat_min
    while lat <= lat_max:
        lon = lon_min
        while lon <= lon_max:
            punti.append((round(lat, 4), round(lon, 4)))
            lon += passo_gradi
        lat += passo_gradi
    return punti


@st.cache_data(ttl=1800, show_spinner=False)
def scarica_e_calcola_punto(lat, lon, oggi_str):
    """Scarica i dati meteo per un singolo punto e calcola lo stato porcini Edulis."""
    try:
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,temperature_2m_mean",
            "timezone": "auto",
            "past_days": 20,   # CRITICO: serve la storia per calcolare pioggia residua
            "forecast_days": 1,
            "models": "icon_d2",
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        daily = data["daily"]

        if not daily["time"]:
            return None

        dati = {}
        for i, d in enumerate(daily["time"]):
            # Salta giorni con dati mancanti
            if daily["temperature_2m_mean"][i] is None:
                continue
            dati[d] = {
                "pioggia_mm": daily["precipitation_sum"][i] or 0.0,
                "temp_max":   daily["temperature_2m_max"][i],
                "temp_min":   daily["temperature_2m_min"][i],
                "temp_media": daily["temperature_2m_mean"][i],
            }

        if len(dati) < 10:  # troppo pochi dati storici, risultato inaffidabile
            return None

        elevazione = data.get("elevation", 0)
        righe = calcola_stato_porcini(dati, TABELLA_EDULIS_PINOPHILUS)
        if not righe:
            return None

        # Trova la riga di oggi (o l'ultima disponibile)
        riga = next((x for x in righe if x["data"] == oggi_str), righe[-1])
        colore, _, testo = stato_colore_porcini(riga)
        bonus = riga.get("bonus_shock", 0)

        return {
            "lat": lat, "lon": lon,
            "colore": colore,
            "testo": testo,
            "elevazione": int(elevazione),
            "pioggia_residua": riga["pioggia_residua"],
            "temp_mediana": riga["temp_mediana"],
            "giorni_consec": riga["giorni_in_range_consecutivi"],
            "giorni_necessari": riga["giorni_necessari"],
            "sbalzo": riga.get("sbalzo_termico", 0),
            "bonus_shock": bonus,
        }
    except Exception:
        return None


regione_scelta = None
centro_punto = None

modalita_screening = st.radio(
    "Modalità screening",
    ["🗺️ Regione intera (griglia ~15km)", "📍 Intorno a un punto (griglia fitta)"],
    horizontal=True,
)

if modalita_screening.startswith("🗺️"):
    regione_scelta = st.selectbox(
        "Seleziona regione",
        list(REGIONI_NORD_ITALIA.keys()),
        index=0
    )
else:
    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        centro_input = st.text_input(
            "Luogo o coordinate centro",
            placeholder="Es. Passo Cereda  o  46.23, 11.85",
        )
    with col_p2:
        raggio_km = st.number_input(
            "Raggio (km)", min_value=5, max_value=100, value=30, step=5
        )
    with col_p3:
        passo_km = st.selectbox("Passo griglia (km)", [5, 7, 10, 15], index=1)

col_s1, col_s2 = st.columns(2)
with col_s1:
    quota_min_screen = st.number_input(
        "Quota minima da considerare (m)",
        min_value=0, max_value=2000, value=400, step=100,
        help="Punti con elevazione inferiore vengono esclusi dalla mappa"
    )
with col_s2:
    quota_max_screen = st.number_input(
        "Quota massima da considerare (m)",
        min_value=100, max_value=3000, value=1800, step=100
    )

col_btn1, col_btn2 = st.columns([3, 1])
with col_btn1:
    avvia_screening = st.button(
        "🔍 Avvia screening", type="secondary", use_container_width=True
    )
with col_btn2:
    if st.button("🔄 Svuota cache", use_container_width=True,
                 help="Forza il ricaricamento dei dati meteo (utile se i risultati sembrano fermi)"):
        st.cache_data.clear()
        st.session_state.pop("screening_risultati", None)
        st.success("Cache svuotata — riavvia lo screening.")
        st.rerun()

if avvia_screening:
    oggi_str_screen = datetime.now().strftime("%Y-%m-%d")

    if modalita_screening.startswith("🗺️"):
        # Modalità regione
        lat_min, lat_max, lon_min, lon_max = REGIONI_NORD_ITALIA[regione_scelta]
        punti = griglia_punti(lat_min, lat_max, lon_min, lon_max, passo_gradi=0.13)
        etichetta_screening = regione_scelta
        centro_lat_screen = (lat_min + lat_max) / 2
        centro_lon_screen = (lon_min + lon_max) / 2
        zoom_screen = 8
    else:
        # Modalità intorno a un punto
        if not centro_input.strip():
            st.error("Inserisci un luogo o coordinate per il centro.")
            st.stop()

        geo_centro = risolvi_luogo(centro_input)
        if geo_centro is None:
            st.error(f"Luogo non trovato: '{centro_input}'. Prova con coordinate (es. 46.23, 11.85).")
            st.stop()

        clat, clon = geo_centro["lat"], geo_centro["lon"]
        # Converti raggio km in gradi (approssimazione: 1° lat ≈ 111km, 1° lon ≈ 111km * cos(lat))
        delta_lat = raggio_km / 111.0
        delta_lon = raggio_km / (111.0 * abs(__import__('math').cos(__import__('math').radians(clat))))
        lat_min, lat_max = clat - delta_lat, clat + delta_lat
        lon_min, lon_max = clon - delta_lon, clon + delta_lon
        passo_gradi = passo_km / 111.0

        # Genera griglia e filtra per distanza (cerchio, non rettangolo)
        import math
        punti_rect = griglia_punti(lat_min, lat_max, lon_min, lon_max, passo_gradi=passo_gradi)
        punti = []
        for plat, plon in punti_rect:
            dist = math.sqrt(((plat - clat) * 111) ** 2 + ((plon - clon) * 111 * math.cos(math.radians(clat))) ** 2)
            if dist <= raggio_km:
                punti.append((plat, plon))

        etichetta_screening = f"{geo_centro['nome']} (raggio {raggio_km}km, passo {passo_km}km)"
        centro_lat_screen = clat
        centro_lon_screen = clon
        zoom_screen = 10 if raggio_km <= 20 else 9 if raggio_km <= 40 else 8

    st.info(f"Analisi di {len(punti)} punti — {etichetta_screening}... attendere.")
    barra = st.progress(0)
    risultati = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(scarica_e_calcola_punto, lat, lon, oggi_str_screen): (lat, lon)
            for lat, lon in punti
        }
        completati = 0
        for future in concurrent.futures.as_completed(futures):
            completati += 1
            barra.progress(completati / len(punti))
            res = future.result()
            if res:
                risultati.append(res)

    barra.empty()

    st.session_state["screening_risultati"] = risultati
    st.session_state["screening_centro_lat"] = centro_lat_screen
    st.session_state["screening_centro_lon"] = centro_lon_screen
    st.session_state["screening_zoom"] = zoom_screen
    st.session_state["screening_mappa_key"] = datetime.now().isoformat()
    st.session_state["screening_lat_min"] = lat_min
    st.session_state["screening_lat_max"] = lat_max
    st.session_state["screening_lon_min"] = lon_min
    st.session_state["screening_lon_max"] = lon_max

# Mostra risultati se presenti in session_state
if "screening_risultati" in st.session_state:
    risultati = st.session_state["screening_risultati"]
    lat_min = st.session_state["screening_lat_min"]
    lat_max = st.session_state["screening_lat_max"]
    lon_min = st.session_state["screening_lon_min"]
    lon_max = st.session_state["screening_lon_max"]
    centro_lat_m = st.session_state.get("screening_centro_lat", (lat_min + lat_max) / 2)
    centro_lon_m = st.session_state.get("screening_centro_lon", (lon_min + lon_max) / 2)
    zoom_m = st.session_state.get("screening_zoom", 8)

    risultati_filtrati = [
        r for r in risultati
        if quota_min_screen <= r["elevazione"] <= quota_max_screen
    ]

    if not risultati_filtrati:
        st.warning("Nessun punto nella fascia di quota selezionata. Allarga il range e riavvia.")
    else:
        conteggi = {"Verde": 0, "Blu": 0, "Giallo": 0, "Rosso": 0}
        for r in risultati_filtrati:
            conteggi[r["colore"]] = conteggi.get(r["colore"], 0) + 1

        col_v, col_b, col_g, col_r = st.columns(4)
        col_v.metric("🟢 Buttata", conteggi["Verde"])
        col_b.metric("🔵 Esaurimento", conteggi["Blu"])
        col_g.metric("🟡 In riproduzione", conteggi["Giallo"])
        col_r.metric("🔴 Non favorevole", conteggi["Rosso"])

        # Mappa Folium
        mappa = folium.Map(
            location=[centro_lat_m, centro_lon_m],
            zoom_start=zoom_m,
            tiles="OpenStreetMap"
        )

        for res in risultati_filtrati:
            colore_f = COLORI_FOLIUM.get(res["colore"], "gray")
            popup_html = (
                f"<b>{res['colore']}</b> — {res['testo']}<br>"
                f"Quota: {res['elevazione']} m<br>"
                f"Pioggia residua: {res['pioggia_residua']} mm<br>"
                f"Temp. mediana: {res['temp_mediana']}°C<br>"
                f"Giorni in range: {res['giorni_consec']}/{res['giorni_necessari']:.0f}<br>"
                f"Sbalzo termico: {res['sbalzo']}°C"
                + (f" (+{res['bonus_shock']} pts)" if res['bonus_shock'] > 0 else "")
            )
            folium.CircleMarker(
                location=[res["lat"], res["lon"]],
                radius=10,
                color=colore_f,
                fill=True,
                fill_color=colore_f,
                fill_opacity=0.7,
                popup=folium.Popup(popup_html, max_width=250),
                tooltip=f"{res['colore']} · {res['elevazione']}m",
            ).add_to(mappa)

        st_folium(mappa, width=700, height=500, returned_objects=[],
                  key=st.session_state.get("screening_mappa_key", "mappa_screening"))

        # Lista punti migliori (verdi, blu, gialli)
        migliori = [r for r in risultati_filtrati if r["colore"] in ("Verde", "Blu", "Giallo")]
        migliori.sort(
            key=lambda x: {"Verde": 3, "Blu": 2, "Giallo": 1}.get(x["colore"], 0),
            reverse=True
        )
        if migliori:
            st.markdown("**Punti con condizioni favorevoli o in riproduzione:**")
            for res in migliori[:20]:
                emoji = {"Verde": "🟢", "Blu": "🔵", "Giallo": "🟡"}.get(res["colore"], "⚪")
                mappa_url = (
                    f"https://www.openstreetmap.org/?mlat={res['lat']}"
                    f"&mlon={res['lon']}#map=14/{res['lat']}/{res['lon']}"
                )
                shock_txt = f" · ⚡ sbalzo {res['sbalzo']}°C +{res['bonus_shock']}pt" if res["bonus_shock"] > 0 else ""
                st.markdown(
                    f"{emoji} **{res['colore']}** &nbsp;|&nbsp; "
                    f"[📍 {res['lat']:.4f}, {res['lon']:.4f}]({mappa_url}) &nbsp;|&nbsp; "
                    f"⛰️ {res['elevazione']} m &nbsp;|&nbsp; "
                    f"🌧️ {res['pioggia_residua']} mm &nbsp;|&nbsp; "
                    f"🌡️ {res['temp_mediana']}°C"
                    f"{shock_txt}",
                    unsafe_allow_html=True,
                )


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
        ETICHETTE_SPECIE = {**{k: v["label"] for k, v in PROFILI.items()}, "porcini": "Porcini", "finferli": "Finferli"}
        riassunto = "  ·  ".join(
            f"{ETICHETTE_SPECIE.get(r['specie'], r['specie'])}: {int(r['punteggio'])}"
            for _, r in punteggi_luogo.iterrows()
        )
        col_a, col_b = st.columns([3, 1])
        with col_a:
            st.write(f"**{row['luogo']}**{', ' + row['regione'] if pd.notna(row['regione']) and row['regione'] else ''}")
            st.caption(riassunto)
        with col_b:
            if st.button("Ripeti", key=f"ripeti_{row['luogo']}"):
                st.session_state["_ripeti_luogo"] = row["luogo"]
                st.rerun()

# --- Diario Uscite ---
st.markdown("---")
st.subheader("📓 Diario Uscite")
st.caption("Registra ogni uscita con quello che hai trovato — confrontato con i punteggi dell'app, "
           "ti aiuta ad affinare i parametri nel tempo.")

# Form inserimento nuova uscita
with st.expander("➕ Aggiungi nuova uscita", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        data_uscita = st.date_input("Data dell'uscita", value=datetime.now().date())
        luogo_uscita = st.text_input("Luogo", placeholder="Es. Asiago, Bosco di Schio…")
        quota_uscita = st.number_input("Quota (m)", min_value=0, max_value=3000, value=0, step=50)
    with col2:
        specie_trovate = st.multiselect("Specie trovate", SPECIE_OPZIONI)
        quantita = st.selectbox("Quantità trovata", QUANTITA_OPZIONI)
        note = st.text_area("Note libere", placeholder="Tipo di bosco, esposizione, condizioni terreno, osservazioni…", height=100)

    # Punteggi app del giorno (presi dallo storico se disponibili)
    storico_df_full = carica_storico()
    data_str_uscita = data_uscita.strftime("%Y-%m-%d")
    luogo_norm = luogo_uscita.strip()

    def punteggio_storico(specie_key):
        if storico_df_full.empty or not luogo_norm:
            return None
        mask = ((storico_df_full["data"] == data_str_uscita) &
                (storico_df_full["luogo"].str.lower().str.contains(luogo_norm.lower(), na=False)) &
                (storico_df_full["specie"] == specie_key))
        righe = storico_df_full[mask]
        return int(righe["punteggio"].iloc[0]) if not righe.empty else None

    p_porcini = punteggio_storico("porcini")
    p_finferli = punteggio_storico("finferli")
    p_russule = punteggio_storico("russule")

    if any([p_porcini, p_finferli, p_russule]):
        st.caption(
            f"Punteggi app per quel giorno: "
            f"Porcini {p_porcini if p_porcini else '—'} · "
            f"Finferli {p_finferli if p_finferli else '—'} · "
            f"Russule {p_russule if p_russule else '—'}"
        )
    else:
        st.caption("Cerca prima il luogo nell'app per associare automaticamente i punteggi del giorno.")

    if st.button("💾 Salva uscita", type="primary"):
        if not luogo_uscita.strip():
            st.error("Inserisci almeno il luogo.")
        else:
            salva_uscita({
                "data_uscita": data_str_uscita,
                "luogo": luogo_uscita.strip(),
                "quota": quota_uscita if quota_uscita > 0 else "",
                "specie_trovate": ", ".join(specie_trovate) if specie_trovate else "",
                "quantita": quantita,
                "punteggio_app_porcini": p_porcini or "",
                "punteggio_app_finferli": p_finferli or "",
                "punteggio_app_russule": p_russule or "",
                "note": note.strip(),
                "registrato_il": datetime.now().isoformat(timespec="seconds"),
            })
            st.success("Uscita salvata!")
            st.rerun()

# Tabella uscite passate
diario_df = carica_diario()
if not diario_df.empty:
    st.markdown("**Uscite registrate:**")
    df_display = diario_df.sort_values("data_uscita", ascending=False).copy()
    for idx, row in df_display.iterrows():
        with st.container():
            col_a, col_b, col_c = st.columns([2, 3, 1])
            with col_a:
                st.markdown(f"**{row['data_uscita']}**")
                quota_txt = f" · {int(row['quota'])}m" if pd.notna(row['quota']) and str(row['quota']).strip() not in ("", "0") else ""
                st.caption(f"{row['luogo']}{quota_txt}")
            with col_b:
                specie_txt = row['specie_trovate'] if pd.notna(row['specie_trovate']) and row['specie_trovate'] else "—"
                st.write(f"{row['quantita']} · {specie_txt}")
                punteggi_txt = " · ".join([
                    f"P:{row['punteggio_app_porcini']}" if pd.notna(row.get('punteggio_app_porcini')) and str(row.get('punteggio_app_porcini')).strip() else "",
                    f"F:{row['punteggio_app_finferli']}" if pd.notna(row.get('punteggio_app_finferli')) and str(row.get('punteggio_app_finferli')).strip() else "",
                    f"R:{row['punteggio_app_russule']}" if pd.notna(row.get('punteggio_app_russule')) and str(row.get('punteggio_app_russule')).strip() else "",
                ])
                punteggi_txt = " · ".join([p for p in punteggi_txt.split(" · ") if p])
                if punteggi_txt:
                    st.caption(f"App: {punteggi_txt}")
                if pd.notna(row.get('note')) and str(row.get('note')).strip():
                    st.markdown(
                        f"<div style='border-left:3px solid #8B6F47; padding:6px 12px; margin-top:4px'>"
                        f"📝 {row['note']}</div>",
                        unsafe_allow_html=True
                    )
            with col_c:
                if st.button("🗑️", key=f"del_uscita_{idx}", help="Elimina questa uscita"):
                    elimina_uscita(idx)
                    st.rerun()
            st.divider()
else:
    st.caption("Nessuna uscita registrata ancora. Aggiungi la tua prima uscita qui sopra!")
