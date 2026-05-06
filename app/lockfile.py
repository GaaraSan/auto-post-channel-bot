import os
from contextlib import contextmanager


class LockAlreadyHeldError(RuntimeError):
    pass


@contextmanager
def file_lock(lock_path: str):
    """
    Simple cross-process lock based on atomic O_EXCL file creation.

    Raises LockAlreadyHeldError immediately if the lock file already exists.
    Works on both Windows and Unix.
    """
    fd = None
    try:
        # O_EXCL makes creation atomic: raises FileExistsError if file is present.
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
            # Swallow cleanup errors — crashing here would mask the original error.
            pass
