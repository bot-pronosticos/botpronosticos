"""
bot/formatter.py
Formato ultra-minimalista para pantalla de móvil.
Sin saturación visual. Solo lo esencial: partido, selección, cuota y 2 líneas de análisis clave.
"""


def format_pick_message(pick: dict, bankroll: float = 100.0) -> str:
    """
    Formato ultra limpio para lectura rápida en smartphone.
    """
    liga        = pick.get("liga", "Fútbol")
    local       = pick.get("equipo_local", "Local")
    visitante   = pick.get("equipo_visitante", "Visitante")
    outcome_lbl = pick.get("outcome_label", pick.get("outcome", ""))
    cuota       = pick.get("cuota", 0.0)
    bookmaker   = pick.get("bookmaker", "Bet365")
    prob_m      = pick.get("prob_modelo", 0.0) * 100
    kelly_f     = pick.get("kelly", 0.02)
    argumento_stats = pick.get("argumento_stats", "")
    argumento_context = pick.get("argumento_context", "")

    # Stake sugerido
    stake_val = min(bankroll * kelly_f, bankroll * 0.04)
    stake_val = max(stake_val, 1.0)  # Mínimo $1.00

    msg = (
        f"⚽ *{liga}*\n"
        f"*{local} vs {visitante}*\n"
        f"👉 *Pronóstico:* `{outcome_lbl}` | *Cuota:* `{cuota:.2f}` ({bookmaker})\n\n"
        f"📊 {argumento_stats}\n"
        f"🎯 {argumento_context} (Prob: {prob_m:.0f}% | Stake: ${stake_val:.2f})"
    )
    return msg


def format_stats_message(stats_data: dict) -> str:
    """
    Formato minimalista de estadísticas.
    """
    total    = stats_data.get("total_picks", 0)
    aciertos = stats_data.get("aciertos", 0)
    pct      = stats_data.get("pct_acierto", 0.0)
    roi      = stats_data.get("roi_unidades", 0.0)

    return (
        f"📈 *Rendimiento del Bot*\n"
        f"• Pronósticos evaluados: `{total}`\n"
        f"• Aciertos: `{aciertos}` (`{pct:.1f}%`)\n"
        f"• Balance neto: `{roi:+.2f}$`\n"
        f"• Casa referencia: *Bet365*"
    )
