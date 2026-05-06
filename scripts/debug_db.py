import sqlite3

conn = sqlite3.connect("anime.db")
cursor = conn.cursor()

print("Tables:")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
for row in cursor.fetchall():
    print("-", row[0])

print("\nAnime count:")
cursor.execute("SELECT COUNT(*) FROM anime")
print(cursor.fetchone()[0])

print("\nPublished count:")
cursor.execute("SELECT COUNT(*) FROM published_anime")
print(cursor.fetchone()[0])

print("\npublished_anime contents:")
cursor.execute("SELECT * FROM published_anime")
rows = cursor.fetchall()
print(rows if rows else "empty")

conn.close()
