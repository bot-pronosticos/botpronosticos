"""
bot/formatter.py
Formato limpio y profesional para pantalla de smartphone:
- Jerarquía visual clara: Liga + Hora, Equipos, Pronóstico + Cuota, Stake + Confianza, Análisis clave.
- Cero relleno ni saturación visual.
"""


def format_pick_message(pick: dict, bankroll: float = 100.0) -> str:
    """
    Formato profesional de lectura rápida en móvil.
    """
    liga          = pick.get("liga", "Fútbol")
    local         = pick.get("equipo_local", "Local")
    visitante     = pick.get("equipo_visitante", "Visitante")
    ko_lbl        = pick.get("kickoff_label", "")
    outcome_lbl   = pick.get("outcome_label", pick.get("outcome", ""))
    cuota         = float(pick.get("cuota", 0.0))
    raw_bm        = pick.get("bookmaker", "Pinnacle")
    bookmaker     = raw_bm.split("|")[0].strip()
    prob_m        = float(pick.get("prob_modelo", 0.0)) * 100
    kelly_f       = float(pick.get("kelly", 0.02))
    stats_line    = pick.get("argumento_stats", "")
    context_line  = pick.get("argumento_context", "")

    # Cálculo de stake disciplinado (Quarter Kelly con tope del 4% de la banca)
    stake_val = min(bankroll * kelly_f, bankroll * 0.04)
    stake_val = max(stake_val, 1.0)
    stake_pct = (stake_val / bankroll * 100) if bankroll > 0 else 2.0

    header_line = f"⚽ *{liga}*" + (f" · 🕒 *{ko_lbl}*" if ko_lbl else "")

    msg = (
        f"{header_line}\n"
        f"*{local} vs {visitante}*\n\n"
        f"🎯 *Pronóstico:* `{outcome_lbl}`\n"
        f"📈 *Cuota:* `{cuota:.2f}` ({bookmaker})\n"
        f"💵 *Stake sugerido:* `${stake_val:.2f}` ({stake_pct:.1f}% banca) · Confianza: `{prob_m:.0f}%`\n\n"
        f"📊 *Métricas:* {stats_line}\n"
        f"💡 *Análisis:* {context_line}"
    )
    return msg


def format_stats_message(stats_data: dict) -> str:
    """
    Formato ejecutivo de estadísticas de rendimiento.
    """
    total       = stats_data.get("total_picks", 0)
    evaluados   = stats_data.get("evaluados", 0)
    apostados   = stats_data.get("apostados", 0)
    aciertos    = stats_data.get("aciertos", 0)
    aciertos_ap = stats_data.get("aciertos_apostados", 0)
    pct         = stats_data.get("pct_acierto", 0.0)
    pct_ap      = (aciertos_ap / apostados * 100) if apostados > 0 else 0.0
    roi         = stats_data.get("roi_unidades", 0.0)

    return (
        f"📈 *RENDIMIENTO DEL MODELO*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Pronósticos generados: `{total}`\n"
        f"• Partidos finalizados: `{evaluados}`\n"
        f"• Tasa de acierto global: `{aciertos}/{evaluados}` (`{pct:.1f}%`)\n"
        f"• Apuestas realizadas por ti: `{apostados}` (Acierto: `{pct_ap:.1f}%`)\n"
        f"• Balance neto acumulado: `{roi:+.2f}$`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"• Cuotas de referencia: *Pinnacle / 1xBet*\n"
        f"• Criterio: *EV Positivo + Quarter Kelly*"
    )
