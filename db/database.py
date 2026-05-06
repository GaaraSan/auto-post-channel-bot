from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.settings import DATABASE_URL

# SQLAlchemy engine bound to the configured database URL.
engine = create_engine(
    DATABASE_URL,
    echo=False,   # set to True to print all SQL statements for debugging
    future=True
)

# Session factory — each call to SessionLocal() returns a new session.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True
)

# Base class for all ORM models.
Base = declarative_base()
