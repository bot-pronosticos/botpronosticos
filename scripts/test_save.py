import sys
sys.path.insert(0, '.')
from harvesters.db_client import upsert, select
print('db_client importado OK')

result = upsert('partidos', [{
    'source_id': 'test_isolated_999',
    'deporte': 'football',
    'liga': 'Test Liga',
    'equipo_local': 'Test A',
    'equipo_visitante': 'Test B',
    'kickoff': '2026-11-20T15:00:00+00:00',
    'estado': 'pendiente'
}], on_conflict='source_id')

print(f'Upsert OK: {len(result)} rows')
if result:
    print(f'ID: {result[0]["id"]}')

# Test save_odds_to_db con 1 evento dummy
print()
from harvesters.odds_harvester import save_odds_to_db
dummy = [{
    'source_id': 'test_isolated_999',
    'deporte': 'football',
    'liga': 'Test Liga',
    'equipo_local': 'Test A',
    'equipo_visitante': 'Test B',
    'kickoff': '2026-11-20T15:00:00+00:00',
    'bookmakers': [{
        'title': 'TestBook',
        'markets': [{
            'key': 'h2h',
            'outcomes': [
                {'name': 'Test A', 'price': 1.90},
                {'name': 'Test B', 'price': 2.10},
                {'name': 'Draw',   'price': 3.40},
            ]
        }]
    }]
}]
saved = save_odds_to_db(dummy)
print(f'save_odds_to_db resultado: {saved}')
