from app import app, db, seed

with app.app_context():
    seed()
    print("Done.")
