import datetime
import json
from dataclasses import dataclass, field

import redis

from otto import settings

redis_url = settings.REDIS_URL
redis_client = redis.from_url(redis_url)


@dataclass
class CeleryTask:
    task_id: str
    name: str
    status: str
    date_done: str
    result: any = None
    traceback: str | None = None
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


class CeleryRedis:

    def __init__(self, redis_client: redis.Redis):
        self.redis_client = redis_client

    def get_celery_task_keys(self, pattern="celery-task-meta-*", cursor=0, count=100):
        """Get Celery task keys from Redis with pagination"""
        return self.redis_client.scan(cursor=cursor, match=pattern, count=count)

    def get_all_celery_task_keys(self) -> list[str]:
        """Get all Celery task keys from Redis"""
        all_keys: list[str] = []
        cursor = 0

        while True:
            cursor, keys = self.get_celery_task_keys(cursor=cursor, count=1000)
            all_keys.extend(keys)
            if cursor == 0:
                break

        return all_keys

    def get_task_info(self, task_key, pattern="celery-task-meta-") -> dict | None:
        """Get task information from Redis"""
        raw_data = redis_client.get(task_key)
        if not raw_data:
            return None

        try:
            task_data = json.loads(raw_data)
            # Extract the task ID from the key
            task_id = task_key.decode("utf-8").replace(pattern, "")
            task_data["task_id"] = task_id
            return task_data
        except json.JSONDecodeError:
            return None

    def get_tasks(self, page_size=100, max_tasks=None):
        """Get task information with pagination"""
        all_tasks: list[dict] = []
        cursor = 0
        processed_count = 0

        while True:
            # Get a batch of task keys
            cursor, task_keys = self.get_celery_task_keys(
                cursor=cursor, count=page_size
            )

            for task_key in task_keys:
                if max_tasks and processed_count >= max_tasks:
                    return all_tasks

                task_data = self.get_task_info(task_key)
                if task_data:
                    all_tasks.append(task_data)
                    processed_count += 1

            if cursor == 0:
                break

        return all_tasks

    def format_task_info(self, task_data: dict | None) -> CeleryTask | None:
        """Format task information for display"""
        if not task_data:
            return None

        task_id = task_data.get("task_id", "Unknown")
        status = task_data.get("status", "Unknown")
        result = task_data.get("result", None)
        traceback = task_data.get("traceback", None)
        task_name = task_data.get("name", "Unknown")

        # Format date
        date_done = task_data.get("date_done")
        if date_done:
            try:
                date_obj = datetime.datetime.fromisoformat(
                    date_done.replace("Z", "+00:00")
                )
                date_str = date_obj.strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, AttributeError):
                date_str = date_done
        else:
            date_str = "Unknown"

        formatted = CeleryTask(
            task_id=task_id,
            name=task_name,
            status=status,
            date_done=date_str,
            result=result,
            traceback=traceback,
            args=task_data.get("args", []),
            kwargs=task_data.get("kwargs", {}),
        )

        return formatted


class CeleryTaskRegistry:
    def __init__(self, app):
        self.app = app

    def get_registered_tasks(self):
        # Get registered tasks from Celery
        inspector = self.app.control.inspect()
        registered_tasks = inspector.registered()

        all_registered_tasks = []
        if registered_tasks:
            for node, node_tasks in registered_tasks.items():
                for task_name in node_tasks:
                    # Clean up task name (remove node info)
                    clean_task_name = (
                        task_name.split(" [name=")[0]
                        if " [name=" in task_name
                        else task_name
                    )
                    # Add a flag if the task is a "load_laws", "sync", or "update" task
                    executable = any(
                        keyword in clean_task_name.lower()
                        for keyword in ["load_laws", "sync", "update"]
                    )
                    all_registered_tasks.append(
                        {
                            "name": clean_task_name,
                            "node": node,
                            "full_name": task_name,
                            "executable": executable,
                        }
                    )

        # Remove duplicates and sort
        unique_tasks = {}
        for task in all_registered_tasks:
            if task["name"] not in unique_tasks:
                unique_tasks[task["name"]] = task

        registered_task_list = sorted(
            unique_tasks.values(),
            key=lambda x: (-int(x["executable"]), x["name"]),
        )
        return registered_task_list
