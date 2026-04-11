import sqlite3

conn = sqlite3.connect("anime.db")
cursor = conn.cursor()

print("Таблицы:")
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
for row in cursor.fetchall():
    print("-", row[0])

print("\nКоличество аниме:")
cursor.execute("SELECT COUNT(*) FROM anime")
print(cursor.fetchone()[0])

print("\nКоличество опубликованных:")
cursor.execute("SELECT COUNT(*) FROM published_anime")
print(cursor.fetchone()[0])

print("\nСодержимое published_anime:")
cursor.execute("SELECT * FROM published_anime")
rows = cursor.fetchall()
print(rows if rows else "пусто")

conn.close()
