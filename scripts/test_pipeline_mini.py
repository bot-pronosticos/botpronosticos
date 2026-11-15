import sys
sys.path.insert(0, '.')
from harvesters.odds_harvester import harvest_odds, save_odds_to_db
from harvesters.basketball_harvester import get_games_today

print("=== Test pipeline aislado ===")

# Carga desde cache (0 requests API)
events = harvest_odds()
print(f"Eventos cargados desde cache: {len(events)}")

# Guarda solo los primeros 5 para test rapido
print("Guardando primeros 5 partidos...")
saved = save_odds_to_db(events[:5])
print(f"Resultado: {saved}/5")

# NBA
print()
print("NBA ESPN test...")
games = get_games_today()
print(f"Juegos NBA: {len(games)}")
if games:
    print(f"  {games[0]['home_team']} vs {games[0]['away_team']}")

print()
print("=== PIPELINE OK ===")
