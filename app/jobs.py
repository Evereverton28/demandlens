"""Background jobs (imports and analysis runs) on a worker thread.

Long operations run outside the request so the page stays responsive; the
browser polls /api/jobs/<id> for progress.
"""
import threading
import traceback

from .db import connect


def active_job(db, user_id: int, kind: str):
    return db.execute("SELECT * FROM jobs WHERE user_id=? AND kind=? AND status='running' ORDER BY job_id DESC LIMIT 1",
                      (user_id, kind)).fetchone()


def start_job(app, db, user_id: int, kind: str, fn) -> int:
    """fn(conn, progress) -> str message. Returns the job id."""
    running = active_job(db, user_id, kind)
    if running:
        return running["job_id"]
    job_id = db.execute("INSERT INTO jobs (user_id, kind, status, progress) VALUES (?, ?, 'running', 'Starting')",
                        (user_id, kind)).lastrowid
    db.commit()

    def work():
        conn = connect(app.config["DATABASE"])

        def progress(msg):
            conn.execute("UPDATE jobs SET progress=? WHERE job_id=?", (msg, job_id))
            conn.commit()
        try:
            message = fn(conn, progress)
            conn.execute("UPDATE jobs SET status='done', message=?, progress='Done', finished_at=CURRENT_TIMESTAMP "
                         "WHERE job_id=?", (message, job_id))
        except Exception as exc:          # the error is shown to the user; the trace goes to the log
            traceback.print_exc()
            conn.rollback()
            conn.execute("UPDATE jobs SET status='failed', message=?, finished_at=CURRENT_TIMESTAMP WHERE job_id=?",
                         (str(exc), job_id))
        conn.commit()
        conn.close()

    if app.config.get("RUN_JOBS_INLINE"):
        work()
    else:
        threading.Thread(target=work, daemon=True).start()
    return job_id


def recover_interrupted(path: str):
    """Jobs still marked running when the server starts were interrupted by a restart."""
    conn = connect(path)
    conn.execute("UPDATE jobs SET status='failed', message='Interrupted because the server restarted. Start it again.', "
                 "finished_at=CURRENT_TIMESTAMP WHERE status='running'")
    conn.execute("UPDATE model_runs SET status='failed', error='Interrupted by a server restart' WHERE status='running'")
    conn.commit()
    conn.close()
