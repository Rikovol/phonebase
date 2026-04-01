from celery import Celery
import os

app = Celery(
    "phonebase",
    broker=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    include=["app.tasks"],
)

_interval = int(os.getenv("IMPORT_INTERVAL_MINUTES", "30"))

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Moscow",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
)

_beat = {}

if _interval > 0:
    _beat["import-used-every-N-min"] = {
        "task": "app.tasks.periodic_import_used",
        "schedule": _interval * 60,
    }
    _beat["import-new-every-N-min"] = {
        "task": "app.tasks.periodic_import_new",
        "schedule": _interval * 60,
    }

_avito_stats = int(os.getenv("AVITO_STATS_INTERVAL_MINUTES", "60"))
_avito_messenger = int(os.getenv("AVITO_MESSENGER_INTERVAL_MINUTES", "5"))
_avito_feed_check = int(os.getenv("AVITO_FEED_CHECK_INTERVAL_MINUTES", "120"))

if _avito_stats > 0:
    _beat["avito-stats"] = {
        "task": "app.tasks.periodic_fetch_avito_stats",
        "schedule": _avito_stats * 60,
    }
if _avito_messenger > 0:
    _beat["avito-messenger"] = {
        "task": "app.tasks.periodic_fetch_avito_messages",
        "schedule": _avito_messenger * 60,
    }
if _avito_feed_check > 0:
    _beat["avito-feed-check"] = {
        "task": "app.tasks.periodic_check_avito_feed",
        "schedule": _avito_feed_check * 60,
    }

if _beat:
    app.conf.beat_schedule = _beat
