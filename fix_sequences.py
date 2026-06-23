"""Fix PostgreSQL sequences after data migration from SQLite."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['DATABASE_URL'] = os.environ.get('DATABASE_URL', 'postgresql://neondb_owner:npg_E36lvXqFRfbh@ep-small-cherry-ahaz43vu-pooler.c-3.us-east-1.aws.neon.tech/neondb?sslmode=require')

from app import app, db
from sqlalchemy import text

with app.app_context():
    seqs = db.session.execute(text(
        "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema='public'"
    )).fetchall()
    for (seq_name,) in seqs:
        if not seq_name.endswith('_id_seq'):
            continue
        qtable = seq_name.replace('_id_seq', '')
        # Quote if reserved word
        if qtable in ('user',):
            qtable = f'"{qtable}"'
        try:
            db.session.execute(text(f"SELECT setval('{seq_name}', (SELECT MAX(id) FROM {qtable}))"))
            cur = db.session.execute(text(f"SELECT currval('{seq_name}')")).scalar()
            print(f"  {seq_name} -> {cur}")
        except Exception as e:
            print(f"  {seq_name} -> SKIP ({e})")
    db.session.commit()
    print("Done!")
