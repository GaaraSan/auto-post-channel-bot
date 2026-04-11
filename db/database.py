from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.settings import DATABASE_URL

# Создаём движок SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    echo=False,          # True — если хочешь видеть SQL-запросы
    future=True
)

# Фабрика сессий (подключений к БД)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True
)

# Базовый класс для всех моделей
Base = declarative_base()
