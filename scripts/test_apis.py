import sys
sys.path.insert(0, 'C:/Users/Santiago/Desktop/bot-pronosticos')
import httpx
from config.settings import RAPIDAPI_KEY

# Test API-Football temporada 2024
resp = httpx.get(
    'https://v3.football.api-sports.io/standings',
    headers={'x-apisports-key': RAPIDAPI_KEY},
    params={'league': 39, 'season': 2024},
    timeout=15
)
data = resp.json()
errors = data.get('errors', {})
response = data.get('response', [])
print(f'API-Football Status: {resp.status_code}')
print(f'Errors: {errors}')
print(f'Response entries: {len(response)}')
if response:
    league_data = response[0].get('league', {})
    standings_groups = league_data.get('standings', [[]])
    if standings_groups and standings_groups[0]:
        top = standings_groups[0][0]
        print(f'Top team: {top["team"]["name"]} — pts: {top["points"]}')

# Test ESPN NBA
print()
resp2 = httpx.get(
    'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    params={'dates': '20261115'},
    timeout=15,
    follow_redirects=True
)
print(f'ESPN NBA Status: {resp2.status_code}')
data2 = resp2.json()
events = data2.get('events', [])
print(f'Juegos NBA encontrados: {len(events)}')
if events:
    ev = events[0]
    competitors = ev.get('competitions', [{}])[0].get('competitors', [])
    teams = [c['team']['displayName'] for c in competitors]
    print(f'Muestra: {" vs ".join(teams)}')
