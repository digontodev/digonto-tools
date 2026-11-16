import threading

from PySide6.QtCore import QObject, Signal

IMG_FILTER = "Pictures (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.tif *.tiff);;All files (*)"


class _Job(QObject):
    done = Signal(object)
    failed = Signal(object)


_alive = set()


def run_bg(work, done, error=None):
    """run work() in a thread; done(result) / error(exception) are called in the window thread"""
    job = _Job()
    _alive.add(job)

    def fin(r):
        _alive.discard(job)
        done(r)

    def bad(e):
        _alive.discard(job)
        if error:
            error(e)
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "Digonto Tools", str(e))
    job.done.connect(fin)
    job.failed.connect(bad)

    def target():
        try:
            r = work()
        except BaseException as e:  # noqa: BLE001
            job.failed.emit(e)
            return
        job.done.emit(r)
    threading.Thread(target=target, daemon=True).start()
