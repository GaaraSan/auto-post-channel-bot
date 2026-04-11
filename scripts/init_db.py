from db.database import engine
from db.models import Base


def init_db():
    Base.metadata.create_all(bind=engine)
    print("База данных и таблицы успешно созданы")


if __name__ == "__main__":
    init_db()
