import asyncio
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import StructuredTool

from api.statistics import _csv_cell, router as statistics_router
from utils.statistics_context import (
    TurnMetrics,
    activate_metrics,
    reset_metrics,
    statistics_callback_handler,
)
from utils.statistics_store import (
    finish_record,
    list_records,
    mark_running_interrupted,
    options,
    start_record,
    summary,
)


class StatisticsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "STATISTICS_ENABLED": "true",
                "STATISTICS_DB_PATH": str(
                    Path(self.temp_dir.name) / "statistics.sqlite3"
                ),
                "STATISTICS_MACHINE_NAME": "TEST-PC",
                "USERDOMAIN": "TEST-DOMAIN",
                "USERNAME": "test.user",
                "STATISTICS_STORE_QUESTION": "true",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp_dir.cleanup()

    def _record(
        self,
        *,
        mode: str = "task",
        status: str = "completed",
        question: str = "analyze project",
        ai_calls: int = 2,
        tool_calls: int = 3,
        duration_ms: int = 1500,
    ) -> str:
        record_id, _ = start_record(
            session_id="session-1",
            mode=mode,
            agent_name="default" if mode == "task" else "Chat",
            role_name="reviewer" if mode == "chat" else "",
            model_name="test-model",
            question=question,
            conversation_id="web-1",
            is_retry=False,
        )
        finish_record(
            record_id,
            status=status,
            duration_ms=duration_ms,
            ai_call_count=ai_calls,
            tool_call_count=tool_calls,
            error_type="TestError" if status == "failed" else None,
        )
        return record_id

    def test_records_summary_and_options(self) -> None:
        self._record(mode="chat", question="hello", duration_ms=1000)
        self._record(
            mode="task",
            status="failed",
            question="fix build",
            ai_calls=4,
            tool_calls=5,
            duration_ms=5000,
        )

        chat_rows = list_records(
            {"mode": "chat", "source": "interactive"}, page=1, page_size=20
        )
        self.assertEqual(chat_rows["total"], 1)
        self.assertEqual(chat_rows["items"][0]["machine_name"], "TEST-PC")
        self.assertEqual(chat_rows["items"][0]["user_name"], "TEST-DOMAIN\\test.user")
        self.assertEqual(chat_rows["items"][0]["question"], "hello")
        self.assertEqual(chat_rows["items"][0]["model_name"], "test-model")

        model_rows = list_records(
            {"model_name": "test-model", "source": "interactive"},
            page=1,
            page_size=20,
        )
        self.assertEqual(model_rows["total"], 2)

        user_rows = list_records(
            {"user_name": "TEST-DOMAIN\\test.user", "source": "interactive"},
            page=1,
            page_size=20,
        )
        self.assertEqual(user_rows["total"], 2)

        aggregate = summary({"source": "interactive"})
        self.assertEqual(aggregate["requestCount"], 2)
        self.assertEqual(aggregate["chatCount"], 1)
        self.assertEqual(aggregate["taskCount"], 1)
        self.assertEqual(aggregate["failedCount"], 1)
        self.assertEqual(aggregate["aiCallCount"], 6)
        self.assertEqual(aggregate["toolCallCount"], 8)
        self.assertEqual(aggregate["averageDurationMs"], 3000)
        self.assertEqual(aggregate["p95DurationMs"], 5000)
        self.assertEqual(len(aggregate["trend"]), 1)

        filter_options = options()
        self.assertEqual(filter_options["machineNames"], ["TEST-PC"])
        self.assertEqual(filter_options["userNames"], ["TEST-DOMAIN\\test.user"])
        self.assertEqual(filter_options["agentNames"], ["Chat", "default"])
        self.assertEqual(filter_options["roleNames"], ["reviewer"])
        self.assertEqual(filter_options["modelNames"], ["test-model"])

    def test_running_records_are_recovered(self) -> None:
        start_record(
            session_id="session-running",
            mode="task",
            agent_name="default",
            role_name="",
            model_name="test-model",
            question="unfinished",
            conversation_id=None,
            is_retry=False,
        )
        self.assertEqual(mark_running_interrupted(), 1)
        result = list_records(
            {"status": "interrupted", "source": "interactive"},
            page=1,
            page_size=20,
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["error_type"], "process_interrupted")

    def test_existing_database_adds_user_name_column(self) -> None:
        db_path = Path(os.environ["STATISTICS_DB_PATH"])
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE execution_statistics (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    machine_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    role_name TEXT,
                    model_name TEXT,
                    tool_call_count INTEGER NOT NULL DEFAULT 0,
                    ai_call_count INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_type TEXT,
                    session_id TEXT NOT NULL,
                    conversation_id TEXT,
                    source TEXT NOT NULL DEFAULT 'interactive',
                    is_retry INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        self._record()
        with closing(sqlite3.connect(db_path)) as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(execution_statistics)"
                ).fetchall()
            }
            row = conn.execute(
                "SELECT user_name FROM execution_statistics"
            ).fetchone()
        self.assertIn("user_name", columns)
        self.assertEqual(row[0], "TEST-DOMAIN\\test.user")

    def test_concurrent_writes(self) -> None:
        def write_record(index: int) -> None:
            self._record(question=f"question-{index}", duration_ms=index + 1)

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(write_record, range(24)))

        result = list_records(
            {"source": "interactive"}, page=1, page_size=50
        )
        self.assertEqual(result["total"], 24)

    def test_keyword_escapes_wildcards(self) -> None:
        self._record(question="100% complete")
        self._record(question="1000 complete")
        result = list_records(
            {"keyword": "100%", "source": "interactive"},
            page=1,
            page_size=20,
        )
        self.assertEqual(result["total"], 1)

    def test_csv_formula_is_escaped(self) -> None:
        self.assertEqual(_csv_cell("=SUM(A1:A2)"), "'=SUM(A1:A2)")
        self.assertEqual(_csv_cell("normal text"), "normal text")

    def test_statistics_api(self) -> None:
        self._record(mode="chat", question="api question")
        app = FastAPI()
        app.include_router(statistics_router)
        client = TestClient(app)

        summary_response = client.get("/api/statistics/summary")
        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(summary_response.json()["requestCount"], 1)

        records_response = client.get(
            "/api/statistics/records",
            params={
                "mode": "chat",
                "modelName": "test-model",
                "userName": "TEST-DOMAIN\\test.user",
                "pageSize": 10,
            },
        )
        self.assertEqual(records_response.status_code, 200)
        self.assertEqual(records_response.json()["total"], 1)

        export_response = client.get("/api/statistics/export.csv")
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("api question", export_response.text)


class StatisticsCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_callbacks_increment_active_metrics(self) -> None:
        metrics = TurnMetrics("turn-test")
        token = activate_metrics(metrics)
        try:
            await statistics_callback_handler.on_chat_model_start({}, [])
            await statistics_callback_handler.on_tool_start({}, "{}")
        finally:
            reset_metrics(token)
        self.assertEqual(metrics.ai_call_count, 1)
        self.assertEqual(metrics.tool_call_count, 1)

    async def test_langchain_invocations_trigger_callbacks(self) -> None:
        metrics = TurnMetrics("turn-integration")
        token = activate_metrics(metrics)
        try:
            model = FakeListChatModel(responses=["ok"])
            await model.ainvoke(
                "hello", config={"callbacks": [statistics_callback_handler]}
            )
            tool = StructuredTool.from_function(
                func=lambda value: value,
                name="echo",
                description="Echo a value",
            )
            await tool.ainvoke(
                {"value": "test"},
                config={"callbacks": [statistics_callback_handler]},
            )
        finally:
            reset_metrics(token)
        self.assertEqual(metrics.ai_call_count, 1)
        self.assertEqual(metrics.tool_call_count, 1)


if __name__ == "__main__":
    unittest.main()
