import json
from datetime import date, datetime, timedelta

from work_report import watchdog
from work_report.watchdog import (
    _due_by,
    check_report_freshness,
    check_token_health,
)


class FakeLark:
    def __init__(self, auth=None):
        self.calls = []
        self._auth = auth if auth is not None else {"ok": True}

    def call(self, args, timeout=45):
        self.calls.append(args)
        if args[:2] == ["auth", "status"]:
            return self._auth
        return {"ok": True}

    def sends(self):
        return [c for c in self.calls if c[:2] == ["im", "+messages-send"]]


def test_due_by_friday_is_monday_afternoon():
    # 2020-01-03 is a Friday; its report is due the following Monday at 14:00.
    assert _due_by(date(2020, 1, 3)) == datetime(2020, 1, 6, 14, 0)


def test_freshness_alerts_for_overdue_missing_day(tmp_path):
    lark = FakeLark()
    state = tmp_path / "watchdog_alert.state"

    check_report_freshness(
        lark, "ou_u", "bot", flag_dir=tmp_path, state_path=state, log=lambda _m: None, today=date(2020, 1, 6)
    )

    sends = lark.sends()
    assert len(sends) == 1
    assert "漏推提醒" in sends[0][sends[0].index("--markdown") + 1]
    assert json.loads(state.read_text()).get("missed_2020-01-06")


def test_freshness_silent_when_all_recent_workdays_flagged(tmp_path):
    lark = FakeLark()
    today = date(2020, 1, 6)
    for back in range(0, 9):
        d = today - timedelta(days=back)
        if d.weekday() < 5:
            (tmp_path / f"daily_{d}.done").touch()

    check_report_freshness(
        lark, "ou_u", "bot", flag_dir=tmp_path, state_path=tmp_path / "s.state", log=lambda _m: None, today=today
    )

    assert lark.sends() == []


def test_freshness_throttled_when_recently_alerted(tmp_path):
    lark = FakeLark()
    state = tmp_path / "s.state"
    state.write_text(json.dumps({"missed_2020-01-06": datetime.now().isoformat()}))

    check_report_freshness(
        lark, "ou_u", "bot", flag_dir=tmp_path, state_path=state, log=lambda _m: None, today=date(2020, 1, 6)
    )

    assert lark.sends() == []


def test_token_alert_when_expiring_within_three_days(tmp_path):
    soon = (datetime.now().astimezone() + timedelta(days=2)).isoformat()
    lark = FakeLark(auth={"ok": True, "refreshExpiresAt": soon})

    check_token_health(lark, "ou_u", "user", state_path=tmp_path / "s.state", log=lambda _m: None)

    sends = lark.sends()
    assert len(sends) == 1
    assert "授权即将到期" in sends[0][sends[0].index("--markdown") + 1]


def test_token_alert_reads_nested_identities_shape(tmp_path):
    # Current lark-cli shape: token fields under identities.user.
    soon = (datetime.now().astimezone() + timedelta(days=1)).isoformat()
    lark = FakeLark(auth={"identities": {"user": {"refreshExpiresAt": soon}}})

    check_token_health(lark, "ou_u", "user", state_path=tmp_path / "s.state", log=lambda _m: None)

    assert len(lark.sends()) == 1


def test_token_no_alert_when_far_off(tmp_path):
    far = (datetime.now().astimezone() + timedelta(days=30)).isoformat()
    lark = FakeLark(auth={"ok": True, "refreshExpiresAt": far})

    check_token_health(lark, "ou_u", "user", state_path=tmp_path / "s.state", log=lambda _m: None)

    assert lark.sends() == []


def test_run_watchdog_without_lark_is_noop():
    assert watchdog.run_watchdog(config={}, binaries={"lark": ""}, log=lambda _m: None) == 0
