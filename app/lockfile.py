import os
from contextlib import contextmanager


class LockAlreadyHeldError(RuntimeError):
    pass


@contextmanager
def file_lock(lock_path: str):
    """
    Простой межпроцессный lock-файл.
    На Windows/Unix используем атомарное создание файла.
    """
    fd = None
    try:
        # O_EXCL гарантирует атомарность: если файл уже есть — ошибка
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    except FileExistsError as e:
        raise LockAlreadyHeldError(f"Lock already held: {lock_path}") from e
    finally:
        try:
            if fd is not None:
                os.close(fd)
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            # если не смогли удалить lock (например, права) — лучше не падать
            pass

