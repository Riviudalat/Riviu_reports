"""The Postgres backend against a real PostgreSQL 17 built from Riviu Data's migrations.

Opt-in (skipped unless both are set):
    RIVIU_PG_TEST_ADMIN_URL  superuser DSN of a throwaway server, e.g. the Riviu Data test
                             compose: postgresql://postgres:test-super@127.0.0.1:55447/postgres
    RIVIU_DATA_APP_DIR       a Riviu_Data_App checkout; its backend/ migrations build the schema
Optional: RIVIU_PG_TEST_OWNER_PASSWORD (test-owner), RIVIU_PG_TEST_SCANNER_PASSWORD (test-scanner),
RIVIU_PG_TEST_APP_PASSWORD (test-app). See docs/riviu-data-postgres.md.

The session creates a fresh database, migrates it as riviu_owner (alembic upgrade head) and
runs the store as riviu_scanner, the column-limited production role.
"""

import asyncio
import datetime as dt
import os
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit

import pytest

asyncpg = pytest.importorskip("asyncpg")

from fastapi.testclient import TestClient  # noqa: E402

from riviu import internal_scan, scan_state  # noqa: E402
from riviu.store import config as store_config  # noqa: E402
from riviu.store import postgres, rules  # noqa: E402
from riviu.workbook.partners import partners_from_doi_tac  # noqa: E402
from tests.pg_fakes import Events  # noqa: E402

ADMIN_URL = os.environ.get("RIVIU_PG_TEST_ADMIN_URL", "")
DATA_APP_DIR = os.environ.get("RIVIU_DATA_APP_DIR", "")
pytestmark = pytest.mark.skipif(
    not (ADMIN_URL and DATA_APP_DIR), reason="set RIVIU_PG_TEST_ADMIN_URL and RIVIU_DATA_APP_DIR to run",
)
DB_NAME = "riviu_reports_it"
TOKEN = "s" * 40
TIKTOK_URL = "https://www.tiktok.com/@demo/video/7400000000000000001"
THREADS_URL = "https://www.threads.com/@miri_viu/post/DdRIHGWCURV"


def _dsn(user, password, database):
    parts = urlsplit(ADMIN_URL)
    netloc = f"{user}:{password}@{parts.hostname}:{parts.port or 5432}"
    return urlunsplit((parts.scheme, netloc, f"/{database}", "", ""))


def run(coro):
    return asyncio.run(coro)


async def _admin(database="postgres"):
    parts = urlsplit(ADMIN_URL)
    return await asyncpg.connect(urlunsplit((parts.scheme, parts.netloc, f"/{database}", "", "")))


@pytest.fixture(scope="session")
def pg():
    owner_pw = os.environ.get("RIVIU_PG_TEST_OWNER_PASSWORD", "test-owner")

    async def create():
        conn = await _admin()
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{DB_NAME}" OWNER riviu_owner')
        finally:
            await conn.close()
        conn = await _admin(DB_NAME)
        try:
            await conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC; ALTER SCHEMA public OWNER TO riviu_owner; "
                               "GRANT USAGE ON SCHEMA public TO riviudata")
        finally:
            await conn.close()

    run(create())
    owner_url = _dsn("riviu_owner", owner_pw, DB_NAME).replace("postgresql://", "postgresql+psycopg://", 1)
    env = {
        **os.environ, "RDA_OWNER_URL": owner_url, "PYTHONDONTWRITEBYTECODE": "1",
        # Never create or touch a venv inside the Riviu Data checkout.
        "UV_PROJECT_ENVIRONMENT": os.path.join(tempfile.gettempdir(), "riviu-reports-rda-venv"),
    }
    env.pop("VIRTUAL_ENV", None)
    subprocess.run(["uv", "run", "--frozen", "--no-dev", "alembic", "upgrade", "head"],
                   cwd=os.path.join(DATA_APP_DIR, "backend"), env=env, check=True, capture_output=True)
    return {
        "scanner": _dsn("riviu_scanner", os.environ.get("RIVIU_PG_TEST_SCANNER_PASSWORD", "test-scanner"), DB_NAME),
        "app": _dsn("riviu_app", os.environ.get("RIVIU_PG_TEST_APP_PASSWORD", "test-app"), DB_NAME),
    }


@pytest.fixture
def db(pg):
    async def reset():
        conn = await _admin(DB_NAME)
        try:
            await conn.execute("TRUNCATE public.tiktok, public.threads, public.scan_run, public.channel_override, "
                               "public.lich_su RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    run(reset())
    return pg


async def sql(query, *args, fetch=False):
    conn = await _admin(DB_NAME)
    try:
        await conn.execute("SELECT set_config('app.user', 'user:test', false)")
        return await (conn.fetch if fetch else conn.execute)(query, *args)
    finally:
        await conn.close()


TABLES = {"tiktok", "threads"}  # test helpers only build SQL for these names


def insert(table, **row):
    assert table in TABLES
    columns = ", ".join(row)
    params = ", ".join(f"${i}" for i in range(1, len(row) + 1))
    return run(sql(f"INSERT INTO public.{table} ({columns}) VALUES ({params}) RETURNING id", *row.values(), fetch=True))[0]["id"]  # noqa: S608


def row(table, row_id):
    assert table in TABLES
    return dict(run(sql(f"SELECT * FROM public.{table} WHERE id = $1", row_id, fetch=True))[0])  # noqa: S608


# ---- reads ---------------------------------------------------------------------------

def test_a_month_is_selected_by_thang_nam_across_the_year_boundary(db):
    store = postgres.PostgresStore("tiktok", db["scanner"])
    ids = {
        "dec25": insert("tiktok", thang="Tháng 12", ngay_air=dt.date(2025, 12, 31), link_air=TIKTOK_URL),
        "jan26": insert("tiktok", thang="Tháng 1", ngay_air=dt.date(2026, 1, 1), link_air=TIKTOK_URL + "?a"),
        "jan25": insert("tiktok", thang="Tháng 1", ngay_air=dt.date(2025, 1, 15), link_air=TIKTOK_URL + "?b"),
        "no_date": insert("tiktok", thang="Tháng 1", ngay_air=None, link_air=TIKTOK_URL + "?c"),
    }

    async def scan_ids(thang):
        session = await store.open_scan(thang)
        await session.close()
        return [item["row"] for item in session.rows]

    assert run(scan_ids("2026-01")) == [ids["jan26"]]
    assert run(scan_ids("2025-12")) == [ids["dec25"]]
    assert run(scan_ids("2025-01")) == [ids["jan25"]]
    assert [r["LINK AIR"] for r in run(store.report_rows("2026-01")).rows] == [TIKTOK_URL + "?a"]


def test_locked_and_soft_deleted_rows_are_neither_scanned_nor_reported_as_deleted(db):
    store = postgres.PostgresStore("threads", db["scanner"])
    day = dt.date(2026, 10, 3)
    live = insert("threads", ngay_air=day, link_air=THREADS_URL, doi_tac="Cafe A")
    locked = insert("threads", ngay_air=day, link_air=THREADS_URL + "?l", doi_tac="Cafe A", khoa_chi_so=True, luot_xem=5)
    deleted = insert("threads", ngay_air=day, link_air=THREADS_URL + "?d", doi_tac="Cafe B",
                     da_xoa_luc=dt.datetime(2026, 10, 4, tzinfo=dt.UTC))

    async def open_rows():
        session = await store.open_scan("2026-10")
        await session.close()
        return session

    session = run(open_rows())
    assert [item["row"] for item in session.rows] == [live] and session.locked_count == 1
    report = run(store.report_rows("2026-10"))
    # Locked rows are still reported (only the scanner skips them); deleted rows are not.
    assert len(report.rows) == 2 and THREADS_URL + "?d" not in [r["LINK AIR"] for r in report.rows]
    assert list(report.partners) == ["Cafe A"]
    assert row("threads", locked)["luot_xem"] == 5
    assert row("threads", deleted)["da_xoa_luc"] is not None


# ---- writes ---------------------------------------------------------------------------

def test_tiktok_null_vs_zero_on_every_outcome(db):
    day = dt.date(2026, 10, 2)
    old = {"luot_xem": 50, "tim": 5, "binh_luan": 0, "luot_luu": 1, "chia_se": 2}
    success = insert("tiktok", ngay_air=day, link_air=TIKTOK_URL, ten_kenh="Demo", **old)
    hidden = insert("tiktok", ngay_air=day, link_air=TIKTOK_URL + "?h", ten_kenh="Demo", **old)
    failed = insert("tiktok", ngay_air=day, link_air=TIKTOK_URL + "?f", ten_kenh="Demo", **old)
    async def scan():
        session = await postgres.PostgresStore("tiktok", db["scanner"]).open_scan("2026-10", events=Events())
        data = {"Views": "0", "Likes": None, "Comments": "3", "Saves": "", "Shares": "1.234"}
        session.write_tiktok_result(success, TIKTOK_URL, data, "Demo", "Success", resolved_url=TIKTOK_URL)
        session.write_tiktok_result(hidden, TIKTOK_URL + "?h", {}, "", "Ẩn số liệu: TikTok không trả số liệu")
        session.write_tiktok_result(failed, TIKTOK_URL + "?f", {}, "", "Error: HTTP 403")
        result = await session.flush()
        await session.close()
        return result

    assert run(scan()) == (True, None)
    got = row("tiktok", success)
    assert (got["luot_xem"], got["tim"], got["binh_luan"], got["luot_luu"], got["chia_se"]) == (0, None, 3, None, 1234)
    assert got["cap_nhat_lan_cuoi"] is not None and got["link_nguon_phan_giai"] == TIKTOK_URL
    got = row("tiktok", hidden)
    assert [got[k] for k in old] == [None] * 5 and got["cap_nhat_lan_cuoi"] is None and got["ten_kenh"] == "Demo"
    got = row("tiktok", failed)
    assert {k: got[k] for k in old} == old and got["trang_thai_quet"] == "Error: HTTP 403" and got["cap_nhat_lan_cuoi"] is None
    audit = run(sql("SELECT DISTINCT nguoi_thuc_hien FROM public.lich_su WHERE bang = 'tiktok' AND hanh_dong = 'UPDATE'", fetch=True))
    assert [r["nguoi_thuc_hien"] for r in audit] == ["app:riviu-reports-scanner"]


def test_threads_null_vs_zero_and_view_invalidation(db):
    day = dt.date(2026, 10, 2)
    kept = insert("threads", ngay_air=day, link_air=THREADS_URL, luot_xem=100, tim=7, repost=2)
    cleared = insert("threads", ngay_air=day, link_air=THREADS_URL + "?v", luot_xem=100, tim=7)

    async def scan():
        session = await postgres.PostgresStore("threads", db["scanner"]).open_scan("2026-10")
        session.write_threads_result(kept, {"channel": "miri", "metrics": {"views": 0, "likes": None, "comments": 0,
                                                                           "reposts": None, "shares": None}, "error": ""})
        session.write_threads_result(cleared, {"channel": "", "metrics": {"views": None, "likes": 9}, "error": "",
                                               "metricSources": {"views": "view_query_unknown"}})
        await session.flush()
        await session.close()

    run(scan())
    got = row("threads", kept)
    assert (got["luot_xem"], got["tim"], got["binh_luan"], got["repost"], got["ten_kenh"]) == (0, 7, 0, 2, "miri")
    got = row("threads", cleared)
    assert (got["luot_xem"], got["tim"]) == (None, 9)


def test_rows_edited_deleted_or_locked_mid_scan_are_skipped_and_logged(db):
    day = dt.date(2026, 10, 2)
    edited = insert("tiktok", ngay_air=day, link_air=TIKTOK_URL, luot_xem=1)
    deleted = insert("tiktok", ngay_air=day, link_air=TIKTOK_URL + "?d", luot_xem=1)
    locked = insert("tiktok", ngay_air=day, link_air=TIKTOK_URL + "?l", luot_xem=1)
    run_id = run(sql("INSERT INTO public.scan_run (platform, phase, started_by) VALUES ('tiktok', 'dang_quet', 't') RETURNING id",
                     fetch=True))[0]["id"]

    async def scan():
        session = await postgres.PostgresStore("tiktok", db["scanner"]).open_scan("2026-10", run_id=run_id, events=Events())
        await sql("UPDATE public.tiktok SET link_air = $2 WHERE id = $1", edited, TIKTOK_URL + "?new")
        await sql("UPDATE public.tiktok SET da_xoa_luc = now() WHERE id = $1", deleted)
        await sql("UPDATE public.tiktok SET khoa_chi_so = true WHERE id = $1", locked)
        for row_id, url in ((edited, TIKTOK_URL), (deleted, TIKTOK_URL + "?d"), (locked, TIKTOK_URL + "?l")):
            session.write_tiktok_result(row_id, url, {"Views": "999"}, "Demo", "Success")
        result = await session.flush()
        await session.close()
        return session, result

    session, result = run(scan())
    assert result == (True, None) and session.written == 0
    assert {item["id"]: item["reason"] for item in session.skipped} == {
        edited: "link đã đổi", deleted: "dòng đã bị xoá", locked: "dòng đã khoá chỉ số"}
    assert sum("đã bị sửa trong lúc quét" in line for line in session.events.logs) == 3
    assert all(row("tiktok", row_id)["luot_xem"] == 1 for row_id in (edited, deleted, locked))
    items = run(sql("SELECT row_id, status FROM public.scan_run_item WHERE run_id = $1 ORDER BY row_id", run_id, fetch=True))
    assert [r["status"] for r in items] == ["Bỏ qua: link đã đổi", "Bỏ qua: dòng đã bị xoá", "Bỏ qua: dòng đã khoá chỉ số"]


def test_partner_split_matches_riviu_data_sql(db):
    values = ["Cafe A", "Cafe\u200b A, Tiệm  Bánh B", "Cafe e\u0301,  ,Cafe é", " Bún\u00a0Bò Huế ,BÚN BÒ HUẾ"]

    async def check():
        conn = await postgres.connect(db["scanner"])
        try:
            for value in values:
                split = await conn.fetchval("SELECT public.rda_split_doi_tac($1)", value)
                assert split == partners_from_doi_tac(value), value
        finally:
            await conn.close()

    run(check())


# ---- POST /internal/scan --------------------------------------------------------------

@pytest.fixture
def api(db, monkeypatch, tmp_path):
    from riviu import app as backend

    monkeypatch.setattr(store_config, "ACTIVE", store_config.StoreConfig(
        backend="postgres", database_url=db["scanner"],
        scan_tokens=(store_config.ScanToken(store_config.token_digest(TOKEN), frozenset({"scan:run"})),),
    ))
    monkeypatch.setattr(scan_state, "manager", scan_state.ConnectionManager())
    monkeypatch.setattr(scan_state, "SCRAPE_TASK", None)
    monkeypatch.setattr(backend.server_context, "EXCEL_DIR", str(tmp_path))

    async def fake_scan(session, events):
        for item in session.rows:
            session.write_threads_result(item["row"], {"channel": "miri", "metrics": {"views": 0, "likes": 3}, "error": ""})
        await session.flush()
        session.totals = {"success": len(session.rows)}

    monkeypatch.setattr(internal_scan, "SCANNERS", {"tiktok": fake_scan, "threads": fake_scan})
    with TestClient(backend.app, base_url="http://riviu-reports:8000") as client:
        yield client


def _post(client, body):
    return client.post("/internal/scan", json=body, headers={"Authorization": f"Bearer {TOKEN}"})


def test_timer_scan_is_synchronous_and_manual_scan_reuses_the_requested_run(api, db):
    target = insert("threads", ngay_air=dt.date(2026, 10, 2), link_air=THREADS_URL)
    response = _post(api, {"platform": "threads", "thang": "2026-10", "trigger": "timer"})
    assert response.status_code == 200, response.text
    run_id = response.json()["scan_run_id"]
    [scan_run] = run(sql("SELECT * FROM public.scan_run WHERE id = $1", run_id, fetch=True))
    assert scan_run["phase"] == "hoan_tat" and scan_run["finished_at"] is not None and scan_run["mode"] == "timer"
    assert scan_run["thang_nam"] == dt.date(2026, 10, 1)
    assert row("threads", target)["luot_xem"] == 0

    async def request_from_riviu_data():
        conn = await asyncpg.connect(db["app"])
        try:
            return await conn.fetchval("INSERT INTO public.scan_run (platform, thang_nam, phase, mode, started_by) "
                                       "VALUES ('threads', '2026-10-01', 'yeu_cau', 'thu_cong', 'user:an') RETURNING id")
        finally:
            await conn.close()

    requested = run(request_from_riviu_data())
    response = _post(api, {"platform": "threads", "thang": "2026-10", "started_by": "user:an", "scan_run_id": requested})
    assert response.status_code == 202 and response.json()["scan_run_id"] == requested
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        [scan_run] = run(sql("SELECT * FROM public.scan_run WHERE id = $1", requested, fetch=True))
        if scan_run["finished_at"] is not None:
            break
        time.sleep(0.05)
    assert scan_run["phase"] == "hoan_tat" and scan_run["mode"] == "thu_cong"
    items = run(sql("SELECT row_id, status FROM public.scan_run_item WHERE run_id = $1", requested, fetch=True))
    assert [(r["row_id"], r["status"]) for r in items] == [(target, "Success")]


def test_advisory_lock_held_elsewhere_gives_409(api, db):
    async def hold_and_post():
        conn = await asyncpg.connect(db["scanner"])
        try:
            assert await conn.fetchval("SELECT pg_try_advisory_lock(hashtext('scan:threads'))")
            return await asyncio.to_thread(_post, api, {"platform": "threads", "thang": "2026-10", "trigger": "timer"})
        finally:
            await conn.close()

    assert run(hold_and_post()).status_code == 409
    assert _post(api, {"platform": "threads", "thang": "2026-10", "trigger": "timer"}).status_code == 200
    assert rules.parse_thang("2026-10")  # the month stays valid after a refused start


def test_a_real_threads_scan_through_the_endpoint_writes_rows_and_records_the_run(api, db, monkeypatch):
    from riviu.platforms import threads
    from tests.pg_fakes import NoPlaywright

    target = insert("threads", ngay_air=dt.date(2026, 10, 2), link_air=THREADS_URL, doi_tac="Cafe A", luot_xem=9)
    monkeypatch.setattr(internal_scan, "SCANNERS", {"tiktok": internal_scan.scan_tiktok, "threads": internal_scan.scan_threads})
    monkeypatch.setattr(threads, "async_playwright", NoPlaywright)
    monkeypatch.setattr(threads, "fetch_threads_http", lambda _url: {
        "channel": "miri_viu", "metrics": {"views": 372, "likes": 0, "comments": None, "reposts": 1, "shares": 2}, "error": "",
    })
    monkeypatch.setattr(store_config, "ACTIVE", store_config.StoreConfig(
        backend="postgres", database_url=db["scanner"], threads_mode="request",
        scan_tokens=store_config.ACTIVE.scan_tokens,
    ))
    response = _post(api, {"platform": "threads", "thang": "2026-10", "trigger": "timer"})
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["phase"] == "hoan_tat" and summary["totals"]["written"] == 1
    got = row("threads", target)
    assert (got["luot_xem"], got["tim"], got["binh_luan"], got["repost"], got["ten_kenh"]) == (372, 0, None, 1, "miri_viu")
    [scan_run] = run(sql("SELECT * FROM public.scan_run WHERE id = $1", summary["scan_run_id"], fetch=True))
    assert scan_run["diagnostic"] and '"success": 1' in scan_run["diagnostic"]


def test_partner_export_from_riviu_data_keeps_untrusted_text_literal(api, db):
    import io

    import openpyxl

    from riviu import app as backend

    insert("tiktok", ngay_air=dt.date(2026, 10, 2), link_air=TIKTOK_URL, doi_tac="Cafe A, Tiệm B",
           ten_kenh='=HYPERLINK("http://evil","x")', luot_xem=500, tim=0)
    insert("tiktok", ngay_air=dt.date(2026, 9, 30), link_air=TIKTOK_URL + "?sep", doi_tac="Cafe A", luot_xem=900)
    with TestClient(backend.app, base_url="http://127.0.0.1") as local:
        local.get("/")  # the local session cookie (desktop-mode middleware)
        partners = local.get("/riviu-data/report-partners", params={"platform": "tiktok", "thang": "2026-10"})
        response = local.post("/riviu-data/export-report", json={"platform": "tiktok", "thang": "2026-10", "partners": ["cafe a"]})
    assert partners.status_code == 200, partners.text
    assert [(p["name"], p["linkCount"]) for p in partners.json()["partners"]] == [("Cafe A", 1), ("Tiệm B", 1)]
    assert response.status_code == 200, response.text
    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    channel = sheet["B4"]
    assert channel.value == '=HYPERLINK("http://evil","x")' and channel.data_type == "s"
    assert (sheet["C4"].value, sheet["D4"].value, sheet["E4"].value) == (TIKTOK_URL, 500, 0)
    # The September row (another thang_nam) is not in the October report.
    assert TIKTOK_URL + "?sep" not in [cell.value for cell in sheet["C"]]
